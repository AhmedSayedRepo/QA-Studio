"""auth_supabase.py — external-user sign-up / sign-in + RBAC for QA Studio.

A free, hosted alternative to Entra for **external** users (customers, partners,
testers) who self-register. Uses **Supabase Auth** (the open-source GoTrue
service) over its plain REST API — no SDK, just `requests` — so it works from a
desktop app with email/password sign-up, email confirmation, password reset, and
(optionally) social/OAuth logins, all on Supabase's free tier.

Why Supabase Auth for external users:
  • Free tier covers a real project (50k monthly active users) — generous for a
    QA/testing tool, and $0.
  • Real **server-side** security: passwords are hashed and stored by Supabase, not
    on the device; JWTs are signed by the project, so roles can't be forged client-
    side. (This is the property a fully-local scheme can't give you.)
  • Self-service **sign-up** with email verification + password reset out of the
    box — exactly what external users need (Entra is geared to org accounts).
  • No server to run or patch; reuses our existing `requests` + `store.py` DPAPI.

Tokens (access + refresh) are cached encrypted at rest with store.py's Windows
DPAPI (per-user, machine-bound) and refreshed silently.

CONFIG (NOT secrets — the project URL and the **anon** public key are meant to ship
in clients): set SUPABASE_URL and SUPABASE_ANON_KEY as environment variables or
fill the constants below. Until BOTH are set, auth is DISABLED (`configured()` is
False) and the app behaves exactly as before — safe to ship "dark".

Roles live in the user's **app_metadata.role** (set by you via the Supabase
dashboard / SQL / service-role — NOT user-editable), and map to QA Studio
capabilities in PERMISSIONS below.

See AUTH_EXTERNAL_PLAN.md for setup, the RBAC model, and the security notes.
"""
import os
import json
import time
import threading

import requests

# ── Config ───────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", "https://psiyktcrggmgralyswua.supabase.co").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get(
    "SUPABASE_ANON_KEY", "sb_publishable_GiALQqFs-_1SXniLm3BCrw_Kp_OIVyJ")  # public key

_TIMEOUT = 20
_REFRESH_SKEW = 60          # refresh this many seconds before expiry
_CACHE_FILE = os.path.join(os.path.expanduser("~"), ".qa_tool", "supabase_session.bin")

# ── Permission model (granular, per-user) ────────────────────────────────────
# Each user carries a set of capability KEYS. "nav.*" keys gate opening a screen;
# "act.*" keys gate performing that screen's actions. An Admin can grant/revoke any
# of these per user from the in-app Users screen (stored in app_metadata.caps).
# Roles (Admin/Member/Viewer) are just presets that fill that set.
#
# CATALOG: (key, human label, kind) — drives both the toggle UI and the gating.
CATALOG = [
    ("nav.setup",         "Setup",                    "nav"),
    ("act.connect",       "Connect / save credentials", "act"),
    ("act.create_plan",   "Create a test plan",       "act"),
    ("act.open_plan",     "Open test plan in Azure",  "act"),
    ("act.sprint_summary", "Generate sprint summary report", "act"),
    ("nav.run",           "Run",                      "nav"),
    ("act.run",           "Start runs / generate cases", "act"),
    ("nav.report",        "Report",                   "nav"),
    ("act.export",        "Export / download",        "act"),
    ("nav.regression",    "Regression Plan",          "nav"),
    ("act.regression",    "Generate regression plan", "act"),
    ("nav.sprint_plan",   "Sprint Plan",              "nav"),
    ("act.sprint",        "Generate sprint plan",     "act"),
    ("nav.sprint_report", "Sprint Report",            "nav"),
    ("act.sprint_report", "Generate sprint report",   "act"),
    ("nav.automation",    "Automation",               "nav"),
    ("act.automation",    "Run automation",           "act"),
    ("nav.links",         "Useful Links",             "nav"),
    ("nav.settings",      "Settings",                 "nav"),
    ("act.settings",      "Change settings",          "act"),
    ("nav.users",         "Users (admin)",            "nav"),
    ("act.manage_users",  "Manage users & roles",     "act"),
]
ALL_KEYS = [k for k, _, _ in CATALOG]
NAV_KEYS = [k for k, _, kind in CATALOG if kind == "nav"]
ACT_KEYS = [k for k, _, kind in CATALOG if kind == "act"]

_NAV_NO_USERS = [k for k in NAV_KEYS if k != "nav.users"]
_ACT_NO_MANAGE = [k for k in ACT_KEYS if k != "act.manage_users"]

# Role presets (the starting point; admins can customise per user afterwards).
ROLE_PRESETS = {
    "Admin":  set(ALL_KEYS),                                   # everything
    "Member": set(_NAV_NO_USERS) | set(_ACT_NO_MANAGE),        # all but user mgmt
    "Viewer": set(_NAV_NO_USERS),                              # see all, do nothing
}
DEFAULT_ROLE = "Viewer"

# Back-compat aliases (older code referenced these).
CAP_VIEW = "nav.report"
CAP_GENERATE = "act.run"
CAP_RUN = "act.run"
CAP_AUTOMATION = "act.automation"
CAP_EDIT_PROVIDERS = "act.connect"
CAP_EXPORT = "act.export"
CAP_REGRESSION = "act.regression"
CAP_SPRINT = "act.sprint"
PERMISSIONS = ROLE_PRESETS

_lock = threading.RLock()
_session_data = None        # {"access_token","refresh_token","expires_at","user"}
_http = None


def configured():
    """True only when the project URL + anon key are present. When False, callers
    treat the app as un-gated (auth off) so QA Studio runs exactly as before."""
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)


# ── HTTP helpers ─────────────────────────────────────────────────────────────
def _client():
    global _http
    if _http is None:
        _http = requests.Session()
        _http.headers.update({"apikey": SUPABASE_ANON_KEY,
                              "Content-Type": "application/json"})
    return _http


def _friendly(resp):
    """Pull a human message out of a GoTrue error response."""
    try:
        j = resp.json()
    except Exception:
        return f"Request failed ({resp.status_code})."
    for k in ("error_description", "msg", "message", "error"):
        if isinstance(j, dict) and j.get(k):
            return str(j[k])
    return f"Request failed ({resp.status_code})."


# ── Encrypted session cache (reuses store.py's DPAPI) ────────────────────────
def _load_session():
    global _session_data
    if _session_data is not None:
        return _session_data
    try:
        import store
        with open(_CACHE_FILE, "rb") as f:
            _session_data = json.loads(store._decrypt(f.read()).decode("utf-8"))
    except Exception:
        _session_data = None
    return _session_data


def _save_session(data):
    global _session_data
    _session_data = data
    try:
        import store
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        if data is None:
            try:
                os.remove(_CACHE_FILE)
            except Exception:
                pass
            return
        blob = store._encrypt(json.dumps(data).encode("utf-8"))
        with open(_CACHE_FILE, "wb") as f:
            f.write(blob)
    except Exception:
        pass


# ── Identity helpers ─────────────────────────────────────────────────────────
def _role_of(user):
    """Role from the SIGNED app_metadata (admin-set), then user_metadata, else the
    least-privilege default. app_metadata is not user-editable, so it's the safe
    source for authorization."""
    if not user:
        return DEFAULT_ROLE
    for src in (user.get("app_metadata") or {}, user.get("user_metadata") or {}):
        role = src.get("role")
        if role:
            return role
    return DEFAULT_ROLE


def _caps_raw(user):
    """Per-user custom capability list from the SIGNED app_metadata.caps, or None
    (meaning: fall back to the role preset)."""
    am = user.get("app_metadata") or {}
    c = am.get("caps")
    return list(c) if isinstance(c, list) else None


def _user_dict(user):
    if not user:
        return None
    meta = user.get("user_metadata") or {}
    return {
        "id": user.get("id") or "",
        "email": user.get("email") or "",
        "name": meta.get("name") or meta.get("full_name") or (user.get("email") or ""),
        "role": _role_of(user),
        "caps": _caps_raw(user),     # None → use the role preset
        "confirmed": bool(user.get("email_confirmed_at") or user.get("confirmed_at")),
    }


def _store_session(payload):
    """Persist a token payload returned by GoTrue; returns the user dict."""
    expires_at = payload.get("expires_at")
    if not expires_at:
        expires_at = int(time.time()) + int(payload.get("expires_in", 3600))
    data = {
        "access_token": payload.get("access_token", ""),
        "refresh_token": payload.get("refresh_token", ""),
        "expires_at": int(expires_at),
        "user": payload.get("user") or {},
    }
    _save_session(data)
    return _user_dict(data["user"])


# ── Public API ───────────────────────────────────────────────────────────────
def sign_up(email, password, name=None):
    """Self-service registration. Returns (ok, message, user|None).

    If the project requires email confirmation (recommended), `ok` is True but the
    session is NOT created until the user clicks the verification link — the
    message tells them to check their inbox, and `user` is None."""
    if not configured():
        return False, "Auth is not configured.", None
    body = {"email": (email or "").strip(), "password": password or ""}
    if name:
        body["data"] = {"name": name}
    try:
        r = _client().post(f"{SUPABASE_URL}/auth/v1/signup", json=body, timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}", None
    if r.status_code not in (200, 201):
        return False, _friendly(r), None
    payload = r.json()
    # When confirmations are on, GoTrue returns the user but no access_token.
    if payload.get("access_token"):
        return True, "Account created and signed in.", _store_session(payload)
    return (True,
            "Account created — check your email to confirm your address, then sign in.",
            None)


def sign_in(email, password):
    """Email/password sign-in. Returns (ok, message, user|None)."""
    if not configured():
        return False, "Auth is not configured.", None
    body = {"email": (email or "").strip(), "password": password or ""}
    try:
        r = _client().post(f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
                           json=body, timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}", None
    if r.status_code != 200:
        msg = _friendly(r)
        low = msg.lower()
        if "not confirmed" in low or "email not confirmed" in low:
            msg = "Please confirm your email first — check your inbox for the link."
        elif "invalid" in low or r.status_code in (400, 401):
            msg = "Incorrect email or password."
        return False, msg, None
    return True, "Signed in.", _store_session(r.json())


def _refresh(refresh_token):
    try:
        r = _client().post(f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
                           json={"refresh_token": refresh_token}, timeout=_TIMEOUT)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    return r.json()


def acquire_silent():
    """Restore a session from the encrypted cache, refreshing the access token if
    it's expired. Returns the user dict if a valid session exists, else None. Cheap
    — call on startup to resume without prompting."""
    if not configured():
        return None
    with _lock:
        data = _load_session()
        if not data or not data.get("refresh_token"):
            return None
        if int(time.time()) < int(data.get("expires_at", 0)) - _REFRESH_SKEW:
            return _user_dict(data.get("user"))         # still valid
        payload = _refresh(data["refresh_token"])       # expired → refresh
        if not payload or not payload.get("access_token"):
            _save_session(None)                         # refresh token dead → sign out
            return None
        return _store_session(payload)


def access_token():
    """Current bearer token (refreshing if needed), or '' — for calling your own
    token-protected backend. Returns '' when not signed in / not configured."""
    if not configured():
        return ""
    with _lock:
        data = _load_session()
        if not data:
            return ""
        if int(time.time()) >= int(data.get("expires_at", 0)) - _REFRESH_SKEW:
            payload = _refresh(data.get("refresh_token", ""))
            if payload and payload.get("access_token"):
                _store_session(payload)
                data = _load_session()
            else:
                return ""
        return data.get("access_token", "")


def request_password_reset(email):
    """Send a password-reset email. Returns (ok, message)."""
    if not configured():
        return False, "Auth is not configured."
    try:
        r = _client().post(f"{SUPABASE_URL}/auth/v1/recover",
                           json={"email": (email or "").strip()}, timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}"
    if r.status_code in (200, 201):
        return True, "If that email exists, a reset link is on its way."
    return False, _friendly(r)


def resend_confirmation(email):
    """Re-send the sign-up confirmation email. Returns (ok, message)."""
    if not configured():
        return False, "Auth is not configured."
    try:
        r = _client().post(f"{SUPABASE_URL}/auth/v1/resend",
                           json={"type": "signup", "email": (email or "").strip()},
                           timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}"
    if r.status_code in (200, 201):
        return True, "Confirmation email re-sent."
    return False, _friendly(r)


def sign_out():
    """Revoke the session server-side (best effort) and wipe the encrypted cache."""
    with _lock:
        data = _load_session()
        token = (data or {}).get("access_token")
        if token:
            try:
                _client().post(f"{SUPABASE_URL}/auth/v1/logout",
                               headers={"Authorization": f"Bearer {token}"},
                               timeout=_TIMEOUT)
            except Exception:
                pass
        _save_session(None)


def current_user():
    """The cached user dict (no network), or None."""
    data = _load_session()
    return _user_dict(data.get("user")) if data else None


def caps_for(user):
    """The set of capability keys granted to a user. Uses the per-user custom list
    (app_metadata.caps) when present, otherwise the role preset. No user → empty."""
    if not user:
        return set()
    custom = user.get("caps")
    if isinstance(custom, list):
        return set(custom)
    return set(ROLE_PRESETS.get(user.get("role"), ROLE_PRESETS.get(DEFAULT_ROLE, set())))


def can(user, key):
    """True if `user` is granted capability `key` (a CATALOG key like 'act.run')."""
    return key in caps_for(user)


# Back-compat: old call sites used has()/permissions_for().
def has(user, key):
    return can(user, key)


def permissions_for(user):
    return caps_for(user)


def is_admin(user):
    """True if the user's signed role is Admin."""
    return bool(user) and (user.get("role") == "Admin")


# ── Admin user management (via the 'admin-users' Edge Function) ───────────────
# These call a server-side Edge Function that holds the service_role key and does
# the privileged work — the desktop app never sees that key. The function verifies
# the caller is an Admin before doing anything. See supabase/functions/admin-users.
def _functions_url(name):
    return f"{SUPABASE_URL}/functions/v1/{name}"


def admin_list_users():
    """Admin-only: list all users. Returns (ok, users_or_message). Each user is
    {id, email, role, created_at, last_sign_in_at, confirmed}."""
    if not configured():
        return False, "Auth is not configured."
    tok = access_token()
    if not tok:
        return False, "You’re not signed in."
    try:
        r = _client().get(_functions_url("admin-users"),
                          headers={"Authorization": f"Bearer {tok}"}, timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}"
    if r.status_code == 404:
        return False, ("The ‘admin-users’ Edge Function isn’t deployed yet — see "
                       "ADMIN_USERS_SETUP.md.")
    if r.status_code != 200:
        return False, _friendly(r)
    return True, (r.json() or {}).get("users", [])


def _admin_post(payload):
    if not configured():
        return False, "Auth is not configured."
    tok = access_token()
    if not tok:
        return False, "You’re not signed in."
    try:
        r = _client().post(_functions_url("admin-users"),
                           headers={"Authorization": f"Bearer {tok}"},
                           json=payload, timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}"
    if r.status_code == 404:
        return False, ("The ‘admin-users’ Edge Function isn’t deployed yet — see "
                       "ADMIN_USERS_SETUP.md.")
    if r.status_code != 200:
        return False, _friendly(r)
    return True, None


def admin_set_role(user_id, role):
    """Admin-only: set a user's role AND reset their capabilities to that role's
    preset. Returns (ok, msg)."""
    if role not in ROLE_PRESETS:
        return False, "Invalid role."
    ok, err = _admin_post({"user_id": user_id, "role": role,
                           "caps": sorted(ROLE_PRESETS[role])})
    return (True, f"Role set to {role}.") if ok else (False, err)


def admin_set_caps(user_id, caps):
    """Admin-only: set a user's exact capability list (per-permission override).
    Returns (ok, msg)."""
    caps = [c for c in (caps or []) if c in ALL_KEYS]
    ok, err = _admin_post({"user_id": user_id, "caps": sorted(caps)})
    return (True, "Permissions updated.") if ok else (False, err)
