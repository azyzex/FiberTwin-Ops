#!/usr/bin/env bash
# Scaled capacity + real propagation delay on every core link, both directions.
#   capacity: 100 Mbit/s here = 100 Gbit/s in the real network (1:1000)
#   delay:    fibre length x 5 us/km (one way)
# tc tree per interface:  htb (rate limit)  ->  netem (delay; later also the optical loss)
set -euo pipefail
P=clab-ft-core
RATE=${RATE:-100mbit}
#      link  node:if   node:if   km
LINKS="L12 r1:eth1 r2:eth1 140
L41 r1:eth2 r4:eth2 270
L13 r1:eth3 r3:eth3 200
L23 r2:eth2 r3:eth1 60
L34 r3:eth2 r4:eth1 120"
while read -r link a b km; do
  delay_us=$((km * 5))
  for end in "$a" "$b"; do
    node=${end%%:*}; ifc=${end##*:}
    docker exec "$P-$node" sh -c "
      tc qdisc del dev $ifc root 2>/dev/null || true
      tc qdisc add dev $ifc root handle 1: htb default 10
      tc class add dev $ifc parent 1: classid 1:10 htb rate $RATE ceil $RATE quantum 30000
      tc qdisc add dev $ifc parent 1:10 handle 10: netem delay ${delay_us}us limit 10000"
  done
  printf '%-4s %-8s <-> %-8s %4s km  %s  one-way %s us\n' "$link" "$a" "$b" "$km" "$RATE" "$delay_us"
done <<< "$LINKS"
