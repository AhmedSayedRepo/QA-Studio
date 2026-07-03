"""window_chrome.py — frameless window chrome (title strip, controls, close).

Extracted from main.py (Step-8 modular refactor). Functions take the QAStudio
app; the app keeps thin delegator methods so render() and close handlers keep
working. force_close is still reachable as app._force_close (used by updater_ui.restart_close and the window close button).
"""
import os, sys, threading, time, subprocess
import flet as ft
import theme as T
from ui import ghost_btn, danger_btn


def with_window_chrome(app, root):
    """Overlay a draggable top strip + minimize/maximize/close buttons for the
    frameless window (the OS title bar is hidden), so the background can fill
    the entire window. Applies to every screen. Fully guarded."""
    win = getattr(app.page, "window", None)
    if win is None:
        return root

    def _min(e):
        try:
            win.minimized = True; app.page.update()
        except Exception:
            pass

    def _tmax(e):
        try:
            win.maximized = not bool(getattr(win, "maximized", False))
            app.page.update()
        except Exception:
            pass

    def _close(e):
        # Frameless window: this custom button is the only close path (there's no
        # native X), so the run-in-progress confirm must happen HERE. If a run or
        # automation is active, show the confirm dialog (stop-after-current);
        # otherwise close immediately.
        try:
            if getattr(app, "_run_active", False) or getattr(app, "_auto_running", False):
                app._confirm_close()
                return
        except Exception:
            pass
        # Use the app's own shutdown: it destroys the Flet CLIENT window first,
        # then taskkills the process tree. (Calling os._exit here killed Python
        # but orphaned the client window -> the stuck "Working…" screen.)
        try:
            app._force_close()
        except Exception:
            try:
                win.destroy()
            except Exception:
                pass

    def _wb(icon, cb, danger=False):
        cc = ft.Container(ft.Icon(icon, size=15, color=T.INK_2),
                          width=46, height=32, alignment=ft.Alignment.CENTER,
                          ink=True, on_click=cb, border_radius=6)

        def _h(e, _c=cc, _d=danger):
            try:
                on = e.data in (True, "true", "True")
                _c.bgcolor = (("#E0474D" if _d else ft.Colors.with_opacity(0.12, T.INK))
                              if on else None)
                if _d:
                    _c.content.color = "#FFFFFF" if on else T.INK_2
                _c.update()
            except Exception:
                pass
        cc.on_hover = _h
        return cc

    buttons = ft.Row([
        _wb(ft.Icons.REMOVE, _min),
        _wb(ft.Icons.CROP_SQUARE, _tmax),
        _wb(ft.Icons.CLOSE, _close, danger=True),
    ], spacing=0, tight=True)

    drag = ft.WindowDragArea(ft.Container(bgcolor=ft.Colors.TRANSPARENT, expand=True),
                             expand=True)
    return ft.Stack([
        root,
        # draggable strip across the top (leaves room on the right for buttons)
        ft.Container(drag, top=0, left=0, right=150, height=34),
        # window controls, top-right
        ft.Container(buttons, top=0, right=4),
    ], expand=True)

def force_close(app):
    """Close the OS window / exit the process, trying every Flet API, and
    as a final guarantee terminate the flet client window process so it can
    never be left orphaned on screen."""
    app._closing = True   # stop background loops (update checker, etc.)
    closed = False
    # 1) modern Flet: window.destroy()
    try:
        if hasattr(app.page, "window") and app.page.window is not None:
            try:
                app.page.window.prevent_close = False
            except Exception:
                pass
            app.page.window.destroy()
            closed = True
    except Exception:
        pass
    # 2) older Flet: page.window_destroy()
    if not closed:
        try:
            app.page.window_destroy()
            closed = True
        except Exception:
            pass
    # 3) Guarantee: terminate this process tree (kills the paired flet.exe
    #    client window so it can't linger). Done last, slightly delayed so
    #    the graceful close above can paint first.
    def _hard_exit():
        import os, time
        time.sleep(0.4)
        if os.name == "nt":
            # Kill this process AND its children (the flet.exe window client)
            # so no orphan taskbar entry remains.
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
        threading.Thread(target=_hard_exit, daemon=True).start()
    except Exception:
        _hard_exit()

def confirm_close(app):
    def do_close(e=None):
        app.stop_flag = True  # behave like "stop after current"
        app._close_dialog()
        try:
            app.page.window.prevent_close = False
            app.page.update()
        except Exception:
            pass
        # window.destroy() is async in Flet 0.90 — schedule it on the loop
        def _destroy():
            try:
                import os, signal
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                pass
        try:
            rt = getattr(app.page, "run_task", None)
            if callable(rt) and hasattr(app.page.window, "destroy"):
                rt(app.page.window.destroy)
            else:
                _destroy()
        except Exception:
            _destroy()
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=20, color=T.AMBER),
            ft.Text("Close QA Studio?", size=15, weight=ft.FontWeight.BOLD, color=T.INK),
        ], spacing=9),
        content=ft.Container(width=380, content=ft.Text(
            "If a run is in progress it will stop after the current test case. "
            "Any unfinished test cases won't be processed. Close anyway?",
            size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500)),
        actions=[
            ghost_btn("Keep working", on_click=lambda e: app._close_dialog()),
            danger_btn("Stop & close", icon=ft.Icons.STOP, on_click=do_close),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=T.R_LG))
    app._sh