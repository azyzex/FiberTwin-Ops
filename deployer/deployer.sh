#!/usr/bin/env bash
# Pull-based GitOps deployer: the lab PULLS the desired state from main. GitHub never runs code here.
#
# One run (called every 30 s by a systemd timer):
#   1. git fetch main from GitHub (public, read-only, no credentials on this machine)
#   2. nothing new -> exit
#   3. render configs from the source of truth at that commit
#   4. topology changed (nodes/links) -> containerlab redeploy  (disruptive, logged as such)
#      only router configs changed     -> frr-reload on those routers (hitless: applies the diff)
#   5. run the live tests; record the result
#   6. remember the deployed commit
# Rollback = `git revert` on main: the deployer applies the revert like any other commit.
set -uo pipefail

BASE=${FT_BASE:-/opt/fibertwin}
REPO="$BASE/repo"
STATE="$BASE/state"
VENV=${FT_VENV:-/opt/ft-venv}
URL=${FT_REPO_URL:-https://github.com/azyzex/FiberTwin-Ops.git}
P=clab-ft-core
LOG="$STATE/deploy.log"
mkdir -p "$STATE"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }

exec 9>"$STATE/lock"
flock -n 9 || { echo "another deploy is running"; exit 0; }

[ -d "$REPO/.git" ] || git clone -q --branch main "$URL" "$REPO"
cd "$REPO"
git fetch -q origin main || { log "ERROR fetch failed (offline?)"; exit 1; }
TARGET=$(git rev-parse origin/main)
CURRENT=$(cat "$STATE/deployed_sha" 2>/dev/null || echo none)

# Drift check (reconcile actual vs desired, not only new commits): after a WSL/Docker restart the
# containers come back WITHOUT their containerlab links. If any link interface is missing, redeploy.
drifted() {
  [ -f "$REPO/build/ft-core/links.tsv" ] || return 1
  grep -v '^#' "$REPO/build/ft-core/links.tsv" | while IFS=$'\t' read -r link a b _; do
    for end in "$a" "$b"; do
      docker exec "$P-${end%%:*}" ip link show "${end##*:}" >/dev/null 2>&1 || { echo "$link:$end"; return; }
    done
  done
}
if [ "$TARGET" = "$CURRENT" ]; then
  missing=$(drifted)
  if [ -z "$missing" ]; then
    # F5: out-of-band config changes (someone edited a router by hand). Git is the truth: put it back.
    cfg=$("$VENV/bin/python" twin/config_drift.py --json 2>/dev/null || true)
    if [ -n "$cfg" ] && [ "$(echo "$cfg" | jq -r '.drift | length')" != "0" ]; then
      sig=$(echo "$cfg" | jq -cS '.drift' | sha256sum | cut -c1-16)
      if [ "$sig" = "$(cat "$STATE/drift_sig" 2>/dev/null)" ]; then
        exit 0   # same drift already handled: a re-apply didn't remove it, a human was told once. No loop.
      fi
      echo "$sig" > "$STATE/drift_sig"
      log "DRIFT out-of-band config change: $(echo "$cfg" | jq -c '.drift')"
      out=$("$VENV/bin/python" twin/config_drift.py --json --reapply 2>/dev/null || true)
      still=$(echo "$out" | jq -c '.still_drifted')
      log "APPLY re-applied Git config on $(echo "$out" | jq -r '.reapplied | join(",")'); still drifted: $still"
      if [ "$still" = "{}" ]; then
        rm -f "$STATE/drift_sig"; msg=":warning: Out-of-band change on $(echo "$cfg" | jq -r '.drift | keys | join(",")') reverted to the Git config."
      else
        msg=":rotating_light: Out-of-band change that re-applying Git did NOT remove: $still. Not retrying; human needed."
      fi
      curl -s -m 5 -X POST localhost:8000/notify -H 'Content-Type: application/json' -d "$(jq -n --arg t "$msg" '{text: $t}')" >/dev/null || true
    fi
    exit 0
  fi
  log "DRIFT running twin differs from main (missing $missing) -> redeploy"
  if bash twin/deploy.sh >>"$LOG" 2>&1 && "$VENV/bin/python" -m pytest -q tests_live -p no:cacheprovider >>"$LOG" 2>&1; then
    log "DONE drift repaired at $TARGET"
  else
    log "ERROR drift repair failed"
  fi
  exit 0
fi

log "NEW main $TARGET (deployed: $CURRENT): $(git log -1 --format=%s "$TARGET")"
OLD_BUILD="$STATE/previous-build"
rm -rf "$OLD_BUILD"; [ -d build/ft-core ] && cp -r build/ft-core "$OLD_BUILD"
git checkout -q --detach "$TARGET"
if ! "$VENV/bin/python" -m netgen.render >/dev/null; then
  log "ERROR render failed at $TARGET: nothing applied"; exit 1
fi
B=build/ft-core

running=$(docker ps --format '{{.Names}}' | grep -c "^$P-" || true)
if [ "$running" -eq 0 ] || ! cmp -s "$B/ft-core.clab.yml" "$OLD_BUILD/ft-core.clab.yml" 2>/dev/null; then
  log "APPLY topology changed or twin not running -> containerlab deploy (disruptive)"
  bash twin/deploy.sh >>"$LOG" 2>&1 || { log "ERROR deploy failed"; exit 1; }
else
  changed=0
  for d in "$B"/configs/*/; do
    node=$(basename "$d")
    if ! cmp -s "$d/frr.conf" "$OLD_BUILD/configs/$node/frr.conf"; then
      changed=$((changed + 1))
      # the file is bind-mounted into the container, so it already holds the new text
      if docker exec "$P-$node" /usr/lib/frr/frr-reload.py --reload /etc/frr/frr.conf >>"$LOG" 2>&1; then
        log "APPLY $node: frr-reload ok (hitless)"
      else
        log "ERROR $node: frr-reload failed"
      fi
    fi
  done
  cmp -s "$B/links.tsv" "$OLD_BUILD/links.tsv" || { bash twin/shape-links.sh >>"$LOG" 2>&1; log "APPLY link shaping updated"; }
  [ "$changed" -eq 0 ] && log "APPLY no router config change in this commit"
fi

if "$VENV/bin/python" -m pytest -q tests_live -p no:cacheprovider >>"$LOG" 2>&1; then
  log "VERIFY live tests passed"
  echo "$TARGET" > "$STATE/deployed_sha"
  log "DONE deployed $TARGET"
else
  log "VERIFY FAILED live tests at $TARGET: fix forward or git revert on main (1e automates the revert PR)"
  echo "$TARGET" > "$STATE/deployed_sha"   # don't retry the same broken commit in a loop
  exit 1
fi
