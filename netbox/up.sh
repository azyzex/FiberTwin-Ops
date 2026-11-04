#!/usr/bin/env bash
# Start NetBox, wait until it answers, then sync the source of truth into it and check it twice:
# the second apply must change nothing (idempotent), and --check must report no drift.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose -f netbox/compose.yaml up -d
echo "waiting for NetBox (first start runs the database migrations, 1-3 min)..."
for _ in $(seq 120); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' ft-netbox-netbox-1 2>/dev/null)" = healthy ] && break
  sleep 5
done
export NETBOX_URL=${NETBOX_URL:-http://localhost:8080} NETBOX_TOKEN=${NETBOX_TOKEN:-0123456789abcdef0123456789abcdef01234567}
PY=${PY:-python}
$PY -m netgen.netbox_sync --apply
$PY -m netgen.netbox_sync --apply | tee /tmp/nb-second.txt
grep -q '^0 change' /tmp/nb-second.txt || { echo "second sync changed something: not idempotent"; exit 1; }
$PY -m netgen.netbox_sync --check
