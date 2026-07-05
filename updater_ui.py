"""updater_ui.py — self-update UI (banner, apply, restart flow).

Extracted from main.py (Step-7 modular refactor). Functions take the QAStudio
app; the app keeps thin delegator methods so render() and in-flow callbacks
(self._update_banner / self._do_update / ...) keep working.
"""
import os, sys, threading, time, subprocess
import flet as ft
import theme as T
import engine as E
from ui import ghost_btn, green_btn


def banner(app):
    """A slim banner shown at the top when a newer version is available."""
    info = app._update_info or {}
    if not info.get("update") or app._update_dismissed:
        return None
    remote = info.get("remote", "")
    local = info.get("local", "")
    if app._updating:
        inner = ft.Row([
            ft.ProgressRing(width=16, height=16, stroke_width=2, color=T.VIOLET_INK),
            ft.Text("Updating…", size=12.5, color=T.VIOLET_INK, weight=ft.FontWeight.BOLD),
        ], spacing=10)
    else:
        update_btn = ft.Container(
            ft.Row([
                ft.Icon(ft.Icons.DOWNLOAD, size=16, color="#FFFFFF"),
                ft.Text("Update now", size=13, color="#FFFFFF",
                        weight=ft.FontWeight.BOLD),
            ], spacing=8, tight=True),
            bgcolor=T.VIOLET, border_radius=T.R_SM,
            padding=ft.Padding.symmetric(horizontal=18, vertical=11),
            on_click=lambda e: app._do_update(), ink=True,
            tooltip="Download and install the latest version",
            animate_scale=ft.Animation(110, ft.AnimationCurve.EASE_OUT),
            shadow=ft.BoxShadow(blur_radius=14, spread_radius=-4,
                                offset=ft.Offset(0, 4),
                                color=ft.Colors.with_opacity(0.35, T.VIOLET)))

        def _btn_hover(e, _b=update_btn):
            on = e.data in (True, "true", "True")
            _b.bgcolor = T.VIOLET_H if on else T.VIOLET
            _b.scale = 1.04 if on else 1.0
            try: _b.update()
            except Exception: pass
        update_btn.on_hover = _btn_hover
        inner = ft.Row([
            ft.Container(ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, size=17, color=T.VIOLET_INK),
                         width=32, height=32, border_radius=9,
                         bgcolor=ft.Colors.with_opacity(0.16, T.VIOLET),
                         alignment=ft.Alignment.CENTER),
            ft.Column([
                ft.Text("Update available", size=12.5, color=T.VIOLET_INK,
                        weight=ft.FontWeight.BOLD),
                ft.Text(f"Version {remote} is ready \u2014 you\u2019re on {local}",
                        size=11, weight=ft.FontWeight.W_500,
                        color=ft.Colors.with_opacity(0.85, T.VIOLET_INK)),
            ], spacing=1, expand=True),
            update_btn,
            ft.IconButton(ft.Icons.CLOSE, icon_size=16, icon_color=T.VIOLET_INK,
                          tooltip="Dismiss",
                          on_click=lambda e: app._dismiss_update()),
        ], spacing=13, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    # Theme-adaptive surface (soft cyan in light, dark teal in dark) with a CRISP
    # bottom rule — NO blurry drop shadow, which was the soft "uncrisp / empty
    # reserved" band under the banner.
    return ft.Container(inner, bgcolor=T.VIOLET_SOFT,
                        padding=ft.Padding.symmetric(horizontal=18, vertical=11),
                        border=ft.Border.only(bottom=ft.BorderSide(1.5, T.VIOLET)))

def dismiss_update(app):
    app._update_dismissed = True
    app.render()

def do_update(app):
    app._updating = True
    app.render()

    def work():
        ok, msg = E.apply_update(cb=lambda m, t="dim": None)
        def finish():
            app._updating = False
            if ok:
                app._update_info = {"update": False}
                app._update_dismissed = True
                app._show_restart_dialog(msg)
            else:
                app.render()
                app._show_update_error(msg)
        app.ui_safe(finish)
    app._bg(work)

def show_update_error(app, msg):
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([ft.Icon(ft.Icons.ERROR_OUTLINE, color=T.RED, size=20),
                      ft.Text("Update failed", weight=ft.FontWeight.BOLD, size=16)],
                     spacing=8, tight=True),
        content=ft.Container(
            ft.Column([
                ft.Text(msg, size=12.5, color=T.INK, selectable=True),
                ft.Container(height=6),
                ft.Text("If a new .exe isn't attached to the latest GitHub "
                        "release, the app can't app-update — attach it as a "
                        "release asset and try again.",
                        size=11.5, color=T.INK_3, weight=ft.FontWeight.W_500),
            ], spacing=2, tight=True), width=460),
        actions=[green_btn("OK", on_click=lambda e: app._close_dialog())],
        actions_alignment=ft.MainAxisAlignment.END)
    app._show_dialog(dlg)

def show_restart_dialog(app, msg):
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE, color=T.GREEN, size=22),
                      ft.Text("Update complete", weight=ft.FontWeight.BOLD, size=17,
                              color=T.INK)],
                     spacing=10, tight=True),
        content=ft.Container(
            ft.Column([
                ft.Text("QA Studio has been updated to the latest version.",
                        size=13.5, color=T.INK, weight=ft.FontWeight.BOLD),
                ft.Container(height=6),
                ft.Text("It will restart to finish updating — closing and reopening "
                        "on the new version automatically.",
                        size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500),
                ft.Container(height=22),
                ft.Row([
                    ghost_btn("Later", on_click=lambda e: app._close_dialog()),
                    green_btn("Restart now", icon=ft.Icons.RESTART_ALT,
                              on_click=lambda e: app._restart_app(), height=46),
                ], spacing=10, alignment=ft.MainAxisAlignment.END,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=2, tight=True),
            width=430),
        actions_alignment=ft.MainAxisAlignment.END,
    )
    app._show_dialog(dlg)

def quit_after_update(app):
    """Simply close the app cleanly so the user can reopen the updated version.
    No auto-relaunch (that proved unreliable across Flet/Windows builds)."""
    app._close_dialog()
    app._run_active = False
    app._auto_running = False
    app._restart_close()

def restart_app(app):
    """Relaunch cleanly. We spawn a helper that is REPARENTED out of our
    process tree (via `start`), so the `taskkill /T` we run on ourselves
    below can't kill it. The helper waits for THIS pid to exit, then starts
    a fresh app."""
    app._close_dialog()
    try:
        import sys, os, subprocess, tempfile
        app_dir = os.path.dirname(os.path.abspath(__file__))
        main_py = os.path.join(app_dir, "main.py")
        pyw = sys.executable
        try:
            cand = os.path.join(os.path.dirname(pyw), "pythonw.exe")
            if os.path.exists(cand):
                pyw = cand
        except Exception:
            pass
        pid = os.getpid()
        if os.name == "nt":
            bat = os.path.join(tempfile.gettempdir(), "qastudio_relaunch.bat")
            script = ("@echo off\r\n"
                      f'set "PID={pid}"\r\n'
                      ":wait\r\n"
                      'tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul '
                      "&& (ping -n 2 127.0.0.1 >nul & goto wait)\r\n"
                      "ping -n 2 127.0.0.1 >nul\r\n"
                      f'start "" /d "{app_dir}" "{pyw}" "{main_py}"\r\n'
                      'del "%~f0" >nul 2>&1\r\n')
            with open(bat, "w", encoding="ascii", errors="ignore", newline="") as f:
                f.write(script)
            DETACHED, NEW_GROUP = 0x00000008, 0x00000200
            # `cmd /c start … cmd /c bat` reparents the helper away from us.
            subprocess.Popen(["cmd", "/c", "start", "", "/min", "cmd", "/c", bat],
                             creationflags=DETACHED | NEW_GROUP, close_fds=True)
        else:
            subprocess.Popen([pyw, main_py], cwd=app_dir, start_new_session=True)
    except Exception:
        pass
    app._run_active = False
    app._auto_running = False
    app._restart_close()

def restart_close(app):
    """Close this process tree (old window + its flet client)."""
    try:
        if hasattr(app.page, "window") and app.page.window is not None:
            try:
                app.page.window.prevent_close = False
            except Exception:
                pass
            app.page.window.destroy()
    except Exception:
        pass
    def _hard():
        import os, time
        time.sleep(0.4)
        if os.name == "nt":
            try:
                import subprocess
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(os.getpid())],
                               creationflags=0x08000000, check=False)
                return
            except Exception:
                pass
        try:
            import signal
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            try:
                import sys; sys.exit(0)
            except Exception:
                pass
    try:
        threading.Thread(target=_hard, daemon=True).start()
    except Exception:
        _hard()

