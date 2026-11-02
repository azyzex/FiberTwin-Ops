"""Tests against the RUNNING twin (CI deploys a temporary one). The intent in sot/ must be what the routers do."""
import ipaddress
import time

import pytest

from netgen import model, whatif

from .conftest import sh, vtysh_json


def wait(check, timeout=90):
    end = time.time() + timeout
    while True:
        try:
            if check():
                return
        except Exception:
            pass
        if time.time() > end:
            raise AssertionError(f"timed out: {check.__doc__}")
        time.sleep(2)


def test_ospf_adjacencies_full(sot):
    for r in sot["routers"]:
        expected = sum(1 for l in sot["links"].values() if r in (l["a"], l["b"]))
        def full(r=r, expected=expected):
            """every core link of a router has a Full OSPF neighbour"""
            nb = vtysh_json(r, "show ip ospf neighbor")["neighbors"]
            return sum(1 for v in nb.values() for s in v if s["converged"] == "Full") == expected
        wait(full)


def test_bfd_up_on_every_core_link(sot):
    for r in sot["routers"]:
        expected = sum(1 for l in sot["links"].values() if r in (l["a"], l["b"]))
        def up(r=r, expected=expected):
            """BFD session up for every core link"""
            return sum(1 for p in vtysh_json(r, "show bfd peers") if p["status"] == "up") == expected
        wait(up)


def test_bgp_sessions_established(sot):
    n_core = len(sot["routers"])
    for r in sot["routers"]:
        expected = n_core - 1 + sum(1 for u in sot["upstreams"].values() if u["attach"]["router"] == r)
        def est(r=r, expected=expected):
            """all iBGP (+ eBGP on the border) sessions Established"""
            peers = vtysh_json(r, "show bgp ipv4 unicast summary")["peers"]
            return sum(1 for p in peers.values() if p["state"] == "Established") == expected
        wait(est)


def test_every_demand_is_reachable(sot):
    ips = {h["name"]: h["ip"].split("/")[0] for h in model.hosts(sot)}
    ips.update({u["server"]: u["server_ip"] for u in model.upstreams(sot)})
    for d in sot["demands"]:
        def ok(d=d):
            """ping between every demand pair"""
            return sh(d["src"], f"ping -c 2 -W 1 -q {ips[d['dst']]}").returncode == 0
        wait(ok)


def dest_subnet(sot, name):
    if name in sot["customers"]:
        return sot["customers"][name]["subnet"]
    return next(u["server"]["subnet"] for u in sot["upstreams"].values() if u["server"]["name"] == name)


def test_forwarding_matches_the_what_if_model(sot):
    """The router's real next hop for each demand = the link the what-if model loads."""
    placed = whatif.place_demands(sot).load
    by_link = {lid: l for lid, l in sot["links"].items()}
    for d in sot["demands"]:
        src_r, dst_r = whatif.attach_point(sot, d["src"]), whatif.attach_point(sot, d["dst"])
        model_links = {k[0] for k, v in placed.items() if k[1] == src_r and v > 0}
        dst_net = dest_subnet(sot, d["dst"])
        route = vtysh_json(src_r, f"show ip route {ipaddress.ip_network(dst_net)}")
        nexthops = [nh for e in route.values() for r in e for nh in r["nexthops"] if nh.get("active")]
        real_links = set()
        for nh in nexthops:
            for lid, l in by_link.items():
                for end, ifn in (("a", "a_if"), ("b", "b_if")):
                    if l[end] == src_r and l[ifn] == nh.get("interfaceName"):
                        real_links.add(lid)
        assert real_links & model_links, f"{d}: router uses {real_links}, model expects one of {model_links}"
