@echo off
setlocal
title QA Studio Installer
cd /d "%~dp0"

rem ?????? QA Studio one-file bootstrapper ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
rem Download this single file, double-click it, and it installs everything:
rem it pulls the project from GitHub, then runs the real installer (installer.py)
rem which installs dependencies, creates a Desktop shortcut, and launches the app.

set "REPO=AhmedSayedRepo/QA-Studio"
set "BRANCH=main"
set "DEST=%LOCALAPPDATA%\QA Studio"
set "ZIP=%TEMP%\qastudio_src.zip"
set "WORK=%TEMP%\qastudio_src"

rem --- find Python; if missing, INSTALL IT AUTOMATICALLY (no manual download) ---
set "PY="
if not defined PY ( py -3 --version   >nul 2>&1 && set "PY=py -3" )
if not defined PY ( python --version  >nul 2>&1 && set "PY=python" )

if not defined PY (
  echo Python 3 was not found - installing it automatically. Please wait...
  rem 1) winget (built into Windows 10/11) - silent, adds Python to PATH
  where winget >nul 2>&1 && winget install --id Python.Python.3.12 -e --scope user --silent --accept-package-agreements --accept-source-agreements
)
if not defined PY ( py -3 --version   >nul 2>&1 && set "PY=py -3" )
if not defined PY ( python --version  >nul 2>&1 && set "PY=python" )

if not defined PY (
  rem 2) fall back to the official python.org installer (silent, per-user, on PATH)
  echo Downloading the Python installer...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '%TEMP%\python-setup.exe' } catch { exit 1 }"
  if exist "%TEMP%\python-setup.exe" (
    echo Installing Python ^(this can take a minute^)...
    "%TEMP%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1
    del "%TEMP%\python-setup.exe" >nul 2>&1
  )
)
if not defined PY ( py -3 --version   >nul 2>&1 && set "PY=py -3" )
if not defined PY ( python --version  >nul 2>&1 && set "PY=python" )
if not defined PY (
  rem 3) last resort: use python.exe from the standard per-user install location
  for /f "delims=" %%P in ('dir /b /s "%LOCALAPPDATA%\Programs\Python\Python3*\python.exe" 2^>nul') do (
    set "PATH=%%~dpP;%PATH%" & set "PY=python"
  )
)

if not defined PY (
  echo.
  echo Automatic Python setup did not complete. Please install Python from
  echo https://www.python.org/downloads/ ^(tick "Add to PATH"^) and re-run this installer.
  pause
  exit /b 1
)

rem --- find pythonw (windowless) to launch the GUI without a console box ---
set "PYW=pyw -3"
where pyw >nul 2>&1 || set "PYW=pythonw"
%PYW% --version >nul 2>&1 || set "PYW=%PY%"

echo Downloading QA Studio...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/%REPO%/archive/refs/heads/%BRANCH%.zip' -OutFile '%ZIP%' } catch { exit 1 }"
if errorlevel 1 (
  echo.
  echo Download failed - check your internet connection and try again.
  pause
  exit /b 1
)

echo Extracting...
if exist "%WORK%" rmdir /s /q "%WORK%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%ZIP%' -DestinationPath '%WORK%' -Force"
if errorlevel 1 (
  echo Extract failed.
  pause
  exit /b 1
)

rem --- copy the extracted "Repo-branch" folder contents into DEST ---
if not exist "%DEST%" mkdir "%DEST%" >nul 2>&1
for /d %%D in ("%WORK%\*") do robocopy "%%D" "%DEST%" /E /NFL /NDL /NJH /NJS /NP >nul
del "%ZIP%" >nul 2>&1
rmdir /s /q "%WORK%" >nul 2>&1

cd /d "%DEST%"

echo Preparing QA Studio installer...
%PY% -m pip install --quiet --disable-pip-version-check --upgrade pip >nul 2>&1

echo Installing native window backend (pywebview)...
%PY% -m pip install --disable-pip-version-check "pywebview>=5.0" pythonnet
if errorlevel 1 (
  echo.
  echo [warn] Could not install the native-window backend. The installer will
  echo        open in a chromeless app window instead.
  echo.
)

echo Launching installer...
start "" %PYW% "%DEST%\installer.py"

endlocal

































































