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
    lines.append("  links:")
    for lid, l in sot["links"].items():
        km = sum(sot["fibres"][f]["length_km"] for f in l["fibres"])
        lines.append(f"    - endpoints: [\"{l['a']}:{l['a_if']}\", \"{l['b']}:{l['b_if']}\"]   # {lid}, {km} km over {'+'.join(l['fibres'])}")
    for u in model.upstreams(sot):
        lines.append(f"    - endpoints: [\"{u['core_router']}:{sot['upstreams'][u['name']]['attach']['router_if']}\", \"{u['name']}:{u['attach_if']}\"]   # eBGP")
        lines.append(f"    - endpoints: [\"{u['name']}:{u['server_if']}\", \"{u['server']}:eth1\"]")
    for h in model.hosts(sot):
        lines.append(f"    - endpoints: [\"{h['router']}:{h['router_if']}\", \"{h['name']}:eth1\"]")
    return "\n".join(lines) + "\n"


def links_tsv(sot: dict) -> str:
    meta = sot["meta"]
    rows = ["# link\ta_end\tb_end\tkm\tdelay_us\trate"]
    for lid, l in sot["links"].items():
        km = sum(sot["fibres"][f]["length_km"] for f in l["fibres"])
        rows.append(f"{lid}\t{l['a']}:{l['a_if']}\t{l['b']}:{l['b_if']}\t{km}\t{km * meta['delay_us_per_km']}\t{meta['capacity_mbps']}mbit")
    return "\n".join(rows) + "\n"


def topology_json(sot: dict) -> dict:
    """Data contract v0 (contracts/topology.schema.json): what the portal draws."""
    rs = sot["routers"]
    nodes = [{"id": n, "role": "core", "label": n.upper(), "site": r["site"], "loopback": f"10.0.0.{r['id']}",
              "pos": r["pos"]} for n, r in rs.items()]
    return {
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
    write(out / "topology.json", json.dumps(topology_json(sot), indent=1) + "\n")
    return out


if __name__ == "__main__":
    out = render()
    print(f"rendered {len(list((out / 'configs').iterdir()))} devices -> {out.relative_to(ROOT)}")
