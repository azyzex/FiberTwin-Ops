"""Resume the Slice 3 experiment after the interruption (PR #26 already opened by the system).

1. wait until the deployer has repaired the twin after the WSL restart
2. T1110: merge the system's block PR #26 when green (the approver), wait for the nftables reload on the OLT,
   probe from the attacker, wait for the playbook's verification
3. T1046: full run (rogue ONT scan -> incident -> block PR -> merge -> verify)
4. unblock both (revert PRs) so the lab ends clean
Writes results/3-security.json.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from experiment import log, merge_when_green, restore, wsl
from security_experiment import SCENARIOS, api, run

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "3-security.json"


def wait_twin() -> None:
    for _ in range(120):
        tail = wsl("grep -E '^20' /opt/fibertwin/state/deploy.log | tail -n 2", check=False)
        if wsl("docker exec clab-ft-core-r1 ip link show eth1 >/dev/null 2>&1 && echo ok", check=False).strip() == "ok" \
                and "DRIFT running twin" not in tail.splitlines()[-1:][0] if tail.strip() else False:
            return
        time.sleep(5)


def t1110() -> dict:
    inc = next(i for i in api("/incidents") if i["pr_number"] == 26)
    t0 = inc["created"]
    m = {"technique": "T1110", "src": "198.51.100.66", "incident": inc["id"], "pr": inc["pr_url"],
         "note": "resumed after CI rejected the first run (tests coupled to the live blocklist, fixed in PR #27)"}
    log(f"T1110: resume {inc['id']}, merging {inc['pr_url']}")
    wsl(SCENARIOS["T1110"]["attack"])                 # the attack is still going when the block lands
    merge_when_green(inc["pr_url"], time.time(), m)
    log(f"merged; deployed {m['deployed_s']} s after merge decision")
    time.sleep(10)
    m["reachable_after_block"] = wsl(SCENARIOS["T1110"]["probe"], check=False).strip()
    m["nft_after"] = wsl("docker exec clab-ft-core-olt1 nft list chain inet fibertwin forward | grep counter", check=False).strip()
    log(f"attacker -> ont2:22 after the block: {m['reachable_after_block']}")
    for _ in range(80):
        ev = api(f"/incidents/{inc['id']}/events")
        if any(e["type"] == "verified" for e in ev):
            break
        time.sleep(5)
    m["events"] = ev
    wsl(SCENARIOS["T1110"]["stop"], check=False)
    log("T1110 events: " + " -> ".join(e["type"] for e in ev))
    return m


def main() -> None:
    out = {"date": datetime.now(timezone.utc).isoformat(), "runs": {}}
    log("waiting for the twin to be repaired after the WSL restart")
    wait_twin()
    out["runs"]["T1110"] = t1110()
    OUT.write_bytes((json.dumps(out, indent=1) + "\n").encode())
    time.sleep(30)
    out["runs"]["T1046"] = run("T1046", SCENARIOS["T1046"])
    OUT.write_bytes((json.dumps(out, indent=1) + "\n").encode())
    for tech, m in out["runs"].items():
        if m.get("merge_sha"):
            log(f"unblock {m['src']} (revert)")
            restore(m)
    log("done -> results/3-security.json")


if __name__ == "__main__":
    main()
