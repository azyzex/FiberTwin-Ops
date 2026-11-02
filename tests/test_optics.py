"""Optical model (plan §6.1): the chain must reproduce the plan's table and the physics of the faults."""
from pathlib import Path

import pytest

from netgen import model
from optics.model import OpticalParams, link_optics, plr_from_ber, plr_from_osnr, required_osnr_db

SOT_1A = Path(__file__).parent / "fixtures" / "sot-1a.yaml"
P = OpticalParams()


@pytest.fixture
def sot():
    return model.load(SOT_1A)


@pytest.mark.parametrize("ber, plr, rel", [(1e-4, 1e-11, 1.0), (1e-3, 0.002, 0.25), (3e-3, 0.70, 0.05)])
def test_plan_table(ber, plr, rel):
    """Pre-FEC BER -> packet loss (1500 B): 1e-4 -> ~1e-11, 1e-3 -> ~0.2 %, 3e-3 -> ~70 %."""
    assert plr_from_ber(ber) == pytest.approx(plr, rel=rel)


def test_the_cliff_is_sharp():
    """About 2 dB separate 'error-free' from 'unusable'."""
    req = required_osnr_db(P)
    assert plr_from_osnr(req + 1, P) < 1e-9
    assert plr_from_osnr(req - 1.5, P) > 0.3


def test_plr_falls_monotonically_with_osnr():
    xs = [plr_from_osnr(o / 2, P) for o in range(10, 40)]
    assert all(a >= b for a, b in zip(xs, xs[1:]))


def test_healthy_network_has_wide_margins(sot):
    for link in sot["links"]:
        o = link_optics(sot, link, P)
        assert o.plr == 0 and o.margin_db > 20


def test_span_fault_hits_every_link_of_the_srlg_and_only_them(sot):
    """Loss inside a span of F12 (fault L1) degrades L12 AND L13 (its lightpath also rides F12)."""
    fault = {("F12", 0): 27.0}
    hit = {l: link_optics(sot, l, P, fault).margin_db for l in sot["links"]}
    base = {l: link_optics(sot, l, P).margin_db for l in sot["links"]}
    assert hit["L12"] < 1 and hit["L13"] < 1
    for l in ("L23", "L34", "L41"):
        assert hit[l] == pytest.approx(base[l])


def test_fault_signatures_differ(sot):
    """RCA signature (plan §6.1). Receiver-side loss (L3): received power falls a lot, OSNR only a little
    (the pre-amplifier adds some noise; the plan's "OSNR unchanged" is the first-order view), one link only.
    Span loss (L1): OSNR collapses, received power stays (line amplifiers restore it)."""
    base = link_optics(sot, "L23", P)
    rx = link_optics(sot, "L23", P, rx_extra_loss={"L23": 10.0})
    assert base.rx_power_dbm - rx.rx_power_dbm == pytest.approx(10)
    assert (base.osnr_db - rx.osnr_db) < 0.3 * 10
    assert link_optics(sot, "L13", P, rx_extra_loss={"L23": 10.0}).rx_power_dbm == pytest.approx(base.rx_power_dbm)
    span = link_optics(sot, "L23", P, {("F23", 0): 10.0})
    assert span.rx_power_dbm == pytest.approx(base.rx_power_dbm)
    assert (base.osnr_db - span.osnr_db) > (base.osnr_db - rx.osnr_db)


def test_receiver_blind_below_sensitivity(sot):
    o = link_optics(sot, "L23", P, rx_extra_loss={"L23": 40.0})
    assert o.power_margin_db < 0 and o.plr == 1.0
