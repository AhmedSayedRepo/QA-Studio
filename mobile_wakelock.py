"""mobile_wakelock.py — MOBILE_PLAN.md Phase 3 Step 1 ("keep-screen-awake
during an active run (wakelock plugin) + warn on background attempt").

Backed by `flet.Wakelock` — a `Service` control bundled in core Flet itself
(flet==0.85.3, already pinned; NOT a separate pip package, so this carries
none of the "new native dependency, unverified on mobile build" risk that
applied to flet-secure-storage/azure-devops this session).

Same async-bridging shape as secure_store_mobile.py: enable()/disable() are
fire-and-forget via page.run_task(), never a blocking call from a sync
on_click/on_change handler (see that file's docstring for why blocking would
deadlock). A run's start/stop already funnels through one place
(main.py's _set_run_active) so wiring in there covers every local-run entry
point without new call sites scattered around.

CRITICAL (found and fixed live in secure_store_mobile.py this same session):
a bare `ft.Wakelock()` does nothing — Service._invoke_method raises unless
the control is actually attached via `page.services.append(...)` +
`page.update()`, which is what init() does here before anything else.
"""

_wakelock = None
_page = None
_enabled = False   # tracks intent, so a rapid stop-before-enable-lands
                    # never leaves the device awake with nothing to show for it


def available():
    try:
        import flet as ft  # noqa: F401
        return hasattr(ft, "Wakelock")
    except Exception:
        return False


def init(page):
    """Call once, mobile only (see main.py's QAStudio.__init__). Attaches
    the Wakelock service to the page so enable()/disable() work later —
    constructing it is cheap and side-effect-free until enable() is called,
    so doing this unconditionally at startup (rather than lazily on first
    run) avoids ever calling page.services.append() from inside a run's
    hot path."""
    global _wakelock, _page
    if not available():
        return
    try:
        import flet as ft
        _wakelock = ft.Wakelock()
        _page = page
        page.services.append(_wakelock)
        page.update()
    except Exception:
        _wakelock = None


def enable():
    global _enabled
    if _wakelock is None or _page is None:
        return
    _enabled = True
    try:
        _page.run_task(_wakelock.enable)
    except Exception:
        pass


def disable():
    global _enabled
    _enabled = False
    if _wakelock is None or _page is None:
        return
    try:
        _page.run_task(_wakelock.disable)
    except Exception:
        pass


def is_run_keeping_awake():
    """Best-effort local flag (the intent, not a round-trip to the native
    side) — used by the background-lifecycle warning to decide whether
    losing foreground actually matters right now."""
    return _enabled
