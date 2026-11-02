# FiberTwin-Ops

FTTx-inspired network operations twin: modelled WDM optical layer, real containerised FRR core, minimal GPON-style access, GitOps + early fault detection + human-approved remediation.

> 🚧 Work in progress. The full public README is written at the end, from measured results.

## How a change reaches the network (Slice 1b ✅)
```
edit sot/network.yaml ─► pull request ─► CI: lint · validate · render · 32 unit tests (incl. capacity + SRLG what-if)
                                           └► temporary twin on the runner ─► 5 live tests ─► ✅ / ❌
            merge (human approval) ─► main (protected) ◄─ deployer on the laptop polls every 30 s
                                                          └► render ─► frr-reload only changed routers ─► live tests
```
Measured: a broken PR (overlapping subnet) was blocked in 16 s; a maintenance drain of L13 went from merge to verified on the routers in **~7 s**, hitless; its `git revert` restored the routes the same way. Decision record: [docs/decisions/0001](docs/decisions/0001-pull-based-deployer.md).

| Folder | What's inside |
|---|---|
| `sot/network.yaml` | **source of truth**: the only place the network is described |
| `netgen/` | Nornir + Jinja2 generator, SoT validation, capacity + SRLG what-if |
| `twin/` | deploy / shape / destroy the twin from generated files |
| `deployer/` | pull-based GitOps agent (systemd timer in WSL) |
| `tests/`, `tests_live/` | unit tests (32) and live tests against the running twin (5) |
| `step0/` | laptop setup + feasibility checks (WSL, Docker, containerlab, netem, BTF) |
| `labs/step0-frr3/` | 3-router FRR/OSPF smoke lab |
| `contracts/` | JSON Schemas for the data the portal reads (topology, metrics, incident events) |
| `portal/` | FiberTwin Portal (React + Vite + TypeScript) |
| `docs/decisions/` | decision records |
