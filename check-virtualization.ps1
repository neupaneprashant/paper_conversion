# Check Virtualization Support and Status
# Run as: Administrator

function Write-Success { Write-Host $args -ForegroundColor Green }
function Write-Error-Custom { Write-Host $args -ForegroundColor Red }
function Write-Info { Write-Host $args -ForegroundColor Cyan }
function Write-Warning-Custom { Write-Host $args -ForegroundColor Yellow }

$isAdmin = [bool]([System.Security.Principal.WindowsIdentity]::GetCurrent().Groups -match 'S-1-5-32-544')
if (-not $isAdmin) {
    Write-Error-Custom "ERROR: Run as Administrator"
    exit 1
}

Write-Info "=== VIRTUALIZATION DIAGNOSTIC CHECK ==="
Write-Info ""

# Check CPU virtualization support
Write-Info "[1/4] Checking CPU virtualization support..."
$cpuInfo = Get-WmiObject -Class Win32_Processor
$cpuName = $cpuInfo.Name

Write-Info "CPU: $cpuName"

# Check for virtualization flags in CPU
$cpuVirt = $false
$wmiOS = Get-WmiObject -Class Win32_OperatingSystem
$vmx = $cpuInfo.VMMonitorModeExtensions

if ($vmx) {
    Write-Success "[OK] CPU supports virtualization (VMX/VT-x detected)"
    $cpuVirt = $true
}
else {
    Write-Warning-Custom "[WARN] VMX flag not detected in WMI"
}

Write-Info ""
Write-Info "[2/4] Checking Windows virtualization features..."

# Check Hyper-V
$hyperV = Get-WindowsOptionalFeature -Online -FeatureName "Hyper-V" -ErrorAction SilentlyContinue
if ($hyperV.State -eq "Enabled") {
    Write-Success "[OK] Hyper-V is ENABLED"
}
else {
    Write-Error-Custom "[FAIL] Hyper-V is DISABLED"
}

# Check Virtual Machine Platform
$vmp = Get-WindowsOptionalFeature -Online -FeatureName "VirtualMachinePlatform" -ErrorAction SilentlyContinue
if ($vmp.State -eq "Enabled") {
    Write-Success "[OK] Virtual Machine Platform is ENABLED"
}
else {
    Write-Error-Custom "[FAIL] Virtual Machine Platform is DISABLED"
}

# Check if hypervisor is running
Write-Info ""
Write-Info "[3/4] Checking if hypervisor is active..."
$hvCheck = Get-WmiObject -Class Win32_ComputerSystem
if ($hvCheck.HypervisorPresent -eq $true) {
    Write-Success "[OK] Hypervisor is RUNNING"
}
else {
    Write-Error-Custom "[FAIL] Hypervisor is NOT RUNNING"
    Write-Warning-Custom "       This usually means virtualization is disabled in BIOS/UEFI"
}

# Check Windows Sandbox (indicates virtualization support)
Write-Info ""
Write-Info "[4/4] Checking Windows Sandbox capability..."
$sandbox = Get-WindowsOptionalFeature -Online -FeatureName "Containers-DisposableVM" -ErrorAction SilentlyContinue
if ($sandbox.State -eq "Enabled") {
    Write-Success "[OK] Windows Sandbox capable (virtualization supported)"
}
else {
    Write-Warning-Custom "[WARN] Windows Sandbox not enabled"
}

# Summary and next steps
Write-Info ""
Write-Info "=== SUMMARY ==="

if ($hvCheck.HypervisorPresent -eq $false) {
    Write-Error-Custom "[CRITICAL] Hypervisor not running!"
    Write-Info ""
    Write-Warning-Custom "SOLUTION: Enable virtualization in BIOS/UEFI"
    Write-Warning-Custom ""
    Write-Warning-Custom "Steps:"
    Write-Warning-Custom "1. RESTART your computer"
    Write-Warning-Custom "2. Press: DEL, F2, F10, or F12 to enter BIOS (during startup splash screen)"
    Write-Warning-Custom "3. Find setting like:"
    Write-Warning-Custom "   - Intel VT-x / VT-d (Intel CPUs)"
    Write-Warning-Custom "   - AMD-V / SVM (AMD CPUs)"
    Write-Warning-Custom "   - Virtualization Technology / Hyper-V"
    Write-Warning-Custom "4. ENABLE the setting"
    Write-Warning-Custom "5. SAVE and EXIT (usually Ctrl+S, F10)"
    Write-Warning-Custom "6. System will restart"
    Write-Warning-Custom "7. After restart, run this script again"
}
else {
    Write-Success "[OK] Virtualization is properly configured!"
    Write-Success "Docker should work now."
}
