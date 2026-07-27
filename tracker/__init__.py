"""tracker — the backend switch point.

Callers ask here for the active tracker and then speak only normalized DTOs:

    import tracker
    backend = tracker.get_backend(app.creds)
    for story in backend.fetch_stories_in_sprint(project, sprint.path):
        ...

WHAT THIS BUYS
`creds["backend"]` selects the implementation. It DEFAULTS TO "azure", which is
what makes adding Jira/Zephyr a non-event for existing users: a creds file
written before this package existed has no "backend" key, resolves to Azure,
and behaves exactly as before.

REGISTRATION IS LAZY, BY DESIGN
Adapters are imported on first use, never at package import:
  * `tracker.azure` imports `engine`, which does module-level configuration
    work — importing it as a side effect of `import tracker` would make a cheap
    import expensive and order-dependent.
  * `engine.py` uses PEP 701 f-strings that only parse on Python 3.12. A
    module-level import here would make the entire package unimportable on
    older interpreters, taking FakeBackend and the whole test suite down with
    it — precisely when tests are most useful.
So `import tracker` stays free, and only the backend you actually ask for gets
loaded.
"""
from __future__ import annotations

from .base import Backend, Capability
from .errors import (
    AuthFailed, BackendUnsupported, ConflictError, NotConfigured, NotFound,
    PermissionDenied, RateLimited, TrackerError, Unavailable,
)
from .models import (
    Plan, Project, Ref, ReportData, Sprint, Step, Story, Suite, TaskStats,
    TestCase, User,
)

DEFAULT_BACKEND = "azure"

#: name -> zero-arg factory. Values are callables so nothing is imported until
#: the corresponding backend is actually selected.
_REGISTRY = {}
_BUILTIN_LOADERS = {}


def register(name, factory, replace=False):
    """Register a backend factory. Third-party/experimental adapters use this."""
    key = (name or "").strip().lower()
    if not key:
        raise ValueError("Backend name is required.")
    if key in _REGISTRY and not replace:
        raise ValueError(f"Backend {key!r} is already registered.")
    _REGISTRY[key] = factory
    return key


def _load_azure(config=None):
    from .azure import AzureBackend
    return AzureBackend()


def _load_fake(config=None):
    from .fake import FakeBackend
    return FakeBackend()


def _load_jira_zephyr(config=None):
    """Build the Jira+Zephyr backend from a creds-shaped mapping.

    Reads the per-account credential block rather than module globals, so an
    account switch cannot leave the previous user's site/token live (the
    cross-account leak class documented in DEV_ROADMAP).
    """
    from .jira_zephyr import JiraZephyrBackend
    cfg = config or {}
    return JiraZephyrBackend(
        site=cfg.get("jira_site") or "",
        email=cfg.get("jira_email") or "",
        api_token=cfg.get("jira_token") or "",
        zephyr_token=cfg.get("zephyr_token") or "",
        allow_any_host=bool(cfg.get("jira_allow_any_host")))


def _load_xray(config=None):
    """Xray Cloud: Jira creds (reads) + an Xray API Key (client id/secret)."""
    from .xray import XrayBackend, XRAY_BASE
    cfg = config or {}
    return XrayBackend(
        site=cfg.get("jira_site") or "",
        email=cfg.get("jira_email") or "",
        api_token=cfg.get("jira_token") or "",
        client_id=cfg.get("xray_client_id") or "",
        client_secret=cfg.get("xray_client_secret") or "",
        xray_base=cfg.get("xray_base") or XRAY_BASE,
        allow_any_host=bool(cfg.get("jira_allow_any_host")))


def _load_testrail(config=None):
    """TestRail: standalone URL + email + API key (no Jira)."""
    from .testrail import TestRailBackend
    cfg = config or {}
    return TestRailBackend(
        base_url=cfg.get("testrail_url") or "",
        email=cfg.get("testrail_email") or "",
        api_key=cfg.get("testrail_key") or "",
        allow_any_host=bool(cfg.get("testrail_allow_any_host")))


def _testrail_target(cfg):
    from .testrail import TestRailBackend
    return TestRailBackend(
        base_url=cfg.get("testrail_url") or "",
        email=cfg.get("testrail_email") or "",
        api_key=cfg.get("testrail_key") or "",
        allow_any_host=bool(cfg.get("testrail_allow_any_host")))


def _jira_read_source(cfg):
    # Jira reads only — no Zephyr token (the hybrid writes to TestRail).
    from .jira_zephyr import JiraZephyrBackend
    return JiraZephyrBackend(
        site=cfg.get("jira_site") or "", email=cfg.get("jira_email") or "",
        api_token=cfg.get("jira_token") or "", zephyr_token="",
        allow_any_host=bool(cfg.get("jira_allow_any_host")))


def _load_azure_testrail(config=None):
    """Hybrid: read stories from Azure DevOps, write cases to TestRail."""
    from .azure import AzureBackend
    from .composite import CompositeBackend
    cfg = config or {}
    # AzureBackend reads via engine's module globals, so seed them from this
    # account's saved creds — makes the composite self-sufficient for reads
    # whether it's built at connect time or later at generation time.
    try:
        import engine as E
        E.set_credentials(org=cfg.get("org") or None, pat=cfg.get("pat"))
    except Exception:
        pass
    return CompositeBackend(AzureBackend(), _testrail_target(cfg),
                            name="azure_testrail", label="Azure → TestRail")


def _load_jira_testrail(config=None):
    """Hybrid: read stories from Jira, write cases to TestRail."""
    from .composite import CompositeBackend
    cfg = config or {}
    return CompositeBackend(_jira_read_source(cfg), _testrail_target(cfg),
                            name="jira_testrail", label="Jira → TestRail")


_BUILTIN_LOADERS["azure"] = _load_azure
_BUILTIN_LOADERS["fake"] = _load_fake
_BUILTIN_LOADERS["jira_zephyr"] = _load_jira_zephyr
_BUILTIN_LOADERS["xray"] = _load_xray
_BUILTIN_LOADERS["testrail"] = _load_testrail
_BUILTIN_LOADERS["azure_testrail"] = _load_azure_testrail
_BUILTIN_LOADERS["jira_testrail"] = _load_jira_testrail


def available_backends():
    """Names that can be selected, for the Setup picker."""
    return sorted(set(_BUILTIN_LOADERS) | set(_REGISTRY))


def resolve_name(creds=None, override=None):
    """Which backend should be active, given saved credentials.

    Kept separate from get_backend() so the UI can ask "what WOULD be used"
    without paying to construct it.
    """
    if override:
        return str(override).strip().lower()
    if isinstance(creds, dict):
        name = (creds.get("backend") or "").strip().lower()
        if name:
            return name
    return DEFAULT_BACKEND


def get_backend(creds=None, name=None, config=None):
    """Construct the active backend.

    Not cached deliberately. Credentials are per-account and are reset on every
    account switch (see main._switch_user_creds / engine.reset_session_credentials);
    a cached backend is exactly the kind of state that produced the documented
    cross-account leak, where one user's org/PAT stayed live for the next
    account signed into the same running process. Construction is cheap —
    adapters hold config, not connections.
    """
    key = resolve_name(creds, override=name)
    factory = _REGISTRY.get(key) or _BUILTIN_LOADERS.get(key)
    if factory is None:
        raise NotConfigured(
            f"Unknown tracker backend {key!r}.",
            remedy=f"Choose one of: {', '.join(available_backends())}.")
    return factory(config)


def validate(creds=None, name=None):
    """(ok, message) wrapper around Backend.validate_credentials().

    Exists so existing UI call sites shaped like `engine.validate_pat` — which
    returns a tuple — can adopt the switch point without being rewritten.
    """
    try:
        get_backend(creds, name=name).validate_credentials()
        return True, "Valid"
    except TrackerError as exc:
        return False, exc.full_message()
    except Exception as exc:
        return False, str(exc)


__all__ = [
    "get_backend", "resolve_name", "register", "available_backends", "validate",
    "DEFAULT_BACKEND", "Backend", "Capability",
    "Ref", "User", "Project", "Sprint", "Story", "Step", "TestCase", "Suite",
    "Plan", "TaskStats", "ReportData",
    "TrackerError", "NotConfigured", "AuthFailed", "PermissionDenied",
    "NotFound", "RateLimited", "Unavailable", "BackendUnsupported",
    "ConflictError",
]
