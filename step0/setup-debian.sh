#!/usr/bin/env bash
# Step 0: prepare the Debian WSL distro for the FiberTwin-Ops lab.
# Run as root:  wsl -d Debian -u root -- bash /mnt/c/.../setup-debian.sh <linux-username>
# Safe to run twice (every step checks before acting).
set -euo pipefail

USERNAME="${1:-aziz}"
log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

log "1/6 System update + base tools"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg git jq iproute2 iputils-ping \
  tcpdump procps python3 python3-venv python3-pip sudo less nano

log "2/6 Linux user '$USERNAME' (no password set: you set it yourself later)"
if ! id "$USERNAME" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$USERNAME"
fi
usermod -aG sudo "$USERNAME"

log "3/6 /etc/wsl.conf: systemd on, default user = $USERNAME"
cat >/etc/wsl.conf <<EOF
[boot]
systemd=true

[user]
default=$USERNAME
EOF

log "4/6 Docker Engine (official Docker repo, no Docker Desktop)"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $CODENAME stable" \
    >/etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
usermod -aG docker "$USERNAME"

log "5/6 containerlab"
if ! command -v containerlab >/dev/null 2>&1; then
  bash -c "$(curl -sL https://get.containerlab.dev)"
fi
if getent group clab_admins >/dev/null; then usermod -aG clab_admins "$USERNAME"; fi

log "6/6 Versions"
. /etc/os-release && echo "Debian: $PRETTY_NAME"
docker --version || true
containerlab version 2>/dev/null | head -n 8 || true

log "Done. Now run from Windows:  wsl --terminate Debian   (applies systemd + default user)"
