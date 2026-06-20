@echo off
setlocal
cd /d "%~dp0"

set /p VER=New version (e.g. 1.9.3): 
if "%VER%"=="" echo No version entered. & pause & exit /b 1

set /p MSG=Commit message: 
if "%MSG%"=="" set "MSG=Release v%VER%"

rem write a clean VERSION file (no trailing space/newline)
<nul set /p="%VER%" > VERSION

powershell -ExecutionPolicy Bypass -File "%~dp0push.ps1" "%MSG%"

pause
endlocal
