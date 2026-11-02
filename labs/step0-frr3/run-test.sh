#!/usr/bin/env bash
# Step 0 smoke test for the 3-router lab. Run as root from this folder.
#   1. deploy  2. wait for OSPF  3. ping  4. netem impairment  5. link cut -> reroute  6. RAM
set -uo pipefail
cd "$(dirname "$0")"
P=clab-step0-frr3
say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
vt() { docker exec "$P-$1" vtysh -c "$2"; }

say "1. Deploy the lab"
containerlab deploy -t step0-frr3.clab.yml --reconfigure

say "2. Wait for OSPF adjacencies (each router should see 2 neighbours 'Full')"
for i in $(seq 1 60); do
  n=$(vt r1 "show ip ospf neighbor" | grep -c Full || true)
  [ "$n" -ge 2 ] && { echo "OSPF converged after ~${i}s"; break; }
  sleep 1
done
for r in r1 r2 r3; do echo "--- $r"; vt $r "show ip ospf neighbor"; done
echo "--- r1 routing table"; vt r1 "show ip route ospf"

# "Full" neighbours come first; SPF then installs the routes a moment later. Wait for the route itself.
until docker exec $P-r1 ip route get 10.0.0.3 2>/dev/null | grep -q via; do sleep 0.2; done

say "3. Ping r3's loopback from r1's loopback (crosses the network)"
docker exec $P-r1 ping -c 5 -I 10.0.0.1 10.0.0.3

say "4. netem: add 50 ms delay + 10% loss on r1 eth2 (the direct r1-r3 link)"
docker exec $P-r1 tc qdisc replace dev eth2 root netem delay 50ms loss 10%
docker exec $P-r1 tc qdisc show dev eth2
docker exec $P-r1 ping -c 20 -i 0.2 -I 10.0.0.1 10.0.0.3 | tail -n 3
docker exec $P-r1 tc qdisc del dev eth2 root
echo "(impairment removed)"

say "5. Cut the r1-r3 link: OSPF must reroute via r2"
echo "path before:"; docker exec $P-r1 ip route get 10.0.0.3
T0=$(date +%s.%N)
docker exec $P-r1 ip link set eth2 down
until docker exec $P-r1 ip route get 10.0.0.3 2>/dev/null | grep -q 10.1.12.2; do sleep 0.1; done
T1=$(date +%s.%N)
echo "path after:";  docker exec $P-r1 ip route get 10.0.0.3
printf 'reroute visible in r1 routing table after %.2f s\n' "$(echo "$T1 - $T0" | bc -l 2>/dev/null || python3 -c "print($T1-$T0)")"
docker exec $P-r1 ping -c 3 -I 10.0.0.1 10.0.0.3 | tail -n 2
docker exec $P-r1 ip link set eth2 up
echo "(link restored)"

say "6. RAM used by the 3 routers"
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' | grep -E "NAME|$P"
free -m

say "Done. Lab is still running. Destroy it with: containerlab destroy -t step0-frr3.clab.yml"
