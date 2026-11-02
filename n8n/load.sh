#!/usr/bin/env bash
# Load (or update) the playbook into the running n8n container and activate it.
set -euo pipefail
C=${N8N_CONTAINER:-ft-monitoring-n8n-1}
docker exec "$C" n8n import:workflow --input=/workflows/golden-path.json
docker exec "$C" n8n publish:workflow --id=ftGoldenPath0001
docker restart "$C" >/dev/null      # n8n registers webhooks of published workflows at startup
for _ in $(seq 1 60); do curl -sf localhost:5678/healthz >/dev/null && break; sleep 2; done
echo "playbook active: POST http://localhost:5678/webhook/fibertwin-alerts"
