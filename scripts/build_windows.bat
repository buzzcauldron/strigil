@echo off
REM Build Windows install package. Run from project root (e.g. in Command Prompt or Git Bash):
REM   scripts\build_windows.bat
REM Output: dist\strigil-win.zip (and dist\strigil\)

cd /d "%~dp0\.."
echo Building strigil for Windows...
pip install -e ".[bundle]" -q
pyinstaller strigil.spec
if errorlevel 1 exit /b 1

set OUT=dist\strigil-win.zip
if exist "%OUT%" del "%OUT%"

powershell -NoProfile -Command "Compress-Archive -Path 'dist\strigil' -DestinationPath '%OUT%' -Force" 2>nul
if errorlevel 1 python scripts\zip_dist.py
echo Done: %OUT%
