"""GPON budget (plan §6.2): budgets, the sensitivity point, and the feeder-vs-drop signature."""
import pytest

from netgen import model
from optics.access import ber_from_margin, path, plr_from_margin

SOT = model.load()
ONTS = list(SOT["access"]["onts"])


def test_budget_inside_class_b_plus_with_margin():
    b = SOT["access"]["budget"]
    for o in ONTS:
        down, up = path(SOT, o, "down"), path(SOT, o, "up")
        assert 13 <= b["olt_tx_dbm"] - down.rx_dbm <= 28          # B+ loss budget
        assert down.margin_db > 5 and up.margin_db > 5 and down.plr < 1e-12


def test_sensitivity_is_ber_1e_10():
    assert ber_from_margin(0.0) == pytest.approx(1e-10, rel=0.02)


def test_loss_cliff_is_sharp():
    assert plr_from_margin(1.0) < 1e-10 and plr_from_margin(-2.0) > 0.2


def test_feeder_fault_shifts_every_ont_equally():
    base = {o: path(SOT, o, "down").margin_db for o in ONTS}
    hit = {o: path(SOT, o, "down", {"feeder:spl1": 6}).margin_db for o in ONTS}
    assert all(base[o] - hit[o] == pytest.approx(6) for o in ONTS)


def test_drop_fault_shifts_one_ont_only():
    hit = {o: path(SOT, o, "up", {"drop:ont3": 6}).margin_db for o in ONTS}
    base = {o: path(SOT, o, "up").margin_db for o in ONTS}
    assert base["ont3"] - hit["ont3"] == pytest.approx(6)
    assert all(hit[o] == pytest.approx(base[o]) for o in ONTS if o != "ont3")


def test_loss_of_signal_far_below_sensitivity():
    p = path(SOT, "ont2", "down", {"drop:ont2": 20})
    assert p.los and p.plr == 1.0
