# Enable Virtualization and Install Docker on Windows
# Requires: Windows 11 Pro (or higher)
# Run as: Administrator

# Color output helpers
function Write-Success { Write-Host $args -ForegroundColor Green }
function Write-Error-Custom { Write-Host $args -ForegroundColor Red }
function Write-Info { Write-Host $args -ForegroundColor Cyan }

# Check if running as Administrator
$isAdmin = [bool]([System.Security.Principal.WindowsIdentity]::GetCurrent().Groups -match 'S-1-5-32-544')
if (-not $isAdmin) {
    Write-Error-Custom "ERROR: This script must be run as Administrator"
    Write-Info "Please right-click PowerShell and select 'Run as Administrator'"
    exit 1
}

Write-Info "=== Docker & Virtualization Setup for Windows 11 Pro ==="
Write-Info ""

# Step 1: Check and Enable Hyper-V
Write-Info "[1/4] Checking Hyper-V status..."
$hyperV = Get-WindowsOptionalFeature -Online -FeatureName "Hyper-V" -ErrorAction SilentlyContinue

if ($hyperV.State -eq "Enabled") {
    Write-Success "✓ Hyper-V is already enabled"
} else {
    Write-Info "Enabling Hyper-V (this may require a restart)..."
    Enable-WindowsOptionalFeature -Online -FeatureName "Hyper-V" -All -NoRestart
    Write-Success "✓ Hyper-V enabled"
}

# Step 2: Check and Install WSL2 (Windows Subsystem for Linux)
Write-Info ""
Write-Info "[2/4] Setting up WSL2..."
$wslCheck = wsl --list --verbose 2>&1
if ($wslCheck -match "default") {
    Write-Success "✓ WSL2 is already installed"
} else {
    Write-Info "Installing WSL2 (this downloads ~200MB)..."
    wsl --install --no-distribution
    Write-Success "✓ WSL2 installed"
}

# Step 3: Enable Virtualization-related features
Write-Info ""
Write-Info "[3/4] Enabling additional virtualization features..."
$features = @(
    "VirtualMachinePlatform",
    "Containers"
)

foreach ($feature in $features) {
    $check = Get-WindowsOptionalFeature -Online -FeatureName $feature -ErrorAction SilentlyContinue
    if ($check.State -eq "Enabled") {
        Write-Success "  ✓ $feature already enabled"
    } else {
        Write-Info "  Enabling $feature..."
        Enable-WindowsOptionalFeature -Online -FeatureName $feature -NoRestart
        Write-Success "  ✓ $feature enabled"
    }
}

# Step 4: Install Docker Desktop
Write-Info ""
Write-Info "[4/4] Installing Docker Desktop..."

# Check if Docker is already installed
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Success "✓ Docker is already installed"
    docker --version
} else {
    Write-Info "Downloading Docker Desktop installer..."
    
    # Download Docker Desktop
    $dockerUrl = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
    $installerPath = "$env:TEMP\Docker Desktop Installer.exe"
    
    try {
        # Use BitsTransfer for reliable download
        if (Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue) {
            Start-BitsTransfer -Source $dockerUrl -Destination $installerPath -ErrorAction Stop
        } else {
            # Fallback to Invoke-WebRequest
            Invoke-WebRequest -Uri $dockerUrl -OutFile $installerPath -ErrorAction Stop
        }
        
        Write-Info "Running Docker Desktop installer..."
        Start-Process -FilePath $installerPath -Wait -ArgumentList "install --quiet"
        
        Write-Success "✓ Docker Desktop installed"
        Remove-Item $installerPath -ErrorAction SilentlyContinue
    } catch {
        Write-Error-Custom "ERROR: Failed to download/install Docker: $_"
        Write-Info "You can manually download from: https://www.docker.com/products/docker-desktop"
        exit 1
    }
}

# Summary
Write-Info ""
Write-Info "=== Setup Complete ==="
Write-Success ""
Write-Success "Required actions:"
Write-Info "  1. RESTART your computer (changes to system features require restart)"
Write-Info "  2. After restart, Docker Desktop will start automatically"
Write-Info "  3. Open PowerShell and run: docker --version"
Write-Info ""
Write-Info "Optional - Run this to verify everything after restart:"
Write-Info "  docker run hello-world"
Write-Info ""

$response = Read-Host "Restart computer now? (Y/N)"
if ($response -eq 'Y' -or $response -eq 'y') {
    Write-Info "Restarting in 30 seconds..."
    Start-Sleep -Seconds 5
    Restart-Computer -Force
} else {
    Write-Info "Restart manually when ready. Changes require restart to take effect."
}
