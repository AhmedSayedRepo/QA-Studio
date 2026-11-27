@echo off
setlocal
rem publish_dist.bat [version] ["notes"]
rem Publishes the PUBLIC download release for the commit currently checked
rem out (HEAD) to the releases-only repo. release.bat calls this after its own
rem release; run it by hand to retry a failed publish or to publish the
rem current version for the first time. Safe to repeat: it re-uploads.
rem
rem Assets: qa-studio-src.zip (git archive of HEAD, one top folder),
rem         VERSION, install.bat, SHA256SUMS (checked by the self-update).
cd /d "%~dp0"
set "DIST=AhmedSayedRepo/QA-Studio-Releases"
set "VER=%~1"
if "%VER%"=="" if exist VERSION set /p VER=<VERSION
if "%VER%"=="" echo [FAIL] No version given and no VERSION file. & exit /b 1
set "NOTES=%~2"
if "%NOTES%"=="" set "NOTES=QA Studio v%VER%"

where gh >nul 2>&1
if errorlevel 1 echo [FAIL] GitHub CLI ^(gh^) is not installed. & exit /b 1
gh repo view %DIST% >nul 2>&1
if errorlevel 1 (
  echo [FAIL] Can't see %DIST%. Create it once with:
  echo          gh repo create %DIST% --public --add-readme --description "QA Studio downloads"
  exit /b 1
)

echo Publishing v%VER% downloads to %DIST%...
if exist dist rmdir /s /q dist
mkdir dist
git archive --format=zip --prefix=QA-Studio/ -o dist\qa-studio-src.zip HEAD
if errorlevel 1 echo [FAIL] git archive failed. & exit /b 1
copy /y VERSION dist\VERSION >nul
copy /y install.bat dist\install.bat >nul
powershell -NoProfile -Command "Set-Location dist; $o = foreach($f in 'qa-studio-src.zip','VERSION','install.bat'){ '{0}  {1}' -f (Get-FileHash -Algorithm SHA256 $f).Hash.ToLower(), $f }; [IO.File]::WriteAllText((Join-Path (Get-Location) 'SHA256SUMS'), (($o -join [char]10) + [char]10))"
if errorlevel 1 echo [FAIL] Couldn't write SHA256SUMS. & exit /b 1

gh release view v%VER% --repo %DIST% >nul 2>&1
if errorlevel 1 (
  gh release create v%VER% dist\qa-studio-src.zip dist\VERSION dist\SHA256SUMS dist\install.bat --repo %DIST% --title "QA Studio v%VER%" --notes "%NOTES%" --latest
) else (
  gh release upload v%VER% dist\qa-studio-src.zip dist\VERSION dist\SHA256SUMS dist\install.bat --repo %DIST% --clobber
)
if errorlevel 1 echo [FAIL] gh couldn't publish the release. & exit /b 1
gh release view v%VER% --repo %DIST% >nul 2>&1
if errorlevel 1 echo [FAIL] Release v%VER% isn't visible on %DIST% yet. & exit /b 1
rmdir /s /q dist >nul 2>&1
echo [OK] https://github.com/%DIST%/releases/tag/v%VER%
endlocal
exit /b 0
