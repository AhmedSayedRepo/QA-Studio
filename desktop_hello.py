"""desktop_hello.py — opt-in Windows Hello unlock for the DESKTOP app.

Desktop twin of the mobile "Require biometric/PIN unlock" setting: when on,
a returning user's cached session is only restored after a Windows Hello
check (PIN, fingerprint or face). Any failure — Hello not set up, the winrt
package missing, the user cancelling — simply leaves the normal email +
password sign-in screen up. Nothing here can lock anyone out.

Needs the Windows-only pywinrt packages (requirements.txt, win32 marker).
Without them available() is False and Settings shows the switch disabled.

The pref is a plain boolean in mobile_prefs' JSON file (~/.qa_tool on
desktop): it must be readable before sign-in, it is not a secret, and the
per-user credential store isn't open yet at that point.
"""
import sys
import threading

PREF_KEY = "desktop_hello"

# UserConsentVerifierAvailability / UserConsentVerificationResult values
# (compared as ints so pywinrt's enum spelling can't break this).
_AVAILABLE = 0
_VERIFIED = 0
_CANCELED = 6

_avail_cache = None
_lock = threading.Lock()
# Why the last availability check said "no" (shown by hello_check.py and
# written to diagnostics.log once). Never contains secrets.
last_reason = ""

_AVAIL_NAMES = {0: "available", 1: "no Windows Hello device found",
                2: "Windows Hello not set up for this Windows user",
                3: "disabled by policy", 4: "device busy"}


def supported_platform():
    return sys.platform == "win32"


def _ucv():
    from winrt.windows.security.credentials.ui import UserConsentVerifier
    return UserConsentVerifier


def _run(coro_fn, timeout):
    """Run one WinRT async call on a private event loop (callers are always
    on a worker thread, never Flet's loop)."""
    import asyncio

    async def _go():
        return await asyncio.wait_for(coro_fn(), timeout)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_go())
    finally:
        loop.close()


def available(refresh=False):
    """True when Windows Hello is set up for this Windows user and the winrt
    package is installed. Cached for the process (the check costs ~100 ms)."""
    global _avail_cache, last_reason
    if not supported_platform():
        return False
    with _lock:
        if _avail_cache is not None and not refresh:
            return _avail_cache
        ok = False
        try:
            U = _ucv()
        except Exception as ex:
            last_reason = ("winrt package not installed for this Python "
                           "(run: py -3.12 -m pip install -r requirements.txt) "
                           "- " + type(ex).__name__ + ": " + str(ex))
            U = None
        if U is not None:
            try:
                res = _run(lambda: U.check_availability_async(), 5.0)
                code = int(res)
                ok = code == _AVAILABLE
                last_reason = "" if ok else _AVAIL_NAMES.get(code, "code %s" % code)
            except Exception as ex:
                last_reason = ("availability check failed - "
                               + type(ex).__name__ + ": " + str(ex))
        if not ok:
            try:
                import diag_log
                diag_log.log_warn("desktop_hello.available", last_reason)
            except Exception:
                pass
        _avail_cache = ok
        return ok


def cached_available():
    """Last known availability without blocking (None = not checked yet)."""
    return _avail_cache


def warm():
    """Check availability in the background so Settings never waits on it."""
    if supported_platform() and _avail_cache is None:
        threading.Thread(target=available, daemon=True).start()


def enabled():
    if not supported_platform():
        return False
    try:
        import mobile_prefs
        return bool(mobile_prefs.get(PREF_KEY, False))
    except Exception:
        return False


def set_enabled(on):
    try:
        import mobile_prefs
        return bool(mobile_prefs.set(PREF_KEY, bool(on)))
    except Exception:
        return False


def _raise_prompt(seconds=4.0):
    """Best effort: the Windows Security prompt can open BEHIND the app window
    for a non-UWP process. Bring it to the front once it appears."""
    try:
        import ctypes
        import time
        user32 = ctypes.windll.user32
        end = time.time() + seconds
        while time.time() < end:
            h = user32.FindWindowW("Credential Dialog Xaml Host", None)
            if h:
                user32.SetForegroundWindow(h)
                return
            time.sleep(0.15)
    except Exception:
        pass


def verify(message, timeout=120.0):
    """Ask Windows Hello to verify the signed-in Windows user.
    Returns (ok, reason) — reason is 'ok', 'canceled', 'unavailable' or
    'failed'. Blocking: call from a worker thread."""
    if not available():
        return False, "unavailable"
    try:
        U = _ucv()
        threading.Thread(target=_raise_prompt, daemon=True).start()
        res = _run(lambda: U.request_verification_async(message), timeout)
        code = int(res)
        if code == _VERIFIED:
            return True, "ok"
        if code == _CANCELED:
            return False, "canceled"
        return False, "failed"
    except Exception:
        return False, "failed"
