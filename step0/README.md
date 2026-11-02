# Step 0: laptop setup (status 1 Nov 2026)

## Done ✅
| What | Result |
|---|---|
| External SSD | **D:** · NTFS · 238 GB (120 GB free) |
| `.wslconfig` | WSL gets **5 GB RAM**, 8 CPUs, 4 GB swap **on D:** · old file saved as `.wslconfig.bak-2026-11-01` |
| Debian install | started on `D:\wsl\debian`, **Windows asked for a restart** to switch on a feature WSL needs |

## Your part (5 minutes) 👇
1. **Restart Windows.**
2. Open PowerShell and run:
   ```powershell
   wsl --install Debian --location D:\wsl\debian --no-launch
   ```
3. Tell Claude "restarted". Claude then runs:
   - `setup-debian.sh` → updates Debian, creates your Linux user **without a password**, installs Docker Engine + containerlab
   - `checks.sh` → PASS/FAIL for each risk: Falco BTF · netem · htb · Docker · containerlab
   - `../labs/step0-frr3/run-test.sh` → 3 FRR routers running OSPF, ping, a netem impairment, a link cut with the reroute time, RAM per router
4. Set your Linux password yourself (Claude never types it):
   ```powershell
   wsl -d Debian -u root passwd aziz
   ```

## What each script proves
```
setup-debian.sh   → "the tools install on this laptop"
checks.sh         → "the kernel can do what the plan needs" (BTF for Falco, netem/htb for faults + capacities)
run-test.sh       → "real routers run, talk OSPF, reroute, and fit in RAM"
```
The results go into `step0/RISK-MATRIX.md` (written from the real output).
