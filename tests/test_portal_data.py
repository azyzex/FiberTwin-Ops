"""The portal's What-if / Optical data come from the same engines; check what they say."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
import make_portal_data as m  # noqa: E402

from netgen import model  # noqa: E402

SOT = model.load()


def by_id(d):
    return {s["id"]: s for s in d["scenarios"]}


def test_every_single_fibre_cut_is_survivable():
    sc = by_id(m.whatif_data(SOT))
    for f in SOT["fibres"]:
        assert sc[f"cut-{f}"]["ok"], f


def test_double_cut_through_the_chord_splits_the_network():
    s = by_id(m.whatif_data(SOT))["cut-F12+F34"]
    assert not s["ok"] and s["paths"]["srv->h1"] == [] and "unreachable" in s["reasons"][0]


def test_drain_f12_matches_the_golden_path_check():
    s = by_id(m.whatif_data(SOT))["drain-F12"]
    assert s["ok"] and s["max_util_pct"] == 70.0 and s["paths"]["srv->h3"] == ["r1", "r4", "r3"]


def test_optics_curve_and_ramp():
    d = m.optics_data(SOT)
    assert 11 < d["required_osnr_db"] < 12
    assert d["curve"][0]["plr"] > 0.9 and d["curve"][-1]["plr"] == 0
    assert d["ramp_F12_span0"][0]["L12"]["margin_db"] > 20 and d["ramp_F12_span0"][-1]["L12"]["plr"] > 0.9
