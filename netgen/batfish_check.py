"""Batfish parse test (plan Slice 2: "Batfish parse test, then Batfish in CI if it works").

Batfish reads FRR through its Cumulus "concatenated" format: /etc/hostname + /etc/network/interfaces +
/etc/cumulus/ports.conf + /etc/frr/frr.conf in one file. This script builds that snapshot from the
generated configs, asks a running Batfish service (localhost:9996) to parse it, and reports:
  - parse status per device, and initialisation issues
  - OSPF adjacencies and BGP sessions Batfish computes, vs what the source of truth expects
It does NOT gate merges: it measures whether Batfish is usable for this network.

    python netgen/batfish_check.py [--host localhost] [--summary $GITHUB_STEP_SUMMARY]
"""
from __future__ import annotations

import argparse
import ipaddress
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from netgen import model  # noqa: E402

SNAP = ROOT / "build" / "batfish" / "snapshot"


def concatenated(sot: dict, name: str, frr_conf: str) -> str:
    """Cumulus-style concatenated config for one FRR router."""
    r = model.routers(sot)[name]
    lines = [name, "# This file describes the network interfaces available on your system",
             "auto lo", "iface lo inet loopback", f"    address {r.loopback}", ""]
    for i in r.ifaces:
        lines += [f"auto {i.name}", f"iface {i.name}", f"    address {i.ip}", ""]
    lines += ["# ports.conf --", "", "frr version 10.2.1"]
    body = [l for l in frr_conf.splitlines() if not l.startswith("frr defaults")]
    return "\n".join(lines + body) + "\n"


def build_snapshot(sot: dict) -> Path:
    cfg = SNAP / "configs"
    cfg.mkdir(parents=True, exist_ok=True)
    for name in sot["routers"]:
        text = (ROOT / "build" / "ft-core" / "configs" / name / "frr.conf").read_text()
        (cfg / f"{name}.cfg").write_bytes(concatenated(sot, name, text).encode())
    return SNAP


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--summary")
    a = ap.parse_args()
    from pybatfish.client.session import Session
    sot = model.load()
    snap = build_snapshot(sot)
    bf = Session(host=a.host)
    bf.set_network("fibertwin")
    bf.init_snapshot(str(snap), name="ft", overwrite=True)
    out = ["## Batfish parse test (non-blocking)", ""]
    ps = bf.q.fileParseStatus().answer().frame()
    out += ["| file | status |", "|---|---|"] + [f"| {r.File_Name} | {r.Status} |" for r in ps.itertuples()]
    issues = bf.q.initIssues().answer().frame()
    out += ["", f"**Initialisation issues: {len(issues)}**"]
    unrecognised = []
    for r in issues.itertuples():
        line = str(getattr(r, "Line_Text", "") or "").strip()
        unrecognised.append(line)
        out.append(f"- {r.Type} on {getattr(r, 'Nodes', '')}: `{line}` ({str(r.Details)[:80]})")
    exp_ospf = 2 * len(sot["links"])                      # one session per direction
    try:
        ospf = bf.q.ospfSessionCompatibility().answer().frame()
        est = int((ospf["Session_Status"] == "ESTABLISHED").sum())
    except Exception as e:  # noqa: BLE001
        est, ospf = -1, None
        out.append(f"- OSPF question failed: {e}")
    try:
        bgp = bf.q.bgpSessionStatus().answer().frame()
        bgp_est = int((bgp["Established_Status"] == "ESTABLISHED").sum())
    except Exception as e:  # noqa: BLE001
        bgp_est = -1
        out.append(f"- BGP question failed: {e}")
    n = len(sot["routers"])
    exp_bgp = n * (n - 1)                                  # iBGP full mesh, both directions (the upstream isn't in the snapshot)
    out += ["", "| check | Batfish | expected (SoT) |", "|---|---|---|",
            f"| OSPF sessions established | {est} | {exp_ospf} |",
            f"| iBGP sessions established | {bgp_est} | {exp_bgp} |"]
    routing_ok = est == exp_ospf and bgp_est == exp_bgp
    only_bfd = all("bfd" in l.lower() for l in unrecognised if l)
    if all(s == "PASSED" for s in ps["Status"]) and routing_ok:
        verdict = "Batfish fully understands these FRR configs"
    elif routing_ok and only_bfd:
        verdict = ("Batfish models OSPF and BGP correctly; it only ignores the BFD lines. "
                   "Usable for routing checks (BFD stays covered by the live twin)")
    else:
        verdict = "Batfish does NOT model these FRR configs well enough (see above)"
    out += ["", f"**Verdict: {verdict}**"]
    text = "\n".join(out) + "\n"
    print(text)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as f:
            f.write(text)
    (ROOT / "build" / "batfish" / "report.md").write_bytes(text.encode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
