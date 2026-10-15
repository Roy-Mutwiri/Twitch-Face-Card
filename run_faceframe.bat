@echo off
rem FaceFrame launcher (uses the project venv; no console window)
set HERE=%~dp0
if not exist "%HERE%.venv\Scripts\pythonw.exe" (
  echo FaceFrame environment missing. Run: powershell -ExecutionPolicy Bypass -File "%HERE%setup.ps1"
  pause
  exit /b 1
)
start "" "%HERE%.venv\Scripts\pythonw.exe" -m faceframe
