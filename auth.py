"""auth.py — Microsoft Entra ID sign-in + role-based authorization for QA Studio.

Uses MSAL (Microsoft Authentication Library) with the OAuth2 Authorization Code +
PKCE flow for desktop apps ("public client" — no client secret on the device).
The user signs in with their existing work account in the SYSTEM BROWSER; QA Studio
never sees the password. Tokens are cached encrypted at rest (reusing store.py's
Windows DPAPI, per-user + machine-bound) and refreshed silently.

Authorization is role-based. Entra **app roles** assigned to a user arrive as a
signed `roles` claim in the ID token — tamper-evident, because Entra signs the
token. Those roles map to QA Studio permissions in PERMISSIONS below.

CONFIG (these are NOT secrets — a public client's tenant/client id are not
confidential): set ENTRA_TENANT_ID and ENTRA_CLIENT_ID as environment variables,
or fill the constants below. Until BOTH are set, auth is DISABLED (`configured()`
is False) and the app behaves exactly as before — so this module is safe to ship
"dark" and switch on per-tenant.

See AUTH_PLAN.md for the full architecture, the Azure app-registration steps, the
threat model, and the path to server-side enforcement.
"""
import os
import threading

# ── Config ───────────────────────────────────────────────────────────────────
TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "")   # GUID or "contoso.onmicrosoft.com"
CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID", "")   # app registration "Application (client) ID"

# Delegated scopes we request. "User.Read" yields basic profile from Microsoft
# Graph; the app-roles claim rides on the ID token regardless of scope.
SCOPES = ["User.Read"]
_AUTHORITY = "https://login.microsoftonline.com/{tenant}"

# ── Permission model (RBAC) ──────────────────────────────────────────────────
# Entra app roles (define these on the app registration → "App roles") mapped to
# QA Studio capabilities. Keep this list in sync with the gates in the UI.
CAP_VIEW = "view"
CAP_GENERATE = "generate"        # generate titles/steps (Run)
CAP_RUN = "run"
CAP_AUTOMATION = "automation"    # Automation screen
CAP_EDIT_PROVIDERS = "edit_providers"   # Setup credentials / AI providers
CAP_EXPORT = "export"
CAP_REGRESSION = "regression"
CAP_SPRINT = "sprint"

PERMISSIONS = {
    "Admin":  {CAP_VIEW, CAP_GENERATE, CAP_RUN, CAP_AUTOMATION,
               CAP_EDIT_PROVIDERS, CAP_EXPORT, CAP_REGRESSION, CAP_SPRINT},
    "Member": {CAP_VIEW, CAP_GENERATE, CAP_RUN, CAP_EXPORT,
               CAP_REGRESSION, CAP_SPRINT},
    "Viewer": {CAP_VIEW, CAP_EXPORT},
}
# Signed in but no app role assigned → least privilege (do NOT default to Admin).
DEFAULT_ROLE = "Viewer"

_CACHE_FILE = os.path.join(os.path.expanduser("~"), ".qa_tool", "auth_cache.bin")
_lock = threading.RLock()
_app_obj = None
_cache = None


def configured():
    """True only when a tenant + client id are present. When False, callers should
    treat the app as un-gated (auth off) so QA Studio runs exactly as before."""
    return bool(TENANT_ID and CLIENT_ID)


# ── Encrypted token cache (reuses store.py's DPAPI) ──────────────────────────
def _load_cache():
    global _cache
    if _cache is not None:
        return _cache
    import msal
    _cache = msal.SerializableTokenCache()
    try:
        import store
        with open(_CACHE_FILE, "rb") as f:
            _cache.deserialize(store._decrypt(f.read()).decode("utf-8"))
    except Exception:
        pass
    return _cache


def _save_cache():
    try:
        if _cache is None or not _cache.has_state_changed:
            return
        import store
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        blob = store._encrypt(_cache.serialize().encode("utf-8"))
        with open(_CACHE_FILE, "wb") as f:
            f.write(blob)
        _cache.has_state_changed = False
    except Exception:
        pass


def _app():
    global _app_obj
    if _app_obj is None:
        import msal
        _app_obj = msal.PublicClientApplication(
            CLIENT_ID,
            authority=_AUTHORITY.format(tenant=TENANT_ID),
            token_cache=_load_cache())
    return _app_obj


# ── Identity helpers ─────────────────────────────────────────────────────────
def _user_from_claims(claims):
    return {
        "name": claims.get("name") or claims.get("preferred_username") or "",
        "email": (claims.get("preferred_username") or claims.get("upn")
                  or claims.get("email") or ""),
        "oid": claims.get("oid") or claims.get("sub") or "",
        "roles": list(claims.get("roles") or []),
    }


# ── Public API ───────────────────────────────────────────────────────────────
def sign_in(scopes=None):
    """Interactive sign-in: opens the system browser, OAuth2 Auth Code + PKCE with
    a loopback redirect. BLOCKING — run off the UI thread. Returns the user dict on
    success, else None."""
    if not configured():
        return None
    with _lock:
        try:
            res = _app().acquire_token_interactive(
                scopes or SCOPES, prompt="select_account")
        except Exception:
            return None
        _save_cache()
    if res and "access_token" in res:
        return _user_from_claims(res.get("id_token_claims") or {})
    return None


def acquire_silent(scopes=None):
    """Silent token acquisition from the cache (auto-refresh). Returns the user dict
    if a valid session exists, else None (interactive sign-in needed). Cheap — call
    on startup to restore a session without prompting."""
    if not configured():
        return None
    with _lock:
        try:
            accts = _app().get_accounts()
            if not accts:
                return None
            res = _app().acquire_token_silent(scopes or SCOPES, account=accts[0])
            _save_cache()
        except Exception:
            return None
    if res and "access_token" in res:
        return _user_from_claims(res.get("id_token_claims") or {})
    return None


def sign_out():
    """Forget the signed-in account and wipe the encrypted cache."""
    with _lock:
        try:
            for a in _app().get_accounts():
                _app().remove_account(a)
            _save_cache()
        except Exception:
            pass
    try:
        os.remove(_CACHE_FILE)
    except Exception:
        pass
    global _cache, _app_obj
    _cache = None
    _app_obj = None


def permissions_for(user):
    """Set of capability strings the user has, derived from their Entra app roles.
    No role assigned → least-privilege DEFAULT_ROLE. No user → nothing."""
    if not user:
        return set()
    perms = set()
    for r in (user.get("roles") or []):
        perms |= PERMISSIONS.get(r, set())
    return perms or set(PERMISSIONS.get(DEFAULT_ROLE, set()))


def has(user, capability):
    """True if `user` is allowed `capability` (one of the CAP_* constants)."""
    return capability in permissions_for(user)
