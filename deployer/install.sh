#!/usr/bin/env bash
# Install the deployer in WSL (run as root once). Creates a dedicated 'deployer' user that can use
# docker/containerlab but has no sudo and no password, clones main, enables the 30 s timer.
set -euo pipefail
id deployer >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin deployer
usermod -aG docker deployer
getent group clab_admins >/dev/null && usermod -aG clab_admins deployer
mkdir -p /opt/fibertwin && chown deployer:deployer /opt/fibertwin
[ -d /opt/fibertwin/repo/.git ] || sudo -u deployer git clone -q https://github.com/azyzex/FiberTwin-Ops.git /opt/fibertwin/repo
[ -x /opt/ft-venv/bin/python ] || { python3 -m venv /opt/ft-venv && /opt/ft-venv/bin/pip install -q -r /opt/fibertwin/repo/requirements.txt; }
install -m 0644 /opt/fibertwin/repo/deployer/fibertwin-deployer.service /etc/systemd/system/
install -m 0644 /opt/fibertwin/repo/deployer/fibertwin-deployer.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fibertwin-deployer.timer
systemctl list-timers fibertwin-deployer.timer --no-pager
