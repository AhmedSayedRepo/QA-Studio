"""idle_watch.py — idle-timeout auto-logout: watchdog thread + the final-
minute 'are you still there?' countdown dialog.

Extracted from main.py's QAStudio class (continuing the same modular-refactor
pattern already used for window_chrome.py, updater_ui.py, login.py, dialogs.py,
etc.) — a self-contained feature with no dependency on any other screen's
state beyond the handful of app.* attributes/methods used below. QAStudio
keeps thin one-line delegator methods for the two entry points other modules
already call directly (app._idle_minutes() / app._set_idle_minutes(), used by
settings.py) — every OTHER call site in this module only ever needs `app`.
"""
import time
import threading

import flet as ft
import strings
import theme as T
import auth_supabase as auth
from ui import primary_btn, ghost_btn


def idle_minutes(app):
    """Current user's idle-logout minutes (0 = off), stored in their prefs."""
    try:
        v = int(app.creds.get("idle_minutes", app.IDLE_MINUTES_DEFAULT))
    except Exception:
        v = app.IDLE_MINUTES_DEFAULT
    return v if v in app.IDLE_MINUTES_CHOICES else app.IDLE_MINUTES_DEFAULT


def set_idle_minutes(app, minutes):
    """Persist this user's idle-logout preference and reset their idle clock."""
    app.creds["idle_minutes"] = int(minutes)
    try:
        import store
        store.save(app.creds)
    except Exception:
        pass
    app._last_activity = time.time()
    app._toast((strings.t("idle_autologout_off") if not minutes else strings.t("idle_autologout_set", minutes=minutes)))
    app.render()


def start_idle_watch(app):
    """Background watchdog: sign the user out after idle_minutes() of no activity
    (0 = off). render() refreshes _last_activity; an active run/automation counts
    as activity so a long generation is never interrupted."""
    if getattr(app, "_idle_watch_on", False):
        return
    app._idle_watch_on = True

    def _loop():
        while True:
            time.sleep(5)
            try:
                if not (auth.configured() and getattr(app, "user", None)):
                    continue
                if getattr(app, "_idle_warning_active", False):
                    continue                         # countdown dialog owns it now
                idle = idle_minutes(app) * 60
                if idle <= 0:                        # auto-logout disabled
                    app._last_activity = time.time()
                    continue
                # Only ACTIVELY-working runs count as busy. A PAUSED automation
                # (e.g. auto-paused on low credit) is waiting on the user, so it's
                # idle and must not block auto-logout.
                _busy = (getattr(app, "_run_active", False)
                         or (getattr(app, "_auto_running", False)
                             and not getattr(app, "_auto_paused", False)))
                if _busy:
                    app._last_activity = time.time()   # busy = not idle
                    continue
                remaining = idle - (time.time() - getattr(app, "_last_activity", time.time()))
                if remaining <= min(app.IDLE_WARN_SECONDS, idle):
                    app._idle_warning_active = True
                    app.ui_safe(lambda: show_idle_warning(app))
            except Exception:
                pass
    try:
        threading.Thread(target=_loop, daemon=True).start()
    except Exception:
        pass


def warn_msg(s):
    s = max(0, int(s))
    return f"Signing out in {s} second{'' if s == 1 else 's'}…"


def show_idle_warning(app):
    """Final-minute dialog: live countdown with 'Stay signed in' / 'Sign out now'."""
    # Guard against a race where the user's session already ended by the time
    # this fires (e.g. revoked/expired via a revalidate() re-render) — without
    # this, the countdown dialog could pop up floating over the (unauthenticated)
    # login gate, which makes no sense since there's no session left to expire.
    if not (auth.configured() and getattr(app, "user", None)):
        app._idle_warning_active = False
        return
    app._idle_left = int(min(app.IDLE_WARN_SECONDS, idle_minutes(app) * 60))
    # WALL-CLOCK deadline, not a decrementing counter. The old `_idle_left -= 1`
    # per time.sleep(1) FREEZES whenever the process is suspended — desktop
    # window closed/minimised, laptop sleep, or the Flet client disconnecting —
    # so on resume it showed a STALE value (reported live: frozen at 43s) and
    # the security timeout never fired. Deriving `remaining` from a fixed
    # deadline means a suspended-then-resumed app sees the time that really
    # elapsed and signs out at once, which is the correct security behaviour.
    app._idle_warn_deadline = time.time() + app._idle_left
    app._idle_warn_cancel = False
    app._idle_warn_txt = ft.Text(warn_msg(app._idle_left),
                                 size=15, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK)
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Container(ft.Icon(ft.Icons.TIMER_OUTLINED, size=18, color=T.VIOLET_INK),
                         width=34, height=34, bgcolor=T.VIOLET_SOFT, border_radius=9,
                         alignment=ft.Alignment.CENTER),
            ft.Text(strings.t("idle_still_there"), size=16, weight=ft.FontWeight.W_800,
                    color=T.INK, expand=True),
        ], spacing=10),
        content=ft.Container(width=380, content=ft.Column([
            ft.Text(strings.t("idle_inactive"), size=13, color=T.INK_2, weight=ft.FontWeight.W_500),
            ft.Container(height=8),
            app._idle_warn_txt,
        ], tight=True, spacing=2)),
        actions=[ft.Row([
            ghost_btn(strings.t("idle_signout_now"), on_click=lambda e: signout_now(app)),
            primary_btn(strings.t("idle_stay"), on_click=lambda e: renew(app)),
        ], alignment=ft.MainAxisAlignment.END, spacing=10, tight=True)],
        actions_alignment=ft.MainAxisAlignment.END)
    # Remember WHICH dialog is the countdown, so any later close only ever
    # dismisses this one — never a different dialog the user may have opened
    # since (e.g. after clicking "Stay signed in").
    app._idle_warn_dlg = dlg
    app._show_dialog(dlg)

    def _tick():
        import math
        while not getattr(app, "_idle_warn_cancel", False):
            # If the session ended some other way mid-countdown (revoked,
            # explicit sign-out, revalidate() finding it gone), just close the
            # dialog quietly instead of letting it sit on top of the login gate.
            if not (auth.configured() and getattr(app, "user", None)):
                app._idle_warn_cancel = True
                app._idle_warning_active = False
                app.ui_safe(lambda: close_idle_dialog(app))
                return
            remaining = getattr(app, "_idle_warn_deadline", 0) - time.time()
            app._idle_left = max(0, math.ceil(remaining))
            if remaining <= 0:
                break                     # deadline reached (incl. after suspend)
            app.ui_safe(lambda: update_idle_warn(app))
            # Sleep to the next whole second, capped at 1s — so a resumed thread
            # re-checks the wall clock promptly instead of overshooting.
            time.sleep(min(1.0, remaining))
        if not getattr(app, "_idle_warn_cancel", False):
            app.ui_safe(lambda: sign_out(app))
        else:
            # The loop exited because _idle_warn_cancel was set from OUTSIDE this
            # thread — the ONE path that doesn't close the dialog itself. renew()
            # and signout_now() (the button handlers) both dismiss it before
            # setting the flag, but _sign_out() (main.py) — reached when a
            # background revalidate() finds the session revoked/expired — only
            # flips the flags. Without this, the countdown is orphaned: left
            # frozen at whatever second it last showed, floating over the now
            # unauthenticated login gate (the reported "stuck at 59s over the
            # sign-in screen"). The in-loop auth guard above can't catch this —
            # once the flag is set, `while not _idle_warn_cancel` short-circuits
            # before the guard runs. Closing here (idempotent; a no-op when the
            # button handlers already closed it) guarantees the dialog never
            # outlives the session.
            app._idle_warning_active = False
            app.ui_safe(lambda: close_idle_dialog(app))
    try:
        threading.Thread(target=_tick, daemon=True).start()
    except Exception:
        pass


def close_idle_dialog(app):
    """Dismiss the countdown dialog — but ONLY if it's still the one on screen.
    Guards the teardown-race close (and the session-ended close) against yanking
    a different dialog the user opened in the meantime: _close_dialog() closes
    whatever `_dialog` currently is, so we first confirm it's still ours."""
    try:
        if getattr(app, "_dialog", None) is getattr(app, "_idle_warn_dlg", None):
            app._close_dialog()
        app._idle_warn_dlg = None
    except Exception:
        pass


def update_idle_warn(app):
    try:
        import math
        # Recompute from the deadline (not the possibly-stale _idle_left) so a
        # render after a reconnect/resume shows the REAL remaining time.
        dl = getattr(app, "_idle_warn_deadline", None)
        if dl is not None:
            app._idle_left = max(0, math.ceil(dl - time.time()))
        t = getattr(app, "_idle_warn_txt", None)
        if t is not None:
            t.value = warn_msg(getattr(app, "_idle_left", 0))
            t.update()
    except Exception:
        pass


def renew(app):
    """User chose to stay — reset the idle clock and dismiss the countdown."""
    app._idle_warn_cancel = True
    app._idle_warning_active = False
    app._last_activity = time.time()
    try: app._close_dialog()
    except Exception: pass
    app._toast(strings.t("idle_renewed"))


def signout_now(app):
    app._idle_warn_cancel = True
    app._idle_warning_active = False
    try: app._close_dialog()
    except Exception: pass
    app._sign_out()


def sign_out(app):
    app._idle_warn_cancel = True
    app._idle_warning_active = False
    try: app._close_dialog()
    except Exception: pass
    app._toast(strings.t("idle_signed_out_after", minutes=idle_minutes(app)))
    app._sign_out()
