@echo off
rem ============================================================
rem  Changjiang Yuketang Solver - one click launcher
rem  This file is intentionally ASCII-only: batch files handle
rem  UTF-8 Chinese badly. All Chinese UI comes from Python.
rem ============================================================
setlocal
cd /d "%~dp0"

chcp 65001 >nul 2>nul
rem Force UTF-8 on the Python side so the 3D YKT logo renders correctly.
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title YKT - Changjiang Yuketang Solver

rem Keep this banner ASCII-only: cmd reads the .bat with the *previous*
rem codepage, so box-drawing characters here would be mangled. The real
rem 3D "YKT" logo is printed by Python (run.py --menu) right after this.
echo ============================================================
echo   YKT  -  Changjiang Yuketang Solver
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python was not found in PATH.
  echo.
  echo     Install Python 3.10+ from https://www.python.org/downloads/
  echo     and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [*] Python %PYVER%

python -c "import playwright, httpx, yaml, rich, dotenv, aiohttp" >nul 2>nul
if errorlevel 1 (
  echo [*] Installing dependencies - this may take a minute...
  echo.
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo [X] Dependency installation failed.
    echo     Try running manually:  python -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
  )
  echo.
)

python run.py --menu
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
  echo.
  echo [!] Exited with code %RC%
  echo.
  pause
)

endlocal
