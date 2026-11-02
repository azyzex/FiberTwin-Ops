"""Optical layer model (plan §6.1): from fibre spans to packet loss. Course-level, labelled approximation.

Chain, for one lightpath (NRZ-OOK, OTU2-class, G.709 RS(255,239) FEC, 10GbE client):
  1. OSNR per span    OSNR_i[dB] = 58 + P_ch[dBm] - L_i[dB] - NF[dB]   (0.1 nm ref. bandwidth)
     spans add as noise: 1/OSNR_tot = sum 1/OSNR_i (linear)
  2. OSNR -> Q        Q = 2*OSNR*sqrt(Bo/Be) / (1 + sqrt(1 + 4*OSNR)), after an implementation penalty
  3. Q -> BER         BER = 1/2 erfc(Q / sqrt 2)
  4. BER -> FEC       p_s = 1-(1-BER)^8 ; P_cw = P[more than 8 byte errors in 255]
  5. FEC -> packets   PLR = 1 - (1 - P_cw)^7   (a 1500 B frame spans ~7 codewords)
Assumptions of step 2 (stated in docs/derivations): NRZ-OOK, infinite extinction ratio, ASE-beat-noise
limited pre-amplified receiver, Gaussian noise, ideal filters, optimal threshold. No nonlinearities,
dispersion or PMD.
Margin = OSNR now - OSNR at which PLR reaches PLR_LIMIT (1e-6): how many dB are left before the cliff.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

H_NU_BREF_DB = 58.0           # -10 log10(h * nu * B_ref) at 1550 nm, 0.1 nm, in dBm
RS_N, RS_T = 255, 8           # RS(255,239) corrects up to 8 byte errors per codeword
CODEWORDS_PER_FRAME = 7       # 1500 B Ethernet frame over OTU2
PLR_LIMIT = 1e-6              # "usable" threshold that defines the margin


@dataclass(frozen=True)
class OpticalParams:
    p_ch_dbm: float = 0.0         # launch power per channel
    alpha_db_km: float = 0.2
    nf_db: float = 5.0            # amplifier noise figure
    bo_ghz: float = 12.5          # optical bandwidth (0.1 nm)
    be_ghz: float = 7.0           # electrical bandwidth
    penalty_db: float = 2.0       # implementation penalty (real transceiver vs ideal formula)
    patch_loss_db: float = 1.0    # connectors between the last amplifier and the receiver pre-amp
    sensitivity_dbm: float = -28.0  # pre-amplified receiver sensitivity


def db_to_lin(db: float) -> float:
    return 10 ** (db / 10)


def lin_to_db(x: float) -> float:
    return 10 * math.log10(x) if x > 0 else -math.inf


def span_osnr_db(loss_db: float, p: OpticalParams) -> float:
    return H_NU_BREF_DB + p.p_ch_dbm - loss_db - p.nf_db


def total_osnr_db(span_losses_db: list[float], p: OpticalParams, rx_extra_db: float = 0.0) -> float:
    """Line amplifiers after each span + the receiver pre-amplifier (fed through patch + extra loss)."""
    inv = sum(1 / db_to_lin(span_osnr_db(l, p)) for l in span_losses_db)
    inv += 1 / db_to_lin(span_osnr_db(p.patch_loss_db + rx_extra_db, p))
    return lin_to_db(1 / inv)


def q_factor(osnr_db: float, p: OpticalParams) -> float:
    o = db_to_lin(osnr_db - p.penalty_db)
    return 2 * o * math.sqrt(p.bo_ghz / p.be_ghz) / (1 + math.sqrt(1 + 4 * o))


def ber_from_q(q: float) -> float:
    return 0.5 * math.erfc(q / math.sqrt(2))


def _log_binom_pmf(n: int, k: int, p: float) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1) + k * math.log(p) + (n - k) * math.log1p(-p)


def codeword_failure(ber: float) -> float:
    """P(more than 8 of 255 bytes in error). Computed in the log domain: P_cw can be ~1e-60."""
    if ber <= 0:
        return 0.0
    if ber >= 0.5:
        return 1.0
    p_s = -math.expm1(8 * math.log1p(-ber))          # 1 - (1-BER)^8, accurate for tiny BER
    if p_s >= 1:
        return 1.0
    logs = [_log_binom_pmf(RS_N, k, p_s) for k in range(RS_T + 1, RS_N + 1)]
    m = max(logs)
    return min(1.0, math.exp(m) * sum(math.exp(x - m) for x in logs))


def plr_from_ber(ber: float) -> float:
    p_cw = codeword_failure(ber)
    return -math.expm1(CODEWORDS_PER_FRAME * math.log1p(-p_cw)) if p_cw < 1 else 1.0


def plr_from_osnr(osnr_db: float, p: OpticalParams) -> float:
    return plr_from_ber(ber_from_q(q_factor(osnr_db, p)))


def required_osnr_db(p: OpticalParams, plr_limit: float = PLR_LIMIT) -> float:
    """OSNR at which the packet loss reaches plr_limit (bisection: PLR falls monotonically with OSNR)."""
    lo, hi = 0.0, 40.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if plr_from_osnr(mid, p) > plr_limit:
            lo = mid
        else:
            hi = mid
    return hi


@dataclass
class LinkOptics:
    link: str
    spans: int
    osnr_db: float
    q: float
    ber: float
    plr: float
    rx_power_dbm: float
    margin_db: float          # OSNR margin to the PLR_LIMIT point (negative = past the cliff)
    power_margin_db: float    # received power above sensitivity (negative = receiver blind)


def lightpath_spans(sot: dict, link: str, p: OpticalParams, extra_span_loss: dict[tuple[str, int], float]) -> list[float]:
    """Span losses along the lightpath of `link`. Each fibre is cut into equal amplified spans;
    extra_span_loss[(fibre, span_index)] adds loss inside that span (fault L1)."""
    losses = []
    for f in sot["links"][link]["fibres"]:
        fib = sot["fibres"][f]
        n = fib["spans"]
        for i in range(n):
            losses.append(p.alpha_db_km * fib["length_km"] / n + extra_span_loss.get((f, i), 0.0))
    return losses


def link_optics(sot: dict, link: str, p: OpticalParams,
                extra_span_loss: dict[tuple[str, int], float] | None = None,
                rx_extra_loss: dict[str, float] | None = None) -> LinkOptics:
    extra_span_loss = extra_span_loss or {}
    rx_extra = (rx_extra_loss or {}).get(link, 0.0)
    spans = lightpath_spans(sot, link, p, extra_span_loss)
    osnr = total_osnr_db(spans, p, rx_extra)
    q = q_factor(osnr, p)
    ber = ber_from_q(q)
    plr = plr_from_ber(ber)
    rx_power = p.p_ch_dbm - p.patch_loss_db - rx_extra   # after the last line amplifier
    power_margin = rx_power - p.sensitivity_dbm
    if power_margin < 0:          # below sensitivity: the receiver can't decide bits at all
        plr = 1.0
    return LinkOptics(link, len(spans), osnr, q, ber, plr, rx_power, osnr - required_osnr_db(p), power_margin)
