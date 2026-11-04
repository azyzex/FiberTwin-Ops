"""Analysis API end to end, with Prometheus, GitHub and Slack replaced by fakes (no lab, no network)."""
import base64
import importlib
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SOT_TEXT = (ROOT / "sot" / "network.yaml").read_text()
SOT = yaml.safe_load(SOT_TEXT)


class FakeProm:
    """Answers the API's PromQL queries from a small state dict."""

    def __init__(self):
        self.alerts: list[dict] = []
        self.margin = {l: 24.0 for l in SOT["links"]}
        self.design = dict(self.margin)
        self.rx = {l: -1.0 for l in SOT["links"]}
        self.pon_shift = {o: 0.0 for o in SOT["access"]["onts"]}
        self.util = {l: 0.3 for l in SOT["links"]}
        self.probe_loss = {"srv->h1": 0.0}

    def __call__(self, q: str) -> list[dict]:
        v = lambda labels, x: {"metric": labels, "value": [0, str(x)]}
        if q.startswith("ALERTS"):
            return [{"metric": a, "value": [0, "1"]} for a in self.alerts]
        if "ft_optical_margin_db" in q:
            return [v({"link": l}, m) for l, m in self.margin.items()]
        if "ft_optical_design_margin_db" in q:
            return [v({"link": l}, m) for l, m in self.design.items()]
        if "ft_optical_rx_power_dbm" in q:
            return [v({"link": l}, m) for l, m in self.rx.items()]
        if q.startswith("deriv(ft_pon_margin_db"):
            return [v({"ont": o, "direction": "up"}, -0.01 if s else 0) for o, s in self.pon_shift.items()]
        if "ft_pon_design_margin_db" in q:
            return [v({"ont": o, "direction": d}, 8.0) for o in self.pon_shift for d in ("down", "up")]
        if "ft_pon_margin_db" in q:
            m = re.search(r'ont="(\w+)"', q)
            onts = [m.group(1)] if m else list(self.pon_shift)
            return [v({"ont": o, "direction": d}, 8.0 - self.pon_shift[o]) for o in onts for d in ("down", "up")]
        if "ft:link_util_ratio" in q:
            return [v({"link": l}, u) for l, u in self.util.items()]
        if "ft_probe_loss_ratio" in q:
            return [v({"src": p.split("->")[0], "dst": p.split("->")[1]}, x) for p, x in self.probe_loss.items()]
        return []


class FakeGitHub:
    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.files = {"sot/network.yaml": SOT_TEXT}
        self.issues: dict[int, dict] = {}

    def __call__(self, method: str, path: str, body: dict | None = None) -> dict:
        self.calls.append((method, path, body))
        if path == "/git/ref/heads/main":
            return {"object": {"sha": "base"}}
        if path.startswith("/contents/sot/network.yaml") and method == "GET":
            return {"content": base64.b64encode(self.files["sot/network.yaml"].encode()).decode(), "sha": "f1"}
        if path == "/contents/sot/network.yaml" and method == "PUT":
            self.files["sot/network.yaml"] = base64.b64decode(body["content"]).decode()
            return {}
        if path == "/pulls" and method == "POST":
            return {"number": 42, "html_url": "https://github.com/x/y/pull/42"}
        if path == "/issues" and method == "POST":
            n = 100 + len(self.issues)
            self.issues[n] = body
            return {"number": n, "html_url": f"https://github.com/x/y/issues/{n}"}
        return {}


@pytest.fixture
def api(monkeypatch, tmp_path):
    sot_file = tmp_path / "network.yaml"
    sot_file.write_text(SOT_TEXT)
    monkeypatch.setenv("FT_SOT", str(sot_file))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
    import analysis.app as app_mod
    app_mod = importlib.reload(app_mod)
    prom, gh, sent = FakeProm(), FakeGitHub(), []
    monkeypatch.setattr(app_mod, "prom", prom)
    monkeypatch.setattr(app_mod, "gh", gh)
    monkeypatch.setattr(app_mod, "slack", lambda text: sent.append(text))
    return TestClient(app_mod.app), prom, gh, sent


def test_healthy_network_does_nothing(api):
    client, *_ = api
    assert client.post("/alerts").json()["action"] == "none"


def test_golden_path_drain_pr(api):
    client, prom, gh, sent = api
    prom.alerts = [{"alertname": "FtOsnrMarginTrend", "link": "L12"}, {"alertname": "FtOsnrMarginTrend", "link": "L13"}]
    prom.margin.update({"L12": 15.0, "L13": 14.0})
    r = client.post("/alerts").json()
    assert (r["action"], r["fault_class"], r["fibre"]) == ("drain", "F1", "F12")
    iid = r["incident"]
    assert client.post("/alerts").json()["action"] == "none"            # same incident: no second one
    assert client.post(f"/incidents/{iid}/whatif").json()["ok"] is True
    pr = client.post(f"/incidents/{iid}/drain-pr").json()
    assert pr["number"] == 42
    edited = yaml.safe_load(gh.files["sot/network.yaml"])
    assert edited["links"]["L12"]["ospf_cost"] == 1000 and edited["links"]["L13"]["ospf_cost"] == 1000
    assert edited["links"]["L23"]["ospf_cost"] == 10
    assert any("Drain PR ready" in m for m in sent)
    types = [e["type"] for e in client.get(f"/incidents/{iid}/events").json()]
    assert types == ["detected", "located", "what_if_passed", "pr_opened"]


def test_drain_pr_refused_for_tickets_only_faults(api):
    client, prom, *_ = api
    prom.alerts = [{"alertname": "FtLinkDown", "link": "L12"}, {"alertname": "FtLinkDown", "link": "L13"}]
    r = client.post("/alerts").json()
    assert (r["fault_class"], r["action"]) == ("F2", "ticket")
    assert client.post(f"/incidents/{r['incident']}/drain-pr").status_code == 409


def test_feeder_work_order_and_repair_check(api):
    client, prom, gh, sent = api
    prom.pon_shift = {o: 5.0 for o in prom.pon_shift}                    # every ONT -5 dB: the feeder
    r = client.post("/alerts").json()
    assert (r["fault_class"], r["fibre"], r["action"]) == ("F3", "feeder:spl1", "ticket")
    iid = r["incident"]
    issue = client.post(f"/incidents/{iid}/ticket").json()["issue"]
    body = next(iter(gh.issues.values()))["body"]
    assert "FEEDER" in body and "5.0 dB" in body and "ont4" in body and issue.endswith("/100")
    assert client.post("/alerts").json()["action"] == "none"            # ticketed stays open: no 2nd issue
    assert client.post(f"/incidents/{iid}/repaired", json={"note": "cleaned SC/APC"}).status_code == 409
    prom.pon_shift = {o: 0.1 for o in prom.pon_shift}                    # really fixed now
    assert client.post(f"/incidents/{iid}/repaired", json={"note": "cleaned SC/APC"}).json()["closed"]
    assert ("PATCH", "/issues/100", {"state": "closed"}) in gh.calls


def test_drop_work_order_names_one_customer(api):
    client, prom, gh, _ = api
    prom.pon_shift["ont3"] = 4.0
    r = client.post("/alerts").json()
    assert (r["fault_class"], r["fibre"], r["links"]) == ("F3", "drop:ont3", ["ont3"])
    client.post(f"/incidents/{r['incident']}/ticket")
    assert "DROP fibre of ont3" in next(iter(gh.issues.values()))["body"]


def test_attack_block_pr(api, monkeypatch):
    client, prom, gh, sent = api
    import analysis.app as app_mod
    monkeypatch.setattr(app_mod, "sigma_alerts", lambda: [
        {"alertname": "FtSshBruteForceOnACpe", "src_ip": "198.51.100.66", "attack_technique": "T1110",
         "container": "ont2", "source": "sigma"}])
    r = client.post("/alerts").json()
    assert (r["fault_class"], r["fibre"], r["action"]) == ("F6", "block:198.51.100.66", "block")
    iid = r["incident"]
    assert client.post(f"/incidents/{iid}/whatif").json()["ok"] is True
    assert client.post(f"/incidents/{iid}/remediation-pr").json()["number"] == 42
    edited = yaml.safe_load(gh.files["sot/network.yaml"])
    assert edited["security"]["blocklist"][0]["ip"] == "198.51.100.66/32"
    assert any("Block PR ready" in m for m in sent)


def test_never_block_the_internet_server(api, monkeypatch):
    client, *_ = api
    import analysis.app as app_mod
    monkeypatch.setattr(app_mod, "sigma_alerts", lambda: [
        {"alertname": "FtSshBruteForceOnACpe", "src_ip": "203.0.113.10", "attack_technique": "T1110", "container": "ont2"}])
    r = client.post("/alerts").json()
    assert r["action"] == "ticket"
