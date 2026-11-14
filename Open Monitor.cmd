@echo off
setlocal
where python >nul 2>nul
if errorlevel 1 (
  echo Install Python and make sure the python command is available.
  pause
  exit /b 1
)
python -c "from PySide6.QtQuick import QQuickWindow; from PySide6.QtQml import QQmlApplicationEngine" >nul 2>nul
if errorlevel 1 (
  echo Installing the Qt desktop interface...
  python -m pip install -r "%~dp0enhanced\monitor-requirements.txt"
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
for /f "delims=" %%P in ('python -c "import sys; from pathlib import Path; print(Path(sys.executable).with_name('pythonw.exe'))"') do set "MCP_PYTHONW=%%P"
if exist "%MCP_PYTHONW%" (
  start "" "%MCP_PYTHONW%" "%~dp0enhanced\monitor.py"
) else (
  python "%~dp0enhanced\monitor.py"
)
