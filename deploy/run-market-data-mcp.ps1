# Launcher for the market-data MCP, invoked by the Scheduled Task.
# Loads .env (API keys + outbound proxy) into the process, fixes the transport
# to http, and runs the venv Python with logs redirected. A PowerShell launcher
# (vs the old .cmd) parses .env robustly — including NO_PROXY's comma list — and
# gives the Scheduled Task a single supervisable child.

$ErrorActionPreference = "Stop"
$Root = "E:\market-data-mcp"
Set-Location $Root
New-Item -ItemType Directory -Force -Path "$Root\logs" | Out-Null

# Load .env into this process. requests/httpx/fredapi/finnhub read HTTPS_PROXY /
# NO_PROXY straight from the environment, so setting them here is enough:
# FRED/Finnhub go out via the proxy, China data hosts stay direct via NO_PROXY.
Get-Content "$Root\.env" | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    Set-Item -Path Env:$($k.Trim()) -Value $v.Trim()
}

# Fixed for the remote deployment regardless of .env.
$env:MCP_TRANSPORT = "http"

$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content "$Root\logs\server.log" "`n==== $ts launch (proxy=$($env:HTTPS_PROXY)) ===="
& "$Root\.venv\Scripts\python.exe" "$Root\server.py" *>> "$Root\logs\server.log"
