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
    static_routes: list[tuple[str, str]] = field(default_factory=list)   # (prefix, next hop)


def _hosts(subnet: str) -> list[str]:
    return [str(h) for h in ipaddress.ip_network(subnet).hosts()]


def load(path: Path = SOT) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate(sot: dict) -> None:
    routers, fibres, links = sot["routers"], sot["fibres"], sot["links"]
    errors: list[str] = []
    ids: dict[int, str] = {}
    for name, r in routers.items():          # router-id / loopback 10.0.0.<id> must be unique
        if r["id"] in ids:
            errors.append(f"{name}: router id {r['id']} already used by {ids[r['id']]}")
        ids[r["id"]] = name
    for uid, u in sot.get("upstreams", {}).items():
        if u["asn"] == sot["meta"]["core_asn"]:
            errors.append(f"{uid}: upstream ASN {u['asn']} equals our own ASN (the eBGP session would be iBGP)")
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
    acc = sot.get("access")
    if acc:
        for oid, o in acc["olts"].items():
            if o["router"] not in routers:
                errors.append(f"{oid}: unknown router {o['router']}")
            use_if(o["router"], o["router_if"], oid)
            use_net(o["uplink_subnet"], oid); use_net(o["pon_subnet"], oid)
        ports: set[tuple[str, int]] = set()
        for sid, sp in acc["splitters"].items():
            if sp["olt"] not in acc["olts"]:
                errors.append(f"{sid}: unknown OLT {sp['olt']}")
        for nid, n in acc["onts"].items():
            if n["splitter"] not in acc["splitters"]:
                errors.append(f"{nid}: unknown splitter {n['splitter']}")
            elif not 1 <= n["port"] <= acc["splitters"][n["splitter"]]["ratio"]:
                errors.append(f"{nid}: port {n['port']} outside 1..{acc['splitters'][n['splitter']]['ratio']}")
            if (n["splitter"], n["port"]) in ports:
                errors.append(f"{nid}: splitter port {n['splitter']}:{n['port']} used twice")
            ports.add((n["splitter"], n["port"]))
    sec = sot.get("security") or {}
    infra = [ipaddress.ip_network("10.0.0.0/8")]                    # loopbacks + core/uplink links
    for e in sec.get("blocklist") or []:
        try:
            ip = ipaddress.ip_network(e["ip"])
        except (KeyError, ValueError):
            errors.append(f"blocklist: bad entry {e}")
            continue
        if any(ip.overlaps(n) for n in infra) or ip.prefixlen < 24:
            errors.append(f"blocklist: {e['ip']} would block infrastructure or too wide (guardrail)")
        if not e.get("reason"):
            errors.append(f"blocklist: {e['ip']} needs a reason (audit)")
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
    for oid, o in (sot.get("access") or {}).get("olts", {}).items():
        r_ip, olt_ip = _hosts(o["uplink_subnet"])[:2]
        r = out[o["router"]]
        r.ifaces.append(Iface(o["router_if"], f"{r_ip}/30", f"uplink to {oid} (PON {o['pon_subnet']})", "customer"))
        r.static_routes.append((o["pon_subnet"], olt_ip))
        r.customer_nets.append(o["pon_subnet"])
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
            "extra_lans": [],
        })
        atk = (sot.get("security") or {}).get("attacker")
        if atk and atk["upstream"] == uid:
            anet = ipaddress.ip_network(atk["subnet"])
            res[-1]["extra_lans"].append({"if": atk["up_if"], "gw": f"{_hosts(atk['subnet'])[0]}/{anet.prefixlen}",
                                          "net": atk["subnet"], "desc": "attacker network (security scenarios)"})
    return res


def security(sot: dict) -> dict:
    """Derived security data: attacker host, CPE SSH, PON isolation, OLT blocklist."""
    sec = sot.get("security") or {}
    out = {"attacker": None, "cpe_ssh": sec.get("cpe_ssh"), "pon_isolation": bool(sec.get("pon_isolation")),
           "blocklist": sec.get("blocklist") or []}
    if sec.get("attacker"):
        a = sec["attacker"]
        net = ipaddress.ip_network(a["subnet"])
        out["attacker"] = {**a, "ip": f"{net.network_address + 66}/{net.prefixlen}", "gw": _hosts(a["subnet"])[0]}
    return out


def hosts(sot: dict) -> list[dict]:
    """Customer hosts: address .10, default route via the router's .1."""
    res = []
    for cid, c in sot["customers"].items():
        net = ipaddress.ip_network(c["subnet"])
        res.append({"name": cid, "router": c["router"], "router_if": c["router_if"],
                    "ip": f"{net.network_address + 10}/{net.prefixlen}", "gw": _hosts(c["subnet"])[0]})
    return res


def access(sot: dict) -> dict:
    """Derived access data: OLT gateway, ONT addresses and the tree (feeder + drops) for the twin."""
    acc = sot.get("access")
    if not acc:
        return {"olts": [], "splitters": [], "onts": []}
    olts, spls, onts = [], [], []
    for oid, o in acc["olts"].items():
        r_ip, olt_ip = _hosts(o["uplink_subnet"])[:2]
        pon = ipaddress.ip_network(o["pon_subnet"])
        olts.append({"name": oid, **o, "router_ip": r_ip, "uplink_ip": f"{olt_ip}/30",
                     "pon_gw": f"{pon.network_address + 1}/{pon.prefixlen}"})
    for sid, sp in acc["splitters"].items():
        spls.append({"name": sid, **sp})
    for nid, n in acc["onts"].items():
        o = acc["olts"][acc["splitters"][n["splitter"]]["olt"]]
        pon = ipaddress.ip_network(o["pon_subnet"])
        onts.append({"name": nid, **n, "ip": f"{pon.network_address + 10 + n['port']}/{pon.prefixlen}",
                     "gw": str(pon.network_address + 1), "splitter_if": f"eth{n['port'] + 1}"})
    return {"olts": olts, "splitters": spls, "onts": onts}
