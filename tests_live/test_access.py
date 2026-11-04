"""Access network on the RUNNING twin: reachability, and the modelled optics acting on the real data plane.

The fault tests need the optical agent (http://localhost:8081); CI starts it next to the temporary twin.
"""
import ipaddress
import json
import os
import time
import urllib.request

import pytest

from netgen import model

from .conftest import sh

AGENT = os.getenv("FT_AGENT_URL", "http://localhost:8081")
SOT = model.load()
ALL_ONTS = {o["name"]: o["ip"].split("/")[0] for o in model.access(SOT)["onts"]}
# the intended state includes the security blocklist: a quarantined ONT (F6) MUST be unreachable, the rest served
_BLOCKED = [ipaddress.ip_network(b["ip"]) for b in model.security(SOT).get("blocklist", [])]
QUARANTINED = {o: ip for o, ip in ALL_ONTS.items() if any(ipaddress.ip_address(ip) in n for n in _BLOCKED)}
ONTS = {o: ip for o, ip in ALL_ONTS.items() if o not in QUARANTINED}


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


@pytest.mark.skipif(not QUARANTINED, reason="no ONT on the blocklist")
def test_quarantined_onts_are_cut_off():
    """A block PR merged: the OLT's nftables must really drop that ONT (enforcement checked, not assumed)."""
    for ont, ip in QUARANTINED.items():
        wait_for(lambda ip=ip: not reachable(ip), 60, f"{ont} ({ip}) still reachable although blocked")


def test_olt_shapes_the_shared_downstream():
    out = sh("olt1", "tc class show dev eth2").stdout
    assert "rate 2500Kbit" in out or "rate 2.5Mbit" in out, out


@pytest.mark.skipif(not agent_up(), reason="optical agent not running")
class TestModelledOptics:
    def teardown_method(self):
        agent("DELETE", "/faults")

    def test_drop_cut_takes_down_only_that_ont(self):
        victim = sorted(ONTS)[-2] if len(ONTS) > 1 else sorted(ONTS)[0]   # ont3 normally
        agent("POST", "/fault/pon", {"segment": f"drop:{victim}", "extra_db": 15})     # far below sensitivity: LOS
        wait_for(lambda: not reachable(ONTS[victim]), 30, f"{victim} unreachable")
        for o in ONTS:
            if o != victim:
                assert reachable(ONTS[o]), o

    def test_feeder_cut_takes_down_every_ont(self):
        agent("POST", "/fault/pon", {"segment": "feeder:spl1", "extra_db": 15})
        wait_for(lambda: not any(reachable(ip) for ip in ONTS.values()), 40, "all ONTs unreachable")

    def test_repair_restores_every_ont(self):
        agent("POST", "/fault/pon", {"segment": "feeder:spl1", "extra_db": 15})
        first = sorted(ONTS)[0]
        wait_for(lambda: not reachable(ONTS[first]), 30, f"{first} down")
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
