@echo off
setlocal
title QA Studio Installer
cd /d "%~dp0"

echo.
echo   ===========================================================
echo      QA Studio  -  Setup
echo      Getting everything ready. Please keep this window open;
echo      the graphical installer opens automatically in a moment.
echo   ===========================================================
echo.

rem ============================ QA Studio one-file bootstrapper ============================
rem Download this single file, double-click it, and it installs everything:
rem it pulls the project from GitHub, then runs the real installer (installer.py)
rem which installs dependencies, creates a Desktop shortcut, and launches the app.

set "REPO=AhmedSayedRepo/QA-Studio"
set "BRANCH=main"
set "DEST=%LOCALAPPDATA%\QA Studio"
set "ZIP=%TEMP%\qastudio_src.zip"
set "WORK=%TEMP%\qastudio_src"

rem --- find Python; if missing, INSTALL IT AUTOMATICALLY (pinned to 3.12) ---
rem QA Studio is built/tested against Python 3.12 specifically. We must NOT install
rem 3.14: pythonnet ships no 3.14 wheel yet, so `pip install pythonnet` would fail and
rem the pywebview native window would never come up. We also must NOT trust a bare
rem `python --version` right after installing: winget / the python.org installer add
rem Python to the PERSISTENT PATH, but the PATH of THIS already-running cmd.exe is not
rem refreshed, so the freshly-installed interpreter is invisible to a PATH lookup.
rem Instead we re-detect by FULL PATH (:find_python) and call python.exe directly from
rem its known install location.

set "PY="
set "PYW="
call :find_python
if defined PY goto have_python

echo Python 3.12 was not found - installing it automatically. Please wait...

rem 1) winget (built into Windows 10/11) - silent, per-user
where winget >nul 2>&1 && winget install --id Python.Python.3.12 --exact --silent --accept-source-agreements --accept-package-agreements --scope user
call :find_python
if defined PY goto have_python

rem 2) fall back to the official python.org silent installer (per-user, on PATH)
echo Downloading the Python 3.12 installer...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '%TEMP%\python-3.12.8-amd64.exe' } catch { exit 1 }"
if exist "%TEMP%\python-3.12.8-amd64.exe" (
  echo Installing Python ^(this can take a minute^)...
  "%TEMP%\python-3.12.8-amd64.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1
  del "%TEMP%\python-3.12.8-amd64.exe" >nul 2>&1
)
call :find_python
if defined PY goto have_python

echo.
echo Automatic Python setup did not complete. Please install Python 3.12 from
echo https://www.python.org/downloads/release/python-3128/ ^(tick "Add Python to PATH"^),
echo then re-run this installer.
pause
exit /b 1

:have_python
echo Using Python: %PY%
if not defined PYW set "PYW=%PY%"

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
exit /b 0

rem ================================ helpers ================================
rem :find_python
rem Re-detect a usable Python 3.12 by FULL PATH first (a Python just installed by
rem winget or the python.org installer is NOT on this running shell's PATH yet) and
rem call python.exe directly. Only fall back to the py launcher / PATH python if no
rem known full-path install exists. Sets PY (and PYW for the windowless GUI launch).
rem Full paths are stored WITH surrounding quotes so a "%ProgramFiles%" location that
rem contains a space still invokes correctly as %PY% / %PYW%.
:find_python
set "PY="
set "PYW="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
  set "PYW="%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe""
)
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
  set "PY="%LOCALAPPDATA%\Programs\Python\Python311\python.exe""
  set "PYW="%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe""
)
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
  set "PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe""
  set "PYW="%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe""
)
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" (
  set "PY="%ProgramFiles%\Python312\python.exe""
  set "PYW="%ProgramFiles%\Python312\pythonw.exe""
)
if not defined PY if exist "%ProgramFiles%\Python311\python.exe" (
  set "PY="%ProgramFiles%\Python311\python.exe""
  set "PYW="%ProgramFiles%\Python311\pythonw.exe""
)
if not defined PY if exist "%ProgramFiles%\Python313\python.exe" (
  set "PY="%ProgramFiles%\Python313\python.exe""
  set "PYW="%ProgramFiles%\Python313\pythonw.exe""
)
if not defined PY ( py -3.12 --version >nul 2>&1 && ( set "PY=py -3.12" & set "PYW=pyw -3.12" ) )
if not defined PY ( py -3 --version   >nul 2>&1 && ( set "PY=py -3"    & set "PYW=pyw -3" ) )
if not defined PY ( python --version  >nul 2>&1 && ( set "PY=python"   & set "PYW=pythonw" ) )
goto :eof
















