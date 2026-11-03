"""Generate the Grafana NOC dashboard from the source of truth.

    python monitoring/make_dashboard.py  ->  monitoring/grafana/dashboards/noc.json

The map (a Canvas panel) places each router at its position from sot/network.yaml and draws one
connection per IP link, coloured by ft:link_health (0 ok / 1 warning / 2 critical). Change the
network in the source of truth, regenerate, and the map follows.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from netgen import model  # noqa: E402

DS = {"type": "prometheus", "uid": "prom"}
HEALTH_THRESHOLDS = {"mode": "absolute", "steps": [
    {"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 2}]}

# canvas coordinates: SoT positions (0-1000) scaled into the panel
SCALE, OX, OY = 0.85, 30, -60
NODE_W, NODE_H = 86, 44


def place(pos: dict) -> tuple[float, float]:
    return OX + pos["x"] * SCALE, OY + pos["y"] * SCALE


def anchor(src: dict, dst: dict) -> tuple[dict, dict]:
    """Connection anchors (-1..1 on each element) pointing towards each other."""
    dx, dy = dst["x"] - src["x"], dst["y"] - src["y"]
    n = max(abs(dx), abs(dy)) or 1
    return {"x": dx / n, "y": -dy / n}, {"x": -dx / n, "y": dy / n}


def node_element(name: str, r: dict, sot: dict) -> dict:
    x, y = place(r["pos"])
    conns = []
    for lid, l in sot["links"].items():
        if l["a"] != name:
            continue
        peer = sot["routers"][l["b"]]
        s, t = anchor(r["pos"], peer["pos"])
        conns.append({
            "source": s, "target": t, "targetName": l["b"].upper(),
            "color": {"field": lid, "fixed": "#808080"},
            "size": {"fixed": 7, "min": 2, "max": 10},
            "path": "straight", "direction": "none",
        })
    return {
        "type": "rectangle", "name": name.upper(),
        "config": {"align": "center", "valign": "middle", "color": {"fixed": "#ffffff"}, "size": 16,
                   "text": {"mode": "fixed", "fixed": f"{name.upper()}\n{r['site']}"}},
        "background": {"color": {"fixed": "#1e293b"}},
        "border": {"color": {"fixed": "#38bdf8"}, "width": 2, "radius": 8},
        "constraint": {"horizontal": "left", "vertical": "top"},
        "placement": {"left": x - NODE_W / 2, "top": y - NODE_H / 2, "width": NODE_W, "height": NODE_H, "rotation": 0},
        "connections": conns,
    }


def link_label(lid: str, l: dict, sot: dict) -> dict:
    a, b = sot["routers"][l["a"]]["pos"], sot["routers"][l["b"]]["pos"]
    x, y = place({"x": (a["x"] + b["x"]) / 2, "y": (a["y"] + b["y"]) / 2})
    # put the label beside its line, not on it
    if a["y"] == b["y"]:
        y -= 24                      # horizontal link: label above
    elif a["x"] == b["x"]:
        x += 58                      # vertical link: label to the right
    else:
        x, y = x + 52, y - 22        # diagonal: up and right
    km = sum(sot["fibres"][f]["length_km"] for f in l["fibres"])
    return {
        "type": "rectangle", "name": f"lbl-{lid}",
        "config": {"align": "center", "valign": "middle", "color": {"fixed": "#ffffff"}, "size": 11,
                   "text": {"mode": "fixed", "fixed": f"{lid} · {km} km"}},
        "background": {"color": {"field": lid, "fixed": "#808080"}},
        "border": {"color": {"fixed": "transparent"}, "radius": 4},
        "constraint": {"horizontal": "left", "vertical": "top"},
        "placement": {"left": x - 46, "top": y - 10, "width": 92, "height": 20, "rotation": 0},
    }


def ts_panel(pid, title, expr, legend, unit, grid, thresholds=None, max_=None):
    p = {
        "id": pid, "type": "timeseries", "title": title, "datasource": DS, "gridPos": grid,
        "targets": [{"refId": "A", "datasource": DS, "expr": expr, "legendFormat": legend}],
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 8, "showPoints": "never"}},
                        "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
    }
    if thresholds:
        p["fieldConfig"]["defaults"]["thresholds"] = thresholds
        p["fieldConfig"]["defaults"]["custom"]["thresholdsStyle"] = {"mode": "dashed"}
    if max_ is not None:
        p["fieldConfig"]["defaults"]["max"] = max_
    if unit == "percentunit":
        p["fieldConfig"]["defaults"]["min"] = 0
        p["fieldConfig"]["defaults"]["custom"]["axisSoftMax"] = 0.05 if max_ is None else max_
    return p


def build() -> dict:
    sot = model.load()
    elements = [link_label(lid, l, sot) for lid, l in sot["links"].items()]
    elements += [node_element(n, r, sot) for n, r in sot["routers"].items()]
    noc_map = {
        "id": 1, "type": "canvas", "title": "NOC map: IP core (colour = link health)", "datasource": DS,
        "gridPos": {"h": 15, "w": 12, "x": 0, "y": 0},
        "targets": [{"refId": "A", "datasource": DS, "expr": "ft:link_health", "legendFormat": "{{link}}", "instant": False}],
        "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": HEALTH_THRESHOLDS,
                                     "min": 0, "max": 2}, "overrides": []},
        "options": {"inlineEditing": False, "showAdvancedTypes": True, "panZoom": False, "infinitePan": False,
                    "root": {"type": "frame", "name": "root", "elements": elements,
                             "background": {"color": {"fixed": "transparent"}},
                             "border": {"color": {"fixed": "transparent"}},
                             "constraint": {"horizontal": "left", "vertical": "top"},
                             "placement": {"left": 0, "top": 0, "width": 100, "height": 100, "rotation": 0}}},
    }
    pct = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 0.8}]}
    loss_t = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 0.01}]}
    panels = [
        noc_map,
        {"id": 2, "type": "table", "title": "Firing alerts", "datasource": DS,
         "gridPos": {"h": 7, "w": 12, "x": 12, "y": 0},
         "targets": [{"refId": "A", "datasource": DS, "expr": 'ALERTS{alertstate="firing"}', "format": "table", "instant": True}],
         "transformations": [{"id": "organize", "options": {"excludeByName": {"Time": True, "Value": True, "__name__": True, "alertstate": True}}}],
         "options": {"showHeader": True}, "fieldConfig": {"defaults": {}, "overrides": []}},
        {"id": 3, "type": "stat", "title": "Control plane (actual / expected OSPF Full)", "datasource": DS,
         "gridPos": {"h": 7, "w": 12, "x": 12, "y": 7},
         "targets": [{"refId": "A", "datasource": DS, "expr": "ft_ospf_full_neighbors - ft_ospf_expected_neighbors", "legendFormat": "{{node}} OSPF Δ"},
                     {"refId": "B", "datasource": DS, "expr": "ft_bgp_established", "legendFormat": "{{node}} BGP"},
                     {"refId": "C", "datasource": DS, "expr": "ft_bfd_up", "legendFormat": "{{node}} BFD"}],
         "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background", "textMode": "value_and_name", "graphMode": "none"},
         "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "blue", "value": None}]}},
                         "overrides": [{"matcher": {"id": "byFrameRefID", "options": "A"}, "properties": [
                             {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 0}]}}]}]}},
        ts_panel(4, "Link utilisation (worst direction)", "ft:link_util_ratio", "{{link}}", "percentunit",
                 {"h": 8, "w": 12, "x": 0, "y": 14}, pct, 1),
        ts_panel(5, "Link packet loss (from the netem counters)", "ft:link_loss_ratio", "{{link}}", "percentunit",
                 {"h": 8, "w": 12, "x": 12, "y": 14}, loss_t),
        ts_panel(6, "Probe RTT per path and class", "ft_probe_rtt_ms", "{{src}}→{{dst}} {{class}}", "ms",
                 {"h": 8, "w": 8, "x": 0, "y": 22}),
        ts_panel(7, "Probe jitter (mdev)", "ft_probe_jitter_ms", "{{src}}→{{dst}} {{class}}", "ms",
                 {"h": 8, "w": 8, "x": 8, "y": 22}),
        ts_panel(8, "Probe loss", "ft_probe_loss_ratio", "{{src}}→{{dst}} {{class}}", "percentunit",
                 {"h": 8, "w": 8, "x": 16, "y": 22}, loss_t),
        ts_panel(9, "Optical: OSNR margin per link (cliff at 0 dB, warning at 3 dB)", "ft_optical_margin_db", "{{link}}", "suffix: dB",
                 {"h": 9, "w": 12, "x": 0, "y": 30},
                 {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 0},
                                                {"color": "green", "value": 3}]}),
        ts_panel(10, "Optical: received power per link", "ft_optical_rx_power_dbm", "{{link}}", "suffix: dBm",
                 {"h": 9, "w": 12, "x": 12, "y": 30}),
        ts_panel(11, "Access: GPON margin to receiver sensitivity per ONT (feeder fault = all move together)",
                 "ft_pon_margin_db", "{{ont}} {{direction}}", "suffix: dB", {"h": 9, "w": 24, "x": 0, "y": 39},
                 {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 0},
                                                {"color": "green", "value": 3}]}),
    ]
    return {
        "uid": "fibertwin-noc", "title": "FiberTwin NOC", "tags": ["fibertwin"], "timezone": "browser",
        "refresh": "5s", "time": {"from": "now-15m", "to": "now"}, "schemaVersion": 41, "version": 1,
        "editable": True, "panels": panels,
        # incident timeline drawn on every graph: one marker per event, from the analysis API's metric
        "annotations": {"list": [{"name": "Incident timeline", "datasource": DS, "enable": True, "iconColor": "#f97316",
                                  "expr": "ft_incident_event", "step": "15s", "titleFormat": "{{type}}",
                                  "textFormat": "{{incident}}", "useValueForTime": True}]},
    }


if __name__ == "__main__":
    out = ROOT / "monitoring" / "grafana" / "dashboards" / "noc.json"
    out.write_bytes((json.dumps(build(), indent=1) + "\n").encode())
    print(f"wrote {out.relative_to(ROOT)}")
