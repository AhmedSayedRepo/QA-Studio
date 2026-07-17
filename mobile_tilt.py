"""mobile_tilt.py — device-tilt backdrop parallax for the mobile login screen.

The desktop login backdrop (login.py's login_parallax) shifts with the mouse
cursor via GestureDetector.on_hover — HoverEvent.local_position. Touchscreens
don't generate hover events from a finger, so that handler simply never fires
on mobile: the backdrop has been completely static there. The requested
mobile equivalent of "moving the mouse" is "moving/tilting the phone", which
needs a real motion sensor, not a pointer position.

Backed by `flet.Accelerometer` — a `Service` control bundled in core Flet
itself (flet==0.85.3, already pinned; NOT a separate pip package, same
"zero new native-dependency risk" situation as ft.Wakelock/ft.Share this
session). Explicitly Android/iOS/web only (raises
FletUnsupportedPlatformException on desktop — confirmed via
Accelerometer.before_update()'s own source), so this is never even
attempted outside platform_caps.is_mobile() call sites.

Raw accelerometer (not Gyroscope/UserAccelerometer) is the right sensor
here: it includes gravity, so its x/y readings reflect the phone's current
TILT/ORIENTATION (like a spirit level) — a position-like signal directly
analogous to a mouse's (x, y), rather than a velocity/rotation-rate signal
better suited to detecting shake gestures.

Same async/Service-attachment lessons already learned this session
(secure_store_mobile.py's page.services bug, mobile_wakelock.py): must be
attached via page.services.append()+page.update() before use, and every
call here is a no-op unless platform_caps.is_mobile() is true at the
call site. Streaming is left OFF (enabled=False) until enable() is called
and stopped again on disable() — this only runs while the login screen
is actually showing, not for the lifetime of the whole app process, so it
doesn't drain battery once a user is signed in.
"""

_accel = None
_page = None
_on_tilt = None
# Exponential moving average state — raw accelerometer readings are noisy
# (200ms default sampling, real hand tremor/micro-motion), so a jittery,
# un-smoothed offset looked twitchy rather than the gentle drift the
# desktop's mouse parallax has. A light EMA damps that without adding
# perceptible lag.
_ema_x = 0.0
_ema_y = 0.0
_EMA_ALPHA = 0.25


def available():
    try:
        import flet as ft  # noqa: F401
        return hasattr(ft, "Accelerometer")
    except Exception:
        return False


def init(page):
    """Call once, mobile only (see main.py's QAStudio.__init__). Attaches
    the Accelerometer service disabled — enable()/disable() toggle actual
    streaming on/off around the login screen's lifetime."""
    global _accel, _page
    if not available():
        return
    try:
        import flet as ft
        _accel = ft.Accelerometer(enabled=False, on_reading=_on_reading)
        _page = page
        page.services.append(_accel)
        page.update()
    except Exception:
        _accel = None


def _on_reading(e):
    global _ema_x, _ema_y
    if _on_tilt is None:
        return
    try:
        # ~9.8 m/s^2 = gravity at a full 90° tilt on that axis; normalizing
        # by it maps a natural handheld tilt range to roughly the same
        # [-0.5, 0.5]-ish span login_parallax's mouse-position math uses,
        # so the SAME offset multiplier (see login.py) produces a
        # comparable-feeling shift instead of needing its own separate
        # tuning constant.
        nx = max(-1.0, min(1.0, e.x / 9.8)) * 0.5
        ny = max(-1.0, min(1.0, e.y / 9.8)) * 0.5
        _ema_x = _EMA_ALPHA * nx + (1 - _EMA_ALPHA) * _ema_x
        _ema_y = _EMA_ALPHA * ny + (1 - _EMA_ALPHA) * _ema_y
        _on_tilt(_ema_x, _ema_y)
    except Exception:
        pass


def enable(on_tilt):
    """Start streaming readings to on_tilt(mx, my) — same (mx, my) shape
    login_parallax computes from the mouse, in the same rough [-0.5, 0.5]
    range. No-op if init() hasn't run / isn't available (desktop, or the
    accelerometer failed to attach)."""
    global _on_tilt, _ema_x, _ema_y
    if _accel is None or _page is None:
        return
    _on_tilt = on_tilt
    _ema_x = _ema_y = 0.0
    try:
        _accel.enabled = True
        _accel.update()
    except Exception:
        pass


def disable():
    global _on_tilt
    _on_tilt = None
    if _accel is None:
        return
    try:
        _accel.enabled = False
        _accel.update()
    except Exception:
        pass
