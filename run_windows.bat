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
if exist ".venv\Scripts\python.exe" goto :check_deps

echo.
echo [OpenDS4] First launch: creating isolated environment in .venv ...
%PY_CMD% -m venv ".venv"
if errorlevel 1 goto :venv_failed

:check_deps
".venv\Scripts\python.exe" -c "import hid, vgamepad" >nul 2>nul
if not errorlevel 1 goto :launch

echo.
echo [OpenDS4] Installing missing Python libraries into .venv (hidapi, vgamepad)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-input -r requirements.txt
if errorlevel 1 goto :pip_failed

".venv\Scripts\python.exe" -c "import hid, vgamepad" >nul 2>nul
if errorlevel 1 goto :pip_failed

if not exist "%ProgramFiles%\Nefarius Software Solutions\ViGEm Bus Driver" (
    echo.
    echo [Note] Nefarius ViGEmBus driver was not detected in Program Files.
    echo        If games do not see the virtual Xbox 360 controller yet, install:
    echo        https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0
    echo.
)
echo [OpenDS4] Setup complete. Starting application...

:launch
if /i "%~1"=="--console" (
    shift
    ".venv\Scripts\python.exe" app.py %1 %2 %3 %4 %5
    exit /b %errorlevel%
)

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" app.py %*
    exit /b 0
)

start "" ".venv\Scripts\python.exe" app.py %*
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
