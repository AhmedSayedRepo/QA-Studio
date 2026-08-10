"""perf/curl.py - build a PerfScenario from pasted cURL command(s).

Pure/offline (stdlib only: re + shlex + urllib). Turns one or more cURL commands
- e.g. DevTools' "Copy as cURL (bash)" - into exact PerfRequests (method, URL,
headers, body), the same normalized IR the HAR importer produces. No Flet /
engine / tracker imports, same "core stays clean" rule as extract.py / har.py.

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

import base64
import re
import shlex
from typing import List, Optional
from urllib.parse import urlparse

from .models import PerfRequest, PerfScenario

# Browser/transport-managed headers we never replay (JMeter recomputes them).
_DROP_HEADERS = {
    "host", "content-length", "connection", "accept-encoding", "proxy-connection",
    "keep-alive", "te", "transfer-encoding",
}
# Flags that take a VALUE we don't use — consume the value so it isn't mistaken
# for the URL (e.g. `--max-time 10`, `-o out.bin`).
_SKIP_VALUE_FLAGS = {
    "-o", "--output", "-w", "--write-out", "--cacert", "--cert", "--key",
    "--cert-type", "-x", "--proxy", "--max-time", "--connect-timeout", "--retry",
    "--limit-rate", "-m", "--form-string", "-F", "--form", "-T", "--upload-file",
    "--resolve", "--interface",
}


def _split_commands(text: str) -> List[str]:
    """Split a blob into individual curl commands, joining line-continuations
    (bash `\\`, Windows `^`) first. Handles several pasted commands at once."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[\\^]\s*\n", " ", t)          # line continuations -> space
    # Start a new command at each 'curl' token.
    parts = re.split(r"(?m)(?=\bcurl\b)", t)
    return [p.strip() for p in parts if p.strip() and re.search(r"\bcurl\b", p)]


def _tokenize(cmd: str) -> List[str]:
    cmd = cmd.replace("$'", "'")               # bash ANSI-C quoting -> plain quote
    try:
        return shlex.split(cmd, posix=True)
    except Exception:
        return cmd.split()


def _parse_one(cmd: str) -> Optional[PerfRequest]:
    toks = _tokenize(cmd)
    if toks and toks[0] == "curl":
        toks = toks[1:]
    method = None
    url = ""
    headers: dict = {}
    body = ""
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        low = t.lower()

        def val():
            nonlocal i
            i += 1
            return toks[i] if i < n else ""

        if t in ("-X", "--request"):
            method = (val() or "GET").upper()
        elif t in ("-H", "--header"):
            h = val()
            if ":" in h:
                k, v = h.split(":", 1)
                if k.strip() and not k.strip().startswith(":"):
                    headers[k.strip()] = v.strip()
        elif t in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii",
                   "--data-urlencode"):
            piece = val()
            body = (body + "&" + piece) if body else piece
        elif t in ("-b", "--cookie"):
            headers["Cookie"] = val()
        elif t in ("-e", "--referer"):
            headers["Referer"] = val()
        elif t in ("-A", "--user-agent"):
            headers["User-Agent"] = val()
        elif t in ("-u", "--user"):
            cred = val()
            headers["Authorization"] = "Basic " + base64.b64encode(cred.encode()).decode()
        elif t == "--url":
            url = val()
        elif t in _SKIP_VALUE_FLAGS:
            val()                              # consume + ignore the value
        elif low.startswith(("http://", "https://")):
            url = t
        elif t.startswith("-"):
            pass                               # unknown boolean flag -> ignore
        elif not url and ("/" in t or "." in t):
            url = t                            # positional URL (maybe scheme-less)
        i += 1

    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/") if "." in url.split("/")[0] else url
    if not method:
        method = "POST" if body else "GET"
    for k in list(headers):
        if k.lower() in _DROP_HEADERS:
            del headers[k]
    return PerfRequest(method=method, url=url, headers=headers, body=body,
                       assertions=[], think_ms=0,
                       source_step=f"{method} {urlparse(url).path or '/'}"[:120])


def scenarios_from_curl(text: str, title: str = "") -> List[PerfScenario]:
    """Parse one or more cURL commands into a single PerfScenario (each command
    becomes one request, in order). Returns [] if nothing parseable is found."""
    reqs = []
    for cmd in _split_commands(text or ""):
        r = _parse_one(cmd)
        if r:
            reqs.append(r)
    if not reqs:
        return []
    return [PerfScenario(id="curl", title=title or "Pasted cURL",
                         requests=reqs, variables=[], story_id="")]


__all__ = ["scenarios_from_curl"]
