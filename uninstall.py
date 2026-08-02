"""uninstall.py - remove QA Studio (per-user install).

Deletes the Desktop shortcut, removes the HKCU "Installed apps" registry entry,
then deletes the install folder (%LOCALAPPDATA%\\QA Studio). Because this script
lives inside that folder, the actual directory removal is handed off to a
detached cmd that waits a moment (so this process exits and releases the folder)
and then deletes it.

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


def _remove_shortcut():
    if os.name != "nt":
        return
    ps = (
        "$d=[Environment]::GetFolderPath('Desktop'); "
        "if(-not $d -or -not (Test-Path $d)){ $d=Join-Path $env:USERPROFILE 'Desktop' }; "
        f"$l=Join-Path $d '{APP_NAME}.lnk'; "
        "if(Test-Path $l){ Remove-Item -Force $l }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            creationflags=0x08000000, check=False)
    except Exception:
        pass


def _remove_registry():
    if os.name != "nt":
        return
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _REG_KEY)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _notify_done():
    if QUIET or os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, f"{APP_NAME} has been removed.", APP_NAME, 0x40)  # MB_ICONINFORMATION
    except Exception:
        pass


def _schedule_folder_delete():
    if os.name != "nt":
        try:
            import shutil
            shutil.rmtree(HERE, ignore_errors=True)
        except Exception:
            pass
        return
    # Detach a cmd (CWD set outside the install folder) that waits ~2s for this
    # process to exit, then removes the install directory.
    DETACHED = 0x00000008
    NEW_GROUP = 0x00000200
    cmd = 'ping 127.0.0.1 -n 3 >nul & rmdir /s /q "%s"' % HERE
    try:
        subprocess.Popen(["cmd", "/c", cmd], cwd=tempfile.gettempdir(),
                         creationflags=DETACHED | NEW_GROUP, close_fds=True)
    except Exception:
        pass


def main():
    if not _confirm():
        return
    _remove_shortcut()
    _remove_registry()
    _notify_done()
    _schedule_folder_delete()


if __name__ == "__main__":
    main()
