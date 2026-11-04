# NetBox: the inventory view of the source of truth

**Git (`sot/network.yaml`) is the source of truth.** Changes there are reviewed in a PR and tested on a temporary twin. NetBox is the copy that inventory, IPAM and cabling tools read. `netgen/netbox_sync.py` keeps NetBox in line with Git, never the other way round.

| Command | What it does |
|---|---|
| `python -m netgen.netbox_sync --plan` | prints what NetBox should contain (needs no NetBox; unit-tested) |
| `python -m netgen.netbox_sync --apply` | creates or updates NetBox to match. Idempotent: a second run reports `0 change(s)` |
| `python -m netgen.netbox_sync --check` | compares NetBox with Git and exits 1 on drift (for example, someone moved an address by hand in NetBox) |
| `bash netbox/up.sh` | starts a local NetBox (`compose.yaml`, http://localhost:8080), then runs apply, apply again and check |

What gets modelled:
- 5 sites.
- 11 devices: 4 FRR core routers, the upstream router, the OLT, the 1:32 splitter and 4 ONTs.
- Their interfaces and addresses, and the prefixes.
- **12 cables.** Each core cable records the fibres its lightpath rides (`L13: lambda 2 over F12+F23 (SRLG)`) and its length. Feeder and drop cables carry their real lengths. Field teams therefore see the same shared-risk groups that the correlation engine uses.

CI (`.github/workflows/netbox.yml`) starts a throwaway NetBox on each SoT change. It syncs the SoT, checks that a second sync is a no-op, then makes a hand edit behind Git's back and checks that `--check` catches it. A final `--apply` puts NetBox back the way Git describes it.

The credentials in `compose.yaml` are lab-only throwaways. This NetBox holds no secrets and listens on localhost only.
