"""Access network on the RUNNING twin: reachability, and the modelled optics acting on the real data plane.

The fault tests need the optical agent (http://localhost:8081); CI starts it next to the temporary twin.
"""
import json
import os
import time
import urllib.request

import pytest

from netgen import model

from .conftest import sh

AGENT = os.getenv("FT_AGENT_URL", "http://localhost:8081")
SOT = model.load()
ONTS = {o["name"]: o["ip"].split("/")[0] for o in model.access(SOT)["onts"]}


def reachable(ip: str) -> bool:
    return sh("srv", f"ping -c 3 -i 0.2 -W 1 -q {ip}").returncode == 0


def wait_for(pred, timeout=60, what=""):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(2)
    raise AssertionError(f"timed out: {what}")


def agent(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(AGENT + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read() or b"{}")


def agent_up() -> bool:
    try:
        agent("GET", "/state")
        return True
    except Exception:
        return False


def test_every_ont_reachable_from_the_internet_server():
    for ont, ip in ONTS.items():
        wait_for(lambda ip=ip: reachable(ip), 90, f"srv -> {ont} ({ip})")


def test_olt_shapes_the_shared_downstream():
    out = sh("olt1", "tc class show dev eth2").stdout
    assert "rate 2500Kbit" in out or "rate 2.5Mbit" in out, out


@pytest.mark.skipif(not agent_up(), reason="optical agent not running")
class TestModelledOptics:
    def teardown_method(self):
        agent("DELETE", "/faults")

    def test_drop_cut_takes_down_only_that_ont(self):
        agent("POST", "/fault/pon", {"segment": "drop:ont3", "extra_db": 15})     # far below sensitivity: LOS
        wait_for(lambda: not reachable(ONTS["ont3"]), 30, "ont3 unreachable")
        for o in ("ont1", "ont2", "ont4"):
            assert reachable(ONTS[o]), o

    def test_feeder_cut_takes_down_every_ont(self):
        agent("POST", "/fault/pon", {"segment": "feeder:spl1", "extra_db": 15})
        wait_for(lambda: not any(reachable(ip) for ip in ONTS.values()), 40, "all ONTs unreachable")

    def test_repair_restores_every_ont(self):
        agent("POST", "/fault/pon", {"segment": "feeder:spl1", "extra_db": 15})
        wait_for(lambda: not reachable(ONTS["ont1"]), 30, "ont1 down")
        agent("DELETE", "/faults")
        for o, ip in ONTS.items():
            wait_for(lambda ip=ip: reachable(ip), 40, f"{o} back")

    def test_small_degradation_keeps_service(self):
        """+4 dB on the feeder: margins ~4 dB, still above sensitivity, no customer impact (early warning zone)."""
        agent("POST", "/fault/pon", {"segment": "feeder:spl1", "extra_db": 4})
        time.sleep(8)
        st = agent("GET", "/state")["pon"]["paths"]
        assert all(1 < m < 5 for m in st.values()), st
        for ip in ONTS.values():
            assert reachable(ip)
