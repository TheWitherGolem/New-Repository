@echo off
REM Double-click this to set Aloud up. It runs install.ps1 for you, so you do
REM not have to touch PowerShell or its execution policy yourself.
cd /d "%~dp0"

echo Setting up Aloud. This takes a few minutes the first time.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

echo.
echo ---------------------------------------------------------------
echo  When this finishes, double-click Aloud.vbs to start the app.
echo  It has no window - look for its icon in the tray, next to the
echo  clock, then press Ctrl+Alt+S with some text highlighted.
echo ---------------------------------------------------------------
echo.
pause
