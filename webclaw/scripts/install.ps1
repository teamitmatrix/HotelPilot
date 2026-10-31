# WebClaw local install for native Windows (no WSL required).
# Usage (from repo):  powershell -ExecutionPolicy Bypass -File webclaw\scripts\install.ps1
# Or from webclaw/:   powershell -ExecutionPolicy Bypass -File scripts\install.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WebclawDir = Split-Path -Parent $ScriptDir
$PmsRoot = Split-Path -Parent $WebclawDir
$ApiDir = Join-Path $WebclawDir "api"
$WebDir = Join-Path $WebclawDir "web"
$DataDir = Join-Path $PmsRoot "data"
$EngineDir = Join-Path $PmsRoot "erpclaw-engine"

Write-Host "PMS root: $PmsRoot"
Write-Host "Ensuring data directory..."
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# --- Python API venv ---
$VenvDir = Join-Path $ApiDir ".venv"
$Py = $null
foreach ($candidate in @("py", "python", "python3")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        $Py = $cmd.Source
        break
    }
}
if (-not $Py) {
    throw "Python not found. Install Python 3.11+ from https://www.python.org/downloads/ and ensure it is on PATH."
}

Write-Host "Using Python: $Py"
if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    Write-Host "Creating API virtualenv..."
    & $Py -m venv $VenvDir
}
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPy -m pip install --upgrade pip
$Req = Join-Path $ApiDir "requirements.txt"
if (Test-Path $Req) {
    & $VenvPy -m pip install -r $Req
} else {
    Write-Host "No requirements.txt found; installing common API deps..."
    & $VenvPy -m pip install fastapi uvicorn[standard] python-multipart
}

# Ensure .env uses relative paths (Windows-safe)
$EnvFile = Join-Path $ApiDir ".env"
if (-not (Test-Path $EnvFile)) {
    @"
ERPCLAW_HOME=data
DATA_DIR=data
ERPCLAW_DB_PATH=data/data.sqlite
WEBCLAW_DB_PATH=data/webclaw.sqlite
ERPCLAW_ENGINE_PATH=erpclaw-engine
WEBCLAW_ENV=development
"@ | Set-Content -Path $EnvFile -Encoding UTF8
}

# --- Frontend ---
$Npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $Npm) {
    throw "npm not found. Install Node.js 20+ from https://nodejs.org/"
}
Push-Location $WebDir
try {
    Write-Host "Installing web dependencies..."
    npm install
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Install complete."
Write-Host ""
Write-Host "Start API (from webclaw\api):"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  uvicorn main:app --reload --host 127.0.0.1 --port 8001"
Write-Host ""
Write-Host "Start web (from webclaw\web):"
Write-Host "  npm run dev"
Write-Host ""
Write-Host "Optional env (PowerShell):"
Write-Host "  `$env:PMS_ROOT = '$PmsRoot'"
Write-Host "  `$env:ERPCLAW_ENGINE_PATH = '$EngineDir'"
Write-Host "  `$env:DATA_DIR = '$DataDir'"
