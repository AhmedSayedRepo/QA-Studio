"""Bounded, conservative diagnostics. Never acquire credentials or payloads."""
from __future__ import annotations

import math
import re
from urllib.parse import urlsplit, urlunsplit, quote, unquote

from diagnostic_safety import mask_query_secrets


REDACTED = "[REDACTED]"
MAX_TEXT = 4096
_SECRET_NAME = re.compile(
    r"password|passwd|passcode|pwd|authorization|cookie|api.?key|access.?token|"
    r"refresh.?token|id.?token|client.?secret|session|secret|credential|signature|"
    r"(?:^|[_-])(?:token|auth|key|sig)(?:$|[_-])", re.I)
_ASSIGNMENT = re.compile(
    r'''(?i)(["']?(?:[\w-]*(?:password|passwd|passcode|pwd|secret|token|api[_-]?key|cookie|authorization|session)[\w-]*|auth|key|sig|signature)["']?\s*[:=]\s*)(\[REDACTED\]|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\r\n,;&}\]]+)''')
_AUTH_LINE = re.compile(r"(?im)\b(?:authorization|proxy-authorization|set-cookie|cookie)\s*[:=][^\r\n]+")
_BEARER = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9+/_=.\-]+")
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s<>\"']+", re.I)
_DATA_URL = re.compile(r"\bdata:[^\s<>\"']+", re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def sensitive_name(name: str) -> bool:
    return bool(_SECRET_NAME.search(name))


def safe_url(value: str) -> str:
    """Drop ALL query, fragment and userinfo, including unfamiliar token names."""
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            return REDACTED
        host = parts.hostname
        if ":" in host:
            host = "[" + host + "]"
        if parts.port:
            host += ":" + str(parts.port)
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except Exception:
        return REDACTED


def sanitize_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    if not isinstance(value, str):
        raise TypeError("Diagnostic text must be a string")
    # Match before truncation so a length limit cannot expose a secret prefix.
    # Unstructured blobs above the bound are omitted, not partially retained.
    if len(value) > 65536:
        return "[diagnostic text omitted: size limit]"
    for secret in sorted(set(s for s in secrets if isinstance(s, str) and s), key=len, reverse=True):
        for variant in {secret, quote(secret, safe=""), unquote(secret)}:
            if variant:
                value = value.replace(variant, REDACTED)
    value = _URL.sub(lambda match: safe_url(match.group()), value)
    value = _DATA_URL.sub("[inline data omitted]", value)
    value = _AUTH_LINE.sub("[authentication metadata redacted]", value)
    value = _BEARER.sub(REDACTED, value)
    value = _JWT.sub(REDACTED, value)
    value = _ASSIGNMENT.sub(lambda match: match[1] + REDACTED, value)
    value = mask_query_secrets(value)
    return value[:MAX_TEXT]


def sanitize_tree(value, secrets: tuple[str, ...] = (), depth: int = 0):
    """Only JSON scalars/containers; omit binaries, opaque objects and deep blobs."""
    if depth > 12:
        return "[omitted: depth limit]"
    if isinstance(value, str):
        return sanitize_text(value, secrets)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (tuple, list)):
        return [sanitize_tree(v, secrets, depth + 1) for v in value[:128]]
    if isinstance(value, dict):
        # Attributes use {name, value}; apply the same rule as dictionary keys.
        if isinstance(value.get("name"), str) and sensitive_name(value["name"]):
            return {"name": sanitize_text(value["name"], secrets), "value": REDACTED}
        # Model field names are structural, not captured text. A typed value
        # such as 'id' must not rename execution_id and make the record unreadable.
        return {sanitize_text(k): (REDACTED if sensitive_name(k) else
                sanitize_text(safe_url(v), secrets) if k == "url" and isinstance(v, str) and v else
                sanitize_tree(v, secrets, depth + 1))
                for k, v in list(value.items())[:128] if isinstance(k, str)}
    return "[omitted: unsupported diagnostic value]"
