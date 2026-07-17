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


def reveal_export(page, path):
    """MOBILE_PLAN.md Phase 0: every export writer (Regression/Sprint Plan/
    AI Usage/Task Manager) hands back a path under a local app-data folder.
    On desktop that's real and open_folder() pops Explorer at it. On mobile
    that path is inside the app's sandboxed storage — there is no Explorer,
    the user has no filesystem access to it at all, and a toast that just
    prints the path is meaningless. The only way the file actually leaves
    the device is the OS share sheet (ft.Share/ft.ShareFile — confirmed part
    of core flet==0.85.3, no new native dependency, same posture as
    mobile_wakelock.py).

    Fire-and-forget via page.run_task(), same async-bridging shape used
    throughout this codebase's mobile modules (see secure_store_mobile.py's
    docstring for why: share_files() is `async def`, and blocking the
    event-loop thread from a sync on_click handler would deadlock).
    Desktop path is untouched — this only ever does something when
    is_mobile() is True.
    """
    if not is_mobile() or page is None:
        return False
    try:
        import flet as ft
        if not hasattr(ft, "Share"):
            return False
    except Exception:
        return False

    async def _do():
        try:
            svc = ft.Share()
            page.services.append(svc)
            page.update()
            await svc.share_files(
                [ft.ShareFile.from_path(path, name=os.path.basename(path))],
                title="Save or send file")
        except Exception:
            pass

    try:
        page.run_task(_do)
        return True
    except Exception:
        return False
