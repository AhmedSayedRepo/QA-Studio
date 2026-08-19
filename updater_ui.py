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
import strings


def banner(app):
    """A floating update CARD that pops OVER the app (rendered top-centre as an
    overlay). Fully theme-adaptive — CARD / INK / BORDER surfaces swap with
    light|dark — and shown on the login screen and in-app alike."""
    info = app._update_info or {}
    if not info.get("update") or app._update_dismissed:
        return None
    remote = info.get("remote", "")
    local = info.get("local", "")
    if app._updating:
        inner = ft.Row([
            ft.Container(ft.ProgressRing(width=18, height=18, stroke_width=2,
                                         color=T.VIOLET), width=36, height=36,
                         border_radius=10, bgcolor=T.VIOLET_SOFT,
                         alignment=ft.Alignment.CENTER),
            ft.Column([
                ft.Text(strings.t("upd_updating"), size=12.5, color=T.INK,
                        weight=ft.FontWeight.BOLD),
                ft.Text(strings.t("upd_updating_desc"), size=10.5, color=T.INK_3,
                        weight=ft.FontWeight.W_500),
            ], spacing=1, tight=True),
        ], spacing=10, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    else:
        update_btn = ft.Container(
            ft.Row([
                ft.Icon(ft.Icons.DOWNLOAD, size=16, color="#FFFFFF"),
                ft.Text(strings.t("upd_update_now"), size=13, color="#FFFFFF",
                        weight=ft.FontWeight.BOLD),
            ], spacing=8, tight=True),
            bgcolor=T.VIOLET, border_radius=T.R_SM,
            padding=ft.Padding.symmetric(horizontal=18, vertical=11),
            on_click=lambda e: app._do_update(), ink=True,
            tooltip=strings.t("upd_update_now_tip"),
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
            ft.Container(ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, size=18, color=T.VIOLET),
                         width=36, height=36, border_radius=10,
                         bgcolor=T.VIOLET_SOFT,
                         alignment=ft.Alignment.CENTER),
            ft.Column([
                ft.Text(strings.t("upd_update_available"), size=12.5, color=T.INK,
                        weight=ft.FontWeight.BOLD),
                ft.Text(strings.t("upd_version_ready", remote=remote, local=local),
                        size=11, weight=ft.FontWeight.W_500, color=T.INK_2),
            ], spacing=1, tight=True),
            ft.Container(width=4),
            update_btn,
            ft.IconButton(ft.Icons.CLOSE, icon_size=15, icon_color=T.INK_3,
                          tooltip=strings.t("upd_dismiss"),
                          on_click=lambda e: app._dismiss_update()),
        ], spacing=11, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    # Floating card that pops OVER the app: theme surface (white in light, dark navy
    # in dark), soft border, and an elevation shadow so it reads as an overlay.
    return ft.Container(
        inner, bgcolor=T.CARD, border_radius=T.R_LG,
        border=ft.Border.all(1, T.BORDER),
        padding=ft.Padding.only(left=13, right=7, top=9, bottom=9),
        shadow=ft.BoxShadow(blur_radius=34, spread_radius=-8, offset=ft.Offset(0, 14),
                            color=ft.Colors.with_opacity(0.40, "#05060F")))

def dismiss_update(app):
    app._update_dismissed = True
    app.render()

def do_update(app):
    # Event queues can contain a second click before the first repaint lands.
    # A guard in the operation itself prevents two concurrent pulls/installs.
    if getattr(app, "_updating", False):
        return
    # Keep the version from the update check.  The updater clears the banner
    # state on success, but the completion dialog should still be able to say
    # exactly which release has just been installed.
    target_version = str((app._update_info or {}).get("remote") or "")
    app._updating = True
    app.render()
    try:
        app._toast(strings.t("upd_update_started"))
    except Exception:
        pass

    def work():
        ok, msg = E.apply_update(cb=lambda m, t="dim": None)
        if ok:
            # If the window is closed before the UI callback can run, the next
            # launch still has a trustworthy, local record to show once.
            E.save_pending_update_notice(target_version)
        def finish():
            app._updating = False
            if ok:
                app._update_info = {"update": False}
                app._update_dismissed = True
                app._show_restart_dialog(msg, target_version)
            else:
                app.render()
                app._show_update_error(msg)
        app.ui_safe(finish)
    app._bg(work)

def _update_error_hint(msg):
    """Pick a hint that actually matches the failure, instead of always
    blaming a missing .exe asset — that message only applies to the frozen
    .exe update path (_apply_update_exe), but most installs run the source/
    zip updater (_apply_update_zip), which fails for very different reasons
    (no internet, DNS/proxy/firewall blocking api.github.com, GitHub itself
    being unreachable, a bad checksum, etc). Showing the exe hint for a
    plain connection error tells the user to do something (attach a release
    asset) that has nothing to do with their actual problem."""
    low = (msg or "").lower()
    if "no .exe attached" in low or "exe attached" in low:
        return (strings.t("upd_hint_no_exe"))
    if any(s in low for s in (
            "max retries exceeded", "connectionerror", "connection error",
            "couldn't reach github", "couldn't resolve the update branch",
            "name or service not known", "timed out", "timeout",
            "failed to establish a new connection", "getaddrinfo")):
        return (strings.t("upd_hint_network"))
    if "checksum" in low:
        return (strings.t("upd_hint_checksum"))
    return (strings.t("upd_hint_generic"))


def show_update_error(app, msg):
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Container(ft.Icon(ft.Icons.ERROR_OUTLINE, size=18, color=T.RED),
                         width=34, height=34, bgcolor=T.RED_SOFT, border_radius=9,
                         alignment=ft.Alignment.CENTER),
            ft.Text(strings.t("upd_update_failed"), size=16, weight=ft.FontWeight.W_800, color=T.INK),
        ], spacing=10, tight=True),
        content=ft.Container(
            ft.Column([
                ft.Text(msg, size=13, color=T.INK, selectable=True),
                ft.Container(height=6),
                ft.Text(_update_error_hint(msg),
                        size=11.5, color=T.INK_3, weight=ft.FontWeight.W_500),
            ], spacing=2, tight=True), width=460),
        actions=[ft.Row([
            ghost_btn(strings.t("upd_retry"), icon=ft.Icons.REFRESH, on_click=lambda e: (
                app._close_dialog(),
                app._do_update())),
            green_btn(strings.t("upd_ok"), on_click=lambda e: app._close_dialog()),
        ], alignment=ft.MainAxisAlignment.END, spacing=10, tight=True)],
        actions_alignment=ft.MainAxisAlignment.END)
    app._show_dialog(dlg)


def _release_notes_card(version):
    """Build the localized release-note card shared by both completion flows."""
    notes = [
        (ft.Icons.DOMAIN, "upd_note_tenants"),
        (ft.Icons.GROUPS, "upd_note_access"),
        (ft.Icons.FACT_CHECK, "upd_note_audit"),
        (ft.Icons.LANGUAGE, "upd_note_experience"),
    ]
    note_rows = []
    for icon, key in notes:
        note_rows.append(ft.Row([
            ft.Container(ft.Icon(icon, size=15, color=T.STORY), width=24,
                         alignment=ft.Alignment.TOP_CENTER),
            ft.Text(strings.t(key), size=11.5, color=T.INK_2,
                    weight=ft.FontWeight.W_500, expand=True),
        ], spacing=7, vertical_alignment=ft.CrossAxisAlignment.START))
    return ft.Container(
        ft.Column([
            ft.Text(strings.t("upd_whats_new"), size=12.5, color=T.INK,
                    weight=ft.FontWeight.W_800),
            ft.Text(strings.t("upd_whats_new_version", version=version or E.local_version()),
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
            ft.Container(height=2),
            *note_rows,
        ], spacing=6, tight=True),
        bgcolor=T.CARD_2, border=ft.Border.all(1, T.BORDER),
        border_radius=T.R_MD, padding=ft.Padding.symmetric(horizontal=13, vertical=11))


def show_restart_dialog(app, msg, version=""):
    """Show the post-update hand-off, including the release highlights.

    This dialog is only opened after ``apply_update`` succeeds, so the notes
    are a useful, once-per-update explanation rather than a persistent banner
    users have to dismiss on every launch.
    """
    release_card = _release_notes_card(version)
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Container(ft.Icon(ft.Icons.CHECK_CIRCLE, size=18, color=T.GREEN),
                         width=34, height=34, bgcolor=T.GREEN_SOFT, border_radius=9,
                         alignment=ft.Alignment.CENTER),
            ft.Text(strings.t("upd_update_complete"), size=16, weight=ft.FontWeight.W_800, color=T.INK),
        ], spacing=10, tight=True),
        content=ft.Container(
            ft.Column([
                ft.Text(strings.t("upd_updated_body"),
                        size=13, color=T.INK, weight=ft.FontWeight.W_700),
                ft.Container(height=6),
                ft.Text(strings.t("upd_restart_body"),
                        size=13, color=T.INK_2, weight=ft.FontWeight.W_500),
                ft.Container(height=5),
                release_card,
            ], spacing=2, tight=True),
            width=480),
        actions=[
            ghost_btn(strings.t("upd_later"), on_click=lambda e: (
                E.clear_pending_update_notice(), app._close_dialog())),
            green_btn(strings.t("upd_restart_now"), icon=ft.Icons.RESTART_ALT,
                      on_click=lambda e: app._restart_app(), height=46),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    app._show_dialog(dlg)


def show_post_update_dialog(app, version):
    """Show the saved completion notice after a restart/early close.

    The update has already completed by this point, so this is informational
    only: it deliberately has no restart action and clears itself on Continue.
    """
    release_card = _release_notes_card(version)
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Container(ft.Icon(ft.Icons.CHECK_CIRCLE, size=18, color=T.GREEN),
                         width=34, height=34, bgcolor=T.GREEN_SOFT, border_radius=9,
                         alignment=ft.Alignment.CENTER),
            ft.Text(strings.t("upd_update_complete"), size=16,
                    weight=ft.FontWeight.W_800, color=T.INK),
        ], spacing=10, tight=True),
        content=ft.Container(ft.Column([
            ft.Text(strings.t("upd_updated_version", version=version), size=13,
                    color=T.INK, weight=ft.FontWeight.W_700),
            ft.Container(height=8),
            release_card,
        ], spacing=2, tight=True), width=480),
        actions=[green_btn(strings.t("upd_continue"), on_click=lambda e: (
            E.clear_pending_update_notice(), app._close_dialog()), height=46)],
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
    # The user has already seen the completion dialog and explicitly chose to
    # restart, so do not show the same notice again after the new process opens.
    E.clear_pending_update_notice()
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

