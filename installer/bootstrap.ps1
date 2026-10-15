<#
  FaceFrame environment bootstrap (run by the installer, or later via "Repair FaceFrame").

  - Python 3.12 is bundled with the installer (no system Python needed); uv.exe installs the packages
  - pinned runtime packages from requirements-app.lock
  - PyTorch with CUDA matching the PC's NVIDIA driver, or none (Fast mode only)
  - model checksums verified, self-test run
  Everything stays inside the install folder (and uv's download cache).
#>
param(
    [string]$AppDir = $PSScriptRoot,
    [switch]$CpuOnly,      # skip PyTorch / GPU enhancement
    [switch]$Quiet
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$log = Join-Path $AppDir "install.log"
Start-Transcript -Path $log -Append | Out-Null
$Host.UI.RawUI.WindowTitle = "FaceFrame setup"

function Step($n, $total, $msg) {
    Write-Host ""
    Write-Host ("  [{0}/{1}] {2}" -f $n, $total, $msg) -ForegroundColor Cyan
}
function Fail($msg) {
    Write-Host ""
    Write-Host "  SETUP FAILED: $msg" -ForegroundColor Red
    Write-Host "  Details: $log" -ForegroundColor Yellow
    Stop-Transcript | Out-Null
    if (-not $Quiet) { Read-Host "  Press Enter to close" }
    exit 1
}

Write-Host ""
Write-Host "  FaceFrame - installing components" -ForegroundColor White
Write-Host "  This downloads Python packages (and the GPU AI runtime on NVIDIA PCs). Keep this window open." -ForegroundColor Gray
$total = 6
$uv = Join-Path $AppDir "uv.exe"
if (-not (Test-Path $uv)) { Fail "uv.exe missing from $AppDir" }
$env:UV_PYTHON_DOWNLOADS = "never"     # the runtime is bundled
$env:UV_LINK_MODE = "copy"
$env:UV_NO_PROGRESS = "0"

# ----------------------------------------------------------------- 1. system checks
Step 1 $total "Checking this PC"
$build = [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
Write-Host "    Windows build $build"
if ($build -lt 18362) { Write-Host "    WARNING: Windows 10 1903+ is required for window capture (direct stream still works)." -ForegroundColor Yellow }
$vcrt = Test-Path "$env:SystemRoot\System32\vcruntime140_1.dll"
Write-Host "    Visual C++ runtime: $(if ($vcrt) {'present'} else {'missing'})"
if (-not $vcrt) {
    Write-Host "    Installing the Microsoft Visual C++ runtime (Windows will ask for permission)..."
    $vc = Join-Path $env:TEMP "vc_redist.x64.exe"
    Invoke-WebRequest "https://aka.ms/vs/17/release/vc_redist.x64.exe" -OutFile $vc
    Start-Process $vc -ArgumentList "/install /quiet /norestart" -Verb RunAs -Wait
}

# ----------------------------------------------------------------- 2. GPU -> torch flavour
Step 2 $total "Detecting the graphics card"
$torchIndex = $null
$torchSpec = "torch"
if (-not $CpuOnly) {
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi -and (Test-Path "$env:SystemRoot\System32\nvidia-smi.exe")) { $smi = "$env:SystemRoot\System32\nvidia-smi.exe" }
    if ($smi) {
        try {
            $q = & $smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null | Select-Object -First 1
            $parts = $q.Split(",") | ForEach-Object { $_.Trim() }
            $gpu, $drv = $parts[0], $parts[1]
            $major = [double]($drv.Split(".")[0] + "." + $drv.Split(".")[1])
            Write-Host "    NVIDIA $gpu, driver $drv"
            if ($major -ge 580) { $torchIndex = "https://download.pytorch.org/whl/cu130"; $torchSpec = "torch==2.14.1" }
            elseif ($major -ge 570) { $torchIndex = "https://download.pytorch.org/whl/cu128" }
            elseif ($major -ge 528) { $torchIndex = "https://download.pytorch.org/whl/cu126"
                if ($gpu -match "RTX 50") { Write-Host "    NOTE: RTX 50-series needs driver 570+ for GPU enhancement; update the NVIDIA driver." -ForegroundColor Yellow; $torchIndex = $null } }
            else { Write-Host "    Driver too old for GPU enhancement (needs 528+). Fast mode will be used." -ForegroundColor Yellow }
        } catch { Write-Host "    Could not query nvidia-smi: $_" -ForegroundColor Yellow }
    } else {
        Write-Host "    No NVIDIA GPU found: FaceFrame will use Fast mode (full stream quality, no AI upscaling)."
    }
} else { Write-Host "    CPU-only install requested." }

# ----------------------------------------------------------------- 3. python + venv
Step 3 $total "Preparing Python 3.12 (bundled private copy)"
$basePy = Join-Path $AppDir "runtime\python.exe"
if (-not (Test-Path $basePy)) { Fail "bundled Python runtime missing ($basePy)" }
$venv = Join-Path $AppDir ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    & $uv venv --python $basePy $venv --quiet
    if ($LASTEXITCODE -ne 0) { Fail "could not create the virtual environment" }
}
$py = Join-Path $venv "Scripts\python.exe"

# ----------------------------------------------------------------- 4. packages
Step 4 $total "Installing FaceFrame components"
# slow / flaky connections: long timeout and up to 3 attempts (uv resumes from its cache)
$env:UV_HTTP_TIMEOUT = "300"
function Install-Retry([string[]]$UvArgs) {
    for ($i = 1; $i -le 3; $i++) {
        & $uv @UvArgs | Out-Host
        if ($LASTEXITCODE -eq 0) { return $true }
        if ($i -lt 3) { Write-Host "    Download interrupted - retrying ($($i + 1)/3) ..." -ForegroundColor Yellow; Start-Sleep 5 }
    }
    return $false
}
if (-not (Install-Retry @("pip", "install", "--python", $py, "-r", (Join-Path $AppDir "requirements-app.lock")))) {
    Fail "package installation failed (check the internet connection)"
}
if ($torchIndex) {
    Write-Host "    Installing the GPU AI runtime (PyTorch, about 2.5 GB - this takes a few minutes)..."
    if (-not (Install-Retry @("pip", "install", "--python", $py, $torchSpec, "--index-url", $torchIndex))) {
        Write-Host "    GPU runtime install failed; continuing without it (Fast mode)." -ForegroundColor Yellow
    }
}

# ----------------------------------------------------------------- 5. models
Step 5 $total "Verifying AI models"
& $py (Join-Path $AppDir "tools\get_models.py")
if ($LASTEXITCODE -ne 0) { Fail "model files could not be verified" }

# ----------------------------------------------------------------- 6. self-test
Step 6 $total "Testing the installation"
$env:PYTHONIOENCODING = "utf-8"
Push-Location $AppDir
& $py -m faceframe --selftest
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Fail "self-test failed (see $env:APPDATA\FaceFrame\selftest.json)" }
Set-Content -Path (Join-Path $AppDir "installed.flag") -Value (Get-Date -Format o)

Write-Host ""
Write-Host "  FaceFrame is ready." -ForegroundColor Green
Stop-Transcript | Out-Null
if (-not $Quiet) { Start-Sleep -Seconds 3 }
exit 0
