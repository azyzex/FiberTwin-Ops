"""E-model (ITU-T G.107, simplified): reference points and the shape of the curve."""
import pytest

from analysis.qoe import label, mos_from_r, r_factor, voice_mos


def test_default_r_gives_mos_4_4():
    assert mos_from_r(93.2) == pytest.approx(4.41, abs=0.01)


def test_delay_impairment_knee_at_177_ms():
    assert r_factor(150, 0) == pytest.approx(93.2 - 0.024 * 150)
    assert r_factor(300, 0) < r_factor(177.3, 0) - 13          # beyond the knee delay hurts much more


def test_loss_impairment_g711_plc():
    # 1 % random loss with G.711 + PLC: Ie_eff = 95 * 1 / (1 + 25.1) = 3.64
    assert 93.2 - r_factor(0, 1.0) == pytest.approx(3.64, abs=0.01)


def test_fibretwin_paths_are_excellent_when_healthy():
    # measured in 1c: srv->h3 RTT ~2.3 ms, jitter ~0.1 ms, no loss
    assert label(voice_mos(2.3, 0.1, 0.0)) == "excellent"


def test_cliff_loss_ruins_the_call():
    # 20 % probe loss at the FEC cliff (run A of the golden path)
    assert voice_mos(2.3, 0.1, 0.20) < 3.1
    assert label(voice_mos(2.3, 0.1, 0.20)) in ("poor", "bad")


def test_mos_is_monotonic_in_loss_and_delay():
    xs = [voice_mos(2, 0.1, l / 100) for l in range(0, 30)]
    assert all(a >= b for a, b in zip(xs, xs[1:]))
    ys = [voice_mos(r, 0.1, 0) for r in range(0, 800, 20)]
    assert all(a >= b for a, b in zip(ys, ys[1:]))
