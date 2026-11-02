#!/usr/bin/env bash
# Demand matrix: steady UDP flows (iperf3), then per-link utilisation from the routers' interface counters.
#   srv -> h1  30 Mbit/s   (customer at r2 downloads)
#   srv -> h3  40 Mbit/s   (customer at r3 downloads)
#   h1  -> h3  20 Mbit/s   (customer to customer)
# Capacity is 100 Mbit/s per link (1:1000 scale), so these are 30 / 40 / 20 % on the links they cross.
set -uo pipefail
P=clab-ft-core
DUR=${DUR:-20}
docker exec $P-h3 sh -c "pgrep -x iperf3 >/dev/null || iperf3 -s -D -p 5204"

#        link node ifc   (tx counter read on the node's interface = traffic leaving that end)
IFS=$'\n' read -r -d '' -a ENDS <<'LIST'
L12 r1 eth1
L12 r2 eth1
L41 r1 eth2
L41 r4 eth2
L13 r1 eth3
L13 r3 eth3
L23 r2 eth2
L23 r3 eth1
L34 r3 eth2
L34 r4 eth1
LIST
txb() { docker exec "$P-$1" cat "/sys/class/net/$2/statistics/tx_bytes"; }

declare -A B0
for e in "${ENDS[@]}"; do read -r l n i <<<"$e"; B0[$n.$i]=$(txb "$n" "$i"); done
T0=$(date +%s.%N)
docker exec $P-h1 iperf3 -c 203.0.113.10 -p 5202 -R -u -b 30M -t "$DUR" >/dev/null 2>&1 &
docker exec $P-h3 iperf3 -c 203.0.113.10 -p 5203 -R -u -b 40M -t "$DUR" >/dev/null 2>&1 &
docker exec $P-h1 iperf3 -c 100.64.3.10  -p 5204    -u -b 20M -t "$DUR" >/dev/null 2>&1 &
wait
T1=$(date +%s.%N)
echo "link  direction       Mbit/s   utilisation (of 100)"
for e in "${ENDS[@]}"; do
  read -r l n i <<<"$e"
  b1=$(txb "$n" "$i")
  python3 -c "m=($b1-${B0[$n.$i]})*8/($T1-$T0)/1e6; print(f'$l   from $n ($i)  {m:7.1f}   ' + '#'*round(m/4) + f' {m:.0f}%')"
done
