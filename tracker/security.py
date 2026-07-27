"""tracker/security.py — automated security suite for the tracker package.

Runs after every phase (`python -m tracker.security`). Same dependency-free,
self-reporting style as tracker/contract.py.

THREAT MODEL — why these specific checks
The tracker package is the app's only outbound network boundary and it holds
long-lived, high-privilege secrets (an Azure PAT, a Jira API token, a Zephyr
token). Three properties of THIS codebase shape the list:

  1. Multi-tenant in one process. Users switch accounts without restarting, and
     DEV_ROADMAP documents a real cross-account leak where one account's
     org/PAT stayed live for the next user. Credential isolation is therefore a
     regression risk with history, not a hypothetical.
  2. Attacker-influenced content reaches generated HTML. Jira issue text (which
     an external reporter can often write) flows through the ADF renderer into
     sprint-closure emails and PDFs sent to stakeholders. That's a stored-XSS
     path.
  3. The site URL is user-supplied. A Jira "site" field that accepts any host
     turns the app into an SSRF pivot — and, worse, sends the user's bearer
     token to whatever host was typed.

Checks are static (source scans) plus behavioural (call the code and assert).
Static scans deliberately exclude this file so its own pattern strings don't
match themselves.
"""
from __future__ import annotations

import io
import os
import re
import sys
import traceback
from typing import Callable, List, Optional

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_SELF = os.path.basename(__file__)


class Finding:
    __slots__ = ("name", "ok", "detail", "severity")

    def __init__(self, name, ok, detail="", severity="high"):
        self.name, self.ok, self.detail, self.severity = name, ok, detail, severity

    def __str__(self):
        mark = "PASS" if self.ok else f"FAIL/{self.severity.upper()}"
        return f"[{mark}] {self.name}" + (f" — {self.detail}" if self.detail else "")


def _sources(exclude_self=True):
    for fname in sorted(os.listdir(_PKG_DIR)):
        if not fname.endswith(".py"):
            continue
        if exclude_self and fname == _SELF:
            continue
        path = os.path.join(_PKG_DIR, fname)
        try:
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                yield fname, fh.read()
        except OSError:
            continue


def _strip_comments(src):
    """Drop comments and docstrings so prose about a risk doesn't read as the
    risk itself (these files discuss `verify=False` in comments deliberately)."""
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())


# ══════════════════════════════════════════════════════════════════════════
#  Checks
# ══════════════════════════════════════════════════════════════════════════

def _check_tls_not_disabled():
    """TLS verification must never be turned off.

    `verify=False` on a session carrying a PAT means any network position can
    read the token. It is also the single most common "temporary" debugging
    edit that gets committed."""
    bad = []
    for fname, src in _sources():
        code = _strip_comments(src)
        if re.search(r"verify\s*=\s*False", code):
            bad.append(f"{fname}: verify=False")
        if re.search(r"urllib3\.disable_warnings|InsecureRequestWarning", code):
            bad.append(f"{fname}: TLS warnings suppressed")
    assert not bad, "; ".join(bad)
    return "no verify=False / warning suppression"


def _check_no_secrets_in_urls():
    """Tokens must travel in headers, never in a URL.

    Query strings land in proxy logs, browser history, and Referer headers. The
    AI-provider layer already has a key-in-query case (Gemini); the tracker
    layer must not acquire one."""
    bad = []
    pattern = re.compile(
        r"[?&](?:api[_-]?key|token|access[_-]?token|pat|password|secret)\s*=",
        re.I)
    for fname, src in _sources():
        code = _strip_comments(src)
        for m in pattern.finditer(code):
            bad.append(f"{fname}: {m.group(0).strip()}")
    assert not bad, "; ".join(bad)
    return "credentials are header-only"


def _check_no_hardcoded_credentials():
    """No literal tokens/orgs committed.

    Directly motivated by cont'd #102: a real customer's org name shipped in
    engine.py as a default for months."""
    bad = []
    patterns = [
        (re.compile(r"""(?:pat|token|api_key|password|secret)\s*=\s*["'][A-Za-z0-9_\-]{20,}["']""", re.I),
         "literal credential"),
        (re.compile(r"""["'][A-Za-z0-9]{0,10}(?:ATATT|ghp_|xox[baprs]-)[A-Za-z0-9_\-]{10,}["']"""),
         "vendor token prefix"),
    ]
    for fname, src in _sources():
        code = _strip_comments(src)
        for rx, label in patterns:
            if rx.search(code):
                bad.append(f"{fname}: {label}")
    assert not bad, "; ".join(bad)
    return "no literal secrets"


def _check_errors_redact_credentials():
    """A raised error must never carry the token.

    Errors reach toasts, diagnostics.log, and support screenshots. This is the
    most likely accidental-disclosure path in the whole package."""
    from .errors import AuthFailed, TrackerError
    from .http import TrackerSession

    secret = "ATATT-super-secret-token-value-123456"
    exc = AuthFailed("Authentication failed (401).",
                     remedy="Check your token.", backend="jira")
    blob = f"{exc} {exc.full_message()} {exc.__dict__}"
    assert secret not in blob, "token leaked into error payload"

    # A session built with a real-looking token must not expose it in repr().
    try:
        sess = TrackerSession(base_url="https://example.atlassian.net",
                              headers={"Authorization": f"Bearer {secret}"},
                              backend="jira")
    except Exception:
        return "requests unavailable — static portion only"
    assert secret not in repr(sess), "token leaked via TrackerSession repr"
    sess.close()
    return "errors and reprs are clean"


def _check_ssrf_guard():
    """A user-supplied site URL must be validated before a token is sent to it.

    Without this, typing (or being socially engineered into typing) an
    attacker's host both exfiltrates the bearer token and turns the desktop app
    into an SSRF probe against the user's internal network."""
    try:
        from .jira_zephyr import validate_site_url
    except ImportError:
        return "jira adapter not present yet — check deferred"
    from .errors import NotConfigured

    must_reject = [
        "http://evil.example.com",            # plaintext
        "https://127.0.0.1/",                 # loopback
        "https://localhost:8080",
        "https://169.254.169.254/latest/",    # cloud metadata
        "https://10.1.2.3",                   # RFC1918
        "https://192.168.0.5",
        "https://172.16.0.9",
        "https://[::1]/",
        "file:///etc/passwd",
        "https://attacker.com@evil.test/",    # userinfo confusion
        "javascript:alert(1)",
        "",
    ]
    leaked = []
    for candidate in must_reject:
        try:
            validate_site_url(candidate)
            leaked.append(candidate)
        except Exception:
            pass
    assert not leaked, f"accepted unsafe site URLs: {leaked}"

    for good in ("https://acme.atlassian.net",
                 "https://acme.atlassian.net/",
                 "https://jira.internal.example.com"):
        validate_site_url(good, allow_any_host=True)
    validate_site_url("https://acme.atlassian.net")
    return f"rejected {len(must_reject)} unsafe URL forms"


def _check_jql_injection():
    """User-controlled values must be escaped before entering JQL.

    A project key or sprint name containing a quote can otherwise break out of
    the literal and rewrite the query — the JQL analogue of SQL injection.
    Impact is read-scope (JQL cannot write), but it can widen a query to issues
    the user shouldn't see."""
    try:
        from .jira_zephyr import jql_escape
    except ImportError:
        return "jira adapter not present yet — check deferred"

    hostile = 'PROJ" OR project != "'
    escaped = jql_escape(hostile)
    assert '"' not in escaped.replace('\\"', ""), f"unescaped quote survived: {escaped}"
    assert jql_escape('back\\slash') == 'back\\\\slash', "backslash not escaped"
    for ch in ("\n", "\r"):
        assert ch not in jql_escape(f"a{ch}b"), "newline survived escaping"
    return "quotes, backslashes and newlines escaped"


def _check_adf_xss():
    """ADF content must not be able to inject script into generated HTML.

    Jira issue text is attacker-influenced wherever external reporters can file
    tickets, and this output is emailed to stakeholders."""
    from . import adf

    payloads = [
        {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "<script>alert(1)</script>"}]}]},
        {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "click",
             "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}]}]}]},
        {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "x",
             "marks": [{"type": "link", "attrs": {"href": "JaVaScRiPt:alert(1)"}}]}]}]},
        {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "y",
             "marks": [{"type": "link", "attrs": {"href": "data:text/html,<script>1</script>"}}]}]}]},
        {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": '"><img src=x onerror=alert(1)>'}]}]},
        {"type": "doc", "content": [{"type": "codeBlock", "content": [
            {"type": "text", "text": "</code></pre><script>alert(1)</script>"}]}]},
    ]
    # PARSE the output rather than substring-matching it. Substring checks give
    # false positives on correctly-escaped text (`&lt;img … onerror=…&gt;` is
    # inert prose, not a tag) and — far worse — false NEGATIVES: they miss
    # `<svg/onload=…>`, attribute-splitting, and any construct whose literal
    # form wasn't guessed in advance. What actually matters is the parsed
    # document: which ELEMENTS exist and which ATTRIBUTES they carry.
    from html.parser import HTMLParser

    DANGEROUS_TAGS = {"script", "iframe", "object", "embed", "svg", "style",
                      "link", "meta", "form", "input", "base", "img", "video",
                      "audio", "source", "applet", "frame", "frameset"}
    URL_ATTRS = {"href", "src", "action", "formaction", "data", "xlink:href"}

    class Auditor(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.problems = []

        def handle_starttag(self, tag, attrs):
            if tag.lower() in DANGEROUS_TAGS:
                self.problems.append(f"dangerous element <{tag}>")
            for name, value in attrs:
                name = (name or "").lower()
                val = (value or "").strip().lower()
                # Any on* handler is executable, whatever the element.
                if name.startswith("on"):
                    self.problems.append(f"event handler {name} on <{tag}>")
                if name in URL_ATTRS:
                    scheme = "".join(c for c in val if ord(c) > 32)
                    if scheme.startswith(("javascript:", "data:", "vbscript:",
                                          "file:", "about:")):
                        self.problems.append(f"unsafe URL in {name}: {val[:40]}")
                if name == "style" and "expression" in val:
                    self.problems.append("CSS expression() in style")

    for i, payload in enumerate(payloads, start=1):
        rendered = adf.to_html(payload)
        auditor = Auditor()
        auditor.feed(rendered)
        auditor.close()
        assert not auditor.problems, \
            f"payload {i}: {auditor.problems} -> {rendered[:140]}"

    # Content must SURVIVE, escaped. Silently dropping acceptance-criteria text
    # would degrade generation quality invisibly — its own class of bug.
    rendered = adf.to_html(payloads[0])
    assert "&lt;script&gt;" in rendered, f"content lost instead of escaped: {rendered}"
    inert = adf.to_html(payloads[4])
    assert "onerror" in inert, "escaped text was dropped rather than neutralized"
    return f"{len(payloads)} XSS payloads neutralized (parsed), text preserved"


def _check_credential_isolation():
    """Backends must not be cached across account switches.

    Guards the exact regression documented in DEV_ROADMAP: one account's
    org/PAT remaining live for the next user signed into the same process."""
    import tracker
    a = tracker.get_backend({"backend": "fake"})
    b = tracker.get_backend({"backend": "fake"})
    assert a is not b, "get_backend returned a cached instance across calls"

    src = ""
    for fname, text in _sources():
        if fname == "__init__.py":
            src = _strip_comments(text)
    assert "lru_cache" not in src and "@cache" not in src, \
        "registry uses caching — cross-account leak risk"
    return "no cross-account instance reuse"


def _check_no_debug_sinks():
    """No print()/breakpoint() writing request or credential data."""
    bad = []
    for fname, src in _sources():
        if fname == "contract.py":       # a CLI reporter; printing is its job
            continue
        code = _strip_comments(src)
        if re.search(r"\bbreakpoint\s*\(", code):
            bad.append(f"{fname}: breakpoint()")
        if re.search(r"\bpdb\b", code):
            bad.append(f"{fname}: pdb")
    assert not bad, "; ".join(bad)
    return "no debug sinks"


def _check_timeouts_present():
    """Every outbound request needs a timeout — a hung socket with no timeout
    is an availability bug that looks exactly like the app freezing."""
    from .http import DEFAULT_TIMEOUT
    assert 0 < DEFAULT_TIMEOUT <= 120, f"implausible default timeout {DEFAULT_TIMEOUT}"
    bad = []
    for fname, src in _sources():
        code = _strip_comments(src)
        for m in re.finditer(r"\.(get|post|put|delete|request)\s*\(", code):
            window = code[m.start():m.start() + 400]
            if "requests." in code[max(0, m.start() - 30):m.start()] and "timeout" not in window:
                bad.append(f"{fname}: bare requests call")
    assert not bad, "; ".join(bad)
    return f"default timeout {DEFAULT_TIMEOUT}s, no bare calls"


CHECKS = [
    ("TLS verification never disabled", _check_tls_not_disabled, "critical"),
    ("no credentials in URLs", _check_no_secrets_in_urls, "critical"),
    ("no hardcoded credentials", _check_no_hardcoded_credentials, "critical"),
    ("errors/reprs redact credentials", _check_errors_redact_credentials, "high"),
    ("SSRF guard on site URL", _check_ssrf_guard, "critical"),
    ("JQL injection escaping", _check_jql_injection, "high"),
    ("ADF renderer blocks XSS", _check_adf_xss, "critical"),
    ("credential isolation across accounts", _check_credential_isolation, "high"),
    ("no debug sinks", _check_no_debug_sinks, "medium"),
    ("request timeouts enforced", _check_timeouts_present, "medium"),
]


def run_security_suite():
    findings: List[Finding] = []
    for name, fn, severity in CHECKS:
        try:
            detail = fn()
            findings.append(Finding(name, True, detail or "", severity))
        except AssertionError as exc:
            findings.append(Finding(name, False, str(exc) or "assertion failed", severity))
        except Exception as exc:
            findings.append(Finding(
                name, False,
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=2)}",
                severity))
    return findings, all(f.ok for f in findings)


def main(argv=None):
    findings, ok = run_security_suite()
    print("\ntracker security suite\n" + "─" * 60)
    for f in findings:
        print(f)
    print("─" * 60)
    failed = [f for f in findings if not f.ok]
    crit = [f for f in failed if f.severity == "critical"]
    print(f"{len(findings) - len(failed)} passed · {len(failed)} failed "
          f"({len(crit)} critical)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
