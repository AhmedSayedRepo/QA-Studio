"""perf/token_prefetch.py - log each user in and collect a per-user auth token.

The "prepare tokens" step for data-driven load tests: given rows of
users/passwords, hit the login endpoint for each one, pull the bearer token out
of the JSON body (or a response header), and return the rows with a `token`
column added — ready to become QA Studio's Data CSV (used as `Bearer {{token}}`).

Stdlib only (urllib). The HTTP call is behind an injectable `login_fn` so the
concurrency/aggregation logic is unit-tested without a network. No Flet/engine.

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl

# login_fn(username, password) -> (token, error). token == "" means failed.
LoginFn = Callable[[str, str], Tuple[str, str]]


@dataclass
class LoginConfig:
    url: str
    method: str = "POST"
    body_format: str = "json"            # "json" | "form"
    api_user_field: str = "email"        # field name the API expects
    api_pass_field: str = "password"
    token_json_path: str = "access_token"  # dotted path, e.g. "data.access_token"
    token_header: str = ""               # OR a response header name (e.g. "authorization")
    extra_headers: Dict[str, str] = field(default_factory=dict)
    strip_bearer: bool = True
    timeout_s: int = 20


def _dig(obj, path: str):
    cur = obj
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return ""
    return cur if isinstance(cur, (str, int, float)) else ""


def make_http_login(cfg: LoginConfig) -> LoginFn:
    """Build a login_fn that POSTs credentials and extracts the token."""
    def login(username: str, password: str) -> Tuple[str, str]:
        if cfg.body_format == "form":
            from urllib.parse import urlencode
            data = urlencode({cfg.api_user_field: username,
                              cfg.api_pass_field: password}).encode()
            ctype = "application/x-www-form-urlencoded"
        else:
            data = json.dumps({cfg.api_user_field: username,
                               cfg.api_pass_field: password}).encode()
            ctype = "application/json"
        headers = {"Content-Type": ctype, "Accept": "application/json"}
        headers.update(cfg.extra_headers or {})
        req = urllib.request.Request(cfg.url, data=data, headers=headers,
                                     method=cfg.method or "POST")
        try:
            with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
                body = resp.read().decode("utf-8", "replace")
                status = getattr(resp, "status", 0)
                if cfg.token_header:
                    token = resp.headers.get(cfg.token_header, "") or ""
                else:
                    try:
                        token = str(_dig(json.loads(body), cfg.token_json_path))
                    except Exception:
                        return "", f"couldn't parse JSON (HTTP {status})"
                token = (token or "").strip()
                if cfg.strip_bearer and token.lower().startswith("bearer "):
                    token = token[7:].strip()
                return (token, "") if token else ("", f"no token in response (HTTP {status})")
        except urllib.error.HTTPError as ex:
            return "", f"HTTP {ex.code}"
        except Exception as ex:
            return "", str(ex)[:100]
    return login


# ---- auto-detect a login config from a recorded login (HAR) -----------------

_LOGIN_URL_RE = re.compile(r"login|auth|signin|sign-in|session|token|oauth", re.I)
_PASS_RE = re.compile(r"pass|pwd", re.I)
_USER_RE = re.compile(r"user|email|login|mail|phone|account", re.I)
_TOKEN_KEY_RE = re.compile(r"token|jwt|access|id_token|bearer|auth", re.I)


def _parse_login_body(text: str, mime: str) -> Tuple[str, Dict[str, object]]:
    text = text or ""
    if "json" in (mime or "").lower() or text.strip().startswith("{"):
        try:
            d = json.loads(text)
            if isinstance(d, dict):
                return "json", dict(d)
        except Exception:
            pass
    return "form", dict(parse_qsl(text))


def _pick_login_fields(fields: Dict[str, object]) -> Tuple[str, str]:
    names = list(fields.keys())
    pass_f = next((n for n in names if _PASS_RE.search(n)), "")
    user_f = next((n for n in names if _USER_RE.search(n)), "")
    if not user_f:
        user_f = next((n for n, v in fields.items() if isinstance(v, str) and "@" in v), "")
    if not pass_f:
        pass_f = next((n for n in names if n != user_f), "")
    if not user_f:
        user_f = next((n for n in names if n != pass_f), "")
    return user_f or "email", pass_f or "password"


def _find_token_path(obj, prefix: str = "") -> str:
    """Dotted path to a likely token string inside a JSON response, or ''."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and _TOKEN_KEY_RE.search(k) and len(v) >= 16:
                return f"{prefix}{k}"
        for k, v in obj.items():
            if isinstance(v, str) and v.startswith("eyJ") and len(v) >= 20:
                return f"{prefix}{k}"
        for k, v in obj.items():
            p = _find_token_path(v, f"{prefix}{k}.")
            if p:
                return p
    return ""


def _pick_login_entry(entries):
    posts = [e for e in entries
             if ((e.get("request") or {}).get("method", "").upper() == "POST"
                 and (e.get("request") or {}).get("postData"))]

    def score(e):
        req = e.get("request") or {}
        url = (req.get("url") or "").lower()
        body = (req.get("postData") or {}).get("text", "") or ""
        return (2 if _LOGIN_URL_RE.search(url) else 0) + (1 if _PASS_RE.search(body) else 0)

    posts.sort(key=score, reverse=True)
    return posts[0] if (posts and score(posts[0]) > 0) else (posts[0] if posts else None)


def detect_login_config_from_har(har_path: str) -> dict:
    """Best-effort: read a HAR of ONE login and infer the LoginConfig fields, so the
    user doesn't type them. Returns {ok, note, url, method, body_format, user_field,
    pass_field, token_json_path, token_header, fields}."""
    with open(har_path, encoding="utf-8") as f:
        har = json.load(f)
    entries = ((har or {}).get("log") or {}).get("entries") or []
    e = _pick_login_entry(entries)
    if not e:
        return {"ok": False, "note": "No POST login request with a body was found in that HAR."}
    req = e.get("request") or {}
    resp = e.get("response") or {}
    pd = req.get("postData") or {}
    body_format, fields = _parse_login_body(pd.get("text", ""), pd.get("mimeType", ""))
    user_f, pass_f = _pick_login_fields(fields)

    token_json_path, token_header = "", ""
    content = resp.get("content") or {}
    body_text = content.get("text", "") or ""
    if content.get("encoding") == "base64" and body_text:
        try:
            body_text = base64.b64decode(body_text).decode("utf-8", "replace")
        except Exception:
            body_text = ""
    try:
        token_json_path = _find_token_path(json.loads(body_text))
    except Exception:
        token_json_path = ""
    if not token_json_path:
        for h in (resp.get("headers") or []):
            name = (h.get("name", "") or "")
            if name.lower() == "authorization" or (_TOKEN_KEY_RE.search(name)
                                                   and (h.get("value") or "")):
                token_header = name
                break

    note = ""
    if not token_json_path and not token_header:
        note = ("Login request detected, but the token wasn't visible in the response "
                "(the HAR may have been saved without response bodies). Set the token "
                "path/header manually.")
    return {"ok": True, "note": note, "url": req.get("url", ""),
            "method": (req.get("method", "POST") or "POST").upper(),
            "body_format": body_format, "user_field": user_f, "pass_field": pass_f,
            "token_json_path": token_json_path, "token_header": token_header,
            "fields": list(fields.keys())}


def prefetch_tokens(rows: List[dict], login_fn: LoginFn, user_col: str, pass_col: str,
                    token_col: str = "token", concurrency: int = 20, retries: int = 2,
                    on_progress: Optional[Callable[[int, int], None]] = None
                    ) -> Tuple[List[dict], int, List[Tuple[str, str]]]:
    """Log every row in concurrently and return (rows_with_token, ok_count, failures).

    `failures` is a list of (username, error). Each output row is the original row
    plus `token_col` (empty string when that user's login failed)."""
    n = len(rows)
    results: List[Optional[Tuple[str, str]]] = [None] * n

    def work(i: int) -> int:
        u = str(rows[i].get(user_col, "") or "")
        p = str(rows[i].get(pass_col, "") or "")
        token, err = "", "unknown"
        for attempt in range(max(0, retries) + 1):
            token, err = login_fn(u, p)
            if token:
                break
            time.sleep(min(0.4 * (attempt + 1), 1.5))    # small backoff
        results[i] = (token, err)
        return i

    if n:
        with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            for done, _ in enumerate(ex.map(work, range(n)), 1):
                if on_progress:
                    on_progress(done, n)

    out: List[dict] = []
    ok = 0
    failures: List[Tuple[str, str]] = []
    for i, r in enumerate(rows):
        token, err = results[i] or ("", "no result")
        rr = dict(r)
        rr[token_col] = token
        out.append(rr)
        if token:
            ok += 1
        else:
            failures.append((str(r.get(user_col, "")), err))
    return out, ok, failures


__all__ = ["LoginConfig", "make_http_login", "prefetch_tokens",
           "detect_login_config_from_har"]
