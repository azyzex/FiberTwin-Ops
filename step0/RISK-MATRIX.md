# Step 0 risk matrix (measured 2 Nov 2026)

Laptop: Ryzen 7 4800H, 7.4 GB RAM · WSL 2 (kernel 6.18.54.1) · Debian 13 "trixie" on the external SSD (D:) · `.wslconfig`: 5 GB RAM, 8 CPUs, 4 GB swap on D:.

| Risk | Check | Result | Status |
|---|---|---|---|
| Falco can't run on the WSL kernel | `/sys/kernel/btf/vmlinux` exists | present (6.0 MB) → modern eBPF driver should work | ✅ (confirm in Slice 0) |
| No fault injection | `tc qdisc … netem delay 10ms loss 1%` on a veth | works | ✅ |
| No scaled capacities | `tc … htb rate 10mbit` | works | ✅ |
| Docker Desktop needed | native Docker Engine 29.9.2 in Debian (systemd) | `hello-world` runs | ✅ |
| containerlab doesn't work in WSL | containerlab 0.82.0 | deploys the FRR lab | ✅ |
| FRR routers too heavy | `docker stats`, 3 × FRR 10.2.1 | **~18.5–19 MiB per router** | ✅ |
| OSPF doesn't converge in containers | 3-router triangle, area 0, point-to-point | all adjacencies `Full` in 8–12 s from deploy | ✅ |
| Rerouting doesn't work | `ip link set eth2 down` on r1 | new route via r2 after **0.80–0.83 s** | ✅ |
| netem has no effect on real traffic | 50 ms + 10 % loss on r1 eth2, 20 pings | RTT 50.2–50.5 ms, 1/20 and 2/20 lost over two runs (≈ the 10 % set; small samples) | ✅ |
| RAM | `free -m` inside WSL with the lab up | ~0.7 GB used of 4.9 GB | ✅ |

**Lessons learnt**
- FRR also needs `/etc/frr/vtysh.conf`; without it every `vtysh` command prints a warning.
- OSPF neighbours reach `Full` *before* SPF installs the routes, so tests must wait for the route itself, not only for the adjacency.
- The 0.8 s reroute is fast because the link is shut locally: r1 sees its own interface go down at once. A failure in the middle of a fibre (the far end stays up) would wait for the 40 s dead interval. That's the reason to measure F2 with BFD or tuned timers in Slice 1a.

**Not yet checked:** GitHub Student Pack / Codespaces access, Falco itself (Slice 0).
