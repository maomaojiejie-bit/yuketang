@echo off
rem ============================================================
rem  Open the local web console directly (skips the menu).
rem ============================================================
setlocal
cd /d "%~dp0"

chcp 65001 >nul 2>nul
title Changjiang Yuketang Console

where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python was not found in PATH.
  echo     Install Python 3.10+ from https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

python -c "import playwright, httpx, yaml, rich, dotenv, aiohttp" >nul 2>nul
if errorlevel 1 (
  echo [*] Installing dependencies - this may take a minute...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [X] Dependency installation failed.
    pause
    exit /b 1
  )
)

echo [*] Starting the local console at http://127.0.0.1:8765/
echo     Press Ctrl+C here to stop it.
echo.
python run.py --web
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
  echo.
  echo [!] Exited with code %RC%
  pause
)

endlocal
