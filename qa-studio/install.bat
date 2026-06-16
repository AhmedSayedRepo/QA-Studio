@echo off
setlocal enabledelayedexpansion
title QA Studio - Installer

echo ===============================================
echo            QA Studio  -  Installer
echo ===============================================
echo.
echo This will:
echo   1. Check for Python (install it if missing)
echo   2. Install QA Studio dependencies
echo   3. Create a Desktop shortcut to launch the app
echo.
pause

:: ---------------------------------------------------------------------------
:: 1. Locate Python
:: ---------------------------------------------------------------------------
echo.
echo [1/3] Checking for Python...

set "PY_CMD="
where py >nul 2>&1 && set "PY_CMD=py"
if not defined PY_CMD (
    where python >nul 2>&1 && set "PY_CMD=python"
)

if not defined PY_CMD (
    echo     Python not found. Installing via winget...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo.
        echo     ERROR: winget is not available on this PC.
        echo     Please install Python 3.11+ manually from:
        echo         https://www.python.org/downloads/
        echo     During setup, TICK "Add python.exe to PATH".
        echo.
        pause
        exit /b 1
    )
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    echo.
    echo     Python installed. Please CLOSE this window and run the installer
    echo     again so the new PATH takes effect.
    echo.
    pause
    exit /b 0
)

echo     Found Python: %PY_CMD%
%PY_CMD% --version

:: ---------------------------------------------------------------------------
:: 2. Install dependencies
:: ---------------------------------------------------------------------------
echo.
echo [2/3] Installing dependencies (this can take a few minutes)...
echo.

%PY_CMD% -m pip install --upgrade pip
%PY_CMD% -m pip install -r "%~dp0requirements.txt"

if errorlevel 1 (
    echo.
    echo     ERROR: Dependency installation failed.
    echo     Check your internet connection and try again.
    echo.
    pause
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: 3. Create Desktop shortcut
:: ---------------------------------------------------------------------------
echo.
echo [3/3] Creating Desktop shortcut...

set "APP_DIR=%~dp0"
set "APP_DIR=%APP_DIR:~0,-1%"

:: Find pythonw.exe (windowless launcher) next to the python we found
for /f "delims=" %%P in ('%PY_CMD% -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%P"
if not exist "!PYW!" set "PYW=pythonw.exe"

set "SHORTCUT=%USERPROFILE%\Desktop\QA Studio.lnk"
set "ICON=%APP_DIR%\app.ico"
set "TARGET=!PYW!"
set "ARGS=\"%APP_DIR%\main.py\""

:: Build the shortcut with a tiny PowerShell call
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$s = $ws.CreateShortcut('%SHORTCUT%');" ^
  "$s.TargetPath = '!TARGET!';" ^
  "$s.Arguments = '!ARGS!';" ^
  "$s.WorkingDirectory = '%APP_DIR%';" ^
  "if (Test-Path '%ICON%') { $s.IconLocation = '%ICON%' };" ^
  "$s.Description = 'QA Studio - AI Test Case Generator';" ^
  "$s.Save()"

if exist "%SHORTCUT%" (
    echo     Shortcut created on your Desktop.
) else (
    echo     Could not create the shortcut automatically.
    echo     You can still launch the app with: launch.bat
)

echo.
echo ===============================================
echo   Installation complete!
echo   Double-click "QA Studio" on your Desktop.
echo ===============================================
echo.
pause
endlocal
