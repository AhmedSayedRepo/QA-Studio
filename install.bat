@echo off
setlocal
cd /d "%~dp0"

rem --- find Python (prefer the py launcher, else python on PATH) ---
set "PY=py -3"
where py >nul 2>&1 || set "PY=python"
%PY% --version >nul 2>&1 || (
  echo Python 3 was not found. Install it from https://www.python.org/downloads/ ^(tick "Add to PATH"^) and re-run.
  pause
  exit /b 1
)

rem --- bootstrap the native-window backend BEFORE launching ---
echo Preparing QA Studio installer...
%PY% -m pip install --quiet --disable-pip-version-check --upgrade pip >nul 2>&1
%PY% -m pip install --quiet --disable-pip-version-check pywebview >nul 2>&1

rem --- launch the installer (opens a native window; falls back to a browser
rem     window only if the native backend is unavailable) ---
%PY% "%~dp0installer.py"

endlocal