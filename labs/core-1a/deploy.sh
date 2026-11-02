#!/usr/bin/env bash
# One command: build the host image, deploy the core, shape the links, wait for routing.
set -euo pipefail
cd "$(dirname "$0")"
docker image inspect fibertwin/host:0.1 >/dev/null 2>&1 || docker build -q -t fibertwin/host:0.1 host
containerlab deploy -t core.clab.yml --reconfigure
bash shape-links.sh
echo "waiting for BGP to the upstream..."
for _ in $(seq 1 90); do
  docker exec clab-ft-core-up1 vtysh -c "show ip route 100.64.3.0/24" 2>/dev/null | grep -q "via" && break; sleep 1
done
docker exec clab-ft-core-h1 ping -c 2 -q 203.0.113.10 | tail -1
