@echo off
setlocal
cd /d "%~dp0"

rem ── QA Studio one-file build ───────────────────────────────────────────────
rem Produces a single windowed exe at:  dist\QA Studio.exe
rem The exe self-updates via Help -> "check updates" (engine.apply_update detects
rem the frozen build, downloads the new exe from the latest GitHub release, and
rem swaps itself on next close).

rem 1) tooling
py -3 -m pip install --upgrade flet pyinstaller >nul 2>&1

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
  --icon "assets\icon.ico" ^
  --add-data "VERSION;." ^
  --add-data "assets;assets"

echo.
if exist "dist\QA Studio.exe" (
  echo Built: dist\QA Studio.exe
) else (
  echo Build did not produce dist\QA Studio.exe - check the errors above.
)
pause
endlocal
