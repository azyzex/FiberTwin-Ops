"""Incident decisions: pure functions, no I/O (unit-tested in tests/test_analysis.py).

correlate()  firing alerts + optical telemetry  ->  which fault class, which fibre, which links
decide()     fault class + guardrails            ->  drain PR / ticket / nothing
verify()     metrics after the change            ->  recovered, or revert

Fault classes (plan §5):
  F1   gray failure inside a span: OSNR falls on EVERY lightpath of one fibre, received power normal
  F1b  receiver-side fault: ONE link degrades and its received power falls
  F2   fibre cut: links of one fibre go down together (OSPF/BFD already rerouted)
"""
from __future__ import annotations

from dataclasses import dataclass, field

OPTICAL_ALERTS = {"FtOsnrMarginTrend", "FtOsnrMarginLow"}
DOWN_ALERTS = {"FtLinkDown"}
POWER_DROP_DB = 3.0          # received power this much below design = receiver-side signature
MARGIN_DROP_DB = 1.0         # margin this much below design = "this link is degrading too"
ALLOWED_TO_REMEDIATE = {"F1"}  # guardrail 1: only F1 may produce a config change


@dataclass
class Correlation:
    fault_class: str | None            # F1 / F1b / F2 / None
    fibre: str | None
    links: list[str]                   # all links riding the fibre (F1/F2) or the one link (F1b)
    evidence: list[str] = field(default_factory=list)


def srlg_map(sot: dict) -> dict[str, list[str]]:
    m: dict[str, list[str]] = {}
    for lid, l in sot["links"].items():
        for f in l["fibres"]:
            m.setdefault(f, []).append(lid)
    return m


def correlate(sot: dict, alerts: list[dict], telemetry: dict[str, dict]) -> Correlation:
    """alerts: firing alerts' labels. telemetry[link] = {margin, design_margin, rx_power, design_rx_power}."""
    srlg = srlg_map(sot)
    optical = sorted({a["link"] for a in alerts if a.get("alertname") in OPTICAL_ALERTS and a.get("link")})
    down = sorted({a["link"] for a in alerts if a.get("alertname") in DOWN_ALERTS and a.get("link")})

    def degrading(link: str) -> bool:
        t = telemetry.get(link, {})
        return link in optical or (t.get("design_margin", 0) - t.get("margin", 0) > MARGIN_DROP_DB)

    def power_dropped(link: str) -> bool:
        t = telemetry.get(link, {})
        return t.get("design_rx_power", 0) - t.get("rx_power", 0) > POWER_DROP_DB

    # F2: every link of one fibre is down
    for f, riders in srlg.items():
        if down and set(riders) <= set(down):
            return Correlation("F2", f, riders, [f"all links on {f} are down: {', '.join(riders)}"])

    if not optical:
        return Correlation(None, None, [], ["no optical alert"])

    # F1b: one degrading link whose received power fell (the fault is after the last amplifier)
    if len(optical) == 1 and power_dropped(optical[0]):
        l = optical[0]
        t = telemetry[l]
        return Correlation("F1b", None, [l], [
            f"only {l} degrades; received power {t['rx_power']:.1f} dBm vs {t['design_rx_power']:.1f} design "
            f"(> {POWER_DROP_DB} dB drop): receiver-side fault, OSNR of the line is not the cause"])

    # F1: a fibre whose riders ALL degrade while received power stays normal
    candidates = []
    for f, riders in srlg.items():
        if not set(riders) & set(optical):
            continue
        if all(degrading(r) for r in riders) and not any(power_dropped(r) for r in riders):
            candidates.append((len(set(riders) & set(optical)), f, riders))
    if candidates:
        candidates.sort(key=lambda c: (-c[0], c[1]))
        best = [c for c in candidates if c[0] == candidates[0][0]]
        if len(best) == 1:
            _, f, riders = best[0]
            lows = ", ".join(f"{r} {telemetry.get(r, {}).get('design_margin', 0) - telemetry.get(r, {}).get('margin', 0):.1f} dB"
                             for r in riders)
            return Correlation("F1", f, riders, [
                f"every link on fibre {f} is degrading (OSNR margin lost: {lows}); {f} is the only fibre they share (SRLG)",
                f"received power normal on {', '.join(riders)}: loss is inside a span, not at a receiver"])
        return Correlation(None, None, optical, [f"ambiguous: fibres {', '.join(c[1] for c in best)} fit equally"])
    return Correlation(None, None, optical, ["degrading links don't match any single fibre: ticket for investigation"])


@dataclass
class Decision:
    action: str                  # drain | ticket | none
    reasons: list[str]


def decide(c: Correlation, open_drain_fibres: set[str], recent_drains: dict[str, float], now: float,
           cooldown_s: float = 1800) -> Decision:
    if c.fault_class is None:
        return Decision("ticket" if c.links else "none", c.evidence)
    if c.fault_class not in ALLOWED_TO_REMEDIATE:
        why = {"F1b": "receiver-side: a config change can't clean a connector",
               "F2": "fibre cut: OSPF/BFD already rerouted; nothing to change"}[c.fault_class]
        return Decision("ticket", [why])
    if c.fibre in open_drain_fibres:                       # guardrail: one change per element at a time
        return Decision("none", [f"a drain of {c.fibre} is already in progress"])
    if now - recent_drains.get(c.fibre, -1e18) < cooldown_s:   # guardrail: cooldown, no loops
        return Decision("ticket", [f"{c.fibre} was drained less than {cooldown_s / 60:.0f} min ago: escalate to a human"])
    return Decision("drain", [f"F1 on {c.fibre}: drain {', '.join(c.links)} before the margin reaches the cliff"])


@dataclass
class Verification:
    recovered: bool
    reasons: list[str]


def verify(drained_links: list[str], util: dict[str, float], probe_loss: dict[str, float],
           firing: list[dict], max_util_after: float = 0.05, max_probe_loss: float = 0.01) -> Verification:
    """After the drain: traffic left the drained links, customers see no loss, no NEW link alert elsewhere."""
    reasons = []
    still = {l: u for l, u in util.items() if l in drained_links and u > max_util_after}
    if still:
        reasons.append("traffic still on drained links: " + ", ".join(f"{l} {u:.0%}" for l, u in still.items()))
    lossy = {p: v for p, v in probe_loss.items() if v > max_probe_loss}
    if lossy:
        reasons.append("customer probes losing packets: " + ", ".join(f"{p} {v:.0%}" for p, v in lossy.items()))
    new = sorted({f"{a['alertname']}:{a.get('link') or a.get('node')}" for a in firing
                  if a.get("alertname") in {"FtLinkPacketLoss", "FtLinkUtilisationHigh", "FtLinkDown"}
                  and a.get("link") not in drained_links})
    if new:
        reasons.append("new alerts on other links: " + ", ".join(new))
    return Verification(not reasons, reasons or ["traffic moved off the drained links, probes clean, no new alerts"])


def drain_sot_text(sot_text: str, links: list[str], cost: int, note: str) -> str:
    """Edit sot/network.yaml TEXT (keeps comments and layout): set ospf_cost on the given links.
    The note goes on its own comment line above the link (a trailing comment broke yamllint's line
    length in the first live run); an empty note removes an earlier "drained:" note (restore)."""
    import re
    out: list[str] = []
    for line in sot_text.splitlines(keepends=True):
        m = re.match(r"^(\s*)(L\w+):\s*\{", line)
        if m and m.group(2) in links:
            if out and out[-1].lstrip().startswith("# drained:"):
                out.pop()
            if note:
                out.append(f"{m.group(1)}# {note}\n")
            line = re.sub(r"ospf_cost:\s*\d+", f"ospf_cost: {cost}", line)
        out.append(line)
    return "".join(out)
