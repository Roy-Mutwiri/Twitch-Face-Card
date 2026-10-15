<#
  Build dist\FaceFrame-Setup-<version>.exe

  1. render artwork (icon, wizard images)
  2. build FaceFrame.exe launcher (PyInstaller, no third-party deps)
  3. stage app files + models + extension + uv.exe
  4. compile the Inno Setup script
#>
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
$version = (& $py -c "import faceframe; print(faceframe.__version__)").Trim()
Write-Host "Building FaceFrame $version"

& $py installer\make_assets.py
(Get-Content installer\before_install_template.txt -Raw).Replace("{VERSION}", $version) |
    Set-Content installer\assets\before_install.txt -Encoding utf8

# launcher exe
& $py -m PyInstaller --noconfirm --onefile --windowed --name FaceFrame `
    --icon (Join-Path $root "installer\assets\faceframe.ico") --distpath build\launcher --workpath build\launcher_work `
    --specpath build (Join-Path $root "installer\launcher.py")
if ($LASTEXITCODE -ne 0) { throw "launcher build failed" }

# stage
$stage = Join-Path $root "build\stage"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory $stage | Out-Null
Copy-Item faceframe $stage -Recurse
Get-ChildItem $stage -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Copy-Item extension $stage -Recurse
New-Item -ItemType Directory "$stage\models", "$stage\tools" | Out-Null
foreach ($m in "face_detection_yunet_2023mar.onnx", "realesr-general-x4v3.pth", "realesr-general-wdn-x4v3.pth", "RealESRGAN_x2plus.pth") {
    Copy-Item "models\$m" "$stage\models\"
}
Copy-Item tools\get_models.py, tools\check_pattern.py "$stage\tools\"
Copy-Item installer\bootstrap.ps1, installer\requirements-app.lock "$stage\"
Copy-Item installer\assets\faceframe.ico "$stage\"
Copy-Item build\launcher\FaceFrame.exe "$stage\"
Copy-Item README.md, THIRD_PARTY_NOTICES.md "$stage\" -ErrorAction SilentlyContinue
$uv = (Get-Command uv).Source
Copy-Item $uv "$stage\uv.exe"
# private Python runtime, bundled: setup never needs junctions (blocked inside the installer's
# protected process tree - measured: os error 448) and never touches the user's PATH / registry
$pyrt = Join-Path $root "build\pyruntime"
if (-not (Test-Path "$pyrt\cpython-3.12.14-windows-x86_64-none\python.exe")) {
    $env:UV_PYTHON_INSTALL_DIR = $pyrt
    & $uv python install 3.12.14 --no-bin --no-registry
    if ($LASTEXITCODE -ne 0) { throw "python runtime download failed" }
}
Copy-Item "$pyrt\cpython-3.12.14-windows-x86_64-none" "$stage\runtime" -Recurse

# compile
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc "/DAppVersion=$version" "/DStageDir=$stage" installer\FaceFrame.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
Get-ChildItem dist\FaceFrame-Setup-*.exe | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
