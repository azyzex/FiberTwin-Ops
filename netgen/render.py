"""Render everything the twin needs from the source of truth, with Nornir + Jinja2.

    python -m netgen.render            -> build/ft-core/
       configs/<node>/{frr.conf,daemons,vtysh.conf}   one task per Nornir host
       ft-core.clab.yml                               containerlab topology
       links.tsv                                      link shaping table (capacity, delay)
       topology.json                                  data contract v0 (the portal's input)

Nornir holds the inventory (one host per router, with its derived data) and runs the render task
on every host in parallel; Jinja2 turns host data into config text.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from nornir.core import Nornir
from nornir.core.inventory import Defaults, Groups, Host, Hosts, Inventory
from nornir.core.task import Result, Task
from nornir.plugins.runners import ThreadedRunner

from . import model

ROOT = model.ROOT
OUT = ROOT / "build" / "ft-core"
ENV = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                  undefined=StrictUndefined, keep_trailing_newline=True,
                  trim_blocks=True, lstrip_blocks=True)


def write(path: Path, text: str) -> None:
    """Always LF line endings: FRR silently ignores daemons=yes\\r (lesson from Slice 1a)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").encode())


def build_inventory(sot: dict) -> Inventory:
    rs = model.routers(sot)
    meta = sot["meta"]
    cost = {lid: l["ospf_cost"] for lid, l in sot["links"].items()}
    hosts = Hosts()
    for name, r in rs.items():
        peers = [{"name": p.name, "id": p.id} for p in rs.values() if p.name != name]
        hosts[name] = Host(name=name, data={
            "kind": "core", "r": r, "peers": peers, "asn": meta["core_asn"], "bfd": meta["bfd"], "cost": cost})
    for u in model.upstreams(sot):
        hosts[u["name"]] = Host(name=u["name"], data={"kind": "upstream", "u": u})
    return Inventory(hosts=hosts, groups=Groups(), defaults=Defaults())


def render_node(task: Task, out: Path) -> Result:
    d = task.host.data
    folder = out / "configs" / task.host.name
    if d["kind"] == "core":
        frr = ENV.get_template("core-frr.conf.j2").render(**{k: d[k] for k in ("r", "peers", "asn", "bfd", "cost")})
        daemons = ENV.get_template("daemons.j2").render(ospf=True)
    else:
        frr = ENV.get_template("upstream-frr.conf.j2").render(u=d["u"])
        daemons = ENV.get_template("daemons.j2").render(ospf=False)
    write(folder / "frr.conf", frr)
    write(folder / "daemons", daemons)
    write(folder / "vtysh.conf", "service integrated-vtysh-config\n")
    return Result(host=task.host, result=f"{len(frr.splitlines())} lines")


def clab_topology(sot: dict) -> str:
    meta, rs = sot["meta"], sot["routers"]
    def binds(n: str) -> str:
        return (f"[configs/{n}/daemons:/etc/frr/daemons, configs/{n}/frr.conf:/etc/frr/frr.conf, "
                f"configs/{n}/vtysh.conf:/etc/frr/vtysh.conf]")
    lines = [
        "# GENERATED from sot/network.yaml by netgen.render. Do not edit: change the source of truth.",
        f"name: {meta['name']}", "", "topology:", "  kinds:", "    linux:", f"      image: {meta['frr_image']}",
        "  nodes:",
    ]
    for n, r in rs.items():
        lines.append(f"    {n}: {{ kind: linux, binds: {binds(n)}, labels: {{ graph-posX: \"{r['pos']['x']}\", "
                     f"graph-posY: \"{r['pos']['y']}\", graph-group: core }} }}")
    for u in model.upstreams(sot):
        lines.append(f"    {u['name']}: {{ kind: linux, binds: {binds(u['name'])}, labels: {{ graph-group: upstream }} }}")
        ex = [f"ip addr add {u['server_ip']}/{u['server_gw'].split('/')[1]} dev eth1",
              f"ip route replace default via {u['server_gw'].split('/')[0]}"]
        ex += [f"iperf3 -s -D -p {p}" for p in u["iperf_ports"]]
        lines += [f"    {u['server']}:", "      kind: linux", f"      image: {meta['host_image']}",
                  f"      exec: {json.dumps(ex)}"]
    for h in model.hosts(sot):
        ex = [f"ip addr add {h['ip']} dev eth1", f"ip route replace default via {h['gw']}"]
        lines += [f"    {h['name']}:", "      kind: linux", f"      image: {meta['host_image']}", f"      exec: {json.dumps(ex)}"]
    acc = model.access(sot)
    for o in acc["olts"]:
        # the OLT: L3 gateway of the PON; downstream capacity shared by all ONTs (htb on the PON port)
        ex = ["sysctl -w net.ipv4.ip_forward=1", f"ip addr add {o['uplink_ip']} dev {o['uplink_if']}",
              f"ip addr add {o['pon_gw']} dev {o['pon_if']}", f"ip route replace default via {o['router_ip']}",
              f"tc qdisc add dev {o['pon_if']} root handle 1: htb default 10",
              f"tc class add dev {o['pon_if']} parent 1: classid 1:10 htb rate {o['down_mbps']}mbit ceil {o['down_mbps']}mbit",
              f"tc qdisc add dev {o['pon_if']} parent 1:10 handle 10: netem delay 0us limit 10000"]
        lines += [f"    {o['name']}:", "      kind: linux", f"      image: {meta['host_image']}",
                  "      labels: { graph-group: access }", f"      exec: {json.dumps(ex)}"]
    for sp in acc["splitters"]:
        ports = [f"eth{n['port'] + 1}" for n in acc["onts"] if n["splitter"] == sp["name"]]
        olt = next(o for o in acc["olts"] if o["name"] == sp["olt"])
        # the splitter: a Linux bridge. eth1 = feeder (to the OLT), eth2.. = drops. Upstream (ONTs -> OLT)
        # is shared and shaped on the feeder port; every port gets a netem stage for the modelled optics.
        ex = ["ip link add br0 type bridge", "ip link set br0 up"]
        for i in ["eth1", *ports]:
            ex += [f"ip link set {i} master br0",
                   f"tc qdisc add dev {i} root handle 1: htb default 10",
                   f"tc class add dev {i} parent 1: classid 1:10 htb rate "
                   + (f"{olt['up_mbps']}mbit ceil {olt['up_mbps']}mbit" if i == "eth1" else "1000mbit"),
                   f"tc qdisc add dev {i} parent 1:10 handle 10: netem delay 0us limit 10000"]
        lines += [f"    {sp['name']}:", "      kind: linux", f"      image: {meta['host_image']}",
                  "      labels: { graph-group: access }", f"      exec: {json.dumps(ex)}"]
    for n in acc["onts"]:
        ex = [f"ip addr add {n['ip']} dev eth1", f"ip route replace default via {n['gw']}",
              "tc qdisc add dev eth1 root handle 1: htb default 10",
              "tc class add dev eth1 parent 1: classid 1:10 htb rate 1000mbit",
              "tc qdisc add dev eth1 parent 1:10 handle 10: netem delay 0us limit 10000"]
        lines += [f"    {n['name']}:", "      kind: linux", f"      image: {meta['host_image']}",
                  "      labels: { graph-group: access }", f"      exec: {json.dumps(ex)}"]
    lines.append("  links:")
    for lid, l in sot["links"].items():
        km = sum(sot["fibres"][f]["length_km"] for f in l["fibres"])
        lines.append(f"    - endpoints: [\"{l['a']}:{l['a_if']}\", \"{l['b']}:{l['b_if']}\"]   # {lid}, {km} km over {'+'.join(l['fibres'])}")
    for u in model.upstreams(sot):
        lines.append(f"    - endpoints: [\"{u['core_router']}:{sot['upstreams'][u['name']]['attach']['router_if']}\", \"{u['name']}:{u['attach_if']}\"]   # eBGP")
        lines.append(f"    - endpoints: [\"{u['name']}:{u['server_if']}\", \"{u['server']}:eth1\"]")
    for h in model.hosts(sot):
        lines.append(f"    - endpoints: [\"{h['router']}:{h['router_if']}\", \"{h['name']}:eth1\"]")
    for o in acc["olts"]:
        lines.append(f"    - endpoints: [\"{o['router']}:{o['router_if']}\", \"{o['name']}:{o['uplink_if']}\"]   # OLT uplink")
    for sp in acc["splitters"]:
        olt = next(o for o in acc["olts"] if o["name"] == sp["olt"])
        lines.append(f"    - endpoints: [\"{olt['name']}:{olt['pon_if']}\", \"{sp['name']}:eth1\"]   # feeder {sp['feeder_km']} km")
    for n in acc["onts"]:
        lines.append(f"    - endpoints: [\"{n['splitter']}:{n['splitter_if']}\", \"{n['name']}:eth1\"]   # drop {n['drop_km']} km")
    return "\n".join(lines) + "\n"


def links_tsv(sot: dict) -> str:
    meta = sot["meta"]
    rows = ["# link\ta_end\tb_end\tkm\tdelay_us\trate"]
    for lid, l in sot["links"].items():
        km = sum(sot["fibres"][f]["length_km"] for f in l["fibres"])
        rows.append(f"{lid}\t{l['a']}:{l['a_if']}\t{l['b']}:{l['b_if']}\t{km}\t{km * meta['delay_us_per_km']}\t{meta['capacity_mbps']}mbit")
    return "\n".join(rows) + "\n"


def access_tsv(sot: dict) -> str:
    """Where the modelled PON optics act: one row per ONT (its drop) + the shared feeder."""
    acc = model.access(sot)
    rows = ["# ont\tsplitter\tsplitter_if\tont_if\tdrop_km\tfeeder_km"]
    for n in acc["onts"]:
        sp = next(s for s in acc["splitters"] if s["name"] == n["splitter"])
        rows.append(f"{n['name']}\t{n['splitter']}\t{n['splitter_if']}\teth1\t{n['drop_km']}\t{sp['feeder_km']}")
    return "\n".join(rows) + "\n"


def topology_json(sot: dict) -> dict:
    """Data contract v0 (contracts/topology.schema.json): what the portal draws."""
    rs = sot["routers"]
    nodes = [{"id": n, "role": "core", "label": n.upper(), "site": r["site"], "loopback": f"10.0.0.{r['id']}",
              "pos": r["pos"]} for n, r in rs.items()]
    acc = model.access(sot)
    nodes += [{"id": o["name"], "role": "olt", "label": "OLT", "pos": o["pos"]} for o in acc["olts"]]
    nodes += [{"id": sp["name"], "role": "splitter", "label": f"Splitter 1:{sp['ratio']}", "pos": sp["pos"]} for sp in acc["splitters"]]
    for n in acc["onts"]:
        sp = next(s for s in acc["splitters"] if s["name"] == n["splitter"])
        nodes.append({"id": n["name"], "role": "ont", "label": n["name"].upper().replace("ONT", "ONT "),
                      "pos": {"x": sp["pos"]["x"] + 90, "y": sp["pos"]["y"] - 120 + 80 * (n["port"] - 1)}})
    access_links = [{"id": f"A-up-{o['name']}", "a": o["router"], "b": o["name"], "segment": "uplink"} for o in acc["olts"]]
    access_links += [{"id": f"A-feed-{sp['name']}", "a": sp["olt"], "b": sp["name"], "segment": "feeder",
                      "length_km": sp["feeder_km"]} for sp in acc["splitters"]]
    access_links += [{"id": f"A-drop-{n['name']}", "a": n["splitter"], "b": n["name"], "segment": "drop",
                      "length_km": n["drop_km"]} for n in acc["onts"]]
    out = {
        "contract_version": "0.1",
        "nodes": nodes,
        "fibres": [{"id": f, "a": v["a"], "b": v["b"], "length_km": v["length_km"], "spans": v["spans"]}
                   for f, v in sot["fibres"].items()],
        "lightpaths": [{"id": f"LP{lid[1:]}", "fibres": l["fibres"], "wavelength": l["wavelength"], "ip_link": lid}
                       for lid, l in sot["links"].items()],
        "ip_links": [{"id": lid, "a": l["a"], "a_if": l["a_if"], "b": l["b"], "b_if": l["b_if"],
                      "capacity_mbps": sot["meta"]["capacity_mbps"], "ospf_cost": l["ospf_cost"], "srlgs": l["fibres"]}
                     for lid, l in sot["links"].items()],
    }
    if access_links:
        out["access_links"] = access_links
    return out


def render(sot_path: Path = model.SOT, out: Path = OUT) -> Path:
    sot = model.load(sot_path)
    nr = Nornir(inventory=build_inventory(sot), runner=ThreadedRunner(num_workers=8))
    results = nr.run(task=render_node, out=out)
    if results.failed:
        for host, r in results.items():
            if r.failed:
                print(f"{host}: {r.exception}", file=sys.stderr)
        raise SystemExit(1)
    write(out / f"{sot['meta']['name']}.clab.yml", clab_topology(sot))
    write(out / "links.tsv", links_tsv(sot))
    write(out / "access.tsv", access_tsv(sot))
    write(out / "topology.json", json.dumps(topology_json(sot), indent=1) + "\n")
    return out


if __name__ == "__main__":
    out = render()
    print(f"rendered {len(list((out / 'configs').iterdir()))} devices -> {out.relative_to(ROOT)}")
