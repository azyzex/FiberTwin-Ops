"""Parsing tests on real output captured from the lab (2 Nov 2026)."""
import sys, types
sys.modules.setdefault("docker", types.SimpleNamespace(from_env=lambda: None))
import exporter as e

TC = """qdisc netem 10: parent 1:10 limit 10000 delay 700us loss 5% seed 4092753250433717618
 Sent 12830 bytes 165 pkt (dropped 1, overlimits 0 requeues 0) """
TC_NOLOSS = """qdisc netem 10: parent 1:10 limit 10000 delay 700us
 Sent 9000 bytes 120 pkt (dropped 0, overlimits 0 requeues 0) """
PING = """3 packets transmitted, 3 received, 0% packet loss, time 202ms
rtt min/avg/max/mdev = 1.612/1.644/1.698/0.038 ms"""
PING_DEAD = "10 packets transmitted, 0 received, 100% packet loss, time 1000ms"

def test_netem():
    m = e.NETEM_STATS.search(TC); assert m.groups() == ("165", "1")
    assert e.NETEM_LOSS.search(TC).group(1) == "5"
    assert e.NETEM_LOSS.search(TC_NOLOSS) is None and e.NETEM_STATS.search(TC_NOLOSS).groups() == ("120", "0")

def test_ping():
    assert e.PING_LOSS.search(PING).group(1) == "0"
    assert e.PING_RTT.search(PING).group(2, 4) == ("1.644", "0.038")
    assert e.PING_LOSS.search(PING_DEAD).group(1) == "100" and e.PING_RTT.search(PING_DEAD) is None

def test_rate():
    assert e.rate_bps("100mbit") == 100e6 and e.rate_bps("10kbit") == 10e3
