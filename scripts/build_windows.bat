@echo off
REM Build Windows install package. Run from project root (e.g. in Command Prompt or Git Bash):
REM   scripts\build_windows.bat
REM Output: dist\basic-scraper-win.zip (and dist\basic-scraper\)

cd /d "%~dp0\.."
echo Building basic-scraper for Windows...
pip install -e ".[bundle]" -q
pyinstaller basic-scraper.spec
if errorlevel 1 exit /b 1

set OUT=dist\basic-scraper-win.zip
if exist "%OUT%" del "%OUT%"

powershell -NoProfile -Command "Compress-Archive -Path 'dist\basic-scraper' -DestinationPath '%OUT%' -Force" 2>nul
if errorlevel 1 python scripts\zip_dist.py
echo Done: %OUT%
