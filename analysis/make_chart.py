"""Before/after chart of the golden-path experiment, from data recorded in Prometheus.

Finds the two ramps (ft_truth_span_extra_loss_db on F12/span0 rising from 0), aligns them on the ramp
start, and plots for both runs: L12's measured OSNR margin, and the customer impact (worst probe loss
on srv->h1 / srv->h3). Marks detection, merge and deployment from results/1e-golden-path.json.
    python analysis/make_chart.py  ->  docs/img/1e-before-after.png
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROM = "http://localhost:9090"


def rng(q: str, start: float, end: float, step: int = 5) -> list[tuple[float, float]]:
    url = f"{PROM}/api/v1/query_range?" + urllib.parse.urlencode({"query": q, "start": start, "end": end, "step": step})
    with urllib.request.urlopen(url, timeout=30) as r:
        res = json.load(r)["data"]["result"]
    return [(float(t), float(v)) for t, v in res[0]["values"]] if res else []


def ramp_starts(hours: float = 3) -> list[float]:
    end = time.time()
    pts = rng('ft_truth_span_extra_loss_db{fibre="F12",span="0"}', end - hours * 3600, end, 5)
    starts, prev = [], 0.0
    for t, v in pts:
        if prev == 0 and v > 0:
            starts.append(t - 5)
        prev = v
    return starts


def main() -> None:
    res = json.loads((ROOT / "results" / "1e-golden-path.json").read_text())["runs"]
    starts = ramp_starts()
    # the experiment's two completed runs are the last two ramps of the "without" and "with" runs
    t_without, t_with = starts[-3], starts[-1]   # [-2] is the run aborted when CI rejected PR #9
    dur = 32 / 4 * 60 + 90
    fig, ax = plt.subplots(2, 1, figsize=(11, 6.6), sharex=True, gridspec_kw={"height_ratios": [1, 1]})
    for name, t0, colour in (("without response", t_without, "#d62728"), ("with response (drain PR)", t_with, "#2ca02c")):
        m = rng('avg_over_time(ft_optical_margin_db{link="L12"}[15s])', t0, t0 + dur)
        loss = rng('max(ft_probe_loss_ratio{dst=~"h1|h3",src="srv"})', t0, t0 + dur)
        ax[0].plot([t - t0 for t, _ in m], [v for _, v in m], color=colour, lw=2, label=name)
        ax[1].plot([t - t0 for t, _ in loss], [100 * v for _, v in loss], color=colour, lw=2, label=name)
    w = res["with"]
    for i, (key, label) in enumerate((("detected_s", "detected (trend alert)"), ("merged_s", "PR merged (approval)"),
                                      ("deployed_s", "drain deployed"))):
        for a in ax:
            a.axvline(w[key], color="#555", ls="--", lw=1)
        ax[0].text(w[key] + 3, 23 - 3.2 * i, label, fontsize=9, color="#333",
                   bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 1})
    ax[0].axhline(0, color="black", lw=1)
    ax[0].axhline(3, color="orange", lw=1, ls=":")
    ax[0].set_ylabel("L12 OSNR margin (dB)")
    ax[0].set_title("Fibre F12 degrading past the FEC cliff: customers WITHOUT vs WITH the early drain")
    ax[0].legend(loc="lower left")
    ax[1].set_ylabel("worst customer probe loss (%)")
    ax[1].set_xlabel("seconds since the fibre started degrading")
    lw, lo = res["without"]["lost_total"], res["with"]["lost_total"]
    ax[1].text(0.01, 0.9, f"packets lost on 2 constant streams (1.14 M packets): without {lw}, with {lo}",
               transform=ax[1].transAxes, fontsize=10, weight="bold")
    ax[1].legend(loc="center left")
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    out = ROOT / "docs" / "img" / "1e-before-after.png"
    fig.savefig(out, dpi=110)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
