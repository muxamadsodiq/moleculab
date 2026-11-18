# Start MOLECULAB (API + web UI) on localhost from Windows PowerShell.
#   .\scripts\start_backend.ps1 [-Port 8765] [-Reload]
param([int]$Port = 8765, [switch]$Reload)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$HostAddr = "127.0.0.1"
$Py = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) {
    Write-Error "No virtual environment at $Root\.venv - follow LAPTOP_SETUP.md first"
    exit 1
}

# Refuse to start a second copy on the same port.
$client = New-Object System.Net.Sockets.TcpClient
try {
    $async = $client.BeginConnect($HostAddr, $Port, $null, $null)
    if ($async.AsyncWaitHandle.WaitOne(500) -and $client.Connected) {
        Write-Error "Something is already listening on http://${HostAddr}:$Port (MOLECULAB already running?)"
        exit 1
    }
} finally { $client.Close() }

if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Host "note: no .env file; using defaults (SQLite at $Root\moleculab.db, no LLM)"
}

$uvArgs = @("-m", "uvicorn", "app.main:app", "--host", $HostAddr, "--port", "$Port")
if ($Reload) { $uvArgs += "--reload" }

Set-Location $Root
Write-Host "MOLECULAB -> http://${HostAddr}:$Port  (API docs: http://${HostAddr}:$Port/docs)   Ctrl+C to stop"
# .env is read by app/config.py itself; its values are never echoed here.
& $Py @uvArgs
exit $LASTEXITCODE
