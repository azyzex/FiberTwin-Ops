import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "twin"))
from config_drift import parse_reload_test  # noqa: E402

OUT = """
Lines To Delete
===============
interface eth1
 ip ospf cost 77

Lines To Add
============
interface eth1
 ip ospf cost 10
"""


def test_parse_frr_reload_test_output():
    d = parse_reload_test(OUT)
    assert d == {"delete": ["interface eth1", "ip ospf cost 77"], "add": ["interface eth1", "ip ospf cost 10"]}


def test_parse_in_sync():
    assert parse_reload_test("\nLines To Delete\n===============\n\nLines To Add\n============\n") == {"delete": [], "add": []}
