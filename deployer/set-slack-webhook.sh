#!/usr/bin/env bash
# Save the Slack webhook that Alertmanager posts to, without it showing on screen, in shell history or in a chat.
#   wsl -d Debian bash deployer/set-slack-webhook.sh      (as your normal user; monitoring/up.sh reads it from here)
set -euo pipefail
F="$HOME/.secrets/slack-webhook"
mkdir -p "$HOME/.secrets" && chmod 700 "$HOME/.secrets"
read -rsp "Paste the Slack webhook URL (nothing will show), then Enter: " U; echo
U=$(printf %s "$U" | tr -d '\r\n ')
case "$U" in https://hooks.slack.com/*) ;; *) echo "That doesn't look like a Slack webhook (https://hooks.slack.com/...). Nothing saved."; exit 1;; esac
umask 077; printf %s "$U" > "$F"
echo "saved to $F (${#U} characters)."
