# Local mode: Redis in Docker, Scanner + GUI run locally
# Usage: .\run_local.ps1 [-Interval 60]

param(
    [int]$Interval = 60
)

Write-Host "=== Intraday Monitor (Local Mode) ===" -ForegroundColor Cyan

# Find virtual environment
$python = $null
foreach ($venvDir in @("venv", ".venv")) {
    $candidate = Join-Path $PSScriptRoot "$venvDir\Scripts\python.exe"
    if (Test-Path $candidate) { $python = $candidate; break }
}
if (-not $python) {
    Write-Host "ERROR: venv not found (tried: venv, .venv)" -ForegroundColor Red
    exit 1
}
Write-Host "Python: $python" -ForegroundColor DarkGray

# Start Docker services
Write-Host "`n[1/3] Starting Docker services (Redis, InfluxDB, Grafana)..." -ForegroundColor Yellow
docker-compose up -d redis influxdb grafana
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to start Docker services. Is Docker Desktop running?" -ForegroundColor Red
    exit 1
}

# Wait for Redis
Write-Host "[2/3] Waiting for Redis..." -ForegroundColor Yellow
$ready = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    $ping = docker exec tracker_redis redis-cli ping 2>$null
    if ($ping -eq "PONG") { $ready = $true; break }
    Write-Host "  [$i] waiting..." -ForegroundColor DarkGray
}
if ($ready) {
    Write-Host "  Redis OK  |  InfluxDB: http://localhost:8086  |  Grafana: http://localhost:3001" -ForegroundColor Green
} else {
    Write-Host "  WARNING: Redis did not respond PONG, continuing anyway..." -ForegroundColor DarkYellow
}

# Start background scripts directly as Python processes (so stored PID = Python PID)
# stdout/stderr redirected to *_stderr.log — without this, any uncaught Python
# exception (Traceback) or asyncio warning vanishes silently: logging module only
# captures what the script explicitly logs, not unhandled crashes. This was the
# reason repeated silent scanner deaths (2026-10-12/13) couldn't be diagnosed.
$logDir = Join-Path $PSScriptRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$today = Get-Date -Format "yyyyMMdd"

Write-Host "[3/3] Starting Scanner + TAIFEX Fetcher + Morning Runner + GUI..." -ForegroundColor Yellow
$pScanner = Start-Process $python -ArgumentList "monitor/scanner.py", "--interval", $Interval `
    -WorkingDirectory $PSScriptRoot -PassThru `
    -RedirectStandardOutput (Join-Path $logDir "scanner_stdout_$today.log") `
    -RedirectStandardError  (Join-Path $logDir "scanner_stderr_$today.log")

Start-Sleep -Seconds 1

$pTaifex = Start-Process $python -ArgumentList "monitor/taifex_fetcher.py", "--interval", "30" `
    -WorkingDirectory $PSScriptRoot -PassThru `
    -RedirectStandardOutput (Join-Path $logDir "taifex_stdout_$today.log") `
    -RedirectStandardError  (Join-Path $logDir "taifex_stderr_$today.log")

Start-Sleep -Seconds 1

$pMorning = Start-Process $python -ArgumentList "monitor/morning_runner.py", "--interval", "30" `
    -WorkingDirectory $PSScriptRoot -PassThru `
    -RedirectStandardOutput (Join-Path $logDir "morning_stdout_$today.log") `
    -RedirectStandardError  (Join-Path $logDir "morning_stderr_$today.log")

Start-Sleep -Seconds 1

# Start GUI in current window (blocking — close GUI to end session)
Write-Host "Starting GUI  (close the GUI window to end session)`n" -ForegroundColor Cyan
try {
    & $python monitor/gui.py --interval $Interval --redis-host 127.0.0.1
} finally {
    # Runs whether GUI closed normally, crashed, or Ctrl+C
    Write-Host "`nGUI closed. Stopping background processes..." -ForegroundColor Yellow
    foreach ($p in @($pScanner, $pTaifex, $pMorning)) {
        if ($p -and -not $p.HasExited) {
            taskkill /PID $p.Id /T /F 2>$null | Out-Null
            Write-Host "  Killed PID $($p.Id)" -ForegroundColor DarkGray
        }
    }
    Write-Host "All processes stopped." -ForegroundColor Green
    Write-Host "Stop Redis:  docker-compose stop redis" -ForegroundColor DarkGray
}
