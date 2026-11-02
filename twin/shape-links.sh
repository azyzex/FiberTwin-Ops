#!/usr/bin/env bash
# Capacity (htb) + fibre delay (netem) on both ends of every core link, from build/ft-core/links.tsv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
P=clab-ft-core
grep -v '^#' "$ROOT/build/ft-core/links.tsv" | while IFS=$'\t' read -r link a b km delay rate; do
  for end in "$a" "$b"; do
    node=${end%%:*}; ifc=${end##*:}
    docker exec "$P-$node" sh -c "
      tc qdisc del dev $ifc root 2>/dev/null || true
      tc qdisc add dev $ifc root handle 1: htb default 10
      tc class add dev $ifc parent 1: classid 1:10 htb rate $rate ceil $rate quantum 30000
      tc qdisc add dev $ifc parent 1:10 handle 10: netem delay ${delay}us limit 10000"
  done
  printf '%-4s %-8s <-> %-8s %4s km  %s  %s us\n' "$link" "$a" "$b" "$km" "$rate" "$delay"
done
