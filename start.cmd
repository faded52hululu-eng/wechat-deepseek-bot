@echo off
cd /d "%~dp0"
if not exist "panel.py" (
  echo Please extract the complete archive before starting.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo First start: installing the local Python environment...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "setup.ps1"
  if errorlevel 1 (
    echo Setup failed. Check the error above.
    pause
    exit /b 1
  )
)
if /i "%~1"=="--check" (
  ".venv\Scripts\python.exe" -c "import panel; import pyweixin; import rapidocr_onnxruntime; print('Launcher path and dependencies OK.')"
  exit /b
)
".venv\Scripts\python.exe" panel.py
if errorlevel 1 pause
