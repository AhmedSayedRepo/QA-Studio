"""platform_caps.py — single source of truth for platform-dependent capabilities.

Mobile Phase 0 (see MOBILE_PLAN.md): every Windows-/desktop-only feature is
asked about HERE instead of call sites sprinkling `os.name == "nt"` checks or
calling Windows-only APIs (os.startfile, DPAPI, ctypes clipboard) directly.
On today's Windows desktop every capability answers True, so behavior is
unchanged; a future `flet build apk/ipa` gets correct answers for free.

Static (process-level) facts are computed from `os.name`/`sys.platform`.
Whether we're on a MOBILE Flet client is only knowable at runtime from the
page, so main() records it once via set_flet_platform(page.platform).
"""
import os

_FLET_PLATFORM = ""   # "windows" | "macos" | "linux" | "android" | "ios"


def set_flet_platform(p):
    """Record the Flet page platform once at startup (main.py's main()).

    `page.platform` is a PagePlatform ENUM, not a string — str() gives
    'PagePlatform.ANDROID', so the old str().lower() produced
    'pageplatform.android' and never matched 'android' (confirmed live: the
    desktop rail rendered on a phone because is_mobile() stayed False).
    Normalize to a bare token: prefer the enum's .value, else the part after
    the last '.', lowercased."""
    global _FLET_PLATFORM
    s = getattr(p, "value", None) or str(p or "")
    _FLET_PLATFORM = s.split(".")[-1].strip().lower()


def is_windows():
    return os.name == "nt"


def is_mobile():
    return _FLET_PLATFORM in ("android", "ios")


def has_automation():
    """The Automation screen generates Selenium/Playwright/Cypress projects
    into local folders and pushes them with git — needs a real desktop
    filesystem and toolchain, meaningless on a phone."""
    return not is_mobile()


def has_self_update():
    """Zipball-over-install self-update: Windows desktop only. Forbidden by
    App Store policy on iOS and replaced by store distribution on mobile."""
    return is_windows() and not is_mobile()


def open_folder(path):
    """Open a folder in the OS file explorer where supported (Windows
    Explorer via os.startfile). Safe no-op elsewhere — returns True only if
    an open was actually attempted, so callers can adjust their toast."""
    try:
        if is_windows():
            os.startfile(path)  # noqa: S606 — Windows-only, guarded above
            return True
    except Exception:
        pass
    return False
