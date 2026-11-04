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
AM = os.getenv("AM_URL", "http://alertmanager:9093")
SOT_PATH = Path(os.getenv("FT_SOT", "/ft/sot/network.yaml"))
DB = Path(os.getenv("DB_PATH", "/data/incidents.db"))
REPO = os.getenv("GH_REPO", "azyzex/FiberTwin-Ops")
GH_TOKEN_FILE = Path(os.getenv("GH_TOKEN_FILE", "/run/secrets/github-token"))
SLACK_FILE = Path(os.getenv("SLACK_WEBHOOK_FILE", "/run/secrets/slack-webhook"))
DEPLOYED_SHA = Path(os.getenv("DEPLOYED_SHA_FILE", "/ft/state/deployed_sha"))
DRAIN_COST = whatif.DRAIN_COST
NL = chr(10)

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


def pon_telemetry() -> dict[str, dict]:
    """Per ONT, worst direction: how far below design the margin is (shift), and the margin itself."""
    meas: dict[tuple[str, str], float] = {}
    design: dict[tuple[str, str], float] = {}
    for r in prom("avg_over_time(ft_pon_margin_db[30s])"):
        meas[(r["metric"]["ont"], r["metric"]["direction"])] = float(r["value"][1])
    for r in prom("ft_pon_design_margin_db"):
        design[(r["metric"]["ont"], r["metric"]["direction"])] = float(r["value"][1])
    out: dict[str, dict] = {}
    for (ont, d), m in meas.items():
        o = out.setdefault(ont, {"shift_db": 0.0, "margin_db": 99.0})
        o["shift_db"] = max(o["shift_db"], design.get((ont, d), m) - m)
        o["margin_db"] = min(o["margin_db"], m)
    return out


def minutes_to_sensitivity(onts: list[str]) -> float | None:
    """At the current slope of the worst margin, minutes until it reaches 0 dB (errors start)."""
    worst = None
    for r in prom("deriv(ft_pon_margin_db[5m])"):
        if r["metric"]["ont"] in onts and float(r["value"][1]) < 0:
            m = next((float(x["value"][1]) for x in prom(f'ft_pon_margin_db{{ont="{r["metric"]["ont"]}",'
                                                         f'direction="{r["metric"]["direction"]}"}}')), None)
            if m is not None and m > 0:
                t = m / -float(r["value"][1]) / 60
                worst = t if worst is None else min(worst, t)
    return worst


def sigma_alerts() -> list[dict]:
    """Security alerts come from Loki's ruler (Sigma rules) straight to Alertmanager, not via Prometheus."""
    q = urllib.parse.urlencode({"active": "true", "silenced": "false", "inhibited": "false", "filter": 'source="sigma"'})
    try:
        with urllib.request.urlopen(f"{AM}/api/v2/alerts?{q}", timeout=5) as r:
            return [a["labels"] for a in json.load(r)]
    except Exception as e:
        print(f"alertmanager: {e}", flush=True)
        return []


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


def reconcile(s: dict) -> None:
    """Close drained incidents whose drain is no longer in main (the restore PR was merged):
    the incident state follows the source of truth instead of being remembered."""
    with closing(db()) as con:
        for inc in con.execute("SELECT * FROM incidents WHERE state IN ('drained','draining') AND merge_sha IS NOT NULL").fetchall():
            orig = json.loads(inc["original_costs"] or "{}")
            if orig and all(s["links"][l]["ospf_cost"] == c for l, c in orig.items()):
                con.execute("UPDATE incidents SET state='closed' WHERE id=?", (inc["id"],))
                con.commit()
                event(inc, "closed", "restore merged: links back to their original OSPF cost", once=True)


def _alerts() -> dict:
    s = sot()
    reconcile(s)
    c = logic.correlate(s, firing(), telemetry(s))
    if c.fault_class is None and not c.links and s.get("access"):
        c = logic.correlate_access(s, pon_telemetry())          # nothing in the core: look at the access tree
    if c.fault_class is None and not c.links and s.get("security"):
        c = logic.correlate_security(s, sigma_alerts())         # nothing optical: an attack?
    if c.fault_class is None and not c.links:
        return {"incident": None, "action": "none", "reasons": c.evidence}
    key = c.fibre or ",".join(c.links)
    with closing(db()) as con:
        open_drains = {r["fibre"] for r in con.execute("SELECT fibre FROM incidents WHERE state IN ('draining','drained')")}
        # cooldown counts only drains that were really applied (merged), not proposals that were rejected
        recent = {r["fibre"]: r["created"] for r in con.execute(
            "SELECT fibre, created FROM incidents WHERE fibre IS NOT NULL AND merge_sha IS NOT NULL")}
        # same element AND same fault class: a cut (F2) on a fibre that had a gray failure (F1) is a new incident
        inc = con.execute("SELECT * FROM incidents WHERE (fibre=? OR links=?) AND fault_class IS ? "
                          "AND state NOT IN ('closed','reverted')",
                          (c.fibre, json.dumps(c.links), c.fault_class)).fetchone()
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
    if inc["fault_class"] == "F6":
        return whatif_block(inc)
    links = json.loads(inc["links"])
    chk = whatif.check_drain(sot(), links, avoid_fibre=inc["fibre"])
    util = {f"{l} {a}->{b}": round(u, 1) for (l, a, b), u in chk.placement.util_pct(sot()["meta"]["capacity_mbps"]).items() if u > 0}
    result = {"ok": chk.ok, "reasons": chk.reasons, "max_util_pct": round(chk.max_util_pct, 1), "util_after_pct": util}
    update(iid, whatif=json.dumps(result))
    event(inc, "what_if_passed" if chk.ok else "what_if_failed",
          f"drain {', '.join(links)}: max utilisation {chk.max_util_pct:.0f}% (limit {sot()['meta']['max_utilisation_pct']}%)"
          + ("" if chk.ok else "; " + "; ".join(chk.reasons)))
    return result


def whatif_block(inc) -> dict:
    """Guardrails for a block: valid, not already blocked, not protected, and the SoT with the block
    still passes validation (which refuses infrastructure and over-wide ranges)."""
    from netgen import model as nm
    s = sot()
    ip = inc["fibre"].split(":", 1)[1]
    reasons = []
    if ip in logic.protected_ips(s):
        reasons.append(f"{ip} serves customers (protected)")
    if any(e["ip"].split("/")[0] == ip for e in (s.get("security") or {}).get("blocklist") or []):
        reasons.append(f"{ip} is already blocked")
    try:
        nm.validate(yaml.safe_load(logic.block_sot_text(SOT_PATH.read_text(), ip, "what-if")))
    except nm.SotError as e:
        reasons.append(str(e))
    result = {"ok": not reasons, "reasons": reasons or [f"blocking {ip} at the OLT touches no customer service"],
              "max_util_pct": None}
    update(inc["id"], whatif=json.dumps(result))
    event(inc, "what_if_passed" if result["ok"] else "what_if_failed", "; ".join(result["reasons"]))
    return result


@app.post("/incidents/{iid}/remediation-pr")
def remediation_pr(iid: str) -> dict:
    """The playbook's single 'propose a change' step: drain for F1, block for F6."""
    inc = get_incident(iid)
    return block_pr(iid) if inc["fault_class"] == "F6" else drain_pr(iid)


def block_pr(iid: str) -> dict:
    inc = get_incident(iid)
    wi = json.loads(inc["whatif"] or "{}")
    if not wi.get("ok"):
        raise HTTPException(409, "guardrail check has not passed")
    if inc["pr_number"]:
        return {"pr": inc["pr_url"]}
    ip = inc["fibre"].split(":", 1)[1]
    ev = [e["summary"] for e in events_of(iid) if e["type"] in ("detected", "located")]
    reason = (ev[0].split(": ATT&CK ")[-1] if ev else "attack") + f" ({iid})"
    main = gh("GET", "/git/ref/heads/main")["object"]["sha"]
    branch = f"block/{ip.replace('.', '-')}-{int(time.time())}"
    gh("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": main})
    f = gh("GET", f"/contents/sot/network.yaml?ref={branch}")
    text = base64.b64decode(f["content"]).decode()
    new = logic.block_sot_text(text, ip, reason[:120])
    gh("PUT", "/contents/sot/network.yaml", {"message": f"Block {ip} at the OLT ({iid})",
                                              "content": base64.b64encode(new.encode()).decode(), "sha": f["sha"], "branch": branch})
    body = NL.join([
        f"**Automated proposal from incident `{iid}`** (fault class F6, attack).", "", "**Evidence**", *[f"- {e}" for e in ev], "",
        f"**Change**: add `{ip}/32` to `security.blocklist`. The generator renders it into the OLT's nftables set: "
        "forwarded traffic from and to that address is dropped (both directions).", "",
        f"**Guardrails passed**: {'; '.join(wi['reasons'])}. Infrastructure (10.0.0.0/8), over-wide ranges and the "
        "addresses customers depend on can never be blocked (SoT validation + analysis API).", "",
        "**Approve by merging** after CI is green; the pull-based deployer applies it, then the system checks that the "
        "attack traffic is being dropped and customers are unaffected.", "", "*Unblock later: revert this PR.*"])
    pr = gh("POST", "/pulls", {"title": f"Block {ip} at the OLT ({reason[:60]})", "head": branch, "base": "main", "body": body})
    update(iid, state="blocking", pr_number=pr["number"], pr_url=pr["html_url"])
    event(inc, "pr_opened", f"PR #{pr['number']}: block {ip} at the OLT", pr["html_url"])
    slack(f":lock: *{iid}*: {ev[0] if ev else ip}. Block PR ready for approval: {pr['html_url']}")
    return {"pr": pr["html_url"], "number": pr["number"]}


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
           "F2": "Fibre cut. OSPF/BFD already rerouted traffic; no config change. Dispatch a splicing team.",
           }.get(inc["fault_class"], "Degradation that matches no single fibre: investigate.")
    if inc["fault_class"] in ("F3", "F4"):
        seg = inc["fibre"]
        onts = json.loads(inc["links"])
        tel = pon_telemetry()
        extra = max((tel.get(o, {}).get("shift_db", 0) for o in onts), default=0)
        where = ("the shared FEEDER between the OLT and splitter " + seg.split(":")[1]) if seg.startswith("feeder:")             else "the DROP fibre of " + ", ".join(onts) + " (between the splitter port and the customer)"
        eta = minutes_to_sensitivity(onts)
        why = (f"{'Cut' if inc['fault_class'] == 'F4' else 'Degradation'} on {where}. "
               f"Estimated extra loss {extra:.1f} dB; customers affected: {', '.join(onts)}. "
               + (f"At the current trend the first customer reaches its receiver sensitivity in ~{eta:.0f} min. "
                  if eta and inc["fault_class"] == "F3" else "")
               + "No protection path in a GPON tree and no config change can fix it: check / clean connectors "
                 "and look for bends on that segment. When done, submit the 'repaired' form: "
                 "http://localhost:5678/form/fibertwin-repaired")
    ev = [e["summary"] for e in events_of(iid) if e["type"] in ("detected", "located")]
    issue = gh("POST", "/issues", {"title": f"[{inc['fault_class'] or '?'}] {inc['fibre'] or ', '.join(json.loads(inc['links']))}: work order ({iid})",
                                    "body": why + "\n\n**Evidence**\n" + "\n".join(f"- {e}" for e in ev),
                                    "labels": ["incident"]})
    update(iid, issue_url=issue["html_url"], state="ticketed")
    event(inc, "work_order" if inc["fault_class"] in ("F1b", "F3", "F4") else "ticket", why, issue["html_url"])
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
                event(inc, "deployed", f"pull-based deployer applied {deployed[:7]} ("
                      + ("nftables reload on the OLT" if inc["fault_class"] == "F6" else "hitless frr-reload") + ")", once=True)
                if inc["state"] == "draining":
                    update(iid, state="drained")
                if inc["state"] == "blocking":
                    update(iid, state="blocked")
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
    if inc["fault_class"] == "F6":
        return verify_block(inc)
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


@app.post("/incidents/{iid}/repaired")
def repaired(iid: str, body: dict | None = None) -> dict:
    """The technician says it's fixed (n8n form). Close only if the measurements agree."""
    inc = get_incident(iid)
    note = (body or {}).get("note", "")
    if inc["fault_class"] in ("F3", "F4"):
        tel = pon_telemetry()
        bad = {o: round(tel.get(o, {}).get("shift_db", 99), 1) for o in json.loads(inc["links"])
               if tel.get(o, {}).get("shift_db", 99) > 1.0}
        if bad:
            slack(f":x: *{iid}*: technician reported a repair, but {bad} are still below design (dB). Incident stays open.")
            raise HTTPException(409, f"still degraded: {bad}")
    update(iid, state="closed")
    event(inc, "closed", f"repaired by technician{': ' + note if note else ''}; measurements back to design", once=True)
    slack(f":white_check_mark: *{iid}* closed: repair confirmed by the measurements. {note}")
    if inc["issue_url"]:
        try:
            num = inc["issue_url"].rstrip("/").split("/")[-1]
            gh("POST", f"/issues/{num}/comments", {"body": f"Repaired and verified by the measurements. {note}"})
            gh("PATCH", f"/issues/{num}", {"state": "closed"})
        except Exception as e:
            print(f"closing issue: {e}", flush=True)
    return {"closed": True}


def verify_block(inc) -> dict:
    """After the block: the OLT is dropping that source's traffic (or the attack stopped), customers unaffected."""
    ip = inc["fibre"].split(":", 1)[1]
    dropped = sum(float(r["value"][1]) for r in prom("increase(ft_nft_blocked_packets_total[2m])"))
    still = [a for a in sigma_alerts() if a.get("src_ip") == ip and a.get("attack_technique") == "T1110"]
    loss = {f"{r['metric']['src']}->{r['metric']['dst']}": float(r["value"][1])
            for r in prom("max by (src, dst) (avg_over_time(ft_probe_loss_ratio[30s]))")}
    lossy = {k: v for k, v in loss.items() if v > 0.01}
    reasons = [f"OLT dropped {dropped:.0f} packets from/to {ip} in the last 2 min" if dropped > 0
               else f"no traffic from {ip} reached the PON since the block"]
    if still:
        reasons.append(f"brute force from {ip} still reaching a CPE")
    if lossy:
        reasons.append(f"customer probes losing packets: {lossy}")
    ok = not still and not lossy
    update(inc["id"], verify=json.dumps({"recovered": ok, "reasons": reasons}))
    if ok:
        event(inc, "verified", "; ".join(reasons), once=True)
        slack(f":white_check_mark: *{inc['id']}*: {ip} blocked at the OLT and verified. {reasons[0]}. Unblock: revert {inc['pr_url']}")
    else:
        slack(f":warning: *{inc['id']}*: block of {ip} not verified: {'; '.join(reasons)}")
    return {"recovered": ok, "reasons": reasons}


@app.post("/sweep")
def sweep() -> dict:
    """Move every open remediation forward from the state stored HERE (SQLite), not from a workflow's memory.
    n8n calls it every minute: if n8n restarts mid-incident (it did, Slice 3), nothing is lost."""
    done = []
    with closing(db()) as c:
        rows = c.execute("SELECT id FROM incidents WHERE pr_number IS NOT NULL AND state IN "
                         "('draining','drained','blocking','blocked')").fetchall()
    for r in rows:
        iid = r["id"]
        if any(e["type"] in ("verified", "revert_opened") for e in events_of(iid)):
            continue
        st = status(iid)
        if st.get("stage") == "deployed":
            dep = next((e for e in events_of(iid) if e["type"] == "deployed"), None)
            age = time.time() - datetime.fromisoformat(dep["ts"].replace("Z", "+00:00")).timestamp() if dep else 0
            if age >= 60:                     # let traffic settle before judging
                done.append({iid: verify(iid)})
            else:
                done.append({iid: "deployed, settling"})
        else:
            done.append({iid: st.get("stage")})
    return {"swept": done}


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
    reconcile(sot())
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
