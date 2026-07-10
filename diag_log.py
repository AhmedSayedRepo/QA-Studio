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

CRED_DIR = os.path.join(os.path.expanduser("~"), ".qa_tool")
LOG_FILE = os.path.join(CRED_DIR, "diagnostics.log")

_logger = None


def _get_logger():
    global _logger
    if _logger is not None:
        return _logger
    lg = logging.getLogger("qa_studio.diag")
    try:
        os.makedirs(CRED_DIR, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)s  %(message)s"))
        lg.addHandler(handler)
        lg.setLevel(logging.INFO)
        lg.propagate = False
    except Exception:
        # No writable home dir, locked-down environment, etc. — logging is a
        # nice-to-have, never a requirement. Fall back to a no-op logger with
        # no handlers so lg.info()/lg.warning() calls are just cheap no-ops.
        pass
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
