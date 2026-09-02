"""Shared diagnostic redaction; the report URL rule predates failure analysis."""
import re


_SECRET_PARAM = re.compile(
    r"((?:access_token|refresh_token|id_token|token|api[_-]?key|apikey|key|"
    r"password|passwd|pwd|secret|client_secret|sig|signature|auth|session|sid)=)"
    r"[^&\s]+", re.I)


def mask_query_secrets(value) -> str:
    """Preserve the existing performance-report redaction contract."""
    return _SECRET_PARAM.sub(r"\1[REDACTED]", str(value or ""))
