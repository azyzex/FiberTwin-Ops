#!/usr/bin/env bash
# Start the monitoring stack for the twin. Run as root (it installs the secret copy).
set -euo pipefail
cd "$(dirname "$0")"
export FT_ROOT="${FT_ROOT:-$(cd .. && pwd)}"
SRC="${SLACK_WEBHOOK_SRC:-/home/aziz/.secrets/slack-webhook}"
[ -r "$SRC" ] || { echo "no Slack webhook at $SRC"; exit 1; }
# Alertmanager runs as 'nobody' (65534): give it a copy readable by that group only (mode 0440),
# instead of loosening the original file.
mkdir -p /opt/fibertwin/secrets
install -m 0440 -o root -g 65534 "$SRC" /opt/fibertwin/secrets/slack-webhook
export SLACK_WEBHOOK_FILE=/opt/fibertwin/secrets/slack-webhook
docker compose up -d --build
# containers that were already running keep their old config: ask Prometheus to reload (lifecycle API)
for _ in $(seq 1 30); do curl -sf -X POST http://localhost:9090/-/reload >/dev/null && break; sleep 1; done
echo "Grafana http://localhost:3000   Prometheus http://localhost:9090   Alertmanager http://localhost:9093"
