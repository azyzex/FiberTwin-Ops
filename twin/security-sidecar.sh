#!/usr/bin/env bash
# (Re)start Suricata inside the OLT's network namespace, watching the PON port. Called by twin/deploy.sh:
# a sidecar shares its target's namespace, so it must be recreated whenever the OLT container is
# (the deployer reconciles it; a Docker restart policy can't, the namespace it points to is gone).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OLT=${OLT:-clab-ft-core-olt1}
LOGDIR=${FT_SURICATA_LOG:-/opt/fibertwin/suricata}
docker inspect "$OLT" >/dev/null 2>&1 || { echo "no $OLT: access network not deployed"; exit 0; }
mkdir -p "$LOGDIR"; chmod 755 "$LOGDIR" 2>/dev/null || true   # the deployer (not root) may not own it: fine if it exists
docker rm -f ft-suricata >/dev/null 2>&1 || true
docker run -d --name ft-suricata --network "container:$OLT" \
  --cap-add NET_ADMIN --cap-add NET_RAW --cap-add SYS_NICE --memory 300m \
  -v "$ROOT/security/suricata:/fibertwin-rules:ro" -v "$LOGDIR:/var/log/suricata" \
  jasonish/suricata:8.0.7 -i eth2 -S /fibertwin-rules/fibertwin.rules -k none \
  --set "vars.address-groups.HOME_NET=[100.64.10.0/24]" --set "outputs.1.eve-log.types=[alert]" >/dev/null
echo "suricata watching $OLT:eth2 -> $LOGDIR/eve.json"
