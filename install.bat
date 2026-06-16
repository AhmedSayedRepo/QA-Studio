@echo off
setlocal
title QA Studio - Installer

:: ---------------------------------------------------------------------------
:: Find Python. If missing, install it (one-time), then the GUI takes over.
:: ---------------------------------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )

if not defined PY (
    echo.
    echo   Python is required and was not found.
    echo   Installing Python automatically...
    echo.
    where winget >nul 2>&1
    if errorlevel 1 (
        echo   winget is unavailable. Please install Python 3.11+ from:
        echo       https://www.python.org/downloads/
        echo   Tick "Add python.exe to PATH" during setup, then run install.bat again.
        echo.
        pause
        exit /b 1
    )
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    echo.
    echo   Python installed. Please close this window and run install.bat again.
    echo.
    pause
    exit /b 0
)

:: ---------------------------------------------------------------------------
:: Launch the graphical installer with pythonw (no console window).
:: ---------------------------------------------------------------------------
set "PYW="
for /f "delims=" %%P in ('%PY% -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%P"
if not defined PYW set "PYW=pythonw.exe"
if not exist "%PYW%" set "PYW=%PY%"

start "" "%PYW%" "%~dp0installer.py"
endlocal