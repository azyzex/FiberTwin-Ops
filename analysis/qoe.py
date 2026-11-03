"""Voice QoE from measured probes: the ITU-T G.107 E-model, simplified (Cole & Rosenbluth, 2001).

    R = 93.2 - Id - Ie_eff
    Id     = 0.024 d + 0.11 (d - 177.3) H(d - 177.3)          d = one-way mouth-to-ear delay (ms)
    Ie_eff = Ie + (95 - Ie) * Ppl / (Ppl / BurstR + Bpl)      Ppl = packet loss (%), random loss: BurstR = 1
    MOS    = 1 + 0.035 R + 7e-6 R (R - 60)(100 - R)          for 0 < R < 100
Codec G.711 with packet-loss concealment (ITU-T G.113: Ie = 0, Bpl = 25.1). The delay budget adds a
20 ms packetisation delay and a jitter buffer of 2 x jitter to the network's one-way delay (RTT / 2).
A planning model: it scores what a call WOULD feel like on these network numbers, not a real call.
"""
from __future__ import annotations

IE, BPL, BURST_R = 0.0, 25.1, 1.0
PACKETISATION_MS = 20.0


def r_factor(one_way_ms: float, loss_pct: float, ie: float = IE, bpl: float = BPL, burst_r: float = BURST_R) -> float:
    d = one_way_ms
    i_d = 0.024 * d + (0.11 * (d - 177.3) if d > 177.3 else 0.0)
    ie_eff = ie + (95 - ie) * loss_pct / (loss_pct / burst_r + bpl)
    return 93.2 - i_d - ie_eff


def mos_from_r(r: float) -> float:
    if r <= 0:
        return 1.0
    if r >= 100:
        return 4.5
    return 1 + 0.035 * r + 7e-6 * r * (r - 60) * (100 - r)


def voice_mos(rtt_ms: float, jitter_ms: float, loss_ratio: float) -> float:
    """MOS for a G.711 call over a path with these probe measurements."""
    one_way = rtt_ms / 2 + PACKETISATION_MS + 2 * jitter_ms
    return mos_from_r(r_factor(one_way, 100 * loss_ratio))


def label(mos: float) -> str:
    return ("excellent" if mos >= 4.3 else "good" if mos >= 4.0 else "fair" if mos >= 3.6
            else "poor" if mos >= 3.1 else "bad")
