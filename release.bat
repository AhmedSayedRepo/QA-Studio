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

rem --- show what's already published before asking for a number -------------
rem Without this you're picking the next version from memory, which is how the
rem original incident (VERSION climbing 2.1.1 -> 3.0.5 with nothing released)
rem went unnoticed for four attempts. Two values, shown separately because the
rem difference is the useful part:
rem   VERSION file   - what this working copy claims
rem   Latest release - what actually exists on GitHub
rem A gap between them means a bump that never published.
rem
rem goto-style flow and a :next_patch subroutine on purpose — same trap this
rem script's sibling documents: a bare ) in prompt text closes a parenthesized
rem block mid-line and kills the window, and %var% inside a block expands at
rem PARSE time so the answer would be ignored.
echo --- Current release state ---------------------------------------------
set "CUR=(none)"
if exist VERSION set /p CUR=<VERSION
echo   VERSION file      %CUR%
for /f "usebackq delims=" %%T in (`gh release view --json tagName -q .tagName 2^>nul`) do set "LAST=%%T"
if not defined LAST set "LAST=(none)"
echo   Latest release    %LAST%
echo -----------------------------------------------------------------------
echo.

set "SUGGEST="
call :next_patch "%CUR%" SUGGEST
if not defined SUGGEST goto ask_plain
set /p VER=New version [%SUGGEST%]:
if "%VER%"=="" set "VER=%SUGGEST%"
goto have_version

:ask_plain
set /p VER=New version, e.g. 2.0.8:
:have_version
if "%VER%"=="" echo No version entered. & pause & exit /b 1
echo Using version %VER%.

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

rem --- check exact-version release notes before changing VERSION ------------
rem The updater deliberately has no fallback notes: showing highlights from an
rem older release under a new version is worse than showing no highlights. This
rem is an ADVISORY check only: a missing note warns clearly, but never blocks a
rem valid release, tag, or installer deployment.
where py >nul 2>&1
if errorlevel 1 goto check_notes_with_python
py -3 "%~dp0_check_release_notes.py" "%VER%" >nul 2>&1
if errorlevel 1 goto notes_not_ready
goto notes_ready

:check_notes_with_python
where python >nul 2>&1
if errorlevel 1 goto notes_python_missing
python "%~dp0_check_release_notes.py" "%VER%" >nul 2>&1
if errorlevel 1 goto notes_not_ready
goto notes_ready

:notes_python_missing
echo.
echo [WARN] Python was not found, so release notes were not checked.
echo        You can still release. Add notes later in release_notes.py and
echo        strings.py so the update popup can describe this version.
goto notes_ready

:notes_not_ready
echo.
echo [WARN] Release v%VER% has no complete exact-version release notes.
echo        Continuing with the release. The update popup will omit highlights
echo        until notes are added for this exact version in all seven languages.
goto notes_ready

:notes_ready
echo [OK] Release-note check complete (advisory only).

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

rem --- Android APK: NOT attached here anymore -------------------------------
rem Used to upload apk-out\qa-studio.apk to THIS release (v%VER%) — but
rem index.html's download link and the in-app update-available check both
rem now point at a dedicated, permanent "android-apk" release instead (see
rem build-apk.yml's own "Publish to rolling Android release" step, which
rem re-uploads there automatically on every successful CI build). Attaching
rem it here too just duplicated the same file onto two releases with nothing
rem ever reading the copy on this one — confusing, not incorrect. If you want
rem the newly-bumped VERSION baked into the next APK, run build-apk.bat
rem AFTER this script (it builds from whatever is on `main` right now,
rem which by then includes the bump this script just pushed).

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
rem Explicit exit so the successful path never falls through into the helper
rem label below.
exit /b 0

:next_patch
rem %1 = current version, %2 = variable to receive current-with-patch-bumped.
rem Leaves %2 untouched if %1 isn't a parseable x.y.z, so a malformed or missing
rem VERSION file falls back to the plain prompt rather than suggesting nonsense.
set "_MAJ="
set "_MIN="
set "_PAT="
for /f "tokens=1,2,3 delims=." %%a in ("%~1") do set "_MAJ=%%a" & set "_MIN=%%b" & set "_PAT=%%c"
if not defined _PAT exit /b 0
set /a "_NEXT=_PAT+1" >nul 2>&1
if errorlevel 1 exit /b 0
call set "%~2=%_MAJ%.%_MIN%.%_NEXT%"
exit /b 0
