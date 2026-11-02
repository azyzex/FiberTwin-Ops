"""FiberTwin analysis API (FastAPI + SQLite): decides, and acts through GitHub/Slack. n8n orchestrates the order.

Endpoints (called by the n8n playbook, in this order for the golden path):
  POST /alerts                     read firing alerts + optical telemetry from Prometheus, correlate,
                                   open/update an incident, return the decision (drain / ticket / none)
  POST /incidents/{id}/whatif      capacity + SRLG check of the drain (same code as CI: netgen.whatif)
  POST /incidents/{id}/drain-pr    open the pull request that raises the OSPF cost of the fibre's links
  POST /incidents/{id}/ticket      GitHub issue (work order) for faults a config change can't fix
  GET  /incidents/{id}/status      CI result, merged (= human approval), deployed by the pull-based deployer
  POST /incidents/{id}/verify      did customers recover? if not: open a revert PR
  POST /notify                     Slack message (escalation reminders from n8n)
  GET  /incidents, /incidents/{id}, /incidents/{id}/events (data contract v0), /healthz, /metrics
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from analysis import logic
from netgen import whatif

PROM = os.getenv("PROM_URL", "http://prometheus:9090")
SOT_PATH = Path(os.getenv("FT_SOT", "/ft/sot/network.yaml"))
DB = Path(os.getenv("DB_PATH", "/data/incidents.db"))
REPO = os.getenv("GH_REPO", "azyzex/FiberTwin-Ops")
GH_TOKEN_FILE = Path(os.getenv("GH_TOKEN_FILE", "/run/secrets/github-token"))
SLACK_FILE = Path(os.getenv("SLACK_WEBHOOK_FILE", "/run/secrets/slack-webhook"))
DEPLOYED_SHA = Path(os.getenv("DEPLOYED_SHA_FILE", "/ft/state/deployed_sha"))
DRAIN_COST = whatif.DRAIN_COST

app = FastAPI(title="FiberTwin analysis API")
_alerts_lock = threading.Lock()   # two webhooks at once (L12 + L13) must not open two incidents


# ---------------------------------------------------------------- storage
def db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE IF NOT EXISTS incidents (
        id TEXT PRIMARY KEY, created REAL, fault_class TEXT, fibre TEXT, links TEXT, state TEXT,
        pr_number INTEGER, pr_url TEXT, merge_sha TEXT, original_costs TEXT, whatif TEXT, verify TEXT, issue_url TEXT);
      CREATE TABLE IF NOT EXISTS events (
        incident_id TEXT, ts TEXT, type TEXT, fault_class TEXT, element TEXT, summary TEXT, url TEXT);""")
    return c


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def event(inc: sqlite3.Row | dict, type_: str, summary: str, url: str | None = None, once: bool = False) -> None:
    with closing(db()) as c:
        if once and c.execute("SELECT 1 FROM events WHERE incident_id=? AND type=?", (inc["id"], type_)).fetchone():
            return
        c.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                  (inc["id"], now_iso(), type_, inc["fault_class"], inc["fibre"] or ",".join(json.loads(inc["links"])),
                   summary, url))
        c.commit()


def get_incident(iid: str) -> sqlite3.Row:
    with closing(db()) as c:
        r = c.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
    if not r:
        raise HTTPException(404, "no such incident")
    return r


def update(iid: str, **fields) -> None:
    with closing(db()) as c:
        c.execute(f"UPDATE incidents SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), iid))
        c.commit()


# ---------------------------------------------------------------- integrations
def prom(q: str) -> list[dict]:
    url = f"{PROM}/api/v1/query?{urllib.parse.urlencode({'query': q})}"
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)["data"]["result"]


def gh(method: str, path: str, body: dict | None = None) -> dict:
    token = GH_TOKEN_FILE.read_text().strip()
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "fibertwin-analysis"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def slack(text: str) -> None:
    try:
        url = SLACK_FILE.read_text().strip()
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:   # a Slack outage must never block the incident flow
        print(f"slack: {e}", flush=True)


def sot() -> dict:
    return yaml.safe_load(SOT_PATH.read_text())


def telemetry(s: dict) -> dict[str, dict]:
    o = s["meta"].get("optics", {})
    design_rx = o.get("p_ch_dbm", 0.0) - o.get("patch_loss_db", 1.0)
    t = {l: {"design_rx_power": design_rx} for l in s["links"]}
    for name, key in (("ft_optical_margin_db", "margin"), ("ft_optical_design_margin_db", "design_margin"),
                      ("ft_optical_rx_power_dbm", "rx_power")):
        for r in prom(f"avg_over_time({name}[30s])"):        # smooth the monitor noise
            if r["metric"].get("link") in t:
                t[r["metric"]["link"]][key] = float(r["value"][1])
    return t


def firing() -> list[dict]:
    return [r["metric"] for r in prom('ALERTS{alertstate="firing"}')]


# ---------------------------------------------------------------- endpoints
@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "github_token": GH_TOKEN_FILE.exists(), "repo": REPO}


@app.post("/alerts")
def alerts(payload: dict | None = None) -> dict:
    """Alertmanager's webhook body is only a trigger: the current state is read from Prometheus."""
    with _alerts_lock:
        return _alerts()


def _alerts() -> dict:
    s = sot()
    c = logic.correlate(s, firing(), telemetry(s))
    if c.fault_class is None and not c.links:
        return {"incident": None, "action": "none", "reasons": c.evidence}
    key = c.fibre or ",".join(c.links)
    with closing(db()) as con:
        open_drains = {r["fibre"] for r in con.execute("SELECT fibre FROM incidents WHERE state IN ('draining','drained')")}
        # cooldown counts only drains that were really applied (merged), not proposals that were rejected
        recent = {r["fibre"]: r["created"] for r in con.execute(
            "SELECT fibre, created FROM incidents WHERE fibre IS NOT NULL AND merge_sha IS NOT NULL")}
        inc = con.execute("SELECT * FROM incidents WHERE (fibre=? OR links=?) AND state NOT IN ('closed','reverted')",
                          (c.fibre, json.dumps(c.links))).fetchone()
    if inc:   # already handling it: just report where we are
        return {"incident": inc["id"], "action": "none", "reasons": [f"incident {inc['id']} already open ({inc['state']})"],
                "fault_class": inc["fault_class"], "fibre": inc["fibre"], "links": json.loads(inc["links"])}
    d = logic.decide(c, open_drains, recent, time.time())
    if d.action == "none":
        return {"incident": None, "action": "none", "reasons": d.reasons}
    iid = f"INC-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{key.replace(',', '+')}"
    with closing(db()) as con:
        con.execute("INSERT INTO incidents (id, created, fault_class, fibre, links, state) VALUES (?,?,?,?,?,?)",
                    (iid, time.time(), c.fault_class, c.fibre, json.dumps(c.links), "open"))
        con.commit()
    inc = get_incident(iid)
    event(inc, "detected", "; ".join(a for a in c.evidence[:1]) or "alert")
    if c.fault_class:
        event(inc, "located", f"{c.fault_class} on {c.fibre or c.links[0]}: " + " | ".join(c.evidence))
    return {"incident": iid, "action": d.action, "reasons": d.reasons, "fault_class": c.fault_class,
            "fibre": c.fibre, "links": c.links}


@app.post("/incidents/{iid}/whatif")
def run_whatif(iid: str) -> dict:
    inc = get_incident(iid)
    links = json.loads(inc["links"])
    chk = whatif.check_drain(sot(), links, avoid_fibre=inc["fibre"])
    util = {f"{l} {a}->{b}": round(u, 1) for (l, a, b), u in chk.placement.util_pct(sot()["meta"]["capacity_mbps"]).items() if u > 0}
    result = {"ok": chk.ok, "reasons": chk.reasons, "max_util_pct": round(chk.max_util_pct, 1), "util_after_pct": util}
    update(iid, whatif=json.dumps(result))
    event(inc, "what_if_passed" if chk.ok else "what_if_failed",
          f"drain {', '.join(links)}: max utilisation {chk.max_util_pct:.0f}% (limit {sot()['meta']['max_utilisation_pct']}%)"
          + ("" if chk.ok else "; " + "; ".join(chk.reasons)))
    return result


@app.post("/incidents/{iid}/drain-pr")
def drain_pr(iid: str) -> dict:
    inc = get_incident(iid)
    if inc["fault_class"] not in logic.ALLOWED_TO_REMEDIATE:
        raise HTTPException(409, f"{inc['fault_class']} is not allowed to change the network")
    wi = json.loads(inc["whatif"] or "{}")
    if not wi.get("ok"):
        raise HTTPException(409, "what-if check has not passed")
    if inc["pr_number"]:
        return {"pr": inc["pr_url"]}
    links, fibre = json.loads(inc["links"]), inc["fibre"]
    main = gh("GET", "/git/ref/heads/main")["object"]["sha"]
    branch = f"drain/{fibre}-{iid[-15:].replace(':', '').replace('+', '-')}".lower()
    gh("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": main})
    f = gh("GET", f"/contents/sot/network.yaml?ref={branch}")
    text = base64.b64decode(f["content"]).decode()
    original = {l: yaml.safe_load(text)["links"][l]["ospf_cost"] for l in links}
    new = logic.drain_sot_text(text, links, DRAIN_COST, f"drained: {fibre} degrading ({iid})")
    gh("PUT", "/contents/sot/network.yaml", {"message": f"Drain {fibre}: {', '.join(links)} OSPF cost -> {DRAIN_COST} ({iid})",
                                              "content": base64.b64encode(new.encode()).decode(), "sha": f["sha"], "branch": branch})
    body = "\n".join([
        f"**Automated proposal from incident `{iid}`**: fibre **{fibre}** is degrading (fault class {inc['fault_class']}).", "",
        "**Evidence**", *[f"- {e['summary']}" for e in events_of(iid) if e["type"] in ("detected", "located")], "",
        "**Change**: raise the OSPF cost of every IP link riding the fibre, so traffic leaves it *before* packets are lost.",
        *[f"- `{l}`: {original[l]} → {DRAIN_COST}" for l in links], "",
        f"**What-if (same engine as CI)**: max utilisation after the drain **{wi['max_util_pct']}%** "
        f"(limit {sot()['meta']['max_utilisation_pct']}%), no traffic left on {fibre}.", "",
        "**Approve by merging** after CI is green. The pull-based deployer applies it; the system then verifies "
        "recovery and opens a revert PR if customers did not recover.", "",
        "*Restore after repair: revert this PR.*"])
    pr = gh("POST", "/pulls", {"title": f"Drain {fibre} (degrading): {', '.join(links)} cost-out", "head": branch,
                               "base": "main", "body": body})
    update(iid, state="draining", pr_number=pr["number"], pr_url=pr["html_url"], original_costs=json.dumps(original))
    event(inc, "pr_opened", f"PR #{pr['number']}: {', '.join(links)} OSPF cost {DRAIN_COST}", pr["html_url"])
    slack(f":rotating_light: *{iid}*: fibre *{fibre}* degrading (F1). Drain PR ready for approval: {pr['html_url']}\n"
          f"What-if: max utilisation after drain {wi['max_util_pct']}%.")
    return {"pr": pr["html_url"], "number": pr["number"]}


@app.post("/incidents/{iid}/ticket")
def ticket(iid: str) -> dict:
    inc = get_incident(iid)
    if inc["issue_url"]:
        return {"issue": inc["issue_url"]}
    why = {"F1b": "Receiver-side loss on one link (dirty connector / patch after the last amplifier). "
                  "A config change can't fix it: clean or replace the patch at the receiving end.",
           "F2": "Fibre cut. OSPF/BFD already rerouted traffic; no config change. Dispatch a splicing team."
           }.get(inc["fault_class"], "Degradation that matches no single fibre: investigate.")
    ev = [e["summary"] for e in events_of(iid) if e["type"] in ("detected", "located")]
    issue = gh("POST", "/issues", {"title": f"[{inc['fault_class'] or '?'}] {inc['fibre'] or ', '.join(json.loads(inc['links']))}: work order ({iid})",
                                    "body": why + "\n\n**Evidence**\n" + "\n".join(f"- {e}" for e in ev),
                                    "labels": ["incident"]})
    update(iid, issue_url=issue["html_url"], state="ticketed")
    event(inc, "work_order" if inc["fault_class"] == "F1b" else "ticket", why, issue["html_url"])
    slack(f":ticket: *{iid}*: {why} {issue['html_url']}")
    return {"issue": issue["html_url"]}


@app.get("/incidents/{iid}/status")
def status(iid: str) -> dict:
    inc = get_incident(iid)
    if not inc["pr_number"]:
        return {"stage": inc["state"]}
    pr = gh("GET", f"/pulls/{inc['pr_number']}")
    if pr.get("merged"):
        # main is protected: a PR can only be merged once the required checks (validate + temporary twin)
        # passed, so "merged" proves CI was green, without needing the token's Checks permission
        event(inc, "ci_passed", "required CI checks passed: validate + temporary twin with live tests", once=True)
        event(inc, "approved", f"merged by {pr['merged_by']['login']} (human approval)", pr["html_url"], once=True)
        update(iid, merge_sha=pr["merge_commit_sha"])
        deployed = DEPLOYED_SHA.read_text().strip() if DEPLOYED_SHA.exists() else ""
        if deployed:
            cmp = gh("GET", f"/compare/{pr['merge_commit_sha']}...{deployed}")
            if cmp.get("status") in ("identical", "ahead"):
                event(inc, "deployed", f"pull-based deployer applied {deployed[:7]} (hitless frr-reload)", once=True)
                if inc["state"] == "draining":
                    update(iid, state="drained")
                return {"stage": "deployed", "deployed_sha": deployed}
        return {"stage": "merged"}
    if pr["state"] == "closed":
        update(iid, state="closed")
        event(inc, "closed", "PR closed without merge: no change made", once=True)
        return {"stage": "rejected"}
    return {"stage": "waiting_approval"}


@app.post("/incidents/{iid}/verify")
def verify(iid: str) -> dict:
    inc = get_incident(iid)
    links = json.loads(inc["links"])
    util = {r["metric"]["link"]: float(r["value"][1]) for r in prom("avg_over_time(ft:link_util_ratio[30s])")}
    loss = {f"{r['metric']['src']}->{r['metric']['dst']}": float(r["value"][1])
            for r in prom("max by (src, dst) (avg_over_time(ft_probe_loss_ratio[30s]))")}
    v = logic.verify(links, util, loss, firing())
    update(iid, verify=json.dumps(v.__dict__))
    if v.recovered:
        event(inc, "verified", "; ".join(v.reasons), once=True)
        slack(f":white_check_mark: *{iid}*: drain of *{inc['fibre']}* verified. {v.reasons[0]}. "
              f"Restore after repair: revert {inc['pr_url']}")
        return {"recovered": True, "reasons": v.reasons}
    # not recovered: automatic revert PR (guardrail 5)
    orig = json.loads(inc["original_costs"] or "{}")
    main = gh("GET", "/git/ref/heads/main")["object"]["sha"]
    branch = f"revert/{inc['fibre']}-{int(time.time())}".lower()
    gh("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": main})
    f = gh("GET", f"/contents/sot/network.yaml?ref={branch}")
    text = base64.b64decode(f["content"]).decode()
    for l, c in orig.items():
        text = logic.drain_sot_text(text, [l], c, "")
    gh("PUT", "/contents/sot/network.yaml", {"message": f"Revert drain of {inc['fibre']} ({iid}): not recovered",
                                              "content": base64.b64encode(text.encode()).decode(), "sha": f["sha"], "branch": branch})
    pr = gh("POST", "/pulls", {"title": f"Revert drain of {inc['fibre']}: verification failed", "head": branch, "base": "main",
                               "body": "Verification failed:\n" + "\n".join(f"- {r}" for r in v.reasons)})
    update(iid, state="reverting")
    event(inc, "revert_opened", "; ".join(v.reasons), pr["html_url"], once=True)
    slack(f":warning: *{iid}*: drain did not restore service ({'; '.join(v.reasons)}). Revert PR: {pr['html_url']}")
    return {"recovered": False, "reasons": v.reasons, "revert_pr": pr["html_url"]}


@app.post("/notify")
def notify(body: dict) -> dict:
    slack(body.get("text", ""))
    return {"ok": True}


def events_of(iid: str) -> list[dict]:
    with closing(db()) as c:
        return [dict(r) | {"incident_id": iid} for r in
                c.execute("SELECT * FROM events WHERE incident_id=? ORDER BY ts", (iid,))]


@app.get("/incidents")
def incidents() -> list[dict]:
    with closing(db()) as c:
        return [dict(r) for r in c.execute("SELECT * FROM incidents ORDER BY created DESC LIMIT 50")]


@app.get("/incidents/{iid}")
def incident(iid: str) -> dict:
    return dict(get_incident(iid)) | {"events": events_of(iid)}


@app.get("/incidents/{iid}/events")
def contract_events(iid: str) -> list[dict]:
    """Events in data contract v0 format (contracts/event.schema.json)."""
    out = []
    for e in events_of(iid):
        ev = {"ts": e["ts"], "incident_id": iid, "type": e["type"], "fault_class": e["fault_class"] or "F1",
              "element": e["element"], "summary": e["summary"]}
        if e["url"]:
            ev["url"] = e["url"]
        out.append(ev)
    return out


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    with closing(db()) as c:
        states = c.execute("SELECT state, COUNT(*) n FROM incidents GROUP BY state").fetchall()
        evs = c.execute("SELECT incident_id, type, ts FROM events WHERE ts > ?",
                        (datetime.fromtimestamp(time.time() - 3600, timezone.utc).isoformat(),)).fetchall()
    lines = ["# HELP ft_incidents Incidents by state", "# TYPE ft_incidents gauge"]
    lines += [f'ft_incidents{{state="{r["state"]}"}} {r["n"]}' for r in states]
    lines += ["# HELP ft_incident_event Incident timeline events (last hour), value = event time", "# TYPE ft_incident_event gauge"]
    for r in evs:
        t = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp()
        lines.append(f'ft_incident_event{{incident="{r["incident_id"]}",type="{r["type"]}"}} {t}')
    return "\n".join(lines) + "\n"
