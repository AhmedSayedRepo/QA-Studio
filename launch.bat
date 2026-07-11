@echo off
:: Launches QA Studio with no console window.
:: Pinned to 3.12 first — QA Studio is built/tested against 3.12 (flet==0.85.3
:: in requirements.txt was never validated on newer Python releases). Bare
:: `py -c ...` below resolves to whichever 3.x is HIGHEST on this machine, so
:: installing any newer Python later (seen live: Node.js/Playwright setup
:: silently pulled in a newer "latest" Python) retargets this launch to that
:: interpreter with no warning, even though flet stays pinned at 0.85.3 there
:: too — same package version, different/less-tested Python minor version.
cd /d "%~dp0"
:: Nuke cached bytecode before every launch. This folder lives under Downloads,
:: which is commonly OneDrive-synced — mtime-based .pyc invalidation can be
:: unreliable across cloud sync/clock skew, so a stale __pycache__ can make
:: Python silently keep running OLD compiled code even after main.py's source
:: files were edited and saved. Forcing a clean recompile on every launch
:: costs well under a second and permanently rules this failure mode out.
if exist "__pycache__" rmdir /s /q "__pycache__" >nul 2>&1
set "PYW="
for /f "delims=" %%P in ('py -3.12 -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%P"
if not defined PYW (
    for /f "delims=" %%P in ('py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%P"
)
if not defined PYW (
    for /f "delims=" %%P in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%P"
)
if not defined PYW set "PYW=pythonw.exe"
start "" "%PYW%" "%~dp0main.py"
