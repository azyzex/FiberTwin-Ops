"""Slice 3 checkpoint: two ATT&CK scenarios, detected and blocked through the GitOps pipeline.

  T1110  the attacker (198.51.100.66) brute-forces the SSH of a customer ONT (ont2)
  T1046  a rogue ONT (ont4) scans its neighbours

For each: start the attack and keep it going, then time Sigma alert -> incident -> block PR -> CI green
-> merge (the approver, played immediately) -> deployer reloads nftables on the OLT -> attack traffic dropped
-> verification. Finally both blocks are reverted. Writes results/3-security.json.
Runs from Windows (lab via wsl, approvals via gh). Usage: python analysis/security_experiment.py
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from experiment import gh, log, merge_when_green, restore, wsl  # reuse the golden-path helpers

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"

RUN = "bash '/mnt/c/Users/AZYZ/Desktop/projects/Network Projects/FiberTwin-Ops/security/attacks/run.sh'"
SCENARIOS = {t: {"src": src, "attack": f"{RUN} {t} start", "probe": f"{RUN} {t} probe", "stop": f"{RUN} {t} stop"}
             for t, src in (("T1110", "198.51.100.66"), ("T1046", "100.64.10.14"))}


def api(path: str) -> list | dict:
    with urllib.request.urlopen(API + path, timeout=15) as r:
        return json.loads(r.read())


def run(tech: str, sc: dict) -> dict:
    log(f"=== {tech}: attack from {sc['src']} ===")
    marks: dict = {"technique": tech, "src": sc["src"], "reachable_before": wsl(sc["probe"], check=False).strip()}
    t0 = time.time()
    wsl(sc["attack"])
    inc = None
    while time.time() - t0 < 600:
        if not inc:
            for i in api("/incidents"):
                if i["fault_class"] == "F6" and i["fibre"] == f"block:{sc['src']}" and i["created"] > t0:
                    inc = i
            if inc and "detected_s" not in marks:
                marks["detected_s"] = round(inc["created"] - t0, 1)
                marks["incident"] = inc["id"]
                log(f"incident {inc['id']} at {marks['detected_s']} s")
        if inc:
            cur = api(f"/incidents/{inc['id']}")
            if cur["pr_url"] and "pr_opened_s" not in marks:
                marks["pr_opened_s"], marks["pr"] = round(time.time() - t0, 1), cur["pr_url"]
                log(f"block PR {cur['pr_url']}")
                merge_when_green(cur["pr_url"], t0, marks)
                log(f"merged {marks['merged_s']} s, deployed {marks['deployed_s']} s")
                time.sleep(10)
                marks["reachable_after_block"] = wsl(sc["probe"], check=False).strip()
                log(f"attacker's view after the block: {marks['reachable_after_block']}")
            ev = [e["type"] for e in api(f"/incidents/{inc['id']}/events")]
            if "verified" in ev:
                marks["verified_s"] = round(time.time() - t0, 1)
                marks["events"] = api(f"/incidents/{inc['id']}/events")
                log(f"verified at {marks['verified_s']} s")
                break
        time.sleep(3)
    wsl(sc["stop"], check=False)
    return marks


def main() -> None:
    out = {"date": datetime.now(timezone.utc).isoformat(), "runs": {}}
    for tech, sc in SCENARIOS.items():
        out["runs"][tech] = run(tech, sc)
        (ROOT / "results" / "3-security.json").write_bytes((json.dumps(out, indent=1) + "\n").encode())
        time.sleep(20)
    for tech, m in out["runs"].items():            # unblock: revert each block PR (restore PR)
        if m.get("merge_sha"):
            log(f"unblock {m['src']} (revert)")
            restore(m)
    log("done -> results/3-security.json")


if __name__ == "__main__":
    main()
