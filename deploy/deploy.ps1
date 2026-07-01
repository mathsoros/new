# One-shot deployment for the market-data MCP on the Windows always-on box.
# Runs the scriptable steps (1-3, 5) in order with checkpoints, and prints the
# two manual steps (4 cloudflared ingress edit, 6 claude.ai connector) with the
# exact commands to paste.
#
# Usage (from an elevated PowerShell on the deploy box):
#   cd E:\market-data-mcp
#   .\deploy\deploy.ps1
#
# Edit the CONFIG block below first if your paths/domain/port differ.

$ErrorActionPreference = "Stop"

# ----------------------------- CONFIG -------------------------------------- #
$Root     = "E:\market-data-mcp"      # where the repo is cloned
$Domain   = "market.popcult.win"      # public hostname on the existing tunnel
$Port     = 8790
$TunnelId = "<tunnelId>"              # the EXISTING gbrain/oura tunnel id
$TaskName = "market-data-mcp"
# --------------------------------------------------------------------------- #

function Step($n, $msg) { Write-Host "`n=== STEP $n : $msg ===" -ForegroundColor Cyan }

Set-Location $Root

# --- STEP 1: venv + dependencies ------------------------------------------- #
Step 1 "Create venv and install dependencies"
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    python -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
New-Item -ItemType Directory -Force -Path "$Root\logs" | Out-Null

# --- STEP 2: .env / API keys ----------------------------------------------- #
Step 2 "Check .env (API keys)"
if (-not (Test-Path ".\.env")) {
    Copy-Item ".\.env.example" ".\.env"
    Write-Host "Created .env from template. EDIT it now: set FRED_API_KEY and FINNHUB_API_KEY," -ForegroundColor Yellow
    Write-Host "and confirm MCP_TRANSPORT=http, MCP_AUTH=oauth, PUBLIC_URL=https://$Domain" -ForegroundColor Yellow
    Read-Host "Press Enter after you have saved .env"
}
if (-not (Select-String -Path ".\.env" -Pattern "FINNHUB_API_KEY=\S" -Quiet)) {
    throw "FINNHUB_API_KEY looks empty in .env — fill it before continuing."
}

# --- STEP 3: live smoke (acceptance criteria #3/#4/#5) --------------------- #
Step 3 "Live data smoke test"
# Load .env into this process so the test sees the keys.
Get-Content ".\.env" | Where-Object { $_ -match "^\s*[^#].*=" } | ForEach-Object {
    $k, $v = $_ -split "=", 2
    [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), "Process")
}
.\.venv\Scripts\python.exe -m tests.live_smoke
if ($LASTEXITCODE -ne 0) {
    Write-Host "live_smoke reported nulls — check provider reachability/keys before going live." -ForegroundColor Yellow
    Read-Host "Press Enter to continue anyway, or Ctrl+C to stop"
}

# --- STEP 4: Cloudflare Tunnel ingress (MANUAL edit + scripted dns/restart) - #
Step 4 "Cloudflare Tunnel ingress"
Write-Host @"
MANUAL: add this route to ~/.cloudflared/config.yml (above the catch-all 404),
reusing the EXISTING tunnel that already serves gbrain/oura:

  - hostname: $Domain
    service: http://localhost:$Port

Then this script will register the DNS route and restart the tunnel task.
"@ -ForegroundColor Yellow
Read-Host "Press Enter after you've edited config.yml"
cloudflared tunnel route dns $TunnelId $Domain
Stop-ScheduledTask  -TaskName cloudflared-tunnel -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName cloudflared-tunnel

# --- STEP 5: register + start the always-on task --------------------------- #
Step 5 "Register and start the MCP Scheduled Task"
& "$Root\deploy\register-scheduled-task.ps1"
# Pitfall #1: hard-kill any stale listener on the port before (re)start.
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 4
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/mcp" -Method POST -TimeoutSec 8 -ErrorAction Stop
} catch {
    # A 401 with WWW-Authenticate is the EXPECTED healthy response when OAuth is on.
    Write-Host "Local probe returned: $($_.Exception.Message)  (401/Unauthorized here is EXPECTED with MCP_AUTH=oauth)" -ForegroundColor DarkGray
}

# --- STEP 6: register the connector in claude.ai (MANUAL) ------------------- #
Step 6 "Register connector in claude.ai + attach to the morning-report routine"
Write-Host @"
MANUAL (in the browser):
 1. claude.ai -> Settings -> Connectors -> Add custom connector
 2. URL: https://$Domain
 3. Complete the OAuth/DCR handshake. If you gate the host by Cloudflare rule
    (Anthropic-IP-only like brain.popcult.win), TEMPORARILY whitelist your
    current public IP for the handshake, then remove it.
 4. Edit the "每日金融市场晨报" routine -> enable the 'market-data-mcp' connector
    for it (keep gbrain/oura enabled too).
 5. No prompt change needed: the report's data layer is already MCP-first /
    web-search fallback.

Verify with ONE manual run of the routine:
 - numbers carry GMT+8 + source-tz timestamps
 - unavailable fields show as — (DXY, consensus), never fabricated
 - market-data + gbrain + oura all usable in the same scheduled run
"@ -ForegroundColor Green

Write-Host "`nDeploy script finished steps 1-5. Complete step 6 in the browser." -ForegroundColor Cyan
