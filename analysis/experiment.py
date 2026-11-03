"""Golden-path experiment (Slice 1e checkpoint): packets lost WITHOUT vs WITH the early drain.

Same scenario twice: constant UDP streams (1000 pkt/s each) cross both links of fibre F12
(srv->h1 over L12, srv->h3 over L13) while loss inside span 0 of F12 ramps up past the FEC cliff.
  run A  "without": the response playbook (n8n) is stopped; alerts fire, nobody acts
  run B  "with":    n8n opens the drain PR, the approver merges it as soon as CI is green,
                    the pull-based deployer applies it, the system verifies recovery
Then the fibre is repaired and the drain is reverted (restore PR) so both runs start equal.

Runs from Windows: lab commands through `wsl`, approvals through `gh`. Writes results/1e-golden-path.json.
Usage: python analysis/experiment.py [--rate 4] [--to-db 32] [--hold 90] [--runs without,with]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPTICAL, API, PROM = "http://localhost:8081", "http://localhost:8000", "http://localhost:9090"
REPO = "azyzex/FiberTwin-Ops"
STREAMS = [("h1", "5202", "srv->h1 (L12)"), ("h3", "5203", "srv->h3 (L13)")]


def wsl(cmd: str, check: bool = True) -> str:
    r = subprocess.run(["wsl.exe", "-d", "Debian", "-u", "root", "--", "bash", "-c", cmd], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"wsl {cmd!r}: {r.stderr}")
    return r.stdout


def http(method: str, url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def prom(q: str) -> list:
    return http("GET", f"{PROM}/api/v1/query?{urllib.parse.urlencode({'query': q})}")["data"]["result"]


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args, "-R", REPO], capture_output=True, text=True).stdout


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def l12_cost_on_main() -> int:
    import yaml
    raw = subprocess.run(["gh", "api", f"repos/{REPO}/contents/sot/network.yaml", "-H", "Accept: application/vnd.github.raw"],
                         capture_output=True, text=True).stdout
    return yaml.safe_load(raw)["links"]["L12"]["ospf_cost"]


def wait_deployed_sha(sha: str, timeout: float = 300) -> float:
    end = time.time() + timeout
    while time.time() < end:
        if wsl("cat /opt/fibertwin/state/deployed_sha", check=False).strip() == sha:
            return time.time()
        time.sleep(2)
    raise TimeoutError("deployer did not apply the commit")


def merge_when_green(pr: str, t0: float, marks: dict) -> None:
    """The human approver, played immediately: merge as soon as the required checks are green."""
    num = pr.rstrip("/").split("/")[-1]
    while True:
        out = gh("pr", "checks", num)
        if out and all("\tpass\t" in l for l in out.strip().splitlines()) and len(out.strip().splitlines()) >= 2:
            break
        if "\tfail\t" in out:
            raise RuntimeError(f"CI failed on {pr}")
        time.sleep(5)
    marks["ci_green_s"] = round(time.time() - t0, 1)
    gh("pr", "merge", num, "--squash", "--delete-branch")
    marks["merged_s"] = round(time.time() - t0, 1)
    sha = json.loads(gh("pr", "view", num, "--json", "mergeCommit"))["mergeCommit"]["oid"]
    marks["deployed_s"] = round(wait_deployed_sha(sha) - t0, 1)
    marks["merge_sha"] = sha


def restore(marks: dict) -> None:
    """After the repair: the restore PR = git revert of the drain (plan guardrail 6)."""
    sha = marks.get("merge_sha")
    if not sha:
        return
    branch = f"restore/F12-{int(time.time())}"
    wt = ROOT.parent / "ft-restore-worktree"           # separate worktree: never touches my working copy
    g = lambda *a, cwd=ROOT: subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)
    g("fetch", "-q", "origin", "main")
    subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)], capture_output=True)
    g("worktree", "add", "-q", "-B", branch, str(wt), "origin/main")
    g("revert", "--no-edit", sha, cwd=wt)
    g("push", "-q", "-u", "origin", branch, cwd=wt)
    g("worktree", "remove", "--force", str(wt))
    url = gh("pr", "create", "--base", "main", "--head", branch, "--title", "Restore F12 after repair (revert drain)",
             "--body", "Fibre F12 repaired: revert the drain so L12/L13 carry traffic again.").strip().splitlines()[-1]
    log(f"restore PR {url}")
    m: dict = {}
    merge_when_green(url, time.time(), m)
    log("restore deployed")


def run(name: str, respond: bool, rate: float, to_db: float, hold: int) -> dict:
    log(f"=== run '{name}' (response {'ON' if respond else 'OFF'}) ===")
    assert l12_cost_on_main() == 10, "main still has a drain: restore first"
    http("DELETE", f"{OPTICAL}/faults")
    wsl(f"docker {'start' if respond else 'stop'} ft-monitoring-n8n-1")
    time.sleep(45)                                     # let margins, OSPF and alerts settle
    dur = int(to_db * 60 / rate + hold)
    for host, port, _ in STREAMS:
        wsl(f"docker exec -d clab-ft-core-{host} sh -c 'iperf3 -c 203.0.113.10 -p {port} -R -u -b 1M -l 125 "
            f"-t {dur} -J > /tmp/{name}.json 2>&1'")
    time.sleep(2)
    t0 = time.time()
    http("POST", f"{OPTICAL}/fault/ramp", {"fibre": "F12", "span": 0, "to_db": to_db, "db_per_min": rate})
    marks: dict = {"ramp_db_per_min": rate, "to_db": to_db}
    seen_pr = None
    first_loss = None
    while time.time() - t0 < dur + 5:
        el = time.time() - t0
        if first_loss is None:
            lossy = [r for r in prom('ft_optical_applied_plr{link=~"L12|L13"}') if float(r["value"][1]) > 1e-6]
            if lossy:
                first_loss = marks["model_loss_starts_s"] = round(el, 1)
        if "detected_s" not in marks and prom('ALERTS{alertname="FtOsnrMarginTrend",alertstate="firing"}'):
            marks["detected_s"] = round(el, 1)
        if respond and not seen_pr:
            incs = http("GET", f"{API}/incidents")
            incs = [i for i in incs if i["created"] > t0 and i["pr_url"]]
            if incs:
                seen_pr = incs[0]["pr_url"]
                marks["incident"], marks["pr"] = incs[0]["id"], seen_pr
                marks["pr_opened_s"] = round(el, 1)
                log(f"drain PR opened at {el:.0f}s: {seen_pr}")
                merge_when_green(seen_pr, t0, marks)
                log(f"merged at {marks['merged_s']}s, deployed at {marks['deployed_s']}s")
        time.sleep(2)
    res = {}
    for host, _, label in STREAMS:
        j = json.loads(wsl(f"docker exec clab-ft-core-{host} cat /tmp/{name}.json"))
        s = j["end"]["sum"]
        res[label] = {"packets": s["packets"], "lost": s["lost_packets"], "loss_pct": round(s["lost_percent"], 3)}
    marks["streams"] = res
    marks["lost_total"] = sum(v["lost"] for v in res.values())
    if respond and marks.get("incident"):
        time.sleep(40)
        marks["events"] = http("GET", f"{API}/incidents/{marks['incident']}/events")
    log(f"run '{name}': lost {marks['lost_total']} packets {json.dumps(res)}")
    http("DELETE", f"{OPTICAL}/faults")
    log("fibre repaired")
    restore(marks)
    return marks


def main() -> None:
    import urllib.parse  # noqa: F401  (used in prom)
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=4.0)
    ap.add_argument("--to-db", type=float, default=32.0)
    ap.add_argument("--hold", type=int, default=90)
    ap.add_argument("--runs", default="without,with")
    a = ap.parse_args()
    res_file = ROOT / "results" / "1e-golden-path.json"
    out = json.loads(res_file.read_text()) if res_file.exists() else {"scenario": "F1 gray failure, F12 span 0", "runs": {}}
    out["date"] = datetime.now(timezone.utc).isoformat()   # re-running one run keeps the other
    for name in a.runs.split(","):
        out["runs"][name] = run(name, name == "with", a.rate, a.to_db, a.hold)
        (ROOT / "results").mkdir(exist_ok=True)
        (ROOT / "results" / "1e-golden-path.json").write_bytes((json.dumps(out, indent=1) + "\n").encode())
    wsl("docker start ft-monitoring-n8n-1", check=False)
    log("done -> results/1e-golden-path.json")


if __name__ == "__main__":
    import urllib.parse
    main()
