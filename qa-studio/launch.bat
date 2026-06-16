@echo off
:: Launches QA Studio with no console window.
set "PYW="
for /f "delims=" %%P in ('py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%P"
if not defined PYW (
    for /f "delims=" %%P in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%P"
)
if not defined PYW set "PYW=pythonw.exe"
start "" "%PYW%" "%~dp0main.py"
