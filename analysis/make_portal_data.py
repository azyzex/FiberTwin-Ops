"""Precompute the data behind the portal's What-if and Optical-cliff screens, with the SAME Python
engines CI and the analysis API use (netgen.whatif, optics.model, optics.access).

The public portal (GitHub Pages) has no backend, so instead of calling the API it reads these files.
CI regenerates them and fails if the committed copy is stale: the page can never disagree with the code.

    python analysis/make_portal_data.py  ->  portal/public/data/{whatif,optics}.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from netgen import model, whatif  # noqa: E402
from optics import access as pon  # noqa: E402
from optics.model import (OpticalParams, ber_from_q, link_optics, plr_from_ber, q_factor,  # noqa: E402
                          required_osnr_db)

OUT = ROOT / "portal" / "public" / "data"


def paths_for(sot: dict, costs: dict, failed: set) -> dict[str, list[str]]:
    import networkx as nx
    g = whatif.graph(sot, costs, failed)
    out = {}
    for d in sot["demands"]:
        a, b = whatif.attach_point(sot, d["src"]), whatif.attach_point(sot, d["dst"])
        try:
            out[f"{d['src']}->{d['dst']}"] = nx.shortest_path(g, a, b, weight="cost")
        except nx.NetworkXNoPath:
            out[f"{d['src']}->{d['dst']}"] = []
    return out


def scenario(sot: dict, name: str, kind: str, fibre: str | None, links: list[str]) -> dict:
    cap, limit = sot["meta"]["capacity_mbps"], sot["meta"]["max_utilisation_pct"]
    if kind == "drain":
        chk = whatif.check_drain(sot, links, avoid_fibre=fibre)
        p, ok, reasons = chk.placement, chk.ok, chk.reasons
        costs, failed = {l: whatif.DRAIN_COST for l in links}, set()
    else:  # cut: the links disappear, OSPF reroutes by itself
        failed = set(links)
        p = whatif.place_demands(sot, failed=failed)
        costs = {}
        util = p.util_pct(cap)
        reasons = []
        if p.unreachable:
            reasons.append("unreachable: " + ", ".join(p.unreachable))
        hot = [f"{l} {a}->{b} {u:.0f}%" for (l, a, b), u in util.items() if u > limit]
        if hot:
            reasons.append(f"above {limit}%: " + ", ".join(hot))
        ok = not reasons
    util = p.util_pct(cap)
    per_link = {}
    for lid in sot["links"]:
        u = max([v for (l, _, _), v in util.items() if l == lid], default=0.0)
        per_link[lid] = {"util_pct": round(u, 1), "state": "down" if lid in failed else ("drained" if lid in costs else "up")}
    return {"id": name, "kind": kind, "fibre": fibre, "links": links, "ok": ok,
            "reasons": reasons or [f"all demands served, max {max(util.values(), default=0):.0f}% (limit {limit}%)"],
            "max_util_pct": round(max(util.values(), default=0.0), 1), "per_link": per_link,
            "paths": paths_for(sot, costs, failed)}


def whatif_data(sot: dict) -> dict:
    from itertools import combinations
    srlg = whatif.srlg_map(sot)
    fibres = sorted(srlg)
    sc = [scenario(sot, "baseline", "drain", None, [])]
    # every combination of fibre cuts (N-1, N-2, ...): the page looks them up by the set the user clicked
    for k in range(1, len(fibres) + 1):
        for combo in combinations(fibres, k):
            riders = sorted({l for f in combo for l in srlg[f]})
            sc.append(scenario(sot, "cut-" + "+".join(combo), "cut", "+".join(combo), riders))
    for f, riders in srlg.items():
        sc.append(scenario(sot, f"drain-{f}", "drain", f, riders))
    for lid in sot["links"]:
        sc.append(scenario(sot, f"cut-{lid}", "cut", None, [lid]))
    return {"generated_by": "analysis/make_portal_data.py (netgen.whatif: SPF + per-hop ECMP)",
            "capacity_mbps": sot["meta"]["capacity_mbps"], "limit_pct": sot["meta"]["max_utilisation_pct"],
            "demands": sot["demands"], "srlg": srlg, "scenarios": sc}


def optics_data(sot: dict) -> dict:
    p = OpticalParams(**sot["meta"].get("optics", {}))
    curve = []
    for i in range(50, 431):                       # OSNR 5.0 .. 43.0 dB (as-built links sit at 33-41 dB)
        o = i / 10
        q = q_factor(o, p)
        ber = ber_from_q(q)
        curve.append({"osnr_db": o, "q": round(q, 3), "ber": ber, "plr": plr_from_ber(ber)})
    links = {l: {"osnr_db": round(x.osnr_db, 2), "margin_db": round(x.margin_db, 2), "spans": x.spans}
             for l in sot["links"] for x in [link_optics(sot, l, p)]}
    ramp = []
    for i in range(0, 321):                        # extra loss in F12 span 0: 0 .. 32 dB
        x = i / 10
        a, b = link_optics(sot, "L12", p, {("F12", 0): x}), link_optics(sot, "L13", p, {("F12", 0): x})
        ramp.append({"extra_db": x, "L12": {"osnr_db": round(a.osnr_db, 2), "margin_db": round(a.margin_db, 2), "plr": a.plr},
                     "L13": {"osnr_db": round(b.osnr_db, 2), "margin_db": round(b.margin_db, 2), "plr": b.plr}})
    access = {}
    if sot.get("access"):
        access = {"onts": {o: {d: round(pon.path(sot, o, d).margin_db, 2) for d in ("down", "up")}
                           for o in sot["access"]["onts"]},
                  "curve": [{"margin_db": m / 10, "plr": pon.plr_from_margin(m / 10)} for m in range(-40, 41)]}
    return {"generated_by": "analysis/make_portal_data.py (optics.model: OSNR -> Q -> BER -> RS(255,239) -> PLR)",
            "params": p.__dict__, "required_osnr_db": round(required_osnr_db(p), 2), "curve": curve,
            "links": links, "ramp_F12_span0": ramp, "access": access}


def main() -> None:
    sot = model.load()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("whatif", whatif_data(sot)), ("optics", optics_data(sot))):
        (OUT / f"{name}.json").write_bytes((json.dumps(data, separators=(",", ":")) + "\n").encode())
        print(f"wrote portal/public/data/{name}.json")


if __name__ == "__main__":
    main()
