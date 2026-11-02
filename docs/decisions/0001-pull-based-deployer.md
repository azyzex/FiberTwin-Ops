# 0001: Deploy with a pull-based agent, not a self-hosted GitHub runner

- **Date:** 2 Nov 2026
- **Status:** accepted

## Problem
Merged changes on `main` must reach the lab running on my laptop (WSL). How does the change get there?

## Options
1. **Self-hosted GitHub Actions runner** on the laptop: GitHub pushes jobs to it.
2. **Pull-based agent** on the laptop: it polls `main` and applies new commits (the GitOps model used by Argo CD / Flux).
3. Manual `git pull && deploy` after each merge.

## Choice
Option 2: `deployer/deployer.sh`, run every 30 s by a systemd timer, as a dedicated `deployer` user (no password, no sudo, only the docker + clab_admins groups). It renders the configs from the commit, applies only what changed (`frr-reload.py`, hitless; a full containerlab redeploy only if the topology changed), runs the live tests and records the deployed commit.

## Why
- A self-hosted runner on a public repo lets anyone who can open a PR get code executed on my machine. The pull agent only ever *reads* `main`, which is protected (PR + both CI checks required, admins included).
- The laptop needs no inbound access and no GitHub credentials (public repo, HTTPS clone).
- Rollback is `git revert` through the same path, so there is one way to change the network, not two.

## Measured (2 Nov 2026)
Merge → change applied on the routers: **~2 s**; merge → live tests passed: **~7 s** (drain of L13 and its revert, hitless reload of r1 and r3 only).

## Known deviations from the plan
- The plan said "WSL automount and interop off" for the deployer. Not done: my own tooling uses `/mnt/c`. Mitigation: the deployer works only on its own clone in `/opt/fibertwin` and its systemd unit makes the rest of the system read-only to it (`ProtectSystem=full`). Revisit if the deployer moves to a dedicated distro.
- No PR approval count is required: GitHub doesn't let the PR author approve their own PR, and I'm the only maintainer. The human approval step is the merge itself, after green checks.
- WSL stops when idle, so the deployer only runs while WSL is up.

## What would change my mind
A second maintainer (then require 1 approving review), or the twin moving to a server (then a dedicated distro/VM for the agent).
