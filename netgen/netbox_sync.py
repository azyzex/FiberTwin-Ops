"""NetBox as the inventory view of the source of truth (Slice 2).

Git (sot/network.yaml) stays the source of truth: it is reviewed in PRs and tested on a twin. NetBox is what an
operator's other tools (IPAM, cabling, field teams) read. This module keeps the two consistent:

  python -m netgen.netbox_sync --plan     what NetBox should contain (no NetBox needed)
  python -m netgen.netbox_sync --apply    create/update it (idempotent: a second run changes nothing)
  python -m netgen.netbox_sync --check    compare NetBox with Git; exit 1 on drift (someone edited NetBox by hand)

Modelled: sites, routers / upstream / OLT / splitter / ONTs, their interfaces and addresses, prefixes, and one cable
per link (core links carry the fibres they ride and their length, so SRLGs are visible in NetBox too).
Env: NETBOX_URL (default http://localhost:8080), NETBOX_TOKEN.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys

from netgen import model

TAG = "fibertwin"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ---------------------------------------------------------------- desired state (pure, unit-tested)
def desired(sot: dict) -> dict:
    rt = model.routers(sot)
    acc = model.access(sot)
    ups = model.upstreams(sot)
    devices, ifaces, ips, cables = [], [], [], []
    prefixes = {}

    def dev(name, role, site, dtype):
        devices.append({"name": name, "role": role, "site": site, "type": dtype})

    def iface(device, name, desc="", kind="other"):
        ifaces.append({"device": device, "name": name, "description": desc, "type": kind})

    def ip(address, device, name, desc=""):
        ips.append({"address": address, "device": device, "interface": name, "description": desc})

    for r in rt.values():
        dev(r.name, "core-router", r.site, "FRR 10.2.1")
        iface(r.name, "lo", "loopback (router id)", "virtual")
        ip(r.loopback, r.name, "lo", "loopback")
        for i in r.ifaces:
            iface(r.name, i.name, i.description)
            ip(i.ip, r.name, i.name, i.description)
            prefixes[str(ipaddress.ip_interface(i.ip).network)] = i.description
    fib = sot["fibres"]
    for lid, l in sot["links"].items():
        km = sum(fib[f]["length_km"] for f in l["fibres"])
        cables.append({"label": lid, "a": [l["a"], l["a_if"]], "b": [l["b"], l["b_if"]], "length_km": km,
                       "description": f"lambda {l['wavelength']} over {'+'.join(l['fibres'])} (SRLG)"})
    for u in ups:
        dev(u["name"], "upstream", "Internet", "FRR 10.2.1")
        iface(u["name"], u["attach_if"], f"eBGP to AS {u['core_asn']}")
        ip(u["attach_ip"], u["name"], u["attach_if"], "eBGP peering")
        at = sot["upstreams"][u["name"]]["attach"]
        cables.append({"label": f"X-{u['name']}", "a": [at["router"], at["router_if"]], "b": [u["name"], u["attach_if"]],
                       "length_km": None, "description": f"transit to AS {u['asn']}"})
    for o in acc["olts"]:
        site = rt[o["router"]].site
        dev(o["name"], "olt", site, "Linux OLT (lab)")
        iface(o["name"], o["uplink_if"], f"uplink to {o['router']}")
        ip(o["uplink_ip"], o["name"], o["uplink_if"], "uplink")
        iface(o["name"], o["pon_if"], "PON port")
        ip(o["pon_gw"], o["name"], o["pon_if"], "PON gateway")
        prefixes[o["pon_subnet"]] = f"PON of {o['name']}"
        cables.append({"label": f"A-up-{o['name']}", "a": [o["router"], o["router_if"]], "b": [o["name"], o["uplink_if"]],
                       "length_km": None, "description": "OLT uplink"})
    for sp in acc["splitters"]:
        olt = next(o for o in acc["olts"] if o["name"] == sp["olt"])
        site = rt[olt["router"]].site
        dev(sp["name"], "splitter", site, f"PLC splitter 1:{sp['ratio']}")
        iface(sp["name"], "eth1", "feeder (common port)")
        cables.append({"label": f"A-feed-{sp['name']}", "a": [olt["name"], olt["pon_if"]], "b": [sp["name"], "eth1"],
                       "length_km": sp["feeder_km"], "description": f"feeder, splitter loss {sp['loss_db']} dB"})
    for n in acc["onts"]:
        sp = next(s for s in acc["splitters"] if s["name"] == n["splitter"])
        olt = next(o for o in acc["olts"] if o["name"] == sp["olt"])
        site = rt[olt["router"]].site
        dev(n["name"], "ont", site, "Linux ONT (lab)")
        iface(n["name"], "eth1", "PON")
        ip(n["ip"], n["name"], "eth1", "customer CPE")
        iface(sp["name"], n["splitter_if"], f"drop to {n['name']}")
        cables.append({"label": f"A-drop-{n['name']}", "a": [sp["name"], n["splitter_if"]], "b": [n["name"], "eth1"],
                       "length_km": n["drop_km"], "description": "drop fibre"})
    for c in sot["customers"].values():
        prefixes[c["subnet"]] = "customer LAN"
    sites = sorted({d["site"] for d in devices})
    return {"sites": sites, "devices": devices, "interfaces": ifaces, "ips": ips,
            "prefixes": [{"prefix": p, "description": d} for p, d in sorted(prefixes.items())], "cables": cables}


# ---------------------------------------------------------------- NetBox client (REST, idempotent)
class NetBox:
    def __init__(self, url: str, token: str):
        import httpx
        self.c = httpx.Client(base_url=url.rstrip("/") + "/api", timeout=30,
                              headers={"Authorization": f"Token {token}", "Accept": "application/json"})
        self.changes: list[str] = []

    def get(self, path: str, **q) -> list[dict]:
        r = self.c.get(path, params={**q, "limit": 1000})
        r.raise_for_status()
        return r.json()["results"]

    def ensure(self, path: str, lookup: dict, fields: dict) -> dict:
        """Find by `lookup`; create if missing; patch the fields that differ. Records each change."""
        found = self.get(path, **lookup)
        if not found:
            r = self.c.post(path, json={**fields})
            if r.status_code >= 400:
                raise RuntimeError(f"POST {path} {fields}: {r.status_code} {r.text}")
            self.changes.append(f"create {path} {lookup}")
            return r.json()
        obj = found[0]
        diff = {k: v for k, v in fields.items() if _val(obj.get(k)) != v}
        if diff:
            r = self.c.patch(f"{path}{obj['id']}/", json=diff)
            if r.status_code >= 400:
                raise RuntimeError(f"PATCH {path} {diff}: {r.status_code} {r.text}")
            self.changes.append(f"update {path} {lookup} {sorted(diff)}")
            obj = r.json()
        return obj


def _val(v):
    """Compare NetBox's nested answers with the flat values we send."""
    if isinstance(v, dict):
        return v.get("value", v.get("id"))
    if isinstance(v, list):
        return [_val(x) for x in v]
    return v


def apply(nb: NetBox, want: dict) -> list[str]:
    tag = nb.ensure("/extras/tags/", {"slug": TAG}, {"name": TAG, "slug": TAG, "color": "2196f3"})
    tags = [tag["id"]]
    site = {s: nb.ensure("/dcim/sites/", {"slug": slug(s)}, {"name": s, "slug": slug(s), "status": "active"})["id"]
            for s in want["sites"]}
    roles, types = {}, {}
    for d in want["devices"]:
        if d["role"] not in roles:
            roles[d["role"]] = nb.ensure("/dcim/device-roles/", {"slug": d["role"]},
                                         {"name": d["role"], "slug": d["role"], "color": "607d8b"})["id"]
        if d["type"] not in types:
            mfr = "FRRouting" if d["type"].startswith("FRR") else "Generic"
            m = nb.ensure("/dcim/manufacturers/", {"slug": slug(mfr)}, {"name": mfr, "slug": slug(mfr)})["id"]
            types[d["type"]] = nb.ensure("/dcim/device-types/", {"slug": slug(d["type"])},
                                         {"model": d["type"], "slug": slug(d["type"]), "manufacturer": m})["id"]
    devs = {d["name"]: nb.ensure("/dcim/devices/", {"name": d["name"]},
                                 {"name": d["name"], "role": roles[d["role"]], "device_type": types[d["type"]],
                                  "site": site[d["site"]], "status": "active", "tags": tags})["id"]
            for d in want["devices"]}
    ifs = {}
    for i in want["interfaces"]:
        ifs[(i["device"], i["name"])] = nb.ensure(
            "/dcim/interfaces/", {"device_id": devs[i["device"]], "name": i["name"]},
            {"device": devs[i["device"]], "name": i["name"], "type": i["type"], "description": i["description"]})["id"]
    for p in want["prefixes"]:
        nb.ensure("/ipam/prefixes/", {"prefix": p["prefix"]},
                  {"prefix": p["prefix"], "description": p["description"], "status": "active", "tags": tags})
    for a in want["ips"]:
        nb.ensure("/ipam/ip-addresses/", {"address": a["address"]},
                  {"address": a["address"], "assigned_object_type": "dcim.interface",
                   "assigned_object_id": ifs[(a["device"], a["interface"])], "description": a["description"],
                   "status": "active", "tags": tags})
    for c in want["cables"]:
        term = lambda end: [{"object_type": "dcim.interface", "object_id": ifs[tuple(end)]}]
        fields = {"label": c["label"], "description": c["description"], "status": "connected", "tags": tags}
        if c["length_km"] is not None:
            fields |= {"length": c["length_km"], "length_unit": "km"}
        if not nb.get("/dcim/cables/", label=c["label"]):
            fields |= {"a_terminations": term(c["a"]), "b_terminations": term(c["b"])}
        nb.ensure("/dcim/cables/", {"label": c["label"]}, fields)
    return nb.changes


def check(nb: NetBox, want: dict) -> list[str]:
    """Differences between NetBox and Git (hand edits in NetBox, or a sync that did not run)."""
    drift = []
    have_dev = {d["name"]: d for d in nb.get("/dcim/devices/", tag=TAG)}
    for d in want["devices"]:
        h = have_dev.pop(d["name"], None)
        if not h:
            drift.append(f"device {d['name']} missing in NetBox")
        elif h["site"]["slug"] != slug(d["site"]):
            drift.append(f"device {d['name']}: site {h['site']['slug']} in NetBox, {slug(d['site'])} in Git")
    drift += [f"device {n} in NetBox but not in Git" for n in have_dev]
    have_ip = {a["address"]: a for a in nb.get("/ipam/ip-addresses/", tag=TAG)}
    for a in want["ips"]:
        h = have_ip.pop(a["address"], None)
        obj = (h or {}).get("assigned_object") or {}
        if not h:
            drift.append(f"address {a['address']} ({a['device']}:{a['interface']}) missing in NetBox")
        elif (obj.get("device", {}).get("name"), obj.get("name")) != (a["device"], a["interface"]):
            drift.append(f"address {a['address']}: on {obj.get('device', {}).get('name')}:{obj.get('name')} in NetBox, "
                         f"{a['device']}:{a['interface']} in Git")
    drift += [f"address {x} in NetBox but not in Git" for x in have_ip]
    have_cab = {c["label"]: c for c in nb.get("/dcim/cables/", tag=TAG)}
    for c in want["cables"]:
        h = have_cab.pop(c["label"], None)
        if not h:
            drift.append(f"cable {c['label']} missing in NetBox")
            continue
        ends = sorted(f"{t['object']['device']['name']}:{t['object']['name']}"
                      for t in h["a_terminations"] + h["b_terminations"])
        if ends != sorted([":".join(c["a"]), ":".join(c["b"])]):
            drift.append(f"cable {c['label']}: {ends} in NetBox, {c['a']}<->{c['b']} in Git")
        if c["length_km"] is not None and h.get("length") != c["length_km"]:
            drift.append(f"cable {c['label']}: length {h.get('length')} km in NetBox, {c['length_km']} in Git")
    drift += [f"cable {x} in NetBox but not in Git" for x in have_cab]
    return drift


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args()
    want = desired(model.load())
    if a.plan:
        print(json.dumps(want, indent=1))
        return
    nb = NetBox(os.getenv("NETBOX_URL", "http://localhost:8080"), os.environ["NETBOX_TOKEN"])
    if a.apply:
        ch = apply(nb, want)
        print(f"{len(ch)} change(s)")
        for c in ch:
            print(" ", c)
        return
    drift = check(nb, want)
    for d in drift:
        print("DRIFT", d)
    print(f"{len(drift)} difference(s) between NetBox and Git")
    sys.exit(1 if drift else 0)


if __name__ == "__main__":
    main()
