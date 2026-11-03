"""Analysis API decision logic: correlation, guardrails, verification, SoT edit."""
from pathlib import Path

import yaml

from analysis import logic
from netgen import model

SOT_1A = Path(__file__).parent / "fixtures" / "sot-1a.yaml"
SOT = model.load(SOT_1A)
DESIGN = {"L12": 24.4, "L13": 23.3, "L23": 29.2, "L34": 26.4, "L41": 22.0}


def tel(**margins):
    """telemetry with design values, overriding margins / rx power per link: L12=(margin, rx)."""
    t = {l: {"margin": m, "design_margin": m, "rx_power": -1.0, "design_rx_power": -1.0} for l, m in DESIGN.items()}
    for l, v in margins.items():
        t[l]["margin"], t[l]["rx_power"] = v
    return t


def alert(name, link):
    return {"alertname": name, "link": link}


def test_f1_both_links_of_f12_point_to_the_fibre():
    c = logic.correlate(SOT, [alert("FtOsnrMarginTrend", "L12"), alert("FtOsnrMarginTrend", "L13")],
                        tel(L12=(18, -1.0), L13=(17, -1.0)))
    assert (c.fault_class, c.fibre, sorted(c.links)) == ("F1", "F12", ["L12", "L13"])


def test_f1_named_from_first_alert_when_the_other_link_is_degrading_but_not_yet_alerting():
    """L12 alerts first; L13 already dropped >1 dB below design: still one fibre explains both."""
    c = logic.correlate(SOT, [alert("FtOsnrMarginTrend", "L12")], tel(L12=(18, -1.0), L13=(21, -1.0)))
    assert c.fault_class == "F1" and c.fibre == "F12"


def test_one_link_degrading_on_a_shared_fibre_is_not_that_fibre():
    """L12 alone degrades but L13 (also on F12) is perfect: not a span fault on F12."""
    c = logic.correlate(SOT, [alert("FtOsnrMarginTrend", "L12")], tel(L12=(18, -1.0)))
    assert c.fault_class is None and c.links == ["L12"]
    assert logic.decide(c, set(), {}, 0).action == "ticket"


def test_f1_on_a_fibre_with_a_single_rider():
    c = logic.correlate(SOT, [alert("FtOsnrMarginLow", "L41")], tel(L41=(2, -1.0)))
    assert (c.fault_class, c.fibre) == ("F1", "F41")


def test_f1b_receiver_side_power_drop_on_one_link():
    c = logic.correlate(SOT, [alert("FtOsnrMarginTrend", "L23")], tel(L23=(26, -12.0)))
    assert c.fault_class == "F1b" and c.links == ["L23"]
    d = logic.decide(c, set(), {}, 0)
    assert d.action == "ticket" and "connector" in d.reasons[0]


def test_f2_cut_is_correlation_only():
    c = logic.correlate(SOT, [alert("FtLinkDown", "L12"), alert("FtLinkDown", "L13")], tel())
    assert (c.fault_class, c.fibre) == ("F2", "F12")
    assert logic.decide(c, set(), {}, 0).action == "ticket"


def test_no_alert_no_incident():
    c = logic.correlate(SOT, [alert("FtLinkUtilisationHigh", "L12")], tel())
    assert c.fault_class is None and logic.decide(c, set(), {}, 0).action == "none"


def test_guardrails_in_progress_and_cooldown():
    c = logic.Correlation("F1", "F12", ["L12", "L13"])
    assert logic.decide(c, set(), {}, 0).action == "drain"
    assert logic.decide(c, {"F12"}, {}, 0).action == "none"
    assert logic.decide(c, set(), {"F12": 1000.0}, 1000.0 + 60).action == "ticket"
    assert logic.decide(c, set(), {"F12": 1000.0}, 1000.0 + 3600).action == "drain"


def test_verify_recovered_and_not():
    ok = logic.verify(["L12", "L13"], {"L12": 0.0, "L13": 0.01, "L41": 0.7}, {"srv->h1": 0.0}, [])
    assert ok.recovered
    bad = logic.verify(["L12", "L13"], {"L12": 0.3, "L13": 0.0}, {"srv->h1": 0.2},
                       [alert("FtLinkPacketLoss", "L41")])
    assert not bad.recovered and len(bad.reasons) == 3


def test_drain_edit_keeps_the_file_and_changes_only_the_costs():
    text = (Path(__file__).resolve().parents[1] / "sot" / "network.yaml").read_text()
    new = logic.drain_sot_text(text, ["L12", "L13"], 1000, "drained: F12 degrading (INC-1)")
    old_y, new_y = yaml.safe_load(text), yaml.safe_load(new)
    assert new_y["links"]["L12"]["ospf_cost"] == 1000 and new_y["links"]["L13"]["ospf_cost"] == 1000
    for l in ("L23", "L34", "L41"):
        assert new_y["links"][l] == old_y["links"][l]
    assert len(new.splitlines()) == len(text.splitlines()) + 2          # one note line per link
    assert max(map(len, new.splitlines())) <= max(map(len, text.splitlines()))  # lines don't grow
    restored = logic.drain_sot_text(new, ["L12", "L13"], 10, "")
    assert restored == text                                               # restore = exact original


def test_drained_sot_passes_yamllint(tmp_path):
    """The first live drain PR was rejected by CI (yamllint line length). Never again."""
    from yamllint import linter
    from yamllint.config import YamlLintConfig
    root = Path(__file__).resolve().parents[1]
    new = logic.drain_sot_text((root / "sot" / "network.yaml").read_text(), ["L12", "L13"], 1000,
                               "drained: F12 degrading (INC-20261102-234715-F12)")
    conf = YamlLintConfig(file=str(root / ".yamllint.yml"))
    problems = [p for p in linter.run(new, conf) if p.level == "error"]
    assert not problems, problems
