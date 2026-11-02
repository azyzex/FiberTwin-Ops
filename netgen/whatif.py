"""Capacity + SRLG what-if: where would traffic go, and is a change safe?

Model: OSPF shortest paths on the link costs, with ECMP split equally per hop (the planning
convention; real routers hash per flow, so one flow takes one of the equal paths). Customers and
the upstream server are attached to their router; demands are placed on directed core links.

Used by CI (tests/test_whatif.py) to block unsafe changes, and later by the analysis API (1e)
before it proposes a drain.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx

DRAIN_COST = 1000  # "cost-out": the link stays up but OSPF avoids it while any other path exists


def srlg_map(sot: dict) -> dict[str, list[str]]:
    """fibre -> IP links riding it (links in the same list fail together)."""
    m: dict[str, list[str]] = defaultdict(list)
    for lid, l in sot["links"].items():
        for f in l["fibres"]:
            m[f].append(lid)
    return dict(m)


def attach_point(sot: dict, endpoint: str) -> str:
    if endpoint in sot["routers"]:
        return endpoint
    if endpoint in sot["customers"]:
        return sot["customers"][endpoint]["router"]
    for u in sot["upstreams"].values():
        if endpoint == u["server"]["name"]:
            return u["attach"]["router"]
    raise KeyError(endpoint)


def graph(sot: dict, costs: dict[str, int] | None = None, failed: set[str] = frozenset()) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_nodes_from(sot["routers"])
    for lid, l in sot["links"].items():
        if lid in failed:
            continue
        c = (costs or {}).get(lid, l["ospf_cost"])
        g.add_edge(l["a"], l["b"], link=lid, cost=c)
        g.add_edge(l["b"], l["a"], link=lid, cost=c)
    return g


def place(g: nx.DiGraph, src: str, dst: str, mbps: float, load: dict) -> bool:
    """Push `mbps` from src to dst over all shortest paths, equal split per hop. False if unreachable."""
    if src == dst:
        return True
    try:
        dist = nx.single_source_dijkstra_path_length(g.reverse(copy=False), dst, weight="cost")
    except nx.NodeNotFound:
        return False
    if src not in dist:
        return False
    flow = {src: mbps}
    for node in sorted(dist, key=lambda n: -dist[n]):   # farthest first: a DAG towards dst
        f = flow.pop(node, 0.0)
        if not f or node == dst:
            continue
        nxt = [v for v in g.successors(node) if v in dist and abs(dist[node] - (g[node][v]["cost"] + dist[v])) < 1e-9]
        for v in nxt:
            load[(g[node][v]["link"], node, v)] += f / len(nxt)
            flow[v] = flow.get(v, 0.0) + f / len(nxt)
    return True


@dataclass
class Placement:
    load: dict = field(default_factory=lambda: defaultdict(float))   # (link, from, to) -> Mbit/s
    unreachable: list = field(default_factory=list)

    def util_pct(self, capacity: float) -> dict:
        return {k: 100 * v / capacity for k, v in self.load.items()}

    def links_used(self) -> set[str]:
        return {k[0] for k, v in self.load.items() if v > 1e-9}


def place_demands(sot: dict, costs: dict[str, int] | None = None, failed: set[str] = frozenset()) -> Placement:
    g = graph(sot, costs, failed)
    p = Placement()
    for d in sot["demands"]:
        if not place(g, attach_point(sot, d["src"]), attach_point(sot, d["dst"]), d["mbps"], p.load):
            p.unreachable.append(f"{d['src']}->{d['dst']}")
    return p


@dataclass
class DrainCheck:
    ok: bool
    reasons: list[str]
    max_util_pct: float
    placement: Placement


def check_drain(sot: dict, links: list[str], avoid_fibre: str | None = None) -> DrainCheck:
    """Is it safe to cost-out `links` (e.g. every link of a degrading fibre)?"""
    costs = {lid: DRAIN_COST for lid in links}
    p = place_demands(sot, costs)
    cap = sot["meta"]["capacity_mbps"]
    limit = sot["meta"]["max_utilisation_pct"]
    util = p.util_pct(cap)
    worst = max(util.values(), default=0.0)
    reasons = []
    if p.unreachable:
        reasons.append(f"unreachable demands: {', '.join(p.unreachable)}")
    if worst > limit:
        hot = [f"{l} {a}->{b} {u:.0f}%" for (l, a, b), u in util.items() if u > limit]
        reasons.append(f"utilisation above {limit}%: {', '.join(hot)}")
    if avoid_fibre:
        riding = set(srlg_map(sot).get(avoid_fibre, []))
        still = sorted(p.links_used() & riding)
        if still:
            reasons.append(f"traffic still crosses fibre {avoid_fibre} via {', '.join(still)}")
    return DrainCheck(ok=not reasons, reasons=reasons, max_util_pct=worst, placement=p)
