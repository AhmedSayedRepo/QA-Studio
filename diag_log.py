"""diag_log.py — local-only diagnostic logging.

QA Studio is local-first (see README's Privacy section): nothing here is ever
sent anywhere. This module exists purely so a "best-effort, fail silently"
except block (there are many, by design — see DEV_ROADMAP.md) leaves a trail
on disk instead of vanishing completely, so a user-reported bug can actually
be diagnosed from their machine after the fact.

Usage:
    import diag_log
    try:
        risky_thing()
    except Exception as ex:
        diag_log.log("module.function", ex)   # swallow as before, now logged

`log()` NEVER raises — a failure to log must never break the caller's own
fail-soft behavior. Rotates at ~2MB x 2 backups so it can't grow unbounded.
"""
import os
import logging
import logging.handlers

def _data_dir():
    """Dir for the diagnostics log — the APPLICATION folder.

    Writes next to the app (the same folder qa_perf.log uses), so all of the
    app's diagnostics sit together where the user is running it, instead of a
    hidden ~/.qa_tool. _get_logger() falls back to ~/.qa_tool only if the app
    dir turns out not to be writable (e.g. a read-only install location).

    MOBILE stays on Flet's app-private FLET_APP_STORAGE_DATA: on an Android
    build the app dir (and ~) is NOT writable (resolves to /data), so every
    diag_log call would silently do nothing there — including the two most
    valuable ones, page.on_error and render()'s own except-block. The env var
    is read directly (not platform_caps.is_mobile()) because this module is
    imported before set_flet_platform() runs, so is_mobile() would still be
    False here."""
    d = os.environ.get("FLET_APP_STORAGE_DATA")
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            pass
    # Desktop: alongside the app (== qa_perf.log's location).
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".qa_tool")


CRED_DIR = _data_dir()
LOG_FILE = os.path.join(CRED_DIR, "diagnostics.log")
# Used only if the application folder can't be written to (read-only install).
_FALLBACK_DIR = os.path.join(os.path.expanduser("~"), ".qa_tool")

_logger = None


def _get_logger():
    global _logger, LOG_FILE
    if _logger is not None:
        return _logger
    lg = logging.getLogger("qa_studio.diag")
    # Try the application folder first; fall back to ~/.qa_tool only if that dir
    # isn't writable, so the log is never lost entirely. LOG_FILE is updated to
    # whichever location actually took, since settings.py surfaces it to the user.
    for _dir in (CRED_DIR, _FALLBACK_DIR):
        _file = os.path.join(_dir, "diagnostics.log")
        try:
            os.makedirs(_dir, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                _file, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter(
                "%(asctime)s  %(levelname)s  %(message)s"))
            lg.addHandler(handler)
            lg.setLevel(logging.INFO)
            lg.propagate = False
            LOG_FILE = _file
            break
        except Exception:
            # This dir wasn't usable — try the next. If none work, lg has no
            # handler and every log() call is a cheap no-op (logging is a
            # nice-to-have, never a requirement).
            continue
    _logger = lg
    return lg


def log(where, exc=None, level="warning"):
    """Record a swallowed exception (or plain message) to the local
    diagnostics file. `where` should be a short 'module.function' style tag
    so log lines are greppable. Never raises."""
    try:
        lg = _get_logger()
        fn = getattr(lg, level, lg.warning)
        if exc is not None:
            fn("%s: %s: %s", where, type(exc).__name__, exc)
        else:
            fn("%s", where)
    except Exception:
        pass


def log_warn(where, msg):
    """Record a plain warning message (no exception object) to the local
    diagnostics file. Never raises."""
    try:
        _get_logger().warning("%s: %s", where, msg)
    except Exception:
        pass
