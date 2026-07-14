@echo off
setlocal
cd /d "%~dp0"

rem ── QA Studio release helper ───────────────────────────────────────────────
rem 1) bump VERSION   2) commit + tag + push (push.ps1)
rem 3) create the GitHub Release and attach install.bat (the one-file installer)
rem 4) VERIFY the release actually landed on GitHub before saying "Done"
rem
rem INCIDENT this script is fixed against: VERSION climbed from 2.1.1 to 3.0.5
rem across many sessions while the last PUBLISHED GitHub Release stayed frozen
rem at v2.1.1 — six versions of drift, silently, because release.bat used to
rem check for `gh` AFTER already bumping VERSION/committing/tagging/pushing,
rem so a missing/unauthenticated `gh` just printed a [warn] (easy to miss in a
rem scrolling terminal) and exited — but the git side had already "succeeded",
rem so nothing about a plain `git log`/VERSION looked wrong. Worse, once `gh`
rem WAS present, the script printed "Done. Share this link..." unconditionally
rem at the end, even on the branch where `gh release create` AND its fallback
rem `gh release upload` both failed — there was no real success check at all.
rem Net effect: the landing page's version badge and the "Download installer"
rem button quietly kept serving v2.1.1 for months. Fixed two ways below:
rem   (a) the gh presence+auth check now runs FIRST, before any repo changes,
rem       so a broken publish step aborts loudly with nothing bumped/pushed
rem       yet instead of leaving git ahead of GitHub Releases.
rem   (b) every gh call's actual exit code is captured and checked; the
rem       script only ever prints success after gh release view CONFIRMS the
rem       release exists — no more optimistic "Done" on a failed publish.

echo Checking GitHub CLI...
where gh >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ABORT] GitHub CLI ^(gh^) is not installed - nothing has been changed yet.
  echo         Install it:   winget install --id GitHub.cli
  echo         Then sign in: gh auth login
  echo         Then re-run this script.
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
    echo [ABORT] Still not signed in - nothing has been changed yet.
    echo         Re-run this script once gh auth login has completed.
    pause
    exit /b 1
  )
)
echo [OK] GitHub CLI is installed and authenticated.
echo.

set /p VER=New version (e.g. 2.0.8):
if "%VER%"=="" echo No version entered. & pause & exit /b 1

rem --- refuse to re-publish a version whose GitHub Release already exists,
rem     rather than silently falling through to a confusing gh error later. ---
gh release view v%VER% >nul 2>&1
if not errorlevel 1 (
  echo.
  echo [ABORT] GitHub release v%VER% already exists:
  echo         https://github.com/AhmedSayedRepo/QA-Studio/releases/tag/v%VER%
  echo         Pick a different version, or if you really mean to replace its
  echo         asset only, run:  gh release upload v%VER% install.bat --clobber
  pause
  exit /b 1
)

set /p MSG=Commit message:
if "%MSG%"=="" set "MSG=Release v%VER%"

rem --- write a clean VERSION file (no trailing space/newline) ---
<nul set /p="%VER%" > VERSION

rem --- make sure install.bat uses CRLF so the downloaded asset never flash-closes ---
powershell -NoProfile -Command "$p='install.bat'; $t=Get-Content -Raw $p; Set-Content -Path $p -Value $t -Encoding ascii"

rem --- commit + tag + push (existing helper) ---
powershell -ExecutionPolicy Bypass -File "%~dp0push.ps1" "%MSG%"
if errorlevel 1 ( echo. & echo push.ps1 failed - fix the error above and retry. & pause & exit /b 1 )

echo.
echo Creating GitHub release v%VER% and attaching install.bat...
gh release create v%VER% install.bat --title "QA Studio v%VER%" --notes "%MSG%"
set "CREATE_RC=%errorlevel%"

if not "%CREATE_RC%"=="0" (
  echo.
  echo [info] gh release create failed - trying gh release upload instead
  echo        ^(covers the case where push.ps1 already made the tag, e.g. a
  echo        retry after this script failed partway through^)...
  gh release upload v%VER% install.bat --clobber
  set "CREATE_RC=%errorlevel%"
)

if not "%CREATE_RC%"=="0" (
  echo.
  echo [FAIL] Could not publish the GitHub release for v%VER% - see the gh
  echo        errors above. VERSION/commit/tag were already pushed, so do NOT
  echo        bump VERSION again to retry - just fix the gh problem ^(usually
  echo        `gh auth login`^) and re-run:
  echo          gh release create v%VER% install.bat --title "QA Studio v%VER%" --notes "%MSG%"
  pause
  exit /b 1
)

rem --- verify: don't trust the exit code above alone, confirm the release is
rem     actually visible on GitHub before calling this a success. ---
echo.
echo Verifying the release is live...
gh release view v%VER% >nul 2>&1
if errorlevel 1 (
  echo [FAIL] gh reported success but v%VER% isn't visible on GitHub yet.
  echo        Wait a few seconds and check yourself:
  echo          gh release view v%VER%
  pause
  exit /b 1
)

echo.
echo [OK] Verified: v%VER% is live and matches VERSION.
echo Share this link - users download install.bat and run it:
echo   https://github.com/AhmedSayedRepo/QA-Studio/releases/latest
echo Tag page:
echo   https://github.com/AhmedSayedRepo/QA-Studio/releases/tag/v%VER%
pause
endlocal
