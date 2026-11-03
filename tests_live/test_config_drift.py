"""F5 out-of-band change on the RUNNING twin: a manual vtysh edit must be detected and reversed."""
import json
import subprocess
import sys
from pathlib import Path

from .conftest import vtysh

ROOT = Path(__file__).resolve().parents[1]


def drift(*args):
    r = subprocess.run([sys.executable, str(ROOT / "twin" / "config_drift.py"), "--json", *args], capture_output=True, text=True)
    return json.loads(r.stdout)


def test_twin_starts_in_sync():
    assert drift()["drift"] == {}


def test_manual_change_is_detected_and_reversed():
    # someone logs into r2 and changes an OSPF cost by hand, outside the pipeline
    subprocess.run(["docker", "exec", "clab-ft-core-r2", "vtysh", "-c", "conf t", "-c", "interface eth1",
                    "-c", "ip ospf cost 77"], check=True, capture_output=True)
    d = drift()["drift"]
    assert "r2" in d and any("ip ospf cost 77" in l for l in d["r2"]["delete"]), d
    assert any("ip ospf cost 10" in l for l in d["r2"]["add"]), d
    r = drift("--reapply")
    assert r["reapplied"] == ["r2"] and r["still_drifted"] == {}
    assert "Cost: 10" in vtysh("r2", "show ip ospf interface eth1")
