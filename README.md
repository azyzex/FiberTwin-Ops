# FiberTwin-Ops

FTTx-inspired network operations twin: modelled WDM optical layer, real containerised FRR core, minimal GPON-style access, GitOps + early fault detection + human-approved remediation.

> 🚧 Work in progress. The full public README is written at the end, from measured results.

| Folder | What's inside |
|---|---|
| `step0/` | laptop setup + feasibility checks (WSL, Docker, containerlab, netem, BTF) |
| `labs/step0-frr3/` | 3-router FRR/OSPF smoke lab |
| `contracts/` | JSON Schemas for the data the portal reads (topology, metrics, incident events) |
| `portal/` | FiberTwin Portal (React + Vite + TypeScript) |
| `docs/decisions/` | decision records |
