"""tracker/http.py — one HTTP layer for every backend: retries, throttling,
429 handling, pagination, and status→typed-error mapping.

WHY CENTRALIZE THIS
Two forces meet here and they are the main operational risk of the Jira port:

  1. This app issues BURSTS. `run_titles`/`run_steps` drive ThreadPoolExecutor
     worker pools; a single story fans out into title generation, dedupe reads,
     per-case creates, and per-case step writes.
  2. Jira Cloud and Zephyr Scale Cloud throttle considerably harder than Azure
     DevOps, and Zephyr returns `Retry-After` that must actually be honoured.

Left to each adapter, this becomes N slightly-different retry loops and a
support burden of "it works for small sprints." Centralizing means the retry
policy, the backoff curve, and the throttle are one thing to reason about and
one thing to tune.

Uses `requests` — already a hard dependency of engine.py, so this adds nothing
to requirements.txt. Deliberately no `urllib3.Retry`: it cannot express
"honour Retry-After on 429 but give up immediately on 401", and it retries at a
layer where we can't map errors to our own types.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Dict, Iterator, Optional

try:
    import requests
except Exception:                                    # pragma: no cover
    requests = None

from .errors import (
    AuthFailed, ConflictError, NotFound, PermissionDenied, RateLimited,
    TrackerError, Unavailable,
)

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_ATTEMPTS = 5
_MAX_BACKOFF = 30.0


class RateLimiter:
    """Token bucket shared by every request on one backend.

    Sized in requests-per-second rather than per-worker so that RAISING the
    worker count can never raise the request rate — which is the failure mode
    that makes throttling look like a random, unreproducible hang. Workers
    queue here instead of at the server.

    `rate=0` disables it (Azure, which doesn't need it today).
    """

    def __init__(self, rate=0.0, burst=None):
        self.rate = float(rate or 0.0)
        self.capacity = float(burst if burst is not None else max(1.0, self.rate))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self):
        if self.rate <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity,
                                   self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = (1.0 - self._tokens) / self.rate
            time.sleep(min(deficit, 1.0))


def _retry_after_seconds(resp):
    """Read Retry-After. Servers send either delta-seconds or an HTTP date; we
    only honour the numeric form and fall back to computed backoff otherwise —
    a mis-parsed date that yields a huge sleep is worse than a short retry."""
    for header in ("Retry-After", "retry-after", "X-RateLimit-Reset"):
        raw = (resp.headers or {}).get(header)
        if not raw:
            continue
        try:
            val = float(str(raw).strip())
            if 0 <= val <= 300:
                return val
        except (TypeError, ValueError):
            continue
    return None


class TrackerSession:
    """A configured HTTP client for one backend connection."""

    def __init__(self, base_url="", auth=None, headers=None, backend="",
                 timeout=DEFAULT_TIMEOUT, max_attempts=DEFAULT_MAX_ATTEMPTS,
                 rate=0.0, burst=None, session=None):
        if requests is None:                          # pragma: no cover
            raise TrackerError("The 'requests' package is required.",
                               remedy="pip install requests", backend=backend)
        self.base_url = (base_url or "").rstrip("/")
        self.backend = backend
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))
        self.limiter = RateLimiter(rate=rate, burst=burst)
        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json",
                                      "User-Agent": "QA-Studio"})
        if headers:
            self._session.headers.update(headers)
        if auth is not None:
            self._session.auth = auth

    # ── error mapping ─────────────────────────────────────────────────────
    def _raise_for_status(self, resp, url):
        code = resp.status_code
        if code < 400:
            return
        snippet = ""
        try:
            snippet = (resp.text or "")[:200].replace("\n", " ").strip()
        except Exception:
            pass
        ctx = f"{self.backend or 'tracker'} {code} for {url}"

        if code == 401:
            raise AuthFailed(f"Authentication failed ({code}).",
                             remedy="Your token is wrong or expired — re-enter it in Setup.",
                             backend=self.backend, status=code)
        if code == 403:
            # A 403 carrying a rate-limit marker is throttling, not permissions.
            # Atlassian does exactly this, and treating it as "no access" sends
            # the user to fix permissions that were never the problem.
            blob = f"{snippet} {resp.headers}".lower()
            if "rate" in blob and "limit" in blob:
                raise RateLimited("Rate limited (403).",
                                  remedy="Too many requests — retrying automatically.",
                                  backend=self.backend, status=code,
                                  retry_after=_retry_after_seconds(resp))
            raise PermissionDenied(f"Access denied ({code}).",
                                   remedy="This account lacks permission for that operation.",
                                   backend=self.backend, status=code)
        if code == 404:
            raise NotFound(f"Not found ({code}).",
                           remedy="The project, plan or item may have been renamed or deleted.",
                           backend=self.backend, status=code)
        if code == 409:
            raise ConflictError(f"Conflict ({code}). {snippet}",
                                backend=self.backend, status=code)
        if code == 429:
            raise RateLimited("Rate limited (429).",
                              remedy="Too many requests — retrying automatically.",
                              backend=self.backend, status=code,
                              retry_after=_retry_after_seconds(resp))
        if code >= 500:
            raise Unavailable(f"Server error ({code}). {snippet}".strip(),
                              remedy="The service is having trouble — retrying.",
                              backend=self.backend, status=code)
        raise TrackerError(f"Request failed ({code}). {snippet}".strip(),
                           backend=self.backend, status=code)

    # ── core request ──────────────────────────────────────────────────────
    def request(self, method, path, params=None, json=None, timeout=None,
                parse=True):
        """Perform a request with throttling, retries and typed errors.

        Retries ONLY `retryable` failures (429/5xx/transport). A 401 or 404 is
        deterministic — retrying it just multiplies the latency of a failure
        the user is already waiting on.
        """
        url = path if str(path).startswith("http") else f"{self.base_url}/{str(path).lstrip('/')}"
        attempts = self.max_attempts
        last = None

        for attempt in range(1, attempts + 1):
            self.limiter.acquire()
            try:
                resp = self._session.request(
                    method, url, params=params, json=json,
                    timeout=timeout or self.timeout)
                self._raise_for_status(resp, url)
                if not parse:
                    return resp
                if not (resp.content or b"").strip():
                    return None
                try:
                    return resp.json()
                except ValueError:
                    return resp.text

            except TrackerError as exc:
                last = exc
                if not exc.retryable or attempt >= attempts:
                    raise
                delay = getattr(exc, "retry_after", None)
                if delay is None:
                    delay = min(_MAX_BACKOFF, (2 ** (attempt - 1)))
                # Jitter: worker pools fail in lockstep, and un-jittered backoff
                # makes them retry in lockstep too — re-triggering the throttle.
                time.sleep(max(0.0, float(delay)) + random.uniform(0, 0.4))

            except Exception as exc:                  # transport-level
                last = Unavailable(f"Cannot reach {self.backend or 'the server'}: "
                                   f"{type(exc).__name__}",
                                   remedy="Check your network, VPN or firewall.",
                                   backend=self.backend)
                if attempt >= attempts:
                    raise last from exc
                time.sleep(min(_MAX_BACKOFF, 2 ** (attempt - 1)) + random.uniform(0, 0.4))

        raise last or TrackerError("Request failed.", backend=self.backend)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    # ── pagination ────────────────────────────────────────────────────────
    def paginate(self, path, params=None, extract=None, cursor="startAt",
                 limit_param="maxResults", limit=100, max_pages=200):
        """Page through a list endpoint, yielding items.

        One helper because the three products disagree on paging and each
        disagreement is a silent-truncation bug waiting to happen:
            Azure  — continuation tokens
            Jira   — startAt / maxResults
            Zephyr — startAtId / limit (server may cap below what you asked)

        `max_pages` is a deliberate circuit breaker: a backend that ignores the
        cursor would otherwise loop forever re-reading page one.
        """
        params = dict(params or {})
        params[limit_param] = limit
        start = 0
        seen = 0

        for _ in range(max_pages):
            params[cursor] = start
            payload = self.get(path, params=params)
            items = (extract or (lambda p: p if isinstance(p, list) else
                                 (p or {}).get("values") or []))(payload)
            if not items:
                return
            for item in items:
                yield item
            seen += len(items)
            if isinstance(payload, dict):
                if payload.get("isLast") is True:
                    return
                total = payload.get("total")
                if isinstance(total, int) and seen >= total:
                    return
            if len(items) < limit:
                return
            start = seen

    def close(self):
        try:
            self._session.close()
        except Exception:
            pass


__all__ = ["TrackerSession", "RateLimiter", "DEFAULT_TIMEOUT"]
