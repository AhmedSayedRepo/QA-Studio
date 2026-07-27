"""tracker/errors.py — one normalized error vocabulary for every backend.

WHY THIS EXISTS
Today every Azure failure mode is a `RuntimeError` carrying a hand-written
English sentence, built inside `engine._azure_get` (401 → "Authentication
failed (401). Check your PAT and its scopes.", 404 → "Not found (404). Check
the project name spelling.", …). That works exactly as long as there is one
backend: the UI can show `str(e)` and be right.

With two backends it breaks down twice over:
  1. The UI cannot BRANCH on a failure — "is this retryable?", "should I send
     the user to Setup?", "is this a permissions problem?" — without string
     matching, which is unmaintainable.
  2. The messages are Azure-specific. "Check your PAT and its scopes" is wrong
     advice for a Jira API token, and "check the project name spelling" is
     actively misleading when the real cause was an unconfigured organization
     (the exact bug fixed in cont'd #102).

So: backends raise a TYPED error; the type carries the semantics and the
message carries the backend-specific remedy. Callers branch on the type.

Every error keeps `str(e)` useful, so existing `self._err(str(e))` call sites
in the UI keep working unchanged if they're pointed at these.
"""
from __future__ import annotations


class TrackerError(Exception):
    """Base for every backend failure. Catch this to catch them all.

    `remedy` is the actionable half of the message — what the USER should do —
    kept separate so the UI can render it differently (e.g. as a Setup link)
    without re-parsing the sentence.
    """

    #: Whether retrying the identical request could plausibly succeed.
    retryable = False

    def __init__(self, message, remedy="", backend="", status=None):
        self.message = str(message)
        self.remedy = remedy or ""
        self.backend = backend or ""
        self.status = status
        super().__init__(self.full_message())

    def full_message(self):
        msg = (self.message or "").strip()
        remedy = (self.remedy or "").strip()
        # Don't repeat advice the message already carries. engine.py's own
        # errors are full sentences that ALREADY end in the remedy ("...(401).
        # Check your PAT and its scopes."), and the Azure adapter re-attaches a
        # remedy when translating them — which produced the doubled tail
        # "Check your PAT and its scopes. Check your PAT and its scopes."
        if remedy and remedy.lower() in msg.lower():
            return msg
        return " ".join(p for p in (msg, remedy) if p)


class NotConfigured(TrackerError):
    """A required connection setting is missing (org/site/project/token).

    Distinct from AuthFailed on purpose: nothing was rejected, there was simply
    nothing to send. This is the class that the old hardcoded-org default used
    to HIDE — a blank org produced a 404 that read as a spelling mistake. The
    UI should route this straight to Setup, never to "check your credentials".
    """


class AuthFailed(TrackerError):
    """Credentials were presented and rejected (401). Token wrong/expired."""


class PermissionDenied(TrackerError):
    """Authenticated, but this account lacks rights for the operation (403)."""


class NotFound(TrackerError):
    """The addressed object does not exist (404)."""


class RateLimited(TrackerError):
    """Throttled (429, or 403 with a rate-limit marker).

    Carries `retry_after` (seconds) when the server supplied it. This matters
    far more for Jira/Zephyr Cloud than for Azure: `run_titles`/`run_steps`
    drive worker pools that issue bursts of calls, and both Atlassian and
    Zephyr throttle harder than Azure DevOps does.
    """

    retryable = True

    def __init__(self, message, remedy="", backend="", status=None, retry_after=None):
        self.retry_after = retry_after
        super().__init__(message, remedy=remedy, backend=backend, status=status)


class Unavailable(TrackerError):
    """Transport/server-side problem: DNS, TLS, timeout, connection reset, 5xx."""

    retryable = True


class BackendUnsupported(TrackerError):
    """The active backend genuinely cannot do this.

    NOT a bug and NOT a failure of the call — an honest capability gap (see
    tracker.base.Capability). Zephyr Squad has no folder tree; a backend
    without `Capability.CHILD_TASKS` cannot create sub-tasks. The UI should
    disable the affected control rather than let the user hit this, but the
    error exists so that a missed guard degrades into a clear message instead
    of an AttributeError.
    """


class ConflictError(TrackerError):
    """The write collided with existing state (duplicate key, stale version)."""


__all__ = [
    "TrackerError", "NotConfigured", "AuthFailed", "PermissionDenied",
    "NotFound", "RateLimited", "Unavailable", "BackendUnsupported",
    "ConflictError",
]
