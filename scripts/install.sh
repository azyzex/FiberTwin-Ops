#!/usr/bin/env bash
# Install everything FiberTwin-Ops needs inside Debian (WSL 2 or a plain Debian/Ubuntu box). Run as root.
#   bash scripts/install.sh [linux-user]   packages, Docker, containerlab, Python venv, Node
#   bash scripts/install.sh --images       pre-download the container images (Docker must be running)
# Safe to run twice: every step checks before acting. On Windows, install.ps1 calls both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" = 0 ] || { echo "run as root (sudo bash scripts/install.sh)"; exit 1; }

if [ "${1:-}" = "--images" ]; then
  for _ in $(seq 30); do docker info >/dev/null 2>&1 && break; systemctl start docker 2>/dev/null || service docker start 2>/dev/null || true; sleep 2; done
  docker info >/dev/null 2>&1 || { echo "Docker is not running"; exit 1; }
  # every public image the lab, monitoring and NetBox stacks use (the fibertwin/* ones are built locally on first start)
  imgs=$(grep -h 'image:' "$ROOT"/labs/core-1a/core.clab.yml "$ROOT"/monitoring/compose.yaml "$ROOT"/netbox/compose.yaml \
         | awk '{print $2}' | grep -v '^fibertwin/' | sort -u)
  for img in $imgs; do
    if docker image inspect "$img" >/dev/null 2>&1; then echo "have $img"; else docker pull -q "$img"; fi
  done
  log "Images ready"
  exit 0
fi

USERNAME="${1:-aziz}"

log "Debian base, Docker Engine, containerlab"
bash "$ROOT/step0/setup-debian.sh" "$USERNAME"

log "Python venv /opt/ft-venv (requirements.txt)"
[ -x /opt/ft-venv/bin/python ] || python3 -m venv /opt/ft-venv
/opt/ft-venv/bin/pip install -q --upgrade pip
/opt/ft-venv/bin/pip install -q -r "$ROOT/requirements.txt"
chown -R "$USERNAME": /opt/ft-venv

log "Node.js + npm (the portal)"
# ignore Windows' node/npm, which WSL also puts on PATH (/mnt/c/...)
[ -x /usr/bin/node ] && [ -x /usr/bin/npm ] || apt-get install -y nodejs npm

log "Installed"
/opt/ft-venv/bin/python --version
docker --version
containerlab version 2>/dev/null | grep -i version | head -n 1 || true
echo "node $(/usr/bin/node --version)"
