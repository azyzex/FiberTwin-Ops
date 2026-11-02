"""lab-exporter: turns the running twin into Prometheus metrics.

Reads, through the Docker API (exec into the containers):
  - interface counters and state on both ends of every core link      (every 5 s)
  - the netem qdisc counters: packets sent / dropped by the impairment (every 5 s)
  - OSPF Full neighbours, BGP Established sessions, BFD peers up       (every 15 s)
  - per-class probes between the demand pairs: RTT, jitter, loss       (every 10 s)
    classes are marked with DSCP (voice = EF, video = AF41, best effort = 0)

The link list comes from build/ft-core/links.tsv and demands from sot/network.yaml,
so monitoring always matches the source of truth.
Security note: Docker socket access is root-equivalent on the lab host. Acceptable for a lab,
documented; a production exporter would run inside each node instead.
"""
from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import docker
import yaml
from prometheus_client import Gauge, start_http_server

PREFIX = os.getenv("FT_PREFIX", "clab-ft-core-")
BUILD = Path(os.getenv("FT_BUILD", "/ft/build/ft-core"))
SOT = Path(os.getenv("FT_SOT", "/ft/sot/network.yaml"))
CLASSES = {"voice": "0xb8", "video": "0x88", "best_effort": "0x00"}

client = docker.from_env()

L = ["node", "ifc", "link"]
IF_TX_BYTES = Gauge("ft_iface_tx_bytes_total", "Bytes sent on a core link interface (counter value)", L)
IF_TX_PKTS = Gauge("ft_iface_tx_packets_total", "Packets sent on a core link interface (counter value)", L)
IF_UP = Gauge("ft_iface_up", "1 if the interface is operationally up", L)
NETEM_SENT = Gauge("ft_netem_sent_packets_total", "Packets that passed the link's netem qdisc (counter value)", L)
NETEM_DROP = Gauge("ft_netem_dropped_total", "Packets dropped by the link's netem qdisc (counter value)", L)
NETEM_LOSS_CFG = Gauge("ft_netem_loss_configured_pct", "Configured netem loss on this interface", L)
CAPACITY = Gauge("ft_link_capacity_bps", "Shaped capacity of a core link", ["link"])
OSPF_FULL = Gauge("ft_ospf_full_neighbors", "OSPF neighbours in Full state", ["node"])
OSPF_EXPECTED = Gauge("ft_ospf_expected_neighbors", "OSPF neighbours expected from the source of truth", ["node"])
BGP_EST = Gauge("ft_bgp_established", "BGP sessions Established", ["node"])
BFD_UP = Gauge("ft_bfd_up", "BFD sessions up", ["node"])
P = ["src", "dst", "class"]
PROBE_RTT = Gauge("ft_probe_rtt_ms", "Probe round-trip time (average of the burst)", P)
PROBE_JIT = Gauge("ft_probe_jitter_ms", "Probe jitter (ping mdev over the burst)", P)
PROBE_LOSS = Gauge("ft_probe_loss_ratio", "Probe loss ratio (0-1) over the burst", P)
SCRAPE_OK = Gauge("ft_exporter_last_success_timestamp", "Last successful collection per collector", ["collector"])


def sh(node: str, cmd: str, timeout: float = 10) -> str:
    c = client.containers.get(PREFIX + node)
    res = c.exec_run(["sh", "-c", cmd], demux=False)
    return res.output.decode(errors="replace") if res.output else ""


def links() -> list[dict]:
    out = []
    for line in (BUILD / "links.tsv").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        link, a, b, km, delay, rate = line.split("\t")
        out.append({"link": link, "ends": [a, b], "rate": rate})
    return out


def rate_bps(rate: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(k|m|g)?bit", rate)
    mult = {"k": 1e3, "m": 1e6, "g": 1e9, None: 1}[m.group(2)]
    return float(m.group(1)) * mult


# two separate patterns: an optional group after a lazy match silently matched nothing
NETEM_STATS = re.compile(r"qdisc netem [^\n]*\n\s*Sent \d+ bytes (\d+) pkt \(dropped (\d+)")
NETEM_LOSS = re.compile(r"qdisc netem [^\n]*?loss (\d+(?:\.\d+)?)%")


def collect_links() -> None:
    by_node: dict[str, list[tuple[str, str]]] = {}
    for l in links():
        CAPACITY.labels(l["link"]).set(rate_bps(l["rate"]))
        for end in l["ends"]:
            node, ifc = end.split(":")
            by_node.setdefault(node, []).append((ifc, l["link"]))
    for node, ifcs in by_node.items():
        script = "; ".join(
            f"echo IF {i} $(cat /sys/class/net/{i}/statistics/tx_bytes) $(cat /sys/class/net/{i}/statistics/tx_packets) "
            f"$(cat /sys/class/net/{i}/operstate); echo QD {i}; tc -s qdisc show dev {i} | grep -A1 netem"
            for i, _ in ifcs)
        out = sh(node, script)
        link_of = dict(ifcs)
        for m in re.finditer(r"^IF (\S+) (\d+) (\d+) (\S+)", out, re.M):
            i = m.group(1)
            lab = (node, i, link_of[i])
            IF_TX_BYTES.labels(*lab).set(int(m.group(2)))
            IF_TX_PKTS.labels(*lab).set(int(m.group(3)))
            IF_UP.labels(*lab).set(1 if m.group(4) == "up" else 0)
        for block in out.split("QD ")[1:]:
            i = block.split()[0]
            m = NETEM_STATS.search(block)
            if m:
                lab = (node, i, link_of[i])
                lm = NETEM_LOSS.search(block)
                NETEM_LOSS_CFG.labels(*lab).set(float(lm.group(1)) if lm else 0.0)
                NETEM_SENT.labels(*lab).set(int(m.group(1)))
                NETEM_DROP.labels(*lab).set(int(m.group(2)))
    SCRAPE_OK.labels("links").set(time.time())


def collect_control_plane(sot: dict) -> None:
    import json
    for node in sot["routers"]:
        OSPF_EXPECTED.labels(node).set(sum(1 for l in sot["links"].values() if node in (l["a"], l["b"])))
        try:
            nb = json.loads(sh(node, "vtysh -c 'show ip ospf neighbor json'"))["neighbors"]
            OSPF_FULL.labels(node).set(sum(1 for v in nb.values() for s in v if s.get("converged") == "Full"))
            peers = json.loads(sh(node, "vtysh -c 'show bgp ipv4 unicast summary json'")).get("peers", {})
            BGP_EST.labels(node).set(sum(1 for p in peers.values() if p.get("state") == "Established"))
            bfd = json.loads(sh(node, "vtysh -c 'show bfd peers json'"))
            BFD_UP.labels(node).set(sum(1 for p in bfd if p.get("status") == "up"))
        except Exception as e:  # a router being restarted must not kill the exporter
            print(f"control plane {node}: {e}", flush=True)
    SCRAPE_OK.labels("control_plane").set(time.time())


PING_LOSS = re.compile(r"([\d.]+)% packet loss")
PING_RTT = re.compile(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms")


def probe(src: str, dst: str, dst_ip: str, cls: str, tos: str) -> None:
    out = sh(src, f"ping -Q {tos} -c 10 -i 0.1 -W 1 -q {dst_ip}")
    lm, rm = PING_LOSS.search(out), PING_RTT.search(out)
    if not lm:
        return
    PROBE_LOSS.labels(src, dst, cls).set(float(lm.group(1)) / 100)
    if rm:
        PROBE_RTT.labels(src, dst, cls).set(float(rm.group(2)))
        PROBE_JIT.labels(src, dst, cls).set(float(rm.group(4)))


def collect_probes(sot: dict, pool: ThreadPoolExecutor) -> None:
    ips = {c: str(__import__("ipaddress").ip_network(v["subnet"]).network_address + 10) for c, v in sot["customers"].items()}
    for u in sot["upstreams"].values():
        ips[u["server"]["name"]] = str(__import__("ipaddress").ip_network(u["server"]["subnet"]).network_address + 10)
    jobs = [pool.submit(probe, d["src"], d["dst"], ips[d["dst"]], cls, tos)
            for d in sot["demands"] for cls, tos in CLASSES.items()]
    for j in jobs:
        j.result()
    SCRAPE_OK.labels("probes").set(time.time())


def loop(fn, every: float, *args) -> None:
    while True:
        t0 = time.time()
        try:
            fn(*args)
        except Exception as e:
            print(f"{fn.__name__}: {e}", flush=True)
        time.sleep(max(0.5, every - (time.time() - t0)))


if __name__ == "__main__":
    sot = yaml.safe_load(SOT.read_text())
    start_http_server(9105)
    pool = ThreadPoolExecutor(max_workers=9)
    threading.Thread(target=loop, args=(collect_control_plane, 15, sot), daemon=True).start()
    threading.Thread(target=loop, args=(collect_probes, 10, sot, pool), daemon=True).start()
    print("lab-exporter on :9105", flush=True)
    loop(collect_links, 5)
