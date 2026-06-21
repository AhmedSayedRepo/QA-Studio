@echo off
setlocal
cd /d "%~dp0"

rem ── QA Studio release helper ───────────────────────────────────────────────
rem 1) bump VERSION   2) commit + tag + push (push.ps1)
rem 3) create the GitHub Release and attach install.bat (the one-file installer)

set /p VER=New version (e.g. 2.0.8): 
if "%VER%"=="" echo No version entered. & pause & exit /b 1

set /p MSG=Commit message: 
if "%MSG%"=="" set "MSG=Release v%VER%"

rem --- write a clean VERSION file (no trailing space/newline) ---
<nul set /p="%VER%" > VERSION

rem --- make sure install.bat uses CRLF so the downloaded asset never flash-closes ---
powershell -NoProfile -Command "$p='install.bat'; $t=Get-Content -Raw $p; Set-Content -Path $p -Value $t -Encoding ascii"

rem --- commit + tag + push (existing helper) ---
powershell -ExecutionPolicy Bypass -File "%~dp0push.ps1" "%MSG%"
if errorlevel 1 ( echo. & echo push.ps1 failed - fix the error above and retry. & pause & exit /b 1 )

rem --- need GitHub CLI to publish the release + upload the asset ---
where gh >nul 2>&1
if errorlevel 1 (
  echo.
  echo [warn] GitHub CLI ^(gh^) is not installed, so the release wasn't created.
  echo        Install it once:   winget install --id GitHub.cli
  echo        Sign in once:      gh auth login
  echo        Then run:          gh release create v%VER% install.bat --title "QA Studio v%VER%" --notes "%MSG%"
  pause
  exit /b 1
)

echo.
echo Creating GitHub release v%VER% and attaching install.bat...
gh release create v%VER% install.bat --title "QA Studio v%VER%" --notes "%MSG%"
if errorlevel 1 (
  echo.
  echo [info] Release v%VER% may already exist - uploading install.bat to it instead...
  gh release upload v%VER% install.bat --clobber
)

echo.
echo Done. Share this link - users download install.bat and run it:
echo   https://github.com/AhmedSayedRepo/QA-Studio/releases/latest
pause
endlocal
