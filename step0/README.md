# Step 0: laptop setup ✅ (2 Nov 2026)

## Done ✅
| What | Result |
|---|---|
| External SSD | **D:** · NTFS · 238 GB (120 GB free) |
| `.wslconfig` | WSL gets **5 GB RAM**, 8 CPUs, 4 GB swap **on D:** · old file saved as `.wslconfig.bak-2026-11-01` |
| Debian 13 | installed on `D:\wsl\debian`, systemd on, user `aziz` (sudo, docker, clab_admins) |
| Docker Engine + containerlab | 29.9.2 + 0.82.0 |
| Checks + FRR lab | all ✅, measured numbers in [RISK-MATRIX.md](RISK-MATRIX.md) |

## Still yours
Set your Linux password (never typed by Claude):
```powershell
wsl -d Debian -u root passwd aziz
```
Then `wsl -d Debian` opens a shell as `aziz`.

## Re-run everything from scratch
```bash
sudo bash step0/setup-debian.sh aziz   # tools
sudo bash step0/checks.sh              # PASS/FAIL per risk
cd labs/step0-frr3 && sudo bash run-test.sh
```
