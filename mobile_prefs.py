"""mobile_prefs.py — tiny, NON-secret local preference store for mobile-only
settings that must be readable synchronously, before the async OS-keychain
bootstrap (secure_store_mobile.py) can possibly have landed.

Why this exists separately from store.py/secure_store_mobile.py: a setting
like "require biometric unlock" has to be known BEFORE constructing the
SecureStorage service (it's a constructor option, AndroidOptions.
enforce_biometrics), which is itself the thing store.py's real credentials
live behind — a chicken-and-egg problem for anything credential-store-backed.
Since these are plain booleans/strings with no confidentiality requirement
(unlike API keys/PATs), a small unencrypted JSON file is the right amount of
mechanism, not a security gap.

Desktop is untouched: nothing here is imported/called unless
platform_caps.is_mobile() gates the call site.
"""
import json
import os

_cache = None


def _dir():
    """The PERSISTENT, writable data dir. On Android/iOS, Flet sets
    FLET_APP_STORAGE_DATA to the app-private files directory — the only place
    guaranteed writable AND surviving relaunch. `os.path.expanduser("~")`
    (the previous location) is NOT reliably either on Android: writes silently
    failed, so `onboarded` and `require_biometric` never persisted — the
    walkthrough replayed every launch and biometrics never engaged. Fall back
    to ~/.qa_tool on desktop, where the env var isn't set and ~ is real."""
    d = os.environ.get("FLET_APP_STORAGE_DATA")
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), ".qa_tool")


def _file():
    return os.path.join(_dir(), "mobile_prefs.json")


def _load():
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(_file(), "r", encoding="utf-8") as f:
            _cache = json.load(f)
        if not isinstance(_cache, dict):
            _cache = {}
    except Exception:
        _cache = {}
    return _cache


def get(key, default=None):
    return _load().get(key, default)


def set(key, value):
    """Write-through: updates the in-memory cache immediately (so a set()
    followed by a get() in the same process is always consistent) and
    persists to disk. Best-effort — a write failure is swallowed, same
    fail-soft posture as store.py's own save()."""
    d = _load()
    d[key] = value
    try:
        _f = _file()
        os.makedirs(os.path.dirname(_f), exist_ok=True)
        with open(_f, "w", encoding="utf-8") as f:
            json.dump(d, f)
        return True
    except Exception:
        return False
