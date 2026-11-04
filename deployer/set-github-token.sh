#!/usr/bin/env bash
# Save the analysis API's GitHub token without it ever appearing on screen, in shell history or in a chat.
#   PowerShell:  wsl -d Debian -u root bash "/mnt/c/Users/AZYZ/Desktop/projects/Network Projects/FiberTwin-Ops/deployer/set-github-token.sh"
set -euo pipefail
F=/opt/fibertwin/secrets/github-token
mkdir -p /opt/fibertwin/secrets
read -rsp "Paste the token (nothing will show), then Enter: " T; echo
T=$(printf %s "$T" | tr -d '\r\n ')
case "$T" in github_pat_*) ;; *) echo "That doesn't look like a fine-grained token (github_pat_...). Nothing saved."; exit 1;; esac
umask 077; printf %s "$T" > "$F"; chmod 440 "$F"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $T" https://api.github.com/repos/azyzex/FiberTwin-Ops)
echo "saved (${#T} characters). GitHub answers HTTP $code for FiberTwin-Ops (200 = the token works)."
