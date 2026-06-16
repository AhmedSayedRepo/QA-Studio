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
set "BASE_DIR=%LOCALAPPDATA%\QA Studio"
set "APP_DIR=%BASE_DIR%\%APP_NAME%"

echo.
echo   Installing QA Studio...
echo.

:: ---------------------------------------------------------------------------
:: 1. Find Git (PATH first, then common install folders)
:: ---------------------------------------------------------------------------
set "GIT="
for /f "delims=" %%G in ('where git 2^>nul') do if not defined GIT set "GIT=%%G"
if not defined GIT if exist "%ProgramFiles%\Git\cmd\git.exe" set "GIT=%ProgramFiles%\Git\cmd\git.exe"
if not defined GIT if exist "%ProgramFiles(x86)%\Git\cmd\git.exe" set "GIT=%ProgramFiles(x86)%\Git\cmd\git.exe"
if not defined GIT if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "GIT=%LOCALAPPDATA%\Programs\Git\cmd\git.exe"

if not defined GIT (
    echo   Git not found. Installing Git...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo   winget unavailable. Please install Git from https://git-scm.com/download/win
        echo   then run install.bat again.
        pause
        exit /b 1
    )
    winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
    :: re-detect after install (PATH in THIS session is stale, so check folders)
    if exist "%ProgramFiles%\Git\cmd\git.exe" set "GIT=%ProgramFiles%\Git\cmd\git.exe"
    if not defined GIT if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "GIT=%LOCALAPPDATA%\Programs\Git\cmd\git.exe"
    if not defined GIT (
        echo.
        echo   Git was installed but needs a PATH refresh. Please CLOSE this window
        echo   and run install.bat once more.
        pause
        exit /b 0
    )
)
echo   Git: !GIT!

:: ---------------------------------------------------------------------------
:: 2. Find Python (PATH first, then common install folders)
:: ---------------------------------------------------------------------------
set "PY="
for /f "delims=" %%P in ('where py 2^>nul') do if not defined PY set "PY=%%P"
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
:: ignore the Windows Store alias stub (0-byte python.exe in WindowsApps)
if defined PY echo !PY! | find /i "WindowsApps" >nul && set "PY="

if not defined PY (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
    if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
    if not defined PY if exist "%ProgramFiles%\Python311\python.exe" set "PY=%ProgramFiles%\Python311\python.exe"
)

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
    :: re-detect after install (check folders, PATH is stale this session)
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
    if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
    if not defined PY (
        echo.
        echo   Python was installed but needs a PATH refresh. Please CLOSE this window
        echo   and run install.bat once more.
        pause
        exit /b 0
    )
)
echo   Python: !PY!

:: ---------------------------------------------------------------------------
:: 3. Clone or update the repo
:: ---------------------------------------------------------------------------
if exist "%APP_DIR%\.git" (
    echo   Updating existing install...
    "!GIT!" -C "%APP_DIR%" pull --ff-only origin main
) else (
    echo   Downloading QA Studio...
    if not exist "%BASE_DIR%" mkdir "%BASE_DIR%"
    "!GIT!" clone "%REPO_URL%" "%APP_DIR%"
    if errorlevel 1 (
        echo.
        echo   ERROR: Could not download the app. Check your internet connection,
        echo   then try again.
        pause
        exit /b 1
    )
)

:: ---------------------------------------------------------------------------
:: 4. Launch the graphical installer from the cloned folder (no console)
:: ---------------------------------------------------------------------------
set "PYW="
for /f "delims=" %%W in ('"!PY!" -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%W"
if not defined PYW set "PYW=!PY!"
if not exist "!PYW!" set "PYW=!PY!"

echo.
echo   Opening the installer window...
start "" "!PYW!" "%APP_DIR%\installer.py"
endlocal