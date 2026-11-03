"""Access optics (plan §6.2): GPON-inspired power budget, per ONT and per direction. Labelled approximation.

Received power (dBm), downstream (OLT -> ONT, 1490 nm) and upstream (ONT -> OLT, 1310 nm):
    P_rx = P_tx - alpha * (L_feeder + L_drop) - splitter_loss - n_conn*c_conn - n_splice*c_splice - extra
    margin = P_rx - receiver sensitivity                       (sensitivity is defined at BER 1e-10)

From margin to packet loss (GPON class B+, NRZ, no FEC assumed), for a thermal-noise-limited PIN
receiver the Q-factor is proportional to the received optical power:
    Q = Q_sens * 10^(margin/10),   Q_sens = 6.36 (BER 1e-10),   BER = 1/2 erfc(Q / sqrt 2)
    PLR = 1 - (1 - BER)^12000      (one 1500 B frame = 12000 bits, any bit error drops it)
Far below sensitivity the receiver loses the signal entirely (LOS): the ONT is down.

The shared feeder is what makes localisation possible: extra loss on the feeder shifts EVERY ONT's
budget by the same amount, extra loss on a drop shifts ONE ONT.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

Q_SENS = 6.361          # Q at BER 1e-10
FRAME_BITS = 12000
LOS_BELOW_DB = 5.0      # this far below sensitivity = loss of signal (link down)


@dataclass
class PonPath:
    ont: str
    direction: str       # down | up
    rx_dbm: float
    margin_db: float
    ber: float
    plr: float
    los: bool


def ber_from_margin(margin_db: float) -> float:
    q = Q_SENS * 10 ** (margin_db / 10)
    return 0.5 * math.erfc(q / math.sqrt(2))


def plr_from_margin(margin_db: float) -> float:
    ber = ber_from_margin(margin_db)
    return -math.expm1(FRAME_BITS * math.log1p(-ber)) if ber < 1 else 1.0


def path(sot: dict, ont: str, direction: str, extra: dict[str, float] | None = None) -> PonPath:
    """extra: {"feeder:spl1": dB, "drop:ont2": dB} added loss on that segment (connector, bend...)."""
    acc = sot["access"]
    b = acc["budget"]
    n = acc["onts"][ont]
    sp = acc["splitters"][n["splitter"]]
    extra = extra or {}
    km = sp["feeder_km"] + n["drop_km"]
    if direction == "down":
        tx, alpha, sens = b["olt_tx_dbm"], b["alpha_down_db_km"], b["ont_sensitivity_dbm"]
    else:
        tx, alpha, sens = b["ont_tx_dbm"], b["alpha_up_db_km"], b["olt_sensitivity_dbm"]
    loss = (alpha * km + sp["loss_db"] + b["connectors"] * b["connector_db"] + b["splices"] * b["splice_db"]
            + extra.get(f"feeder:{n['splitter']}", 0.0) + extra.get(f"drop:{ont}", 0.0))
    rx = tx - loss
    margin = rx - sens
    los = margin < -LOS_BELOW_DB
    ber = 0.5 if los else ber_from_margin(margin)
    plr = 1.0 if los else plr_from_margin(margin)
    return PonPath(ont, direction, rx, margin, ber, plr, los)


def all_paths(sot: dict, extra: dict[str, float] | None = None) -> list[PonPath]:
    return [path(sot, o, d, extra) for o in sot["access"]["onts"] for d in ("down", "up")]
