@echo off
REM Runs Aloud in a console with verbose logging and the window open, for
REM working out why something is not behaving.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Aloud is not set up yet. Run install.ps1 first:
  echo     powershell -ExecutionPolicy Bypass -File install.ps1
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m aloud --verbose --show
pause
