"""Analysis API decision logic: correlation, guardrails, verification, SoT edit."""
from pathlib import Path

import re

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


# ---------------------------------------------------------------- access
ACC = model.load()          # the live SoT has the access tree

def sot_text_without_blocks() -> str:
    """The live SoT with an empty blocklist: these tests check the logic, not today's network state
    (a legitimate block PR changes the live blocklist and must not fail them; it did, PR #26)."""
    text = (Path(__file__).resolve().parents[1] / "sot" / "network.yaml").read_text()
    return re.sub(r"  blocklist:\n(?:    - .*\n)*", "  blocklist: []\n", text)



def pon(**shifts):
    """shift (dB below design) per ONT; margin derived from a design margin of 8 dB."""
    out = {o: {"shift_db": 0.0, "margin_db": 8.0} for o in ACC["access"]["onts"]}
    for o, sh in shifts.items():
        out[o] = {"shift_db": sh, "margin_db": 8.0 - sh}
    return out


def test_feeder_degradation_all_onts_same_shift():
    c = logic.correlate_access(ACC, pon(ont1=5.1, ont2=5.0, ont3=4.8, ont4=5.2))
    assert (c.fault_class, c.fibre) == ("F3", "feeder:spl1") and len(c.links) == 4
    assert logic.decide(c, set(), {}, 0).action == "ticket"


def test_drop_degradation_one_ont():
    c = logic.correlate_access(ACC, pon(ont3=4.0))
    assert (c.fault_class, c.fibre, c.links) == ("F3", "drop:ont3", ["ont3"])


def test_all_onts_shifted_but_unequally_is_drops_not_feeder():
    c = logic.correlate_access(ACC, pon(ont1=2.0, ont2=6.0, ont3=2.1, ont4=2.2))
    assert c.fault_class == "F3" and c.fibre.startswith("drop:")


def test_feeder_cut_and_drop_cut():
    assert logic.correlate_access(ACC, pon(ont1=20, ont2=20, ont3=20, ont4=20)).fibre == "feeder:spl1"
    c = logic.correlate_access(ACC, pon(ont2=20))
    assert (c.fault_class, c.fibre) == ("F4", "drop:ont2")


def test_access_never_changes_the_network():
    for cl in ("F3", "F4"):
        assert logic.decide(logic.Correlation(cl, "drop:ont1", ["ont1"]), set(), {}, 0).action == "ticket"


def test_healthy_access_no_incident():
    assert logic.correlate_access(ACC, pon()).fault_class is None


# ---------------------------------------------------------------- security (F6)
def sig(src, tech="T1110", target="ont2"):
    return {"alertname": "FtSshBruteForceOnACpe", "src_ip": src, "attack_technique": tech, "container": target}


def test_external_brute_force_is_blocked():
    c = logic.correlate_security(ACC, [sig("198.51.100.66")])
    assert (c.fault_class, c.fibre) == ("F6", "block:198.51.100.66") and "external source" in c.evidence[0]
    assert logic.decide(c, set(), {}, 0).action == "block"


def test_rogue_ont_scan_is_quarantined():
    c = logic.correlate_security(ACC, [{"alertname": "FtNetworkScanFromAHost", "src_ip": "100.64.10.14",
                                         "attack_technique": "T1046"}])
    assert c.fibre == "block:100.64.10.14" and "rogue ONT ont4" in c.evidence[0] and c.links == ["ont4"]


def test_never_block_the_service_customers_use():
    c = logic.correlate_security(ACC, [sig("203.0.113.10")])
    assert logic.decide(c, set(), {}, 0).action == "ticket"


def test_block_edit_is_valid_sot():
    new = logic.block_sot_text(sot_text_without_blocks(), "198.51.100.66", "T1110 SSH brute force on ont2 (INC-1)")
    sec = yaml.safe_load(new)["security"]["blocklist"]
    assert sec == [{"ip": "198.51.100.66/32", "reason": "T1110 SSH brute force on ont2 (INC-1)"}]
    new2 = logic.block_sot_text(new, "100.64.10.14", "T1046 scan")
    assert len(yaml.safe_load(new2)["security"]["blocklist"]) == 2
    from netgen import model as m
    m.validate(yaml.safe_load(new2))


def test_blocking_infrastructure_is_refused_by_validation():
    import pytest
    from netgen import model as m
    root = Path(__file__).resolve().parents[1]
    bad = yaml.safe_load(logic.block_sot_text(sot_text_without_blocks(), "10.0.0.1", "oops"))
    with pytest.raises(m.SotError, match="infrastructure"):
        m.validate(bad)
