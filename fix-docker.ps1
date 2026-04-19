# Docker Desktop Troubleshooting & Fix
# Run as: Administrator

function Write-Success { Write-Host $args -ForegroundColor Green }
function Write-Error-Custom { Write-Host $args -ForegroundColor Red }
function Write-Info { Write-Host $args -ForegroundColor Cyan }
function Write-Warning-Custom { Write-Host $args -ForegroundColor Yellow }

# Check if running as Administrator
$isAdmin = [bool]([System.Security.Principal.WindowsIdentity]::GetCurrent().Groups -match 'S-1-5-32-544')
if (-not $isAdmin) {
    Write-Error-Custom "ERROR: This script must be run as Administrator"
    exit 1
}

Write-Info "=== Docker Desktop Troubleshooting ==="
Write-Info ""

# Step 1: Check WSL2 status
Write-Info "[1/5] Checking WSL2 status..."
try {
    $wslVersion = wsl --version 2>&1
    if ($wslVersion) {
        Write-Success "[OK] WSL2 is installed:"
        Write-Info "  $wslVersion"
    }
}
catch {
    Write-Error-Custom "[FAIL] WSL2 issue detected"
}

# Step 2: Set WSL2 as default
Write-Info ""
Write-Info "[2/5] Setting WSL2 as default version..."
try {
    wsl --set-default-version 2
    Write-Success "[OK] WSL2 set as default"
}
catch {
    Write-Warning-Custom "[WARN] Could not set WSL2 default (may already be set)"
}

# Step 3: Check Hyper-V status
Write-Info ""
Write-Info "[3/5] Checking Hyper-V status..."
$hyperV = Get-WindowsOptionalFeature -Online -FeatureName "Hyper-V"
if ($hyperV.State -eq "Enabled") {
    Write-Success "[OK] Hyper-V is enabled"
}
else {
    Write-Error-Custom "[FAIL] Hyper-V is NOT enabled - enabling now..."
    Enable-WindowsOptionalFeature -Online -FeatureName "Hyper-V" -All -NoRestart
    Write-Warning-Custom "[WARN] Hyper-V enabled - RESTART REQUIRED"
}

# Step 4: Restart Docker Desktop service
Write-Info ""
Write-Info "[4/5] Restarting Docker Desktop..."
Write-Info "Stopping Docker daemon..."
Stop-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue -Force
Start-Sleep -Seconds 2

Write-Info "Stopping Docker service..."
Stop-Service -Name "docker" -ErrorAction SilentlyContinue -Force
Start-Sleep -Seconds 2

Write-Info "Starting Docker service..."
Start-Service -Name "docker" -ErrorAction SilentlyContinue

Write-Info "Starting Docker Desktop application..."
$dockerPath = "C:\Program Files\Docker\Docker\Docker.exe"
if (Test-Path $dockerPath) {
    Start-Process $dockerPath
    Write-Success "[OK] Docker Desktop started"
    Write-Info "Waiting 15 seconds for startup..."
    Start-Sleep -Seconds 15
}
else {
    Write-Error-Custom "[FAIL] Docker Desktop executable not found at $dockerPath"
}

# Step 5: Test Docker
Write-Info ""
Write-Info "[5/5] Testing Docker..."
$result = docker ps 2>&1
if ($result -and -not ($result -match "error")) {
    Write-Success "[OK] Docker is working!"
}
else {
    Write-Warning-Custom "[WARN] Docker test result: $result"
}

Write-Info ""
Write-Info "=== Troubleshooting Complete ==="
Write-Info ""
Write-Info "Next steps:"
Write-Info "  1. Try: docker run hello-world"
Write-Info "  2. If still failing, check Docker Desktop logs at:"
Write-Info "     %APPDATA%\Docker\log.txt"
Write-Info ""
Write-Warning-Custom "If issues persist, try:"
Write-Warning-Custom "  - Restart your computer"
Write-Warning-Custom "  - Uninstall and reinstall Docker Desktop"
Write-Warning-Custom "  - Check Event Viewer (Services) for Docker errors"
