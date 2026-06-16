@echo off
setlocal enabledelayedexpansion
title QA Studio - Installer

:: ===========================================================================
:: QA Studio bootstrap installer
:: Ensures Git + Python exist, clones (or updates) the repo so the app can
:: self-update later, then launches the graphical installer.
:: ===========================================================================

set "REPO_URL=https://github.com/AhmedSayedRepo/qa-studio.git"
set "APP_NAME=qa-studio"
:: Install location: %LOCALAPPDATA%\QA Studio\qa-studio
set "BASE_DIR=%LOCALAPPDATA%\QA Studio"
set "APP_DIR=%BASE_DIR%\%APP_NAME%"

echo.
echo   Installing QA Studio...
echo.

:: ---------------------------------------------------------------------------
:: 1. Ensure Git
:: ---------------------------------------------------------------------------
where git >nul 2>&1
if errorlevel 1 (
    echo   Git not found. Installing Git...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo   winget unavailable. Please install Git from https://git-scm.com/download/win
        echo   then run install.bat again.
        pause
        exit /b 1
    )
    winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
    echo.
    echo   Git installed. Please CLOSE this window and run install.bat again
    echo   so the new PATH takes effect.
    pause
    exit /b 0
)

:: ---------------------------------------------------------------------------
:: 2. Ensure Python
:: ---------------------------------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )

if not defined PY (
    echo   Python not found. Installing Python...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo   winget unavailable. Please install Python 3.11+ from
        echo   https://www.python.org/downloads/  (tick "Add python.exe to PATH"),
        echo   then run install.bat again.
        pause
        exit /b 1
    )
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    echo.
    echo   Python installed. Please CLOSE this window and run install.bat again
    echo   so the new PATH takes effect.
    pause
    exit /b 0
)

:: ---------------------------------------------------------------------------
:: 3. Clone or update the repo
:: ---------------------------------------------------------------------------
if exist "%APP_DIR%\.git" (
    echo   Updating existing install...
    git -C "%APP_DIR%" pull --ff-only origin main
) else (
    echo   Downloading QA Studio...
    if not exist "%BASE_DIR%" mkdir "%BASE_DIR%"
    git clone "%REPO_URL%" "%APP_DIR%"
    if errorlevel 1 (
        echo.
        echo   ERROR: Could not download the app. Check your internet connection
        echo   and that you have access to the repository, then try again.
        pause
        exit /b 1
    )
)

:: ---------------------------------------------------------------------------
:: 4. Launch the graphical installer from the cloned folder (no console)
:: ---------------------------------------------------------------------------
set "PYW="
for /f "delims=" %%P in ('%PY% -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%P"
if not defined PYW set "PYW=pythonw.exe"
if not exist "!PYW!" set "PYW=%PY%"

start "" "!PYW!" "%APP_DIR%\installer.py"
endlocal