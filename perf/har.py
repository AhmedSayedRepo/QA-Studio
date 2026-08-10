"""perf/har.py - build PerfScenarios from a captured HAR (real traffic).

A HAR (HTTP Archive) is the browser's exact record of what it sent: every
request's method, full URL, headers, and body. Importing it removes the guesswork
of prose extraction - the emitted load test replays REAL requests, so it is a
faithful test of the API instead of a best-effort reconstruction from English.

Pure/offline: stdlib only (json + urllib + datetime). No Flet / engine / tracker
imports - same "core stays clean" rule as extract.py. The Flet screen picks the
file; this module turns bytes into normalized PerfScenarios.

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from .models import Assertion, AssertionKind, PerfRequest, PerfScenario

# Headers the browser/transport manages or JMeter recomputes - never replay these.
# (Authorization, Cookie, Content-Type ARE kept: they're needed to hit real,
# authenticated endpoints. The emitted .jmx therefore embeds captured credentials
# - the caller warns the user, since it's their own local file.)
_DROP_HEADERS = {
    "host", "content-length", "connection", "accept-encoding", "proxy-connection",
    "keep-alive", "te", "transfer-encoding", "upgrade-insecure-requests",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "sec-fetch-user",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform", "dnt", "priority",
}
_STATIC_EXT = (".js", ".mjs", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
               ".webp", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".map",
               ".mp4", ".webm", ".avi", ".mov", ".mp3", ".wav", ".pdf")
_STATIC_MIME = ("image/", "font/", "text/css", "javascript", "text/javascript",
                "video/", "audio/")


def _is_static(url: str, mime: str) -> bool:
    path = (urlparse(url).path or "").lower()
    if any(path.endswith(e) for e in _STATIC_EXT):
        return True
    m = (mime or "").lower()
    return any(s in m for s in _STATIC_MIME)


def _clean_headers(entry_headers) -> dict:
    out: dict = {}
    for h in entry_headers or []:
        name = str((h or {}).get("name", "") or "").strip()
        if not name or name.startswith(":"):        # HTTP/2 pseudo-headers (:method…)
            continue
        if name.lower() in _DROP_HEADERS:
            continue
        out[name] = str((h or {}).get("value", "") or "")
    return out


def _parse_ts(s: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _host_matches(host: str, domains: List[str]) -> bool:
    host = (host or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def scenarios_from_har(har_path: str, include_domains: Optional[List[str]] = None,
                       title: str = "", keep_static: bool = False,
                       max_think_ms: int = 30000) -> List[PerfScenario]:
    """Parse a .har into a single PerfScenario of exact requests.

    include_domains: keep only requests whose host equals or is a subdomain of one
        of these (e.g. ["app.example.com"]). Empty/None keeps every non-static host
        - the usual first pass, then narrow once you see the noise.
    keep_static: include JS/CSS/image/font/media requests too (default: drop them,
        since load-testing your CDN's logo is rarely the goal).
    max_think_ms: cap the think-time derived from the real gap between requests, so
        a long pause while you read the page doesn't bloat the test's duration.

    Values are captured LITERALLY (no {{variables}}); this is exact replay. Use the
    Data CSV / a later parameterization pass to vary accounts or search terms.
    """
    with open(har_path, encoding="utf-8") as f:
        har = json.load(f)
    log = (har or {}).get("log") or {}
    entries = log.get("entries") or []
    domains = [d.strip().lower() for d in (include_domains or []) if d and d.strip()]

    kept: List[Tuple[PerfRequest, Optional[float]]] = []
    for e in entries:
        req = (e or {}).get("request") or {}
        resp = (e or {}).get("response") or {}
        url = str(req.get("url", "") or "")
        if not url or url.startswith(("data:", "blob:")):
            continue
        host = (urlparse(url).hostname or "").lower()
        if domains and not _host_matches(host, domains):
            continue
        mime = ((resp.get("content") or {}) or {}).get("mimeType") or ""
        if not keep_static and _is_static(url, mime):
            continue

        method = str(req.get("method", "") or "GET").upper()
        body = ""
        pd = req.get("postData")
        if isinstance(pd, dict):
            body = str(pd.get("text", "") or "")
        asserts: List[Assertion] = []
        status = resp.get("status")
        if isinstance(status, int) and 100 <= status < 600:
            asserts.append(Assertion(AssertionKind.STATUS, str(status)))

        pr = PerfRequest(
            method=method, url=url, headers=_clean_headers(req.get("headers")),
            body=body, assertions=asserts, think_ms=0,
            source_step=f"{method} {urlparse(url).path or '/'}"[:120])
        kept.append((pr, _parse_ts(e.get("startedDateTime"))))

    if not kept:
        return []

    # Second pass: derive think-time from the real gap to the NEXT kept request
    # (pacing lives AFTER a request), capped so idle reading time can't dominate.
    import dataclasses
    requests: List[PerfRequest] = []
    for i, (pr, ts) in enumerate(kept):
        think = 0
        if i + 1 < len(kept):
            nxt = kept[i + 1][1]
            if ts is not None and nxt is not None and nxt > ts:
                think = int(min(max((nxt - ts) * 1000.0, 0), max_think_ms))
        requests.append(dataclasses.replace(pr, think_ms=think))

    pages = log.get("pages") or []
    name = (title or (pages[0].get("title") if pages else "")
            or os.path.basename(har_path) or "Recorded session")
    return [PerfScenario(id="har", title=str(name), requests=requests,
                         variables=[], story_id="")]


__all__ = ["scenarios_from_har"]
