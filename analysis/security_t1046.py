"""Run only the T1046 scenario (rogue ONT scan -> quarantine PR -> verify -> unblock)."""
import json
from pathlib import Path

from experiment import log, restore
from security_experiment import SCENARIOS, run

m = run("T1046", SCENARIOS["T1046"])
Path(__file__).resolve().parents[1].joinpath("results", "3-security-t1046.json").write_bytes((json.dumps(m, indent=1) + "\n").encode())
if m.get("merge_sha"):
    log(f"unblock {m['src']} (revert)")
    restore(m)
log("done T1046")
