@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title OpenDS4 Launcher

set "PY_CMD="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=py -3"
    goto :check_tk
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=python"
    goto :check_tk
)

echo.
echo [OpenDS4] Python 3.10 or newer was not found on this PC.
echo.
echo 1. Download Python from: https://www.python.org/downloads/
echo 2. During setup, check "Add python.exe to PATH" and keep "tcl/tk" enabled.
echo 3. Run this script again.
echo.
pause
exit /b 1

:check_tk
%PY_CMD% -c "import tkinter" >nul 2>nul
if not errorlevel 1 goto :check_venv

echo.
echo [OpenDS4] Your Python installation is missing Tcl/Tk (tkinter).
echo Re-run the Python installer, choose "Modify", and enable "tcl/tk and IDLE".
echo.
pause
exit /b 1

:check_venv
if exist "%~dp0.venv\Scripts\python.exe" goto :check_deps

echo.
echo [OpenDS4] First launch: creating isolated environment in .venv ...
%PY_CMD% -m venv "%~dp0.venv"
if errorlevel 1 goto :venv_failed

:check_deps
"%~dp0.venv\Scripts\python.exe" -c "import importlib.util as u; raise SystemExit(0 if u.find_spec('hid') and u.find_spec('vgamepad') else 1)" >nul 2>nul
if not errorlevel 1 goto :preflight

echo.
echo [OpenDS4] Installing missing Python libraries into .venv (hidapi, vgamepad)...
"%~dp0.venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-input -r "%~dp0requirements.txt"
if errorlevel 1 goto :pip_failed

"%~dp0.venv\Scripts\python.exe" -c "import importlib.util as u; raise SystemExit(0 if u.find_spec('hid') and u.find_spec('vgamepad') else 1)" >nul 2>nul
if errorlevel 1 goto :pip_failed

if not exist "%ProgramFiles%\Nefarius Software Solutions\ViGEm Bus Driver" (
    echo.
    echo [Note] Nefarius ViGEmBus driver was not detected in Program Files.
    echo        If games do not see the virtual Xbox 360 controller yet, install:
    echo        https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0
    echo.
)
echo [OpenDS4] Setup complete. Starting application...

:preflight
"%~dp0.venv\Scripts\python.exe" "%~dp0app.py" --self-test >nul 2>nul
if errorlevel 1 goto :startup_failed

:launch
if /i "%~1"=="--console" (
    shift
    "%~dp0.venv\Scripts\python.exe" "%~dp0app.py" %1 %2 %3 %4 %5
    if errorlevel 1 pause
    exit /b %errorlevel%
)

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py" %*
    exit /b 0
)

start "" "%~dp0.venv\Scripts\python.exe" "%~dp0app.py" %*
exit /b 0

:venv_failed
echo.
echo [Error] Could not create ".venv". Make sure this folder is writable.
echo.
pause
exit /b 1

:pip_failed
echo.
echo [Error] Could not download or install packages from requirements.txt.
echo Check your internet connection for this one-time setup and try again.
echo.
pause
exit /b 1

:startup_failed
echo.
echo [Error] OpenDS4 preflight check failed:
echo ----------------------------------------
"%~dp0.venv\Scripts\python.exe" "%~dp0app.py" --self-test
echo.
pause
exit /b 1
