"""optical-agent: the modelled L0 layer of the twin, running next to the real IP layer.

Every 5 s:
  1. take the current faults (set through a small HTTP API, see below)
  2. compute each lightpath's optics with optics.model (OSNR -> Q -> BER -> FEC -> packet loss)
  3. apply that packet loss + the fibre delay with `tc netem` on BOTH ends of the IP link riding it
     (the effect lands on the real data plane, where real probes measure it)
  4. publish optical telemetry, with measurement noise like a real optical performance monitor

Honesty rule (plan §6.3): detectors may only use ft_optical_* (noisy observations) and design values.
The fault itself is exported as ft_truth_* for evaluation only.

HTTP API (port 8081):
  GET    /state                                         current faults + per-link optics
  POST   /fault/span  {"fibre":"F12","span":0,"extra_db":5}          set loss inside a span (L1)
  POST   /fault/ramp  {"fibre":"F12","span":0,"to_db":30,"db_per_min":6}   degrade gradually (gray failure)
  POST   /fault/rx    {"link":"L23","extra_db":10}      loss after the last amplifier, one link (L3)
  POST   /fault/pon   {"segment":"feeder:spl1"|"drop:ont2","extra_db":6}   access: dirty connector / bend
  POST   /fault/pon-ramp {"segment":"drop:ont2","to_db":12,"db_per_min":2}  access degradation
  DELETE /faults                                        repair everything
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import docker
import yaml
from prometheus_client import Gauge, start_http_server

from optics import access as pon
from optics.model import OpticalParams, link_optics

PREFIX = os.getenv("FT_PREFIX", "clab-ft-core-")
BUILD = Path(os.getenv("FT_BUILD", "/ft/build/ft-core"))
SOT = Path(os.getenv("FT_SOT", "/ft/sot/network.yaml"))
OSNR_NOISE_DB = float(os.getenv("OSNR_NOISE_DB", "0.15"))     # optical performance monitor accuracy
POWER_NOISE_DB = float(os.getenv("POWER_NOISE_DB", "0.1"))
TICK_S = 5.0

client = docker.from_env()
lock = threading.Lock()
span_faults: dict[tuple[str, int], float] = {}
ramps: dict[tuple[str, int], dict] = {}
rx_faults: dict[str, float] = {}
pon_faults: dict[str, float] = {}               # access segment ('feeder:spl1', 'drop:ont2') -> extra dB
pon_ramps: dict[str, dict] = {}
applied: dict[tuple[str, str], str] = {}        # (node, ifc) -> last netem args
last_state: dict = {}

L = ["link"]
OSNR = Gauge("ft_optical_osnr_db", "Measured OSNR of the link's lightpath (0.1 nm, with monitor noise)", L)
MARGIN = Gauge("ft_optical_margin_db", "Measured OSNR margin to the usable limit (PLR 1e-6); <0 = past the cliff", L)
RXP = Gauge("ft_optical_rx_power_dbm", "Measured received power after the last amplifier", L)
BER = Gauge("ft_optical_pre_fec_ber", "Pre-FEC BER (model)", L)
PLR = Gauge("ft_optical_applied_plr", "Packet loss ratio applied to the link with netem (model output)", L)
DESIGN = Gauge("ft_optical_design_margin_db", "As-built OSNR margin with no fault (design value)", L)
TRUTH_SPAN = Gauge("ft_truth_span_extra_loss_db", "GROUND TRUTH, evaluation only: injected loss inside a span", ["fibre", "span"])
PL = ["ont", "direction"]
PON_RX = Gauge("ft_pon_rx_power_dbm", "Measured received power on the PON path (ONT for down, OLT for up), with noise", PL)
PON_MARGIN = Gauge("ft_pon_margin_db", "Measured margin to receiver sensitivity (BER 1e-10); <0 = errors", PL)
PON_DESIGN = Gauge("ft_pon_design_margin_db", "As-built margin with no fault (design value)", PL)
PON_PLR = Gauge("ft_pon_applied_plr", "Packet loss applied on the PON path with netem (model output)", PL)
TRUTH_PON = Gauge("ft_truth_pon_extra_loss_db", "GROUND TRUTH, evaluation only: injected loss on an access segment", ["segment"])
TRUTH_RX = Gauge("ft_truth_rx_extra_loss_db", "GROUND TRUTH, evaluation only: injected loss before a receiver", L)


def links_tsv() -> dict[str, dict]:
    out = {}
    for line in (BUILD / "links.tsv").read_text().splitlines():
        if line and not line.startswith("#"):
            link, a, b, km, delay, rate = line.split("\t")
            out[link] = {"ends": [a, b], "delay_us": delay}
    return out


def netem_args(delay_us: str, plr: float) -> str:
    if plr < 1e-9:                                  # below netem's resolution: no loss
        return f"delay {delay_us}us"
    return f"delay {delay_us}us loss {min(plr, 1.0) * 100:.7f}%"


def apply_netem(links: dict, plrs: dict[str, float], force: bool = False) -> None:
    for link, info in links.items():
        args = netem_args(info["delay_us"], plrs.get(link, 0.0))
        for end in info["ends"]:
            node, ifc = end.split(":")
            if not force and applied.get((node, ifc)) == args:
                continue
            try:
                c = client.containers.get(PREFIX + node)
                r = c.exec_run(["sh", "-c", f"tc qdisc change dev {ifc} parent 1:10 handle 10: netem {args} limit 10000"])
                if r.exit_code == 0:
                    applied[(node, ifc)] = args
                else:
                    applied.pop((node, ifc), None)
            except Exception as e:
                applied.pop((node, ifc), None)
                print(f"netem {node}:{ifc}: {e}", flush=True)


def access_rows() -> list[dict]:
    f = BUILD / "access.tsv"
    if not f.exists():
        return []
    rows = []
    for line in f.read_text().splitlines():
        if line and not line.startswith("#"):
            ont, spl, spl_if, ont_if, drop_km, feeder_km = line.split("\t")
            rows.append({"ont": ont, "splitter": spl, "splitter_if": spl_if, "ont_if": ont_if,
                         "delay_us": str(round((float(drop_km) + float(feeder_km)) * 5))})
    return rows


def tick_access(sot: dict, force: bool) -> None:
    """PON budget per ONT and direction; downstream effect on the splitter port, upstream on the ONT."""
    if not sot.get("access"):
        return
    now = time.time()
    with lock:
        for seg, r in list(pon_ramps.items()):
            v = r["from_db"] + r["db_per_min"] * (now - r["t0"]) / 60
            pon_faults[seg] = min(v, r["to_db"])
            if v >= r["to_db"]:
                pon_ramps.pop(seg)
        extra = dict(pon_faults)
    targets: dict[str, dict] = {}
    for row in access_rows():
        for d in ("down", "up"):
            pp = pon.path(sot, row["ont"], d, extra)
            design = pon.path(sot, row["ont"], d)
            PON_RX.labels(row["ont"], d).set(pp.rx_dbm + random.gauss(0, POWER_NOISE_DB))
            PON_MARGIN.labels(row["ont"], d).set(pp.margin_db + random.gauss(0, POWER_NOISE_DB))
            PON_DESIGN.labels(row["ont"], d).set(design.margin_db)
            PON_PLR.labels(row["ont"], d).set(pp.plr)
            end = f"{row['splitter']}:{row['splitter_if']}" if d == "down" else f"{row['ont']}:{row['ont_if']}"
            targets[end] = {"ends": [end], "delay_us": row["delay_us"], "plr": pp.plr}
    for seg in [f"feeder:{s}" for s in sot["access"]["splitters"]] + [f"drop:{o}" for o in sot["access"]["onts"]]:
        TRUTH_PON.labels(seg).set(extra.get(seg, 0.0))
    apply_netem({k: {"ends": v["ends"], "delay_us": v["delay_us"]} for k, v in targets.items()},
                {k: v["plr"] for k, v in targets.items()}, force)
    last_state["pon"] = {"faults": {k: round(v, 2) for k, v in extra.items()},
                         "paths": {f"{r['ont']}/{d}": round(pon.path(sot, r["ont"], d, extra).margin_db, 2)
                                   for r in access_rows() for d in ("down", "up")}}


def tick(sot: dict, p: OpticalParams, links: dict, force: bool) -> None:
    global last_state
    now = time.time()
    with lock:
        for key, r in list(ramps.items()):          # advance gradual degradations
            v = r["from_db"] + r["db_per_min"] * (now - r["t0"]) / 60
            span_faults[key] = min(v, r["to_db"])
            if v >= r["to_db"]:
                ramps.pop(key)
        sf, rf = dict(span_faults), dict(rx_faults)
    state, plrs = {}, {}
    for link in sot["links"]:
        o = link_optics(sot, link, p, sf, rf)
        d = link_optics(sot, link, p)
        plrs[link] = o.plr
        osnr_meas = o.osnr_db + random.gauss(0, OSNR_NOISE_DB)
        margin_meas = o.margin_db + (osnr_meas - o.osnr_db)
        rx_meas = o.rx_power_dbm + random.gauss(0, POWER_NOISE_DB)
        OSNR.labels(link).set(osnr_meas)
        MARGIN.labels(link).set(margin_meas)
        RXP.labels(link).set(rx_meas)
        BER.labels(link).set(o.ber)
        PLR.labels(link).set(o.plr)
        DESIGN.labels(link).set(d.margin_db)
        TRUTH_RX.labels(link).set(rf.get(link, 0.0))
        state[link] = {"osnr_db": round(o.osnr_db, 2), "margin_db": round(o.margin_db, 2), "ber": o.ber, "plr": o.plr,
                       "rx_power_dbm": round(o.rx_power_dbm, 2), "power_margin_db": round(o.power_margin_db, 2)}
    for f, fib in sot["fibres"].items():
        for i in range(fib["spans"]):
            TRUTH_SPAN.labels(f, str(i)).set(sf.get((f, i), 0.0))
    apply_netem(links, plrs, force)
    last_state = {"span_faults": {f"{k[0]}/span{k[1]}": round(v, 2) for k, v in sf.items()},
                  "ramps": {f"{k[0]}/span{k[1]}": r for k, r in ramps.items()}, "rx_faults": rf, "links": state}


class Api(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, indent=1, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        self._send(200, last_state) if self.path == "/state" else self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            b = self._body()
            with lock:
                if self.path == "/fault/span":
                    key = (b["fibre"], int(b.get("span", 0)))
                    ramps.pop(key, None)
                    span_faults[key] = float(b["extra_db"])
                elif self.path == "/fault/ramp":
                    key = (b["fibre"], int(b.get("span", 0)))
                    ramps[key] = {"from_db": span_faults.get(key, 0.0), "to_db": float(b["to_db"]),
                                  "db_per_min": float(b["db_per_min"]), "t0": time.time()}
                elif self.path == "/fault/rx":
                    rx_faults[b["link"]] = float(b["extra_db"])
                elif self.path == "/fault/pon":
                    pon_ramps.pop(b["segment"], None)
                    pon_faults[b["segment"]] = float(b["extra_db"])
                elif self.path == "/fault/pon-ramp":
                    pon_ramps[b["segment"]] = {"from_db": pon_faults.get(b["segment"], 0.0), "to_db": float(b["to_db"]),
                                               "db_per_min": float(b["db_per_min"]), "t0": time.time()}
                else:
                    return self._send(404, {"error": "not found"})
            self._send(200, {"ok": True})
        except (KeyError, ValueError) as e:
            self._send(400, {"error": str(e)})

    def do_DELETE(self):
        if self.path != "/faults":
            return self._send(404, {"error": "not found"})
        with lock:
            span_faults.clear(); ramps.clear(); rx_faults.clear(); pon_faults.clear(); pon_ramps.clear()
        self._send(200, {"ok": True})

    def log_message(self, *args):  # quiet
        pass


def main() -> None:
    sot = yaml.safe_load(SOT.read_text())
    p = OpticalParams(**sot["meta"].get("optics", {}))
    start_http_server(9106)
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", 8081), Api).serve_forever, daemon=True).start()
    print("optical-agent: metrics :9106, API :8081", flush=True)
    n = 0
    while True:
        t0 = time.time()
        try:
            # re-assert every netem once a minute: the twin may have been redeployed underneath us
            tick(sot, p, links_tsv(), force=(n % 12 == 0))
            tick_access(sot, force=(n % 12 == 0))
        except Exception as e:
            print(f"tick: {e}", flush=True)
        n += 1
        time.sleep(max(0.5, TICK_S - (time.time() - t0)))


if __name__ == "__main__":
    main()
