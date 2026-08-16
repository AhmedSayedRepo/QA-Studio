@echo off
setlocal
cd /d "%~dp0"

rem ── QA Studio one-file build ───────────────────────────────────────────────
rem Produces a single windowed exe at:  dist\QA Studio.exe
rem The exe self-updates via Help -> "check updates" (engine.apply_update detects
rem the frozen build, downloads the new exe from the latest GitHub release, and
rem swaps itself on next close).

rem 1) tooling
rem Pinned to 3.12 (see launch.bat/install.bat for why: `py -3` drifts to
rem whichever 3.x is highest on this machine, and flet==0.85.3 was never
rem validated against newer releases). Also stopped blindly `--upgrade`-ing
rem flet on every build — that silently overrides the flet==0.85.3 pin in
rem requirements.txt with whatever's newest on PyPI at build time, which
rem defeats the whole point of pinning it. Installs the exact pinned
rem versions from requirements.txt instead, plus pyinstaller (unpinned,
rem build-tool-only, not part of the shipped app).
where py >nul 2>&1 && (py -3.12 -m pip install --upgrade -r requirements.txt pyinstaller >nul 2>&1) || (py -3 -m pip install --upgrade -r requirements.txt pyinstaller >nul 2>&1)

rem 2) clean previous build
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

rem 3) build.  IMPORTANT:
rem    --add-data "VERSION;."   bundles VERSION so the app reports its version
rem    --add-data "assets;assets" bundles your icons/logo (edit/remove if your
rem      assets live elsewhere or aren't in an "assets" folder)
rem    --icon points at your .ico (edit the path if different)
flet pack main.py ^
  --name "QA Studio" ^
  --product-name "QA Studio" ^
  --icon "app.ico" ^
  --add-data "VERSION;." ^
  --add-data "app.ico;." ^
  --add-data "app.png;." ^
  --add-data "qa-logo.png;." ^
  --add-data "qa-logo-email.png;." ^
  --add-data "login_bg_dark.png;." ^
  --add-data "login_bg_light.png;."

echo.
if exist "dist\QA Studio.exe" (
  echo Built: dist\QA Studio.exe
) else (
  echo Build did not produce dist\QA Studio.exe - check the errors above.
)
pause
endlocal
