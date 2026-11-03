"""Out-of-band change detection (fault F5): is every router running exactly what Git says?

For each FRR node, `frr-reload.py --test` compares the RUNNING config with the intended file rendered
from the source of truth (bind-mounted as /etc/frr/frr.conf) and lists what it would delete/add.
Anything listed = someone changed the router outside the pipeline.

    python twin/config_drift.py            # report (exit 1 if drift)
    python twin/config_drift.py --reapply  # put the intended config back (hitless frr-reload), then re-check
    python twin/config_drift.py --json     # machine-readable, used by the deployer
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "clab-ft-core-"


def nodes() -> list[str]:
    cfg = ROOT / "build" / "ft-core" / "configs"
    return sorted(p.name for p in cfg.iterdir() if (p / "frr.conf").exists())


CONTEXTS = ("interface ", "router ", "bfd", "profile ", "address-family ", "vrf ", "route-map ", "line ")


def leaves(lines: list[str]) -> list[str]:
    """frr-reload lists context lines, the changed line, then 'exit's. Turn that into full paths:
    ["interface eth1", "ip ospf cost 77", "exit"] -> ["interface eth1 > ip ospf cost 77"]."""
    out, stack, pending = [], [], None
    for l in lines:
        if l == "exit":
            if pending is not None:          # a context with nothing inside: the context line itself changed
                out.append(" > ".join(stack))
                pending = None
            if stack:
                stack.pop()
            continue
        if l.startswith(CONTEXTS):
            stack.append(l)
            pending = True
            continue
        pending = None
        out.append(" > ".join([*stack, l]))
    return out


def parse_reload_test(out: str) -> dict[str, list[str]]:
    """frr-reload.py --test prints a 'Lines To Delete' and a 'Lines To Add' section."""
    raw: dict[str, list[str]] = {"delete": [], "add": []}
    section = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Lines To Delete"):
            section = "delete"
            continue
        if s.startswith("Lines To Add"):
            section = "add"
            continue
        if not s or set(s) <= {"="}:
            continue
        if section:
            raw[section].append(s)
    return {k: leaves(v) for k, v in raw.items()}


# FRR display normalisation, measured on FRR 10.2 (Slice 2): the same intent written two ways, or a default
# value FRR doesn't print. These are NOT human changes. Anything else is reported as drift.
BFD_DEFAULTS = {"detect-multiplier 3", "receive-interval 300", "transmit-interval 300"}


def normalise(d: dict[str, list[str]], intended: str) -> dict[str, list[str]]:
    # kernel / host defaults FRR prints but nobody configured: IPv6 forwarding, the host's domain name
    # (seen on the laptop only: WSL gives the containers a 'localdomain' search domain)
    delete = [x for x in d["delete"] if x != "ipv6 forwarding" and not x.removeprefix("no ").startswith("domainname")]
    add = list(d["add"])
    # BFD profile values equal to FRR's defaults are not printed in the running config
    add = [x for x in add if not (x.startswith("bfd > profile ") and x.split(" > ")[-1] in BFD_DEFAULTS)]
    # "neighbor X activate" is implicit when the router keeps "bgp default ipv4-unicast" (the default)
    if "no bgp default ipv4-unicast" not in intended:
        add = [x for x in add if not (x.startswith("router bgp ") and x.endswith(" activate"))]
    # FRR 10 stores "router ospf / passive-interface lo" as interface-level config on lo
    if "router ospf > passive-interface lo" in add and "interface lo > no ip ospf passive" in delete:
        add.remove("router ospf > passive-interface lo")
        delete.remove("interface lo > no ip ospf passive")
    return {"delete": delete, "add": add}


def check(node: str) -> dict[str, list[str]]:
    r = subprocess.run(["docker", "exec", PREFIX + node, "/usr/lib/frr/frr-reload.py", "--test", "/etc/frr/frr.conf"],
                       capture_output=True, text=True)
    intended = (ROOT / "build" / "ft-core" / "configs" / node / "frr.conf").read_text()
    return normalise(parse_reload_test(r.stdout), intended)


def reapply(node: str) -> bool:
    r = subprocess.run(["docker", "exec", PREFIX + node, "/usr/lib/frr/frr-reload.py", "--reload", "/etc/frr/frr.conf"],
                       capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reapply", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    drift = {n: d for n in nodes() if (d := check(n))["delete"] or d["add"]}
    if a.reapply and drift:
        for n in drift:
            reapply(n)
        after = {n: d for n in drift if (d := check(n))["delete"] or d["add"]}
        result = {"drift": drift, "reapplied": sorted(drift), "still_drifted": after}
    else:
        result = {"drift": drift}
    if a.json:
        print(json.dumps(result))
    else:
        if not drift:
            print("in sync: every router runs exactly the config rendered from Git")
        for n, d in drift.items():
            print(f"DRIFT {n}: running config differs from Git")
            for l in d["delete"]:
                print(f"   - {l}   (on the router, not in Git)")
            for l in d["add"]:
                print(f"   + {l}   (in Git, missing on the router)")
        if a.reapply and drift:
            print("re-applied:", ", ".join(sorted(drift)), "| still drifted:", ", ".join(result["still_drifted"]) or "none")
    return 1 if (result.get("still_drifted") if a.reapply else drift) else 0


if __name__ == "__main__":
    sys.exit(main())
