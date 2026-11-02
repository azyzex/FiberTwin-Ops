"""Load the source of truth (sot/network.yaml) and derive what every device needs.

Pure Python, no I/O except reading the YAML: everything here is unit-tested.
Addressing rule: in each /30 the 'a' end gets the first usable address and the 'b' end the second;
on a customer or server LAN the router/upstream gets .1 and the host gets .10.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOT = ROOT / "sot" / "network.yaml"


class SotError(ValueError):
    """The source of truth is inconsistent."""


@dataclass
class Iface:
    name: str
    ip: str           # with prefix length, e.g. 10.1.12.1/30
    description: str
    kind: str         # core | customer | upstream
    link: str | None = None
    peer: str | None = None


@dataclass
class Router:
    name: str
    id: int
    site: str
    loopback: str
    ifaces: list[Iface] = field(default_factory=list)
    customer_nets: list[str] = field(default_factory=list)
    upstream: dict | None = None   # set on the border router


def _hosts(subnet: str) -> list[str]:
    return [str(h) for h in ipaddress.ip_network(subnet).hosts()]


def load(path: Path = SOT) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate(sot: dict) -> None:
    routers, fibres, links = sot["routers"], sot["fibres"], sot["links"]
    errors: list[str] = []
    for lid, l in links.items():
        for end in ("a", "b"):
            if l[end] not in routers:
                errors.append(f"{lid}: unknown router {l[end]}")
        for f in l["fibres"]:
            if f not in fibres:
                errors.append(f"{lid}: unknown fibre {f}")
        if ipaddress.ip_network(l["subnet"]).prefixlen != 30:
            errors.append(f"{lid}: core links use /30")
        if not 1 <= l["ospf_cost"] <= 65535:
            errors.append(f"{lid}: ospf_cost out of range")
        # the lightpath must be a continuous route from a to b over its fibres
        if any(f not in fibres for f in l["fibres"]):
            continue
        here = l["a"]
        for f in l["fibres"]:
            fa, fb = fibres[f]["a"], fibres[f]["b"]
            if here == fa:
                here = fb
            elif here == fb:
                here = fa
            else:
                errors.append(f"{lid}: fibre {f} does not continue the lightpath from {here}")
                break
        else:
            if here != l["b"]:
                errors.append(f"{lid}: lightpath ends at {here}, not {l['b']}")
    # no interface or subnet used twice
    seen_if: set[tuple[str, str]] = set()
    seen_net: set[str] = set()
    def use_if(r: str, i: str, who: str) -> None:
        if (r, i) in seen_if:
            errors.append(f"{who}: interface {r}:{i} used twice")
        seen_if.add((r, i))
    def use_net(n: str, who: str) -> None:
        net = ipaddress.ip_network(n)
        for other in seen_net:
            if net.overlaps(ipaddress.ip_network(other)):
                errors.append(f"{who}: subnet {n} overlaps {other}")
        seen_net.add(n)
    for lid, l in links.items():
        use_if(l["a"], l["a_if"], lid); use_if(l["b"], l["b_if"], lid); use_net(l["subnet"], lid)
    for cid, c in sot["customers"].items():
        use_if(c["router"], c["router_if"], cid); use_net(c["subnet"], cid)
    for uid, u in sot["upstreams"].items():
        use_if(u["attach"]["router"], u["attach"]["router_if"], uid)
        use_net(u["attach"]["subnet"], uid); use_net(u["server"]["subnet"], uid)
    if errors:
        raise SotError("; ".join(errors))


def routers(sot: dict) -> dict[str, Router]:
    validate(sot)
    out = {
        name: Router(name=name, id=r["id"], site=r["site"], loopback=f"10.0.0.{r['id']}/32")
        for name, r in sot["routers"].items()
    }
    for lid, l in sot["links"].items():
        a_ip, b_ip = _hosts(l["subnet"])[:2]
        out[l["a"]].ifaces.append(Iface(l["a_if"], f"{a_ip}/30", f"{lid} to {l['b']}", "core", lid, l["b"]))
        out[l["b"]].ifaces.append(Iface(l["b_if"], f"{b_ip}/30", f"{lid} to {l['a']}", "core", lid, l["a"]))
    for cid, c in sot["customers"].items():
        net = ipaddress.ip_network(c["subnet"])
        gw = f"{_hosts(c['subnet'])[0]}/{net.prefixlen}"
        r = out[c["router"]]
        r.ifaces.append(Iface(c["router_if"], gw, f"customer LAN ({cid})", "customer"))
        r.customer_nets.append(c["subnet"])
    for uid, u in sot["upstreams"].items():
        at = u["attach"]
        r_ip, up_ip = _hosts(at["subnet"])[:2]
        r = out[at["router"]]
        r.ifaces.append(Iface(at["router_if"], f"{r_ip}/30", f"eBGP to {uid} (AS {u['asn']})", "upstream"))
        r.upstream = {"name": uid, "asn": u["asn"], "peer_ip": up_ip}
    return out


def upstreams(sot: dict) -> list[dict]:
    """Data for each upstream router + its server."""
    res = []
    for uid, u in sot["upstreams"].items():
        at, sv = u["attach"], u["server"]
        r_ip, up_ip = _hosts(at["subnet"])[:2]
        snet = ipaddress.ip_network(sv["subnet"])
        res.append({
            "name": uid, "asn": u["asn"], "router_id": u["router_id"], "core_asn": sot["meta"]["core_asn"],
            "attach_if": at["up_if"], "attach_ip": f"{up_ip}/30", "core_peer_ip": r_ip, "core_router": at["router"],
            "server_if": sv["up_if"], "server_gw": f"{_hosts(sv['subnet'])[0]}/{snet.prefixlen}",
            "server_net": sv["subnet"], "server": sv["name"], "server_ip": str(snet.network_address + 10),
            "iperf_ports": sv.get("iperf_ports", []),
        })
    return res


def hosts(sot: dict) -> list[dict]:
    """Customer hosts: address .10, default route via the router's .1."""
    res = []
    for cid, c in sot["customers"].items():
        net = ipaddress.ip_network(c["subnet"])
        res.append({"name": cid, "router": c["router"], "router_if": c["router_if"],
                    "ip": f"{net.network_address + 10}/{net.prefixlen}", "gw": _hosts(c["subnet"])[0]})
    return res
