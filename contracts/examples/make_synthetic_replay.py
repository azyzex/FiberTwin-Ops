"""Build a SYNTHETIC F1 replay bundle for portal development.

This is a hand-made fixture, not a measurement: meta.source = "synthetic".
It follows the design: a 4-router ring + one chord, where the chord's lightpath
rides two fibres (so fibre F12 carries two IP links = an SRLG).

    r1 ---F12--- r2           IP links: L12 (on F12), L23 (on F23), L34, L41,
    |  \\         |                     L13 chord (lightpath over F12 + F23)
   F41  (L13)   F23           Fault: extra loss in F12 -> L12 and L13 degrade
    |         \\  |           Response: drain both (OSPF cost up) -> traffic r1->r3
    r4 ---F34--- r3                     moves from r1-r3 to r1-r4-r3
                 |
               olt1 - spl1 - ont1..ont4

Run:  python make_synthetic_replay.py  ->  writes synthetic-f1.json next to this file.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
STEP = 15  # seconds between frames
N = 44

nodes = [
    {"id": "r1", "role": "core", "label": "R1", "site": "Tunis", "loopback": "10.0.0.1", "pos": {"x": 200, "y": 150}},
    {"id": "r2", "role": "core", "label": "R2", "site": "Sousse", "loopback": "10.0.0.2", "pos": {"x": 600, "y": 150}},
    {"id": "r3", "role": "core", "label": "R3", "site": "Mahdia", "loopback": "10.0.0.3", "pos": {"x": 600, "y": 500}},
    {"id": "r4", "role": "core", "label": "R4", "site": "Sfax", "loopback": "10.0.0.4", "pos": {"x": 200, "y": 500}},
    {"id": "olt1", "role": "olt", "label": "OLT", "pos": {"x": 800, "y": 500}},
    {"id": "spl1", "role": "splitter", "label": "Splitter 1:32", "pos": {"x": 920, "y": 500}},
] + [
    {"id": f"ont{i}", "role": "ont", "label": f"ONT {i}", "pos": {"x": 1000, "y": 380 + 80 * (i - 1)}}
    for i in range(1, 5)
]

fibres = [
    {"id": "F12", "a": "r1", "b": "r2", "length_km": 140, "spans": 2},
    {"id": "F23", "a": "r2", "b": "r3", "length_km": 60, "spans": 1},
    {"id": "F34", "a": "r3", "b": "r4", "length_km": 120, "spans": 2},
    {"id": "F41", "a": "r4", "b": "r1", "length_km": 270, "spans": 4},
]

ip_links = [
    {"id": "L12", "a": "r1", "a_if": "eth1", "b": "r2", "b_if": "eth1", "capacity_mbps": 100, "ospf_cost": 10, "srlgs": ["F12"]},
    {"id": "L23", "a": "r2", "a_if": "eth2", "b": "r3", "b_if": "eth1", "capacity_mbps": 100, "ospf_cost": 10, "srlgs": ["F23"]},
    {"id": "L34", "a": "r3", "a_if": "eth2", "b": "r4", "b_if": "eth1", "capacity_mbps": 100, "ospf_cost": 10, "srlgs": ["F34"]},
    {"id": "L41", "a": "r4", "a_if": "eth2", "b": "r1", "b_if": "eth2", "capacity_mbps": 100, "ospf_cost": 10, "srlgs": ["F41"]},
    {"id": "L13", "a": "r1", "a_if": "eth3", "b": "r3", "b_if": "eth3", "capacity_mbps": 100, "ospf_cost": 10, "srlgs": ["F12", "F23"]},
]

lightpaths = [
    {"id": "LP12", "fibres": ["F12"], "wavelength": 1, "ip_link": "L12"},
    {"id": "LP23", "fibres": ["F23"], "wavelength": 1, "ip_link": "L23"},
    {"id": "LP34", "fibres": ["F34"], "wavelength": 1, "ip_link": "L34"},
    {"id": "LP41", "fibres": ["F41"], "wavelength": 1, "ip_link": "L41"},
    {"id": "LP13", "fibres": ["F12", "F23"], "wavelength": 2, "ip_link": "L13"},
]

access_links = [
    {"id": "A-up", "a": "r3", "b": "olt1", "segment": "uplink"},
    {"id": "A-feed", "a": "olt1", "b": "spl1", "segment": "feeder", "length_km": 8},
] + [{"id": f"A-d{i}", "a": "spl1", "b": f"ont{i}", "segment": "drop", "length_km": 0.3 + 0.2 * i} for i in range(1, 5)]

topology = {
    "contract_version": "0.1",
    "nodes": nodes,
    "fibres": fibres,
    "lightpaths": lightpaths,
    "ip_links": ip_links,
    "access_links": access_links,
}

# --- scenario timeline (frame index) ---
FAULT_START, DETECT, LOCATE, WHATIF, PR, CI, APPROVE, DEPLOY, VERIFY = 4, 14, 15, 15, 16, 18, 19, 20, 24


def ts(i: int) -> str:
    return (T0 + timedelta(seconds=STEP * i)).isoformat().replace("+00:00", "Z")


def health_from_margin(m: float) -> str:
    return "ok" if m > 3 else ("warn" if m > 1 else "crit")


frames = []
for i in range(N):
    drained = i >= DEPLOY
    # the fibre keeps degrading after the drain: without it, L13 would cross the FEC cliff at ~frame 26
    extra = 0.0 if i < FAULT_START else min(0.25 * (i - FAULT_START), 7.0)
    links = {}
    for l in ip_links:
        base_margin = {"L12": 6.0, "L13": 5.5}.get(l["id"], 6.5)
        hit = "F12" in l["srlgs"]
        margin = round(base_margin - (extra if hit else 0.0), 2)
        cost = 1000 if (drained and hit) else l["ospf_cost"]
        if drained and hit:
            util, health = 0.0, "drained"
        else:
            # before the drain, r1->r3 traffic uses L13; after, it moves to L41 + L34
            util = {"L13": 55.0, "L12": 30.0, "L23": 20.0, "L34": 15.0, "L41": 12.0}[l["id"]]
            if drained and l["id"] in ("L41", "L34"):
                util += 55.0 if l["id"] == "L41" else 40.0
            health = health_from_margin(margin)
        loss = 0.0 if margin > 0.5 else round(0.2 * (0.5 - margin) ** 2, 3)
        links[l["id"]] = {
            "health": health,
            "util_pct": round(util, 1),
            "loss_pct": 0.0 if (drained and hit) else loss,
            "latency_ms": round(sum(f["length_km"] for f in fibres if f["id"] in l["srlgs"]) * 0.005 + 0.3, 2),
            "osnr_margin_db": margin,
            "ospf_cost": cost,
        }
    f12_health = "ok" if extra < 1 else ("warn" if extra < 3 else "crit")
    snap = {
        "ts": ts(i),
        "links": links,
        "fibres": {f["id"]: {"health": f12_health if f["id"] == "F12" else "ok", "extra_loss_db": round(extra, 2) if f["id"] == "F12" else 0.0} for f in fibres},
        "onts": {f"ont{k}": {"health": "ok", "rx_down_dbm": -21.5 - 0.4 * k, "rx_up_dbm": -23.0 - 0.3 * k, "margin_db": round(5.5 - 0.4 * k, 1)} for k in range(1, 5)},
        "paths": {"r1->olt1": ["r1", "r4", "r3", "olt1"] if drained else ["r1", "r3", "olt1"]},
    }
    frames.append(snap)

INC = "INC-F1-0001"
events = [
    {"ts": ts(DETECT), "incident_id": INC, "type": "detected", "fault_class": "F1", "element": "L12,L13", "summary": "OSNR margin trend on L12 and L13 crosses the warning line; no packet loss yet"},
    {"ts": ts(LOCATE), "incident_id": INC, "type": "located", "fault_class": "F1", "element": "F12", "summary": "Both links share fibre F12 (SRLG): one physical cause"},
    {"ts": ts(WHATIF), "incident_id": INC, "type": "what_if_passed", "fault_class": "F1", "element": "F12", "summary": "Draining L12 + L13: max utilisation 67% (< 80%), new paths avoid F12"},
    {"ts": ts(PR), "incident_id": INC, "type": "pr_opened", "fault_class": "F1", "element": "F12", "summary": "PR: OSPF cost 10 -> 1000 on L12 and L13"},
    {"ts": ts(CI), "incident_id": INC, "type": "ci_passed", "fault_class": "F1", "element": "F12", "summary": "CI: temporary twin, adjacencies, reachability, capacity, SRLG diversity all green"},
    {"ts": ts(APPROVE), "incident_id": INC, "type": "approved", "fault_class": "F1", "element": "F12", "summary": "Human approval in PR review"},
    {"ts": ts(DEPLOY), "incident_id": INC, "type": "deployed", "fault_class": "F1", "element": "F12", "summary": "Pull-based deployer applied main; traffic moved to r1-r4-r3"},
    {"ts": ts(VERIFY), "incident_id": INC, "type": "verified", "fault_class": "F1", "element": "F12", "summary": "No loss on the new path, no new alarms: drain verified; repair ticket open"},
]

bundle = {
    "meta": {
        "scenario": "F1 core fibre gray failure on F12",
        "source": "synthetic",
        "notes": "Hand-made fixture for portal development. Not a measurement.",
    },
    "topology": topology,
    "frames": frames,
    "events": events,
}

out = Path(__file__).with_name("synthetic-f1.json")
out.write_text(json.dumps(bundle, indent=1), encoding="utf-8")
print(f"wrote {out} ({len(frames)} frames, {len(events)} events)")
