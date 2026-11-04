#!/usr/bin/env bash
# Deploy the twin from the GENERATED files (run `python -m netgen.render` first). Needs root for containerlab.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
B="$ROOT/build/ft-core"
[ -f "$B/ft-core.clab.yml" ] || { echo "build/ft-core missing: run python -m netgen.render"; exit 1; }
docker image inspect fibertwin/host:0.2 >/dev/null 2>&1 || docker build -q -t fibertwin/host:0.2 "$ROOT/labs/core-1a/host"
containerlab deploy -t "$B/ft-core.clab.yml" --reconfigure
bash "$ROOT/twin/shape-links.sh"
bash "$ROOT/twin/security-sidecar.sh" || echo "suricata sidecar not started"
echo "waiting for routing to converge..."
for _ in $(seq 1 120); do
  docker exec clab-ft-core-up1 vtysh -c "show ip route bgp" 2>/dev/null | [ "$(grep -c "^B>")" -ge 2 ] && break; sleep 1
done
docker exec clab-ft-core-up1 vtysh -c "show ip route bgp" | grep "^B>" || true
