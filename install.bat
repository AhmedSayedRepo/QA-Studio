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

echo Preparing QA Studio installer...
%PY% -m pip install --quiet --disable-pip-version-check --upgrade pip >nul 2>&1

rem --- bootstrap the native-window backend BEFORE launching ---
rem pywebview needs a backend. On Windows that is EdgeChromium (WebView2) via
rem pythonnet. We install BOTH and do NOT hide errors, so a failed backend is
rem visible instead of silently falling back to a browser tab.
echo Installing native window backend (pywebview)...
%PY% -m pip install --disable-pip-version-check "pywebview>=5.0" pythonnet
if errorlevel 1 (
  echo.
  echo [warn] Could not install the native-window backend. The installer will
  echo        open in a chromeless app window or your browser instead.
  echo.
)

rem --- launch the installer ---
%PY% "%~dp0installer.py"

endlocal
