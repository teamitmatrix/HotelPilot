# Dependency check for native Windows (companion to check_deps.sh).
# Usage: powershell -ExecutionPolicy Bypass -File webclaw\scripts\check_deps.ps1

$ErrorActionPreference = "Continue"
$ok = $true

function Test-Cmd($Name) {
    $c = Get-Command $Name -ErrorAction SilentlyContinue
    if ($c) {
        Write-Host "[ok] $Name -> $($c.Source)"
        return $true
    }
    Write-Host "[missing] $Name"
    return $false
}

Write-Host "Checking Windows-native PMS dependencies..."
if (-not (Test-Cmd "py") -and -not (Test-Cmd "python") -and -not (Test-Cmd "python3")) {
    Write-Host "Python 3.11+ is required."
    $ok = $false
}
if (-not (Test-Cmd "node")) { $ok = $false }
if (-not (Test-Cmd "npm")) { $ok = $false }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PmsRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
foreach ($rel in @("webclaw\api", "webclaw\web", "erpclaw-engine", "hospitalityclaw", "data")) {
    $p = Join-Path $PmsRoot $rel
    if (Test-Path $p) {
        Write-Host "[ok] $rel"
    } else {
        Write-Host "[missing] $rel ($p)"
        $ok = $false
    }
}

if ($ok) {
    Write-Host "All checks passed."
    exit 0
}
Write-Host "One or more checks failed."
exit 1
