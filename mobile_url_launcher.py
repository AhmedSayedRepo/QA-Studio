"""mobile_url_launcher.py — persistent `flet.UrlLauncher` service instance.

ROOT CAUSE this fixes: "links not working" was reported live even AFTER
_open_url() was fixed to skip the unreliable webbrowser.open() on mobile and
fall through to Flet's own `page.launch_url()`. That fallback turned out to
be broken too, for the exact same class of bug already found and fixed twice
this session (mobile_wakelock.py, secure_store_mobile.py): `page.launch_url()`
is a thin, DEPRECATED convenience wrapper that constructs a brand-new,
throwaway `ft.UrlLauncher()` on every single call —

    async def launch_url(self, url, ...):
        await UrlLauncher().launch_url(url)

— and never attaches that instance to `page.services`. `Service._invoke_method`
needs the control to have actually been synced down to the Flutter client via
`page.services.append(...)` + `page.update()` first; an ad-hoc, never-attached
instance has no matching control on the client side, so the RPC silently goes
nowhere. Worse, this happens deep inside a coroutine scheduled via
`page.run_task()` (run on the event loop via `run_coroutine_threadsafe`), so
the failure never surfaces back to the caller's try/except — no exception, no
toast, just a dead tap. Exactly the same silent-failure shape as the earlier
webbrowser.open() false-positive.

Fixed the same way as every other native service in this app: attach ONE
persistent `ft.UrlLauncher()` at startup (cheap, side-effect-free until a URL
is actually opened), then call THAT instance's launch_url() — never the
deprecated page.launch_url() again.
"""

_launcher = None
_page = None


def available():
    try:
        import flet as ft  # noqa: F401
        return hasattr(ft, "UrlLauncher")
    except Exception:
        return False


def init(page):
    """Call once at app startup (main.py's QAStudio.__init__) — both
    platforms benefit (desktop keeps webbrowser.open as its first choice in
    _open_url(), this is only its fallback there, but a link opened before
    that first choice fails still needs a live target to fall back to)."""
    global _launcher, _page
    if not available():
        return
    try:
        import flet as ft
        _launcher = ft.UrlLauncher()
        _page = page
        page.services.append(_launcher)
        page.update()
    except Exception:
        _launcher = None


def open(url):
    """Fire-and-forget open — safe to call from a sync on_click handler.
    Returns True once the open has been SCHEDULED (not once it has actually
    opened — this is a one-way fire-and-forget, matching every other
    Service call in this app)."""
    if _launcher is None or _page is None:
        return False
    try:
        _page.run_task(_launcher.launch_url, url)
        return True
    except Exception:
        return False
