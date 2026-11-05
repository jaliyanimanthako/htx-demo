<# : run_windows.cmd - the venue demo on this laptop, Windows side. Double-click it.
@echo off
rem A batch header, then a PowerShell script: cmd runs the lines up to "exit /b", and
rem PowerShell reads them as a comment and runs the rest. No execution policy gets in the way.
set "RUN_WINDOWS=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create([IO.File]::ReadAllText($env:RUN_WINDOWS)))"
exit /b %errorlevel%
#>
# Opens the laptop to the venue network and starts MediaMTX (run_demo/running.md, steps 2
# and 4), then prints the addresses for DJI Mimo and the tablet. It asks for administrator
# rights itself. Safe to run again; run it again after every reboot, because WSL's internal
# address changes. The WSL side (Qwen, backend, web pages) is run_demo/run_wsl.sh.

$MediaMtxDir = 'C:\mediamtx'                  # where MediaMTX for Windows is unzipped

function Exit-AfterEnter($code) { Read-Host "`nPress Enter to close this window"; exit $code }

$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # port forwards and firewall rules need it: start this file again as administrator (UAC prompt)
    try { Start-Process -FilePath $env:RUN_WINDOWS -Verb RunAs }
    catch { Write-Host 'Not started: it needs administrator rights.' -ForegroundColor Red; Exit-AfterEnter 1 }
    exit 0
}

# This file's WSL distro and folder (\\wsl.localhost\<distro>\home\...); else WSL's default distro
$wslArgs = @()
$wslScript = '~/htx-demo/run_demo/run_wsl.sh'
if ($env:RUN_WINDOWS -match '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)(\\.*)\\[^\\]+$') {
    $wslArgs = @('-d', $Matches[1])
    $wslScript = ($Matches[2] -replace '\\', '/') + '/run_wsl.sh'
}
$wsl = ((& wsl.exe @wslArgs hostname -I) -join ' ').Trim().Split(' ')[0]   # changes after a reboot
if ($wsl -notmatch '^\d+\.\d+\.\d+\.\d+$') {
    Write-Host "Can't get WSL's IP address (got '$wsl'). Is WSL installed?" -ForegroundColor Red
    Exit-AfterEnter 1
}

function Open-WslPort($port, $name) {         # forward a port to WSL, let the local subnet in
    netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$port 2>&1 | Out-Null
    netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$port connectaddress=$wsl connectport=$port | Out-Null
    if (-not (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port | Out-Null
    }
    Set-NetFirewallRule -DisplayName $name -Profile Private,Public -RemoteAddress LocalSubnet
}

Write-Host "`nNetwork" -ForegroundColor Cyan
Open-WslPort 8100 'htx backend 8100'
Open-WslPort 5174 'htx web page 5174'
if (-not (Get-NetFirewallRule -DisplayName 'MediaMTX' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'MediaMTX' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 1935,8554 | Out-Null
}
Write-Host "  ports 8100 (backend) and 5174 (tablet web page) forwarded to WSL ($wsl)"
Write-Host '  firewall open to the local network on 8100 and 5174, and on 1935 + 8554 (MediaMTX)'

Write-Host "`nMediaMTX" -ForegroundColor Cyan
$mtx = Join-Path $MediaMtxDir 'mediamtx.exe'
if (Get-Process mediamtx -ErrorAction SilentlyContinue) {
    Write-Host '  already running: its window shows the log'
} elseif (Test-Path $mtx) {
    Start-Process -FilePath $mtx -WorkingDirectory $MediaMtxDir
    Write-Host '  started in its own window: keep it open, its log shows the camera'
} else {
    Write-Host "  not found: $mtx (unzip MediaMTX for Windows there, or edit `$MediaMtxDir)" -ForegroundColor Red
}
$listening = $false
for ($i = 0; $i -lt 10 -and -not $listening; $i++) {
    $listening = [bool](Get-NetTCPConnection -State Listen -LocalPort 1935 -ErrorAction SilentlyContinue)
    if (-not $listening) { Start-Sleep -Milliseconds 500 }
}
if ($listening) { Write-Host '  listening for the camera on port 1935' }
else { Write-Host '  nothing listens on port 1935: check the MediaMTX window' -ForegroundColor Red }

Write-Host "`nBackend" -ForegroundColor Cyan
try {
    Invoke-WebRequest -Uri 'http://localhost:8100/api/health' -UseBasicParsing -TimeoutSec 3 | Out-Null
    Write-Host '  answering on port 8100'
} catch {
    Write-Host "  not answering yet: in WSL, run  bash $wslScript" -ForegroundColor Yellow
}

Write-Host "`nFor Mimo and the tablet" -ForegroundColor Cyan
$ip = (Get-NetIPAddress -InterfaceAlias 'Wi-Fi' -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1).IPAddress
if ($ip) {
    Write-Host "  Laptop Wi-Fi IP       : $ip"
    Write-Host "  DJI Mimo RTMP address : rtmp://${ip}:1935/live/x2"
    Write-Host "  Tablet app backend URL: http://${ip}:8100"
    Write-Host "  Tablet browser check  : http://${ip}:8100/api/health"
    Write-Host "  Tablet web page       : http://${ip}:5174"
} else {
    Write-Host '  No Wi-Fi IP: join the venue Wi-Fi, then run this again.' -ForegroundColor Red
}
Write-Host "`nToken and the full status: in WSL, bash $wslScript check"
Exit-AfterEnter 0
