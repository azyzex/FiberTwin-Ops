"""Resume T1046 (rogue ONT scan) after its block PR was rejected by CI and the live tests were fixed (PR #30).

The system's quarantine PR (#29) is still open and its incident is waiting for approval. Here:
merge it when green (the approver, played immediately) while the scan keeps going, wait for the deployer's
nftables reload, probe from the rogue ONT, wait for the playbook's verification (n8n, or the every-minute sweep),
then unblock with a revert PR. Writes results/3-security-t1046.json.
"""
import json
import time
from pathlib import Path

from experiment import log, merge_when_green, restore, wsl
from security_experiment import SCENARIOS, api

ROOT = Path(__file__).resolve().parents[1]
PR = 29


def main() -> None:
    sc = SCENARIOS["T1046"]
    inc = next(i for i in api("/incidents") if i["pr_number"] == PR)
    m = {"technique": "T1046", "src": sc["src"], "incident": inc["id"], "pr": inc["pr_url"],
         "note": "first CI run rejected the quarantine (live tests expected every ONT reachable); tests now read the "
                 "blocklist (PR #30), the same PR was re-tested and merged"}
    m["reachable_before"] = wsl(sc["probe"], check=False).strip()
    wsl(sc["attack"])                                  # the scan is still going when the block lands
    log(f"T1046: {inc['id']}, rogue ont4 -> ont1:22 before: {m['reachable_before']}; merging {inc['pr_url']}")
    merge_when_green(inc["pr_url"], time.time(), m)
    log(f"merged; deployed {m['deployed_s']} s after the merge decision")
    time.sleep(10)
    m["reachable_after_block"] = wsl(sc["probe"], check=False).strip()
    m["nft_after"] = wsl("docker exec clab-ft-core-olt1 nft list chain inet fibertwin forward | grep counter", check=False).strip()
    log(f"rogue ont4 -> ont1:22 after the block: {m['reachable_after_block']}")
    ev = []
    for _ in range(80):
        ev = api(f"/incidents/{inc['id']}/events")
        if any(e["type"] in ("verified", "verify_failed") for e in ev):
            break
        time.sleep(5)
    m["events"] = ev
    wsl(sc["stop"], check=False)
    log("T1046 events: " + " -> ".join(e["type"] for e in ev))
    (ROOT / "results" / "3-security-t1046.json").write_bytes((json.dumps(m, indent=1) + "\n").encode())
    log(f"unblock {m['src']} (revert)")
    restore(m)
    log("done -> results/3-security-t1046.json")


if __name__ == "__main__":
    main()
