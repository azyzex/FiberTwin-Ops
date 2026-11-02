#!/usr/bin/env bash
# Slice 1a checkpoint: how long is traffic interrupted when link L12 (r1-r2) fails?
# A constant UDP stream (iperf3, 1000 pkt/s) flows srv -> h1 (customer at r2); its normal path uses L12.
# outage = lost packets / 1000. (ping is not used: it slows down when replies stop, which hides long outages.)
#   A. "port down":  r1 sees its own interface go down (carrier loss) -> immediate
#   B. silent cut:   the fibre goes dark mid-span, both interfaces stay UP -> BFD must notice (3 x 300 ms)
#   C. silent cut without BFD: only OSPF hellos (dead interval 40 s) -> what BFD saves us from
set -uo pipefail
P=clab-ft-core
vt() { docker exec "$P-$1" vtysh -c "$2" >/dev/null; }
netem() { # node ifc loss%   (keeps the fibre delay)
  local d; d=$(docker exec "$P-$1" tc qdisc show dev "$2" | sed -n 's/.*delay \([0-9.]*[mu]*s\).*/\1/p' | head -1)
  docker exec "$P-$1" tc qdisc change dev "$2" parent 1:10 handle 10: netem delay "$d" loss "$3%" limit 10000
}
path() { docker exec $P-h1 tracepath -n -m 5 203.0.113.10 2>/dev/null | awk '/^ *[0-9]+:/ && $2!="no" {print $2}' | uniq | paste -sd' ' -; }

run() { # name, break-command, fix-command, seconds
  local name=$1 brk=$2 fix=$3 dur=$4
  # precondition: traffic really uses L12 again (OSPF adjacency + BFD rebuilt after the previous test)
  for _ in $(seq 1 60); do path | grep -q "10.1.12.1" && break; sleep 1; done
  path | grep -q "10.1.12.1" || { echo "── $name: SKIPPED, L12 not carrying traffic"; return; }
  sleep 2
  echo "── $name"
  echo "   path before: $(path)"
  docker exec $P-h1 sh -c "iperf3 -c 203.0.113.10 -p 5201 -R -u -b 1M -l 125 -t $dur -J > /tmp/probe.json 2>&1" &
  local pid=$!
  sleep 2; eval "$brk"; sleep $((dur - 4))
  echo "   path during: $(path)"
  wait $pid
  docker exec $P-h1 cat /tmp/probe.json | python3 "$(dirname "$0")/outage.py"
  eval "$fix"; sleep 3
}

run "A. port down on r1 (carrier loss)" \
    "docker exec $P-r1 ip link set eth1 down" \
    "docker exec $P-r1 ip link set eth1 up" 12
run "B. silent fibre cut, BFD on (300 ms x 3)" \
    "netem r1 eth1 100; netem r2 eth1 100" \
    "netem r1 eth1 0; netem r2 eth1 0" 12
echo "   (turning BFD off on L12 for scenario C)"
docker exec $P-r1 vtysh -c "conf t" -c "interface eth1" -c "no ip ospf bfd" >/dev/null
docker exec $P-r2 vtysh -c "conf t" -c "interface eth1" -c "no ip ospf bfd" >/dev/null
run "C. silent fibre cut, BFD off (OSPF dead interval 40 s)" \
    "netem r1 eth1 100; netem r2 eth1 100" \
    "netem r1 eth1 0; netem r2 eth1 0" 55
docker exec $P-r1 vtysh -c "conf t" -c "interface eth1" -c "ip ospf bfd" -c "ip ospf bfd profile core" >/dev/null
docker exec $P-r2 vtysh -c "conf t" -c "interface eth1" -c "ip ospf bfd" -c "ip ospf bfd profile core" >/dev/null
echo "(BFD restored; waiting for traffic to return to L12)"
for _ in $(seq 1 60); do path | grep -q "10.1.12.1" && break; sleep 1; done
echo "path after all tests: $(path)"
