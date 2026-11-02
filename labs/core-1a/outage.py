"""Read iperf3 JSON (UDP, reverse mode: srv -> h1) on stdin and print the outage.

iperf3 sends at a constant rate even when nothing comes back (unlike ping, which backs off),
so   outage = lost packets / packets per second.
"""
import json
import sys

d = json.load(sys.stdin)
s = d["end"]["sum"]
pps = s["packets"] / s["seconds"] if s.get("seconds") else 0
lost = s["lost_packets"]
print(f"   {s['packets']} packets at ~{pps:.0f} pkt/s, {lost} lost -> outage ~ {lost / pps:.2f} s")
