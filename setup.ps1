# FaceFrame bootstrap: project-local venv + locked dependencies + model check.
# Requires uv (https://docs.astral.sh/uv/) and internet for the first run only.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv not found. Install: winget install astral-sh.uv" }
if (-not (Test-Path .venv)) { uv venv --python 3.12 .venv }
# torch comes from the CUDA 13.0 index (RTX 50-series / sm_120 needs cu128+); everything else from PyPI
uv pip install --python .venv\Scripts\python.exe -r requirements.lock --index-strategy unsafe-best-match `
   --extra-index-url https://download.pytorch.org/whl/cu130
.venv\Scripts\python.exe tools\get_models.py
Write-Host "Done. Start FaceFrame with run_faceframe.bat"
