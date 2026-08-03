"""uninstall.py - remove QA Studio (per-user install).

Removes, in order:
  1. any running QA Studio instance (so its folder/files aren't locked),
  2. the Desktop / Start-Menu shortcut (checking EVERY known desktop location,
     including OneDrive-redirected ones, then refreshing the shell so no ghost
     icon lingers),
  3. the HKCU "Installed apps" registry entry,
  4. the install folder itself (%LOCALAPPDATA%\\QA Studio) - handed off to a
     detached cmd that waits for this process to exit, retries, and writes the
     result to %TEMP%\\qastudio_uninstall.log so it's verifiable.

Invoked by Windows "Installed apps" -> Uninstall (the UninstallString written by
installer.py), or run directly. Pass /quiet to skip the confirmation dialogs.
"""
import os
import sys
import subprocess
import tempfile

APP_NAME = "QA Studio"
HERE = os.path.dirname(os.path.abspath(__file__))
QUIET = any(a.lower() == "/quiet" for a in sys.argv[1:])
_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\QAStudio"
NOWIN = 0x08000000  # CREATE_NO_WINDOW
LOG = os.path.join(tempfile.gettempdir(), "qastudio_uninstall.log")


def _log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[uninstall] {msg}\n")
    except Exception:
        pass


def _ps(script):
    """Run a PowerShell script windowless; return (rc, stdout, stderr)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, creationflags=NOWIN, timeout=30)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 1, "", str(e)


def _confirm():
    if QUIET or os.name != "nt":
        return True
    try:
        import ctypes
        # MB_YESNO (0x4) | MB_ICONQUESTION (0x20); IDYES == 6
        return ctypes.windll.user32.MessageBoxW(
            0, f"Remove {APP_NAME} and its files?", f"Uninstall {APP_NAME}", 0x24) == 6
    except Exception:
        return True


def _kill_running_app():
    """Terminate any python/pythonw running THIS folder's main.py, excluding our
    own PID, so the install directory and its files aren't locked during removal."""
    if os.name != "nt":
        return
    me = os.getpid()
    folder = HERE.lower()
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
        "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }")
    _, out, _ = _ps(script)
    for line in out.splitlines():
        pid, _, cmd = line.partition("|")
        pid = pid.strip()
        if not pid.isdigit():
            continue
        pid = int(pid)
        low = (cmd or "").lower()
        if pid != me and "main.py" in low and folder in low:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               creationflags=NOWIN, check=False, timeout=10)
                _log(f"killed running app pid {pid}")
            except Exception:
                pass


def _remove_shortcuts():
    """Delete the QA Studio shortcut from every known Desktop / Start-Menu
    location (incl. OneDrive-redirected desktops), then refresh the shell so a
    ghost icon doesn't linger on the desktop."""
    if os.name != "nt":
        return
    script = r"""
$targets = @()
$dirs = @(
  [Environment]::GetFolderPath('Desktop'),
  [Environment]::GetFolderPath('CommonDesktopDirectory'),
  [Environment]::GetFolderPath('Programs'),
  [Environment]::GetFolderPath('CommonPrograms')
)
# Add OneDrive-redirected desktops ONLY when the env var exists. Passing an empty
# value to Join-Path throws a terminating error that would abort the whole list
# (which is why nothing was being removed on machines with only one OneDrive kind).
foreach ($base in @($env:USERPROFILE, $env:OneDrive, $env:OneDriveConsumer, $env:OneDriveCommercial)) {
  if ($base) { $dirs += (Join-Path $base 'Desktop') }
}
$dirs = $dirs | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
foreach ($d in $dirs) {
  $p = Join-Path $d 'QA Studio.lnk'
  if (Test-Path $p) { try { Remove-Item -Force $p -ErrorAction Stop; $targets += $p } catch {} }
}
# Force the desktop to repaint so any cached/ghost icon disappears.
try {
  $sig = '[DllImport("shell32.dll")] public static extern void SHChangeNotify(int e,int f,IntPtr a,IntPtr b);'
  $sh = Add-Type -MemberDefinition $sig -Name Shell32 -Namespace WinApi -PassThru
  $sh::SHChangeNotify(0x8000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)  # SHCNE_ASSOCCHANGED
} catch {}
Write-Output ("removed:" + ($targets -join ';'))
"""
    _, out, err = _ps(script)
    _log(f"shortcuts {out or err}")


def _remove_registry():
    if os.name != "nt":
        return
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _REG_KEY)
        _log("registry key removed")
    except FileNotFoundError:
        _log("registry key already absent")
    except Exception as e:
        _log(f"registry remove failed: {e}")


def _notify_done():
    if QUIET or os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, f"{APP_NAME} has been removed.\nRemaining files are cleaned up automatically.",
            APP_NAME, 0x40)  # MB_ICONINFORMATION
    except Exception:
        pass


def _schedule_folder_delete():
    """Delete the install folder. Since this script lives inside it, hand the
    removal to a detached cmd (CWD outside the folder) that waits for us to exit,
    retries once, and logs whether the folder is gone."""
    if os.name != "nt":
        try:
            import shutil
            shutil.rmtree(HERE, ignore_errors=True)
        except Exception:
            pass
        return
    DETACHED = 0x00000008
    NEW_GROUP = 0x00000200
    h = HERE
    bat = os.path.join(tempfile.gettempdir(), "qastudio_cleanup.bat")
    # Write a standalone .bat and run THAT, instead of passing a complex one-liner
    # (quotes + & + () + >> redirects) through `cmd /c`. subprocess re-quotes such
    # a string and Windows then mis-parses it, so the previous version silently did
    # nothing and left the folder in place. Batch-file contents are read literally,
    # so there is no quoting to mangle. Each step is its own simple line; the folder
    # is retried a few times (in case a handle is briefly still held) and the result
    # is logged, then the .bat deletes itself.
    script = (
        "@echo off\r\n"
        "ping 127.0.0.1 -n 4 >nul\r\n"
        f'rmdir /s /q "{h}"\r\n'
        f'if exist "{h}" ping 127.0.0.1 -n 4 >nul\r\n'
        f'if exist "{h}" rmdir /s /q "{h}"\r\n'
        f'if exist "{h}" ping 127.0.0.1 -n 6 >nul\r\n'
        f'if exist "{h}" rmdir /s /q "{h}"\r\n'
        f'if exist "{h}" (>>"{LOG}" echo [uninstall] REMAINED "{h}") else (>>"{LOG}" echo [uninstall] REMOVED "{h}")\r\n'
        '(goto) 2>nul & del "%~f0"\r\n'
    )
    try:
        with open(bat, "w", encoding="ascii", errors="ignore", newline="") as f:
            f.write(script)
        subprocess.Popen(["cmd", "/c", bat], cwd=tempfile.gettempdir(),
                         creationflags=DETACHED | NEW_GROUP, close_fds=True)
        _log(f"scheduled folder delete via {bat}")
    except Exception as e:
        _log(f"schedule delete failed: {e}")


def main():
    _log(f"uninstall start (HERE={HERE}, quiet={QUIET})")
    if not _confirm():
        _log("user cancelled")
        return
    _kill_running_app()
    _remove_shortcuts()
    _remove_registry()
    _notify_done()
    _schedule_folder_delete()
    _log("uninstall handoff complete")


if __name__ == "__main__":
    main()
