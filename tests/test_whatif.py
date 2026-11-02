"""Capacity + SRLG what-if. The baseline must match what was measured on the lab in 1a."""
import copy

import pytest

from netgen import model, whatif


@pytest.fixture
def sot():
    return model.load()


def test_srlg_map(sot):
    m = whatif.srlg_map(sot)
    assert sorted(m["F12"]) == ["L12", "L13"]       # the chord shares fibre F12 with L12
    assert sorted(m["F23"]) == ["L13", "L23"]


def test_baseline_matches_1a_measurement(sot):
    """1a measured 29.3 / 39.1 / 19.5 Mbit/s on L12 r1->r2, L13 r1->r3, L23 r2->r3."""
    load = whatif.place_demands(sot).load
    assert load[("L12", "r1", "r2")] == pytest.approx(30)
    assert load[("L13", "r1", "r3")] == pytest.approx(40)
    assert load[("L23", "r2", "r3")] == pytest.approx(20)
    assert ("L41", "r1", "r4") not in load and ("L34", "r3", "r4") not in load


def test_draining_fibre_f12_is_safe(sot):
    """The golden-path drain: cost-out every link on F12."""
    c = whatif.check_drain(sot, ["L12", "L13"], avoid_fibre="F12")
    assert c.ok, c.reasons
    assert c.max_util_pct == pytest.approx(70)      # L41 r1->r4 carries 30 + 40
    assert not ({"L12", "L13"} & c.placement.links_used())


def test_drain_blocked_when_backup_would_overload(sot):
    hot = copy.deepcopy(sot)
    hot["demands"][1]["mbps"] = 60                  # srv->h3 grows: the backup path would hit 90%
    c = whatif.check_drain(hot, ["L12", "L13"], avoid_fibre="F12")
    assert not c.ok and any("utilisation above 80%" in r for r in c.reasons)


def test_ecmp_splits_equal_cost_paths(sot):
    """Drain only L13: r1->r3 has two equal paths (via r2 and via r4), so srv->h3 splits 20/20."""
    load = whatif.place_demands(sot, costs={"L13": whatif.DRAIN_COST}).load
    assert load[("L41", "r1", "r4")] == pytest.approx(20)
    assert load[("L12", "r1", "r2")] == pytest.approx(30 + 20)


def test_unreachable_demand_detected(sot):
    p = whatif.place_demands(sot, failed={"L12", "L13", "L41"})   # r1 cut off from the core
    assert set(p.unreachable) == {"srv->h1", "srv->h3"}


def test_intended_state_fits_capacity():
    """Gate for every PR: with the costs in sot/network.yaml, every demand is reachable
    and no link goes above the utilisation limit."""
    sot = model.load()
    p = whatif.place_demands(sot)
    assert not p.unreachable, p.unreachable
    limit = sot["meta"]["max_utilisation_pct"]
    hot = {k: round(u) for k, u in p.util_pct(sot["meta"]["capacity_mbps"]).items() if u > limit}
    assert not hot, f"links above {limit}%: {hot}"
