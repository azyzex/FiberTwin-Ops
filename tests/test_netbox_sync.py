"""The NetBox view of the source of truth (no NetBox needed: the desired state is pure)."""
from collections import Counter

from netgen import model
from netgen.netbox_sync import desired

SOT = model.load()
W = desired(SOT)


def test_every_network_element_is_a_device():
    names = {d["name"] for d in W["devices"]}
    assert set(SOT["routers"]) | set(SOT["upstreams"]) | set(SOT["access"]["onts"]) | {"olt1", "spl1"} == names


def test_every_cable_end_and_address_lands_on_a_declared_interface():
    ifs = {(i["device"], i["name"]) for i in W["interfaces"]}
    for c in W["cables"]:
        assert tuple(c["a"]) in ifs and tuple(c["b"]) in ifs, c
    for a in W["ips"]:
        assert (a["device"], a["interface"]) in ifs, a


def test_no_duplicates():
    for key, items in (("address", W["ips"]), ("label", W["cables"])):
        dup = [k for k, n in Counter(x[key] for x in items).items() if n > 1]
        assert not dup, dup
    ends = Counter(tuple(e) for c in W["cables"] for e in (c["a"], c["b"]))
    assert all(n == 1 for n in ends.values()), "an interface has two cables"


def test_core_cables_carry_the_fibres_they_ride():
    cab = {c["label"]: c for c in W["cables"]}
    assert cab["L13"]["length_km"] == 140 + 60 and "F12+F23" in cab["L13"]["description"]
    assert cab["A-drop-ont3"]["length_km"] == 1.1 and cab["A-feed-spl1"]["length_km"] == 8.0


def test_onts_sit_at_the_olt_site():
    site = {d["name"]: d["site"] for d in W["devices"]}
    assert {site[o] for o in SOT["access"]["onts"]} == {site["olt1"]} == {site["r3"]}
