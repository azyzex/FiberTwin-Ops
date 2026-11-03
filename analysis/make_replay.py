"""Export the recorded golden-path run (run B "with") as a replay bundle (data contract v0).

Frames come from Prometheus every 15 s over the run (measured utilisation, link loss, OSNR margin,
injected loss labelled as such); events come from the analysis API's incident timeline; the topology
from the generated build. Paths follow the deployed OSPF costs, computed with the what-if engine.
    python analysis/make_replay.py -> results/f1-recorded.json + portal/public/replay/f1-recorded.json
"""
import json
import sys

import networkx as nx
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))
from make_chart import ramp_starts  # noqa: E402
from netgen import model, render, whatif  # noqa: E402

PROM = "http://localhost:9090"
STEP = 15


def series(q: str, start: float, end: float) -> dict[str, dict[float, float]]:
    url = f"{PROM}/api/v1/query_range?" + urllib.parse.urlencode({"query": q, "start": start, "end": end, "step": STEP})
    with urllib.request.urlopen(url, timeout=30) as r:
        res = json.load(r)["data"]["result"]
    return {r["metric"].get("link", r["metric"].get("fibre", "")): {float(t): float(v) for t, v in r["values"]} for r in res}


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> None:
    sot = model.load(ROOT / "tests" / "fixtures" / "sot-1a.yaml")          # costs before the drain
    run = json.loads((ROOT / "results" / "1e-golden-path.json").read_text())["runs"]["with"]
    t0 = ramp_starts()[-1]
    t_end = t0 + 32 / 4 * 60 + 90
    t_deploy = t0 + run["deployed_s"]
    util = series("ft:link_util_ratio", t0, t_end)
    loss = series("ft:link_loss_ratio", t0, t_end)
    margin = series("avg_over_time(ft_optical_margin_db[15s])", t0, t_end)
    trend = series('max by (link) (ALERTS{alertname=~"FtOsnrMargin.*",alertstate="firing"})', t0, t_end)
    extra = series('ft_truth_span_extra_loss_db{fibre="F12",span="0"}', t0, t_end).get("F12", {})
    drained = set(whatif.srlg_map(sot)["F12"])          # the links riding F12 = what the PR drained
    frames = []
    t = t0
    while t <= t_end:
        after = t >= t_deploy
        costs = {l: whatif.DRAIN_COST for l in drained} if after else {}
        links = {}
        for lid, l in sot["links"].items():
            m = margin.get(lid, {}).get(t)
            lo = loss.get(lid, {}).get(t, 0.0)
            if after and lid in drained:
                health = "drained"
            elif lo > 0.01:
                health = "crit"
            elif (m is not None and m < 3) or lo > 0.001 or trend.get(lid, {}).get(t):
                health = "warn"
            else:
                health = "ok"
            km = sum(sot["fibres"][f]["length_km"] for f in l["fibres"])
            links[lid] = {"health": health, "util_pct": round(100 * util.get(lid, {}).get(t, 0.0), 1),
                          "loss_pct": round(100 * lo, 3), "latency_ms": round(km * 0.005, 2),
                          "ospf_cost": costs.get(lid, l["ospf_cost"])}
            if m is not None:
                links[lid]["osnr_margin_db"] = round(m, 2)
        x = extra.get(t, 0.0)
        fib = {f: {"health": "ok", "extra_loss_db": 0.0} for f in sot["fibres"]}
        fib["F12"] = {"health": "crit" if x > 27 else ("warn" if x > 5 else "ok"), "extra_loss_db": round(x, 2)}
        # forwarding path srv -> h3 (enters at r1, leaves at r3) under the deployed costs
        g = whatif.graph(sot, costs)
        path = nx.shortest_path(g, "r1", "r3", weight="cost")
        frames.append({"ts": iso(t), "links": links, "fibres": fib, "paths": {"srv->h3": path}})
        t += STEP
    events = []
    for e in run.get("events", []):
        events.append({k: v for k, v in e.items() if k in ("ts", "incident_id", "type", "fault_class", "element", "summary", "url")})
    topo = render.topology_json(sot)
    bundle = {"meta": {"scenario": "F1 gray failure of fibre F12, drained before the cliff (recorded run)",
                       "source": "recorded", "recorded_at": iso(t0),
                       "notes": "Recorded from the twin: measured utilisation/loss, modelled OSNR margin, injected loss "
                                "on F12 (time-compressed 4 dB/min). Packets lost on 2 customer streams: 0 (1567 without the drain)."},
              "topology": topo, "frames": frames, "events": events}
    for out in (ROOT / "results" / "f1-recorded.json", ROOT / "portal" / "public" / "replay" / "f1-recorded.json"):
        out.write_bytes((json.dumps(bundle, indent=1) + "\n").encode())
    print(f"{len(frames)} frames, {len(events)} events -> results/f1-recorded.json")


if __name__ == "__main__":
    main()
