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

rem --- find pythonw (windowless) to launch the GUI without a console box ---
set "PYW=pyw -3"
where pyw >nul 2>&1 || set "PYW=pythonw"
%PYW% --version >nul 2>&1 || set "PYW=%PY%"

echo Preparing QA Studio installer...
%PY% -m pip install --quiet --disable-pip-version-check --upgrade pip >nul 2>&1

rem --- bootstrap the native-window backend BEFORE launching (visible so any
rem     error is shown). pywebview needs the EdgeChromium/WebView2 backend via
rem     pythonnet on Windows. ---
echo Installing native window backend (pywebview)...
%PY% -m pip install --disable-pip-version-check "pywebview>=5.0" pythonnet
if errorlevel 1 (
  echo.
  echo [warn] Could not install the native-window backend. The installer will
  echo        open in a chromeless app window instead.
  echo.
)

rem --- launch the installer GUI WITHOUT a console window (pythonw). The window
rem     is native via pywebview; if that's unavailable it opens a chromeless
rem     Edge/Chrome app window. No black console box is left behind. ---
start "" %PYW% "%~dp0installer.py"

endlocal
