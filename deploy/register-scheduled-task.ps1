# Register the always-on Scheduled Task for the market-data MCP.
# Mirrors the gbrain/oura deployment. Run from an elevated PowerShell if you
# want BootTrigger+S4U; without admin, fall back to AtLogOn (see note).
#
# Playbook pitfalls baked in:
#   #1 single instance  -> MultipleInstances = IgnoreNew, and the redeploy step
#                          hard-kills the old process by PORT (Stop-ScheduledTask
#                          alone does NOT reliably kill the cmd->python grandchild).
#   #2 trust proxy      -> N/A for FastMCP/uvicorn; cloudflared stays on loopback.

$ErrorActionPreference = "Stop"
$TaskName = "market-data-mcp"
$Launcher = "E:\market-data-mcp\deploy\run-market-data-mcp.ps1"
$Port     = 8790

New-Item -ItemType Directory -Force -Path "E:\market-data-mcp\logs" | Out-Null

$action   = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Launcher`""
$trigger  = New-ScheduledTaskTrigger -AtStartup     # AtLogOn if no admin/S4U
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

# Identity = charl, S4U, highest available (same as gbrain-serve).
$principal = New-ScheduledTaskPrincipal -UserId "charl" -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force

Write-Host "Registered '$TaskName'. Start with: Start-ScheduledTask -TaskName $TaskName"

# --- Redeploy recipe (run after `git pull` + `pip install -r requirements.txt`) ---
# 1. Stop the task:           Stop-ScheduledTask -TaskName market-data-mcp
# 2. Hard-kill stale by port (pitfall #1 — kills the cmd->python grandchild):
#      Get-NetTCPConnection -LocalPort $using:Port -State Listen |
#        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
# 3. Restart:                 Start-ScheduledTask -TaskName market-data-mcp
# 4. Cold-start race: the first request after restart may return empty — retry.
