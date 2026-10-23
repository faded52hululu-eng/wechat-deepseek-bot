@echo off
cd /d "%~dp0"
if not exist "ocr_probe.py" (
  echo Please extract the complete archive before starting.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File setup.ps1
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" ocr_probe.py
if errorlevel 1 pause
