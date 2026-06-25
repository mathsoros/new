@echo off
REM Launcher for the market-data MCP, run by the Scheduled Task.
REM Pattern matches gbrain/oura: a .cmd wrapper sets cwd + redirects logs so the
REM Scheduled Task has a single, supervisable child. Playbook pitfall #1: keep
REM ONE instance; the task sets MultipleInstances=IgnoreNew and we hard-kill by
REM port before redeploy (see register-scheduled-task.ps1 comments).

setlocal
cd /d E:\market-data-mcp

REM Load env (keys, transport). Keep .env out of git; this reads it line by line.
if exist .env (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
  )
)

set MCP_TRANSPORT=http
set HOST=127.0.0.1
set PORT=8787

REM Use the venv interpreter.
E:\market-data-mcp\.venv\Scripts\python.exe E:\market-data-mcp\server.py >> E:\market-data-mcp\logs\server.log 2>&1
