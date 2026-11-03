import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "twin"))
from config_drift import normalise, parse_reload_test  # noqa: E402

MANUAL = """
Lines To Delete
===============
interface eth1
 ip ospf cost 77
exit

Lines To Add
============
interface eth1
 ip ospf cost 10
exit
"""

# what CI saw on a freshly deployed, untouched r1 (FRR 10.2): display normalisation only
FRESH = """
Lines To Delete
===============
ipv6 forwarding
interface lo
 no ip ospf passive
exit

Lines To Add
============
router ospf
 passive-interface lo
exit
bfd
 profile core
  detect-multiplier 3
 exit
exit
bfd
 profile core
  receive-interval 300
 exit
exit
"""


def test_manual_change_is_reported_with_its_context():
    d = normalise(parse_reload_test(MANUAL), "no bgp default ipv4-unicast")
    assert d == {"delete": ["interface eth1 > ip ospf cost 77"], "add": ["interface eth1 > ip ospf cost 10"]}


def test_frr_display_normalisation_is_not_drift():
    assert normalise(parse_reload_test(FRESH), "no bgp default ipv4-unicast") == {"delete": [], "add": []}


REAL_BFD = """
Lines To Add
============
bfd
 profile core
  detect-multiplier 5
 exit
exit
"""


def test_a_real_bfd_change_is_still_drift():
    assert normalise(parse_reload_test(REAL_BFD), "")["add"] == ["bfd > profile core > detect-multiplier 5"]


def test_host_domainname_is_not_drift():
    d = {"delete": ["no domainname localdomain", "domainname lab.local"], "add": []}
    assert normalise(d, "") == {"delete": [], "add": []}
