@echo off
setlocal
cd /d "%~dp0"

rem == QA Studio APK build helper (mirrors release.bat's discipline) ==========
rem 1) verify gh is installed + authenticated BEFORE touching anything
rem 2) optionally commit + push pending changes (push.ps1 — the build runs
rem    whatever is on GitHub's main branch, NOT your local working tree!)
rem 3) dispatch the build-apk.yml workflow and WAIT for it (exit-status aware)
rem 4) download the APK artifact into apk-out\ and VERIFY the file exists
rem    before ever saying "Done" — no optimistic success prints.

echo Checking GitHub CLI...
where gh >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ABORT] GitHub CLI ^(gh^) is not installed - nothing has been changed.
  echo         Install it:   winget install --id GitHub.cli
  echo         Then sign in: gh auth login
  pause
  exit /b 1
)
gh auth status >nul 2>&1
if errorlevel 1 (
  echo.
  echo [info] gh is installed but not signed in - launching gh auth login...
  echo        Follow the prompts ^(pick GitHub.com, HTTPS, and "Login with a
  echo        web browser" when asked^) to finish signing in.
  echo.
  gh auth login
  echo.
  gh auth status >nul 2>&1
  if errorlevel 1 (
    echo.
    echo [ABORT] Still not signed in - nothing has been changed.
    echo         Re-run this script once gh auth login has completed.
    pause
    exit /b 1
  )
)
echo [OK] GitHub CLI is installed and authenticated.
echo.

rem --- the workflow builds MAIN on GitHub, not your local files ---------------
rem goto-style flow on purpose: a `set /p` prompt inside a parenthesized if-
rem block is a batch trap — a bare ) in the prompt text closes the block
rem mid-line (killed the window on the first version of this script), and
rem %var% inside a block expands at PARSE time so the answer gets ignored.
for /f %%i in ('git status --porcelain ^| find /c /v ""') do set DIRTY=%%i
if "%DIRTY%"=="0" goto dispatch
echo You have %DIRTY% uncommitted change^(s^). The APK is built from GitHub's
echo main branch - local changes NOT pushed will NOT be in the APK.
set /p PUSHNOW=Commit and push them first? [y/n]:
if /i not "%PUSHNOW%"=="y" (
  echo [info] Building WITHOUT local changes - main as-is.
  goto dispatch
)
set /p MSG=Commit message:
if "%MSG%"=="" set "MSG=APK build"
rem Plain commit+push — deliberately NOT push.ps1: that helper is release-
rem coupled (reads VERSION and creates the vX.Y.Z tag, and fails when the tag
rem already exists — hit live on this script's first run). An APK build must
rem never bump/tag versions; that's release.bat's job.
git add -A
git commit -m "%MSG%"
if errorlevel 1 ( echo. & echo git commit failed - see the error above. & pause & exit /b 1 )
git push
if errorlevel 1 ( echo. & echo git push failed - see the error above. & pause & exit /b 1 )

:dispatch
echo.
echo Dispatching the APK build workflow...
gh workflow run build-apk.yml
if errorlevel 1 ( echo. & echo [ABORT] Could not dispatch build-apk.yml. & pause & exit /b 1 )

rem --- give GitHub a moment to register the run, then grab its id ------------
timeout /t 6 /nobreak >nul
set RUNID=
for /f %%i in ('gh run list --workflow=build-apk.yml --limit 1 --json databaseId -q ".[0].databaseId"') do set RUNID=%%i
if "%RUNID%"=="" (
  echo [ABORT] Could not find the dispatched run - check: gh run list --workflow=build-apk.yml
  pause
  exit /b 1
)

echo Waiting for run %RUNID% (build takes ~6-10 minutes)...
gh run watch %RUNID% --exit-status
if errorlevel 1 (
  echo.
  echo [FAIL] The APK build failed. Inspect it with:
  echo          gh run view %RUNID% --log-failed
  pause
  exit /b 1
)

echo.
echo Downloading the APK artifact...
if exist apk-out rmdir /s /q apk-out
gh run download %RUNID% -n qa-studio-apk -D apk-out
if errorlevel 1 ( echo. & echo [FAIL] Artifact download failed. & pause & exit /b 1 )

rem --- verify: never print success unless the .apk actually exists -----------
set APK=
for /f "delims=" %%i in ('dir /b /s apk-out\*.apk 2^>nul') do set APK=%%i
if "%APK%"=="" (
  echo [FAIL] Download reported success but no .apk found under apk-out\.
  pause
  exit /b 1
)

echo.
echo [OK] Verified: APK built and downloaded.
echo   %APK%
echo Send it to the phone (USB / Drive / message-to-self) and open it to install.
echo (apk-out\ is git-ignored - safe to leave or delete.)
pause
endlocal
