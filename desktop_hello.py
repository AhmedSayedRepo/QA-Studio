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
    """Run one WinRT async call on a private event loop in its OWN thread.
    Callers may be on Flet's event-loop thread (a Settings switch handler),
    where run_until_complete raises "Cannot run the event loop while another
    loop is running" — so never run the loop on the caller's thread."""
    import asyncio
    box = {}

    async def _go():
        return await asyncio.wait_for(coro_fn(), timeout)

    def _worker():
        loop = asyncio.new_event_loop()
        try:
            box["res"] = loop.run_until_complete(_go())
        except BaseException as ex:          # re-raised on the caller side
            box["err"] = ex
        finally:
            loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout + 2.0)
    if t.is_alive():
        raise TimeoutError("Windows Hello did not answer in time")
    if "err" in box:
        raise box["err"]
    return box.get("res")


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


# ── Window-owned prompt (IUserConsentVerifierInterop) ───────────────────────
# UserConsentVerifier.RequestVerificationAsync is meant for UWP apps: from a
# plain desktop process the Windows Security prompt never shows (reported
# live: nothing appeared, then "didn't complete" after the timeout). Desktop
# apps must call IUserConsentVerifierInterop::RequestVerificationForWindowAsync
# with their window handle, which owns the prompt and puts it in FRONT of the
# app. pywinrt doesn't project that interop, so it is called via ctypes.
_INTEROP_IID = "39e050c3-4e74-441a-8dc0-b81104df949c"
_IASYNCINFO_IID = "00000036-0000-0000-c000-000000000046"
_IASYNCOP_GENERIC = "9fc2b0bb-e446-44e2-aa61-9cab8f636af2"     # IAsyncOperation`1
_RESULT_ENUM = "Windows.Security.Credentials.UI.UserConsentVerificationResult"
_PINTERFACE_NS = "11f47ad5-7b73-42c0-abae-878b1e16adee"
_ASYNC_STARTED, _ASYNC_COMPLETED = 0, 1


def async_result_iid():
    """IID of IAsyncOperation<UserConsentVerificationResult>, derived with the
    WinRT parameterized-interface rule (UUIDv5 over the type signature)."""
    import uuid
    sig = "pinterface({%s};enum(%s;i4))" % (_IASYNCOP_GENERIC, _RESULT_ENUM)
    return str(uuid.uuid5(uuid.UUID(_PINTERFACE_NS), sig))


def foreground_window():
    """HWND of the window the user is clicking in (QA Studio's flet window)."""
    try:
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _app_window(hwnd=0):
    if hwnd:
        return hwnd
    try:
        import ctypes
        h = ctypes.windll.user32.FindWindowW(None, "QA Studio")
        return int(h or 0) or foreground_window()
    except Exception:
        return foreground_window()


def _verify_for_window(hwnd, message, timeout):
    """Returns the UserConsentVerificationResult code, or raises."""
    import ctypes
    import time
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                    ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

    def guid(s):
        return GUID.from_buffer_copy(uuid.UUID(s).bytes_le)

    HR = ctypes.c_long
    combase = ctypes.windll.combase

    def vcall(ptr, index, *types):
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(HR, ctypes.c_void_p, *types)(vtbl[index])

    def check(hr, what):
        if hr < 0:
            raise OSError("%s failed: HRESULT 0x%08X" % (what, hr & 0xFFFFFFFF))

    def release(ptr):
        if ptr:
            try:
                vcall(ptr, 2)(ptr)
            except Exception:
                pass

    combase.RoInitialize(ctypes.c_int(1))           # MTA; "already" is fine

    def hstring(text):
        h = ctypes.c_void_p()
        check(combase.WindowsCreateString(ctypes.c_wchar_p(text),
                                          ctypes.c_uint32(len(text)),
                                          ctypes.byref(h)), "WindowsCreateString")
        return h

    cls = hstring("Windows.Security.Credentials.UI.UserConsentVerifier")
    msg = hstring(message or "")
    factory = ctypes.c_void_p()
    op = ctypes.c_void_p()
    info = ctypes.c_void_p()
    try:
        iid = guid(_INTEROP_IID)
        check(combase.RoGetActivationFactory(cls, ctypes.byref(iid),
                                             ctypes.byref(factory)),
              "RoGetActivationFactory")
        rid = guid(async_result_iid())
        # IInspectable (6 slots) → RequestVerificationForWindowAsync is slot 6
        check(vcall(factory, 6, wintypes.HWND, ctypes.c_void_p,
                    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                  factory, wintypes.HWND(hwnd), msg, ctypes.byref(rid),
                  ctypes.byref(op)), "RequestVerificationForWindowAsync")
        aiid = guid(_IASYNCINFO_IID)
        check(vcall(op, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                  op, ctypes.byref(aiid), ctypes.byref(info)), "QueryInterface(IAsyncInfo)")
        status = ctypes.c_int(_ASYNC_STARTED)
        end = time.time() + timeout
        while True:
            # IAsyncInfo: get_Id 6, get_Status 7, get_ErrorCode 8, Cancel 9
            check(vcall(info, 7, ctypes.POINTER(ctypes.c_int))(
                      info, ctypes.byref(status)), "IAsyncInfo.get_Status")
            if status.value != _ASYNC_STARTED:
                break
            if time.time() > end:
                vcall(info, 9)(info)
                raise TimeoutError("Windows Hello did not answer in time")
            time.sleep(0.1)
        if status.value != _ASYNC_COMPLETED:
            raise OSError("Windows Hello request ended with status %d" % status.value)
        result = ctypes.c_int(-1)
        # IAsyncOperation<T>: put_Completed 6, get_Completed 7, GetResults 8
        check(vcall(op, 8, ctypes.POINTER(ctypes.c_int))(op, ctypes.byref(result)),
              "IAsyncOperation.GetResults")
        return result.value
    finally:
        release(info)
        release(op)
        release(factory)
        for h in (msg, cls):
            try:
                combase.WindowsDeleteString(h)
            except Exception:
                pass


def verify(message, timeout=120.0, hwnd=0):
    """Ask Windows Hello to verify the signed-in Windows user, with the prompt
    owned by QA Studio's window (pass `hwnd` captured on the UI thread when
    the user clicked; otherwise the app window is looked up).
    Returns (ok, reason) — reason is 'ok', 'canceled', 'unavailable' or
    'failed'. Blocking: call from a worker thread."""
    global last_reason
    if not available():
        return False, "unavailable"
    try:
        code = _verify_for_window(_app_window(hwnd), message, timeout)
    except Exception as ex:
        last_reason = "verify failed - %s: %s" % (type(ex).__name__, ex)
        try:
            import diag_log
            diag_log.log_warn("desktop_hello.verify", last_reason)
        except Exception:
            pass
        return False, "failed"
    if code == _VERIFIED:
        return True, "ok"
    if code == _CANCELED:
        return False, "canceled"
    last_reason = "verify result code %s" % code
    try:
        import diag_log
        diag_log.log_warn("desktop_hello.verify", last_reason)
    except Exception:
        pass
    return False, "failed"
