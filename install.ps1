# FiberTwin-Ops: install everything that is missing, from a fresh clone on Windows.
#
#   git clone https://github.com/azyzex/FiberTwin-Ops.git
#   cd FiberTwin-Ops
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# Safe to run again at any time: every step checks first and skips what is already there.
# Options:
#   -InstallDir D:\wsl\debian   where the Debian disk lives (default: the external SSD)
#   -LinuxUser  aziz            Linux user to create (no password is set; you set it yourself)
#   -NoImages                   skip pre-downloading the container images (~3 GB)
param(
    [string]$Distro = "Debian",
    [string]$InstallDir = "D:\wsl\debian",
    [string]$LinuxUser = "aziz",
    [switch]$NoImages
)
$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }

Step "1/5 WSL 2"
wsl.exe --status *> $null
if ($LASTEXITCODE -ne 0) {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { Write-Host "WSL is not installed. Run this script once 'as Administrator', reboot, then run it again." -ForegroundColor Yellow; exit 1 }
    wsl.exe --install --no-distribution
    Write-Host "WSL installed. Reboot Windows, then run .\install.ps1 again." -ForegroundColor Yellow
    exit 0
}
Write-Host "ok"

Step "2/5 Debian distro ($Distro)"
# 'wsl -l' prints UTF-16: read it as such (only here: Linux commands below print UTF-8)
$enc = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.Encoding]::Unicode
$list = wsl.exe -l -q
[Console]::OutputEncoding = $enc
$have = $list -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -eq $Distro }
if ($have) {
    Write-Host "already installed"
} else {
    $drive = Split-Path -Qualifier $InstallDir
    if (-not (Test-Path "$drive\")) {
        Write-Host "$drive is not there. Plug in the SSD, or pass -InstallDir C:\wsl\debian" -ForegroundColor Yellow; exit 1
    }
    New-Item -ItemType Directory -Force $InstallDir | Out-Null
    wsl.exe --install -d $Distro --location $InstallDir --no-launch
    if ($LASTEXITCODE -ne 0) { throw "wsl --install -d $Distro failed" }
}

Step "3/5 Linux packages, Docker, containerlab, Python venv, Node (inside $Distro, as root)"
# C:\a\b -> /mnt/c/a/b (where WSL mounts Windows drives)
$repo = "/mnt/" + $PSScriptRoot.Substring(0, 1).ToLower() + ($PSScriptRoot.Substring(2) -replace '\\', '/')
wsl.exe -d $Distro -u root -- bash "$repo/scripts/install.sh" $LinuxUser
if ($LASTEXITCODE -ne 0) { throw "scripts/install.sh failed (see the output above)" }

Step "4/5 Restart $Distro (turns systemd on, so Docker starts by itself)"
wsl.exe --terminate $Distro | Out-Null
Write-Host "ok"

if (-not $NoImages) {
    Step "5/5 Container images (FRR, Prometheus, Grafana, n8n, NetBox...)"
    wsl.exe -d $Distro -u root -- bash "$repo/scripts/install.sh" --images
    if ($LASTEXITCODE -ne 0) { throw "image download failed (re-run .\install.ps1, it resumes)" }
} else {
    Step "5/5 Container images: skipped (-NoImages); they download on first start"
}

Write-Host "`nDone. Next:" -ForegroundColor Green
Write-Host "  wsl -d $Distro -u root passwd $LinuxUser          # set your Linux password (once)"
Write-Host "  wsl -d $Distro                                     # then, in the repo folder:"
Write-Host "  source /opt/ft-venv/bin/activate"
Write-Host "  python -m netgen.render && sudo bash twin/deploy.sh && pytest -q tests"
Write-Host "Secrets (Slack webhook, GitHub token) and media: see 'Rebuild from zero' in README.md"
