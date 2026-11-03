"""Generate the n8n playbook (n8n/workflows/golden-path.json).

Kept as code so the workflow is reviewable in a PR. n8n only ORCHESTRATES (order, waits, escalation);
every decision and every external action is a call to the analysis API (tested with pytest).

  Alertmanager webhook ─► Analyse alerts (API) ─► drain? ─► What-if ─► safe? ─► Open drain PR
                                               │                                  │
                                               └► ticket? ─► Open work order      ▼
                     ┌──────────────────────────── Wait 20 s ◄──────────────┐  Status (API)
                     │                                                       │     │
                     └──► Status ─► deployed? ─► Settle 60 s ─► Verify (API)  │     │
                                    rejected? ─► stop                        │     │
                                    every 15 min ─► remind approver (Slack) ─┘ ◄───┘
"""
import json
import uuid
from pathlib import Path

API = "http://analysis:8000"
INC = "{{ $('Analyse alerts').first().json.incident }}"
nodes, conns = [], {}


def node(name, type_, version, x, y, params, **extra):
    nodes.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, name)), "name": name, "type": type_, "typeVersion": version,
                  "position": [x, y], "parameters": params, **extra})


def http(name, method, path, x, y, body=None):
    p = {"method": method, "url": f"={API}{path}", "options": {"timeout": 30000}}
    if body is not None:
        p.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
    node(name, "n8n-nodes-base.httpRequest", 4.2, x, y, p)


def cond(name, left, op, right, x, y):
    c = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, name + "c")), "leftValue": left, "operator": op}
    if right is not None:
        c["rightValue"] = right
    node(name, "n8n-nodes-base.if", 2, x, y, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                       "conditions": [c], "combinator": "and"}, "options": {}})


def wait(name, seconds, x, y):
    node(name, "n8n-nodes-base.wait", 1.1, x, y, {"resume": "timeInterval", "amount": seconds, "unit": "seconds"},
         webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, name + "w")))


def link(src, dst, branch=0):
    outs = conns.setdefault(src, {"main": []})["main"]
    while len(outs) <= branch:
        outs.append([])
    outs[branch].append({"node": dst, "type": "main", "index": 0})


STR_EQ = {"type": "string", "operation": "equals"}
node("Alertmanager webhook", "n8n-nodes-base.webhook", 2, 0, 300,
     {"httpMethod": "POST", "path": "fibertwin-alerts", "responseMode": "onReceived", "options": {}},
     webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, "fibertwin-alerts")))
http("Analyse alerts", "POST", "/alerts", 220, 300, body="={{ JSON.stringify($json.body) }}")
cond("Drain?", "={{ $json.action }}", STR_EQ, "drain", 440, 300)
cond("Ticket?", "={{ $json.action }}", STR_EQ, "ticket", 660, 460)
http("Open work order", "POST", f"/incidents/{INC}/ticket", 880, 460)
http("What-if", "POST", f"/incidents/{INC}/whatif", 660, 200)
cond("Safe?", "={{ $json.ok }}", {"type": "boolean", "operation": "true", "singleValue": True}, None, 880, 200)
http("Not safe: escalate", "POST", "/notify", 1100, 320,
     body="={{ JSON.stringify({text: ':no_entry: ' + $('Analyse alerts').first().json.incident + ': drain of ' + "
          "$('Analyse alerts').first().json.fibre + ' is NOT safe (' + $json.reasons.join('; ') + '). Human decision needed.'}) }}")
http("Open drain PR", "POST", f"/incidents/{INC}/drain-pr", 1100, 100)
wait("Wait 20 s", 20, 1320, 100)
http("Status", "GET", f"/incidents/{INC}/status", 1540, 100)
cond("Deployed?", "={{ $json.stage }}", STR_EQ, "deployed", 1760, 100)
wait("Settle 60 s", 60, 1980, 0)
http("Verify recovery", "POST", f"/incidents/{INC}/verify", 2200, 0)
cond("Rejected?", "={{ $json.stage }}", STR_EQ, "rejected", 1980, 220)
cond("Every 15 min?", "={{ $runIndex % 45 }}", {"type": "number", "operation": "equals"}, 44, 2200, 320)
http("Remind approver", "POST", "/notify", 2420, 420,
     body="={{ JSON.stringify({text: ':hourglass: ' + $('Analyse alerts').first().json.incident + ' still waits for approval: ' + "
          "'the fibre keeps degrading. Review the drain PR.'}) }}")

link("Alertmanager webhook", "Analyse alerts")
link("Analyse alerts", "Drain?")
link("Drain?", "What-if", 0); link("Drain?", "Ticket?", 1)
link("Ticket?", "Open work order", 0)
link("What-if", "Safe?")
link("Safe?", "Open drain PR", 0); link("Safe?", "Not safe: escalate", 1)
link("Open drain PR", "Wait 20 s")
link("Wait 20 s", "Status")
link("Status", "Deployed?")
link("Deployed?", "Settle 60 s", 0); link("Deployed?", "Rejected?", 1)
link("Settle 60 s", "Verify recovery")
link("Rejected?", "Every 15 min?", 1)
link("Every 15 min?", "Remind approver", 0); link("Every 15 min?", "Wait 20 s", 1)
link("Remind approver", "Wait 20 s")

wf = {"id": "ftGoldenPath0001", "name": "FiberTwin golden path", "nodes": nodes, "connections": conns,
      "active": False, "settings": {"executionOrder": "v1"}, "pinData": {}, "tags": []}
out = Path(__file__).parent / "workflows" / "golden-path.json"
out.parent.mkdir(exist_ok=True)
out.write_bytes((json.dumps(wf, indent=1) + "\n").encode())
print(f"wrote {out} ({len(nodes)} nodes)")
