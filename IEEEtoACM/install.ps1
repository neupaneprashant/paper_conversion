$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $PSScriptRoot ".venv"
$pythonExe = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    python -m venv $venv
}

& $pythonExe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip." }

& $pythonExe -m pip install -e "$root[pdf,latex,dev]"
if ($LASTEXITCODE -ne 0) { throw "Failed to install the root paper conversion system." }

& $pythonExe -m pip install -e $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw "Failed to install the IEEEtoACM app." }

Write-Host "IEEEtoACM environment is ready."
Write-Host "Use .\run.ps1 env-check to inspect installed tools."
