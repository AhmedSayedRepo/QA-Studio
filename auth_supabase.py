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

try:
    import diag_log as _diag
except Exception:
    _diag = None

# ── Config ───────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", "https://psiyktcrggmgralyswua.supabase.co").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get(
    "SUPABASE_ANON_KEY", "sb_publishable_GiALQqFs-_1SXniLm3BCrw_Kp_OIVyJ")  # public key

_TIMEOUT = 20
_REFRESH_SKEW = 60          # refresh this many seconds before expiry


def _cache_dir():
    """PERSISTENT, writable data dir for the cached session.

    ROOT CAUSE this fixes (four rounds of "biometrics doesn't log me in on
    relaunch" were all chasing the wrong layer): this file used to live under
    `os.path.expanduser("~")`, which on an Android Flet build does NOT resolve
    to a writable, relaunch-surviving location (it lands on /data). So every
    _save_session() silently failed and the Supabase session was NEVER
    persisted on mobile at all. The biometric gate was working correctly the
    whole time — it prompted, the user passed, and then acquire_silent() had
    literally nothing on disk to restore, so the app sat on the login screen.

    Flet sets FLET_APP_STORAGE_DATA to the app-private files directory on
    Android/iOS — the only place guaranteed both writable and durable across
    relaunches. Same fix already applied to mobile_prefs (onboarding/biometric
    flags, which silently never persisted for the same reason) and to the
    exporters (which failed loudly with "[Errno 13] Permission denied:
    '/data/QA Studio'"). Desktop is unchanged: the env var isn't set there, so
    it falls back to ~/.qa_tool exactly as before.

    Resolved lazily (not at import) so it reflects the environment Flet has
    actually set up by the time a session is read or written."""
    d = os.environ.get("FLET_APP_STORAGE_DATA")
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), ".qa_tool")


def _cache_file():
    return os.path.join(_cache_dir(), "supabase_session.bin")

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
    ("nav.task_manager",  "Task Manager",             "nav"),
    ("act.task_manager",  "Use Task Manager",         "act"),
    ("nav.automation",    "Automation",               "nav"),
    ("act.automation",    "Run automation",           "act"),
    ("nav.links",         "Useful Links",             "nav"),
    ("nav.settings",      "Settings",                 "nav"),
    ("act.settings",      "Change settings",          "act"),
    ("nav.users",         "Users (admin)",            "nav"),
    ("act.manage_users",  "Manage users & roles",     "act"),
    ("nav.orgs",          "Organizations (admin)",    "nav"),
    ("act.manage_orgs",   "Manage organizations",     "act"),
    ("nav.ai_usage",      "AI Usage",                 "nav"),
    ("act.view_usage",    "View ALL users' AI usage (admin)", "act"),
]
ALL_KEYS = [k for k, _, _ in CATALOG]
NAV_KEYS = [k for k, _, kind in CATALOG if kind == "nav"]
ACT_KEYS = [k for k, _, kind in CATALOG if kind == "act"]

# nav.ai_usage is open to every role (Member/Viewer included) — everyone can
# view their OWN AI usage; only nav.users (account/role management) stays
# Admin-only. act.view_usage is a separate, narrower capability: it's what
# lets someone see EVERY signed-in user's activity, not just their own, so
# it's held out of the Member/Viewer presets the same way act.manage_users
# is. The real security boundary for "everyone's usage" is server-side — the
# Edge Function's own hard Admin-role check (see supabase/functions/ai-usage)
# — this preset just keeps the nav/UI consistent with that.
_NAV_NO_USERS = [k for k in NAV_KEYS if k not in ("nav.users", "nav.orgs")]
_ACT_NO_MANAGE = [k for k in ACT_KEYS if k not in ("act.manage_users", "act.view_usage", "act.manage_orgs")]

# Role presets (the starting point; admins can customise per user afterwards).
#   SuperAdmin  — global admin: manages users across ALL orgs, assigns org_id.
#   OrgManager  — org-scoped admin: manages only their own org's users.
#   Admin       — legacy alias of SuperAdmin (existing installs keep working).
ROLE_PRESETS = {
    "SuperAdmin": set(ALL_KEYS),                               # everything, all orgs
    "Admin":      set(ALL_KEYS),                               # legacy alias of SuperAdmin
    "OrgManager": set(ALL_KEYS) - {"act.view_usage", "nav.orgs", "act.manage_orgs"},  # own-org user mgmt only
    "Member":     set(_NAV_NO_USERS) | set(_ACT_NO_MANAGE),    # all but user mgmt
    "Viewer":     set(_NAV_NO_USERS),                          # see all, do nothing
}
DEFAULT_ROLE = "Viewer"
# Roles that carry GLOBAL admin power (all orgs). "Admin" is the legacy alias.
SUPER_ROLES = ("SuperAdmin", "Admin")

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


def _post_retry(url, tries=3, **kw):
    """POST with a short backoff for TRANSIENT failures only — network drops and
    502/503/504 from the gateway. Auth errors (400/401/422) are returned as-is on
    the first try, never retried. This is what stops the intermittent 'failed to
    authenticate' when the request just hit a hiccup rather than a real rejection.
    Raises the last exception if every attempt fails at the socket level."""
    kw.setdefault("timeout", _TIMEOUT)
    last = None
    for i in range(max(1, tries)):
        try:
            r = _client().post(url, **kw)
        except Exception as ex:
            last = ex
            time.sleep(0.4 * (i + 1))
            continue
        if r.status_code in (502, 503, 504) and i < tries - 1:
            time.sleep(0.4 * (i + 1))
            continue
        return r
    raise last if last else RuntimeError("request failed")


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
    # MOBILE: prefer the Keystore-encrypted vault over the local file. The file
    # path uses store._encrypt(), which on the mobile build degrades to base64
    # (no DPAPI, no Fernet) — see secure_store_mobile.save_session. Only trusted
    # once the vault's async bootstrap has landed; before that we fall through
    # to the file, and the caller (main._on_secure_creds_ready) re-runs the
    # restore after the bootstrap anyway.
    try:
        import secure_store_mobile as _ssm
        if _ssm.session_available():
            _s = _ssm.load_session()
            if _s:
                _session_data = _s
                return _session_data
    except Exception:
        pass
    try:
        import store
        with open(_cache_file(), "rb") as f:
            _session_data = json.loads(store._decrypt(f.read()).decode("utf-8"))
    except FileNotFoundError:
        _session_data = None   # normal — no cached session yet, not worth logging
    except Exception as ex:
        # A corrupt/undecryptable cache silently drops the user back to signed-out
        # with no trace of why — worth a local log line so "I keep getting logged
        # out" is diagnosable instead of a mystery.
        if _diag: _diag.log("auth_supabase._load_session", ex)
        _session_data = None
    return _session_data


def _save_session(data):
    global _session_data
    _session_data = data
    # MOBILE: write the session into the Keystore-encrypted vault and do NOT
    # also leave a base64 copy on disk — that copy was the security hole (a
    # refresh token in base64 is effectively plaintext, and sign-out now keeps
    # that token on purpose so biometrics can restore the session). Falls
    # through to the file path below if the vault isn't ready yet.
    try:
        import secure_store_mobile as _ssm
        _avail = _ssm.session_available()
        if _avail and _ssm.save_session(data):
            # Remove any pre-existing plaintext-ish file from before this fix.
            try:
                if data is None or os.path.exists(_cache_file()):
                    os.remove(_cache_file())
            except Exception:
                pass
            return
        # Mobile + reached here = the vault write did NOT confirm: the durability
        # gap that loses a rotated refresh token. Surface it (was silent) — no
        # token material, just the fact of a dropped persist.
        if _diag:
            try:
                _diag.log_warn("auth_supabase._save_session",
                               f"vault persist NOT confirmed "
                               f"(session_available={_avail}, data={'none' if data is None else 'set'})")
            except Exception:
                pass
    except Exception:
        pass
    # MOBILE: never fall through to the base64 file for the SESSION either —
    # it holds the refresh token, the single most sensitive value on the
    # device. Same reasoning as store.save(): the only writes reaching here are
    # pre-bootstrap ones, and skipping them beats writing a token in the clear.
    try:
        import platform_caps as _pc_sess
        if _pc_sess.is_mobile():
            return
    except Exception:
        pass
    try:
        import store
        os.makedirs(os.path.dirname(_cache_file()), exist_ok=True)
        if data is None:
            try:
                os.remove(_cache_file())
            except Exception:
                pass
            return
        blob = store._encrypt(json.dumps(data).encode("utf-8"))
        with open(_cache_file(), "wb") as f:
            f.write(blob)
    except Exception as ex:
        # Silent failure here means sign-in APPEARS to succeed but the session
        # never persists — the user gets signed out again on next launch with
        # no indication why. Log it so that's diagnosable.
        if _diag: _diag.log("auth_supabase._save_session", ex)


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


def _org_of(user):
    """Organization id from the SIGNED app_metadata.org_id ('' if unset). Like
    role, this is admin-set and NOT user-editable, so it is safe for scoping."""
    am = user.get("app_metadata") or {}
    o = am.get("org_id")
    return o if isinstance(o, str) and o else ""


def _user_dict(user):
    if not user:
        return None
    meta = user.get("user_metadata") or {}
    am = user.get("app_metadata") or {}
    return {
        "id": user.get("id") or "",
        "email": user.get("email") or "",
        "name": meta.get("name") or meta.get("full_name") or (user.get("email") or ""),
        "role": _role_of(user),
        "org_id": _org_of(user),
        "caps": _caps_raw(user),     # None → use the role preset
        "confirmed": bool(user.get("email_confirmed_at") or user.get("confirmed_at")),
        # Admin-set (app_metadata, not user-editable): the invitee must change
        # their temporary password on first sign-in before using the app.
        "must_reset": bool(am.get("must_reset")),
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
        r = _post_retry(f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
                        json=body)
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
    """Attempt a token refresh. Returns a (payload, dead) tuple:

      - (dict, False)  — success; caller stores the rotated session.
      - (None, True)   — the refresh token is DEFINITIVELY dead: an HTTP 400/401
                         from the token endpoint is an auth REJECTION (invalid_grant
                         / refresh_token_not_found — rotated, revoked, or past
                         Supabase's session time-box). Caller signs out.
      - (None, False)  — TRANSIENT failure: network error, timeout, 429, or 5xx.
                         The token is (as far as we know) still valid; the caller
                         MUST keep the session. Destroying a good token over a
                         cold-start network blip is exactly the "reopen after a
                         while → biometric restore bounced to login" bug.
    """
    # Fingerprint the token we're about to present (sha256[:8], NOT the token).
    # Cross-reference with secure_store_mobile.save_session's rt_fp: if a refresh
    # is rejected (dead) and this fp == the last persisted fp, the token expired
    # SERVER-SIDE (Supabase session time-box / reuse) — a dashboard-config issue,
    # not a client bug; if it DIFFERS, the app presented a rotated-away token (a
    # durability drop) and the bug is on our side.
    if _diag:
        try:
            import hashlib
            _fp = (hashlib.sha256((refresh_token or "").encode("utf-8")).hexdigest()[:8]
                   if refresh_token else "none")
            _diag.log_warn("auth_supabase._refresh", f"presenting rt_fp={_fp}")
        except Exception:
            pass
    # SINGLE-SHOT on purpose — do NOT use _post_retry here. A refresh ROTATES the
    # token, so the call is NOT idempotent: if the server rotates it but the
    # response is lost (or a 5xx after the backend already processed it), a retry
    # re-presents a token the server just rotated away, which reuse-detection
    # ("Detect and revoke compromised refresh tokens") treats as theft and revokes
    # the WHOLE session family — the freshly-minted token included. That was
    # diagnosed live: presenting rt_fp == the last persisted rt_fp, i.e. the app
    # held the correct token and the server had revoked it (server-side, not a
    # client durability drop). A network/timeout/5xx here is transient — we keep
    # the session and retry on the NEXT launch with the still-valid token.
    try:
        r = _client().post(f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
                           json={"refresh_token": refresh_token}, timeout=_TIMEOUT)
    except Exception as ex:
        # Network/timeout — transient by definition; never proof of a dead token.
        if _diag: _diag.log("auth_supabase._refresh", ex)
        return None, False
    if r.status_code == 200:
        return r.json(), False
    # Non-200. Only 400/401 from THIS endpoint means the refresh token itself was
    # rejected (invalid_grant / refresh_token_not_found) → dead. 429 (rate limit)
    # and 5xx (outage) are transient and must NOT wipe the session.
    dead = r.status_code in (400, 401)
    if _diag:
        try:
            _diag.log_warn("auth_supabase._refresh",
                           f"refresh rejected status={r.status_code} "
                           f"dead={dead} body={(r.text or '')[:240]}")
        except Exception:
            pass
    return None, dead


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
        payload, dead = _refresh(data["refresh_token"])  # expired → refresh
        if payload and payload.get("access_token"):
            return _store_session(payload)
        if dead:
            _save_session(None)                         # token revoked/expired → sign out
            return None
        # TRANSIENT failure (network/5xx/timeout on cold start): DO NOT destroy the
        # token. Stay signed in on the cached identity — access_token() will refresh
        # again once connectivity returns. This is the fix for the biometric restore
        # bouncing to the login screen after the app sat killed for a while.
        return _user_dict(data.get("user"))


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
            payload, dead = _refresh(data.get("refresh_token", ""))
            if payload and payload.get("access_token"):
                _store_session(payload)
                data = _load_session()
            elif dead:
                return ""     # token revoked → no bearer (session cleared at next
                              # acquire_silent); never emit a known-dead token
            else:
                # transient — hand back the cached (expired) token rather than "";
                # the protected call may 401, but we don't treat a blip as signed-out
                return data.get("access_token", "")
        return data.get("access_token", "")


def change_own_password(new_password):
    """Set the SIGNED-IN user's own password and clear the forced-reset flag,
    server-side (the set-password Edge Function keys off the caller's JWT, so a
    user can only ever change their OWN password). Returns (ok, msg)."""
    if not configured():
        return False, "Auth is not configured."
    pw = new_password or ""
    if len(pw) < 8:
        return False, "Password must be at least 8 characters."
    tok = access_token()
    if not tok:
        return False, "You’re not signed in."
    try:
        r = _client().post(_functions_url("set-password"),
                           headers={"Authorization": f"Bearer {tok}"},
                           json={"password": pw}, timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}"
    if r.status_code == 404:
        return False, "The ‘set-password’ Edge Function isn’t deployed yet."
    if r.status_code != 200:
        return False, _friendly(r)
    return True, "Password updated."


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


def revalidate():
    """Fetch the CURRENT user record from the server (fresh app_metadata role/caps)
    and update the cached session, so an admin's revoke/role change takes effect
    without waiting for a token refresh or re-login. GET /auth/v1/user returns the
    live DB record (not the JWT's baked-in metadata). Returns fresh user dict / None."""
    if not configured():
        return None
    tok = access_token()
    if not tok:
        return None
    try:
        r = _client().get(f"{SUPABASE_URL}/auth/v1/user",
                          headers={"Authorization": f"Bearer {tok}"}, timeout=_TIMEOUT)
    except Exception as ex:
        # revalidate() is what picks up an admin's role/cap change without
        # waiting for a token refresh — a silent failure here just means the
        # change quietly doesn't take effect, with nothing to go on. Log it.
        if _diag: _diag.log("auth_supabase.revalidate", ex)
        return None
    if r.status_code != 200:
        return None
    try:
        raw = r.json()
    except Exception:
        return None
    if not (raw and raw.get("id")):
        return None
    with _lock:
        data = _load_session()
        if data:
            data["user"] = raw
            _save_session(data)
    return _user_dict(raw)


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
    """True if the user is a SuperAdmin (global admin). "Admin" is the legacy
    alias. Gates the GLOBAL admin surfaces (all-users AI usage, org-wide email
    settings, shared links) — NOT org-scoped user management (see
    can_manage_users), which org managers also have."""
    return bool(user) and (user.get("role") in SUPER_ROLES)


def is_super_admin(user):
    """Global admin: manages users across ALL orgs and assigns org_id."""
    return bool(user) and (user.get("role") in SUPER_ROLES)


def is_org_manager(user):
    """Org-scoped admin: manages only users in their own org_id."""
    return bool(user) and (user.get("role") == "OrgManager")


def can_manage_users(user):
    """May reach the Users screen (super admin OR org manager). The Edge
    Function still enforces WHICH users they can actually see/change."""
    return is_super_admin(user) or is_org_manager(user)


def org_id_of(user):
    """The user's organization id ('' if none / signed out)."""
    return ((user or {}).get("org_id") or "")


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


def _admin_post_json(payload):
    """Like _admin_post but returns the response BODY on success — some endpoints
    (invite) return data such as the generated temporary password."""
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
    try:
        return True, (r.json() or {})
    except Exception:
        return True, {}


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


def admin_revoke_access(user_id):
    """Admin-only SOFT revoke: strip every capability so the account remains and the
    user can still sign in, but has access to nothing until a role is set again.
    Fully reversible (set any role to restore). Uses the existing admin-users Edge
    Function — no backend change. Returns (ok, msg)."""
    ok, err = _admin_post({"user_id": user_id, "caps": []})
    return (True, "Access revoked.") if ok else (False, err)


def admin_invite_user(email, role="Viewer", org_id=None, name=None):
    """Invite a new user by email INTO an org. Org managers may only invite into
    their OWN org as OrgManager/Member/Viewer; super admins may name the org.
    All of that is enforced server-side. Returns (ok, msg)."""
    email = (email or "").strip().lower()
    dom = email.split("@")[-1] if "@" in email else ""
    if "@" not in email or "." not in dom:
        return False, "Enter a valid email address."
    if role not in ROLE_PRESETS:
        return False, "Invalid role."
    payload = {"invite_email": email, "role": role}
    if org_id is not None:
        payload["org_id"] = org_id
    if (name or "").strip():
        payload["name"] = name.strip()
    ok, res = _admin_post_json(payload)
    if not ok:
        return False, res
    return True, {
        "email": res.get("invited") or email,
        "temp_password": res.get("temp_password") or "",
        "role": res.get("role") or role,
        "org_id": res.get("org_id") or (org_id or ""),
        "id": res.get("id") or "",
    }


def admin_add_existing_user(email, role="Member", org_id=None):
    """Add an ALREADY signed-up user (by email) to an org WITHOUT an email
    invite. An org manager may only add an ORG-LESS user into their OWN org;
    a super admin may name the org. All enforced server-side. Returns (ok, msg)."""
    email = (email or "").strip().lower()
    dom = email.split("@")[-1] if "@" in email else ""
    if "@" not in email or "." not in dom:
        return False, "Enter a valid email address."
    payload = {"add_existing_email": email, "role": role}
    if org_id is not None:
        payload["org_id"] = org_id
    ok, err = _admin_post(payload)
    return (True, f"Added {email}.") if ok else (False, err)


def admin_set_org(user_id, org_id):
    """Super-admin only: assign / move a user to an organization (server-
    enforced). Returns (ok, msg)."""
    ok, err = _admin_post({"user_id": user_id, "org_id": (org_id or "").strip()})
    return (True, "Organization updated.") if ok else (False, err)


# -- Organizations (via the 'orgs' Edge Function) -----------------------------
def list_orgs():
    """List organizations. Super admin gets the full directory (with contact
    details); an org manager gets only their own org (id + name). Returns
    (ok, [ {id, name, ...} ])."""
    if not configured():
        return False, "Auth is not configured."
    tok = access_token()
    if not tok:
        return False, "You’re not signed in."
    try:
        r = _client().get(_functions_url("orgs"),
                          headers={"Authorization": f"Bearer {tok}"}, timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}"
    if r.status_code == 404:
        return False, "The ‘orgs’ Edge Function isn’t deployed yet."
    if r.status_code != 200:
        return False, _friendly(r)
    return True, (r.json() or {}).get("orgs", [])


def _orgs_post(payload):
    if not configured():
        return False, "Auth is not configured."
    tok = access_token()
    if not tok:
        return False, "You’re not signed in."
    try:
        r = _client().post(_functions_url("orgs"),
                           headers={"Authorization": f"Bearer {tok}"},
                           json=payload, timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}"
    if r.status_code == 404:
        return False, "The ‘orgs’ Edge Function isn’t deployed yet."
    if r.status_code != 200:
        return False, _friendly(r)
    return True, (r.json() or {})


def admin_upsert_org(org_id, name, contact_name="", contact_email="", contact_phone=""):
    """Super-admin only: create or edit an organization. Returns (ok, msg)."""
    org_id = (org_id or "").strip()
    name = (name or "").strip()
    if not org_id:
        return False, "Organization id is required."
    if not name:
        return False, "Organization name is required."
    ok, res = _orgs_post({"op": "upsert", "id": org_id, "name": name,
                          "contact_name": contact_name, "contact_email": contact_email,
                          "contact_phone": contact_phone})
    return (True, "Organization saved.") if ok else (False, res)


def admin_delete_org(org_id):
    """Super-admin only: delete an org (server blocks it if any user is still
    assigned to it). Returns (ok, msg)."""
    ok, res = _orgs_post({"op": "delete", "id": (org_id or "").strip()})
    return (True, "Organization deleted.") if ok else (False, res)


# ── Shared org-wide settings (via the 'org-settings' Edge Function) ───────────
# Same pattern as admin-users above: a server-side Edge Function holds the
# service_role key and enforces Admin-only writes; the desktop app only ever
# sends the caller's own token. Any signed-in user may READ (so a value an
# Admin sets is picked up by every install), only an Admin may WRITE. Today
# this holds one key, "email" (Gmail sender/App Password used to send
# reports) — the {key, value} shape leaves room for more shared settings later
# without another backend change.
def get_org_settings():
    """Any signed-in user: fetch the shared org-wide settings dict, e.g.
    {"email": {"sender": ..., "sender_name": ..., "app_password": ...}}.
    Returns (ok, settings_dict_or_message). EVERY failure mode — auth not
    configured, not signed in, offline, function not deployed, bad response —
    returns (False, message) rather than raising. Callers should treat False
    as 'fall back to local defaults', never as a fatal error: this is a
    convenience sync, not the only source of truth (each machine's local
    creds file still works standalone, exactly as before this existed)."""
    if not configured():
        return False, "Auth is not configured."
    tok = access_token()
    if not tok:
        return False, "You’re not signed in."
    try:
        r = _client().get(_functions_url("org-settings"),
                          headers={"Authorization": f"Bearer {tok}"}, timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}"
    if r.status_code == 404:
        return False, ("The ‘org-settings’ Edge Function isn’t deployed yet — see "
                       "ADMIN_USERS_SETUP.md.")
    if r.status_code != 200:
        return False, _friendly(r)
    try:
        return True, (r.json() or {}).get("settings", {})
    except Exception as ex:
        return False, f"Bad response from org-settings: {ex}"


def _org_settings_post(payload):
    if not configured():
        return False, "Auth is not configured."
    tok = access_token()
    if not tok:
        return False, "You’re not signed in."
    try:
        r = _client().post(_functions_url("org-settings"),
                           headers={"Authorization": f"Bearer {tok}"},
                           json=payload, timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}"
    if r.status_code == 404:
        return False, ("The ‘org-settings’ Edge Function isn’t deployed yet — see "
                       "ADMIN_USERS_SETUP.md.")
    if r.status_code == 403:
        return False, "Admins only."
    if r.status_code != 200:
        return False, _friendly(r)
    return True, None


def admin_set_org_email(sender, sender_name, app_password):
    """Admin-only: set the shared org-wide email sender config (address,
    display name, Gmail App Password) that every signed-in user's install
    picks up — configured once here instead of per-user/per-machine. The
    server verifies the caller is an Admin (app_metadata.role) before writing;
    a non-admin token gets a clean 403 back, never a silent partial write.
    Returns (ok, msg)."""
    value = {"sender": (sender or "").strip(),
            "sender_name": (sender_name or "").strip(),
            "app_password": (app_password or "").strip()}
    ok, err = _org_settings_post({"key": "email", "value": value})
    return (True, "Email settings saved for everyone.") if ok else (False, err)


# ── AI usage tracking (via the 'ai-usage' Edge Function) ──────────────────────
# Same server-side-privileged pattern as org-settings/admin-users. Every
# signed-in user may log THEIR OWN calls (the function derives user_id/email
# from the caller's verified JWT — never from the request body, so a call
# can't be logged under someone else's identity). Every signed-in user may
# also READ usage — but the function scopes what comes back by the caller's
# own role (hard check, not a capability toggle): an Admin gets rows across
# every user, anyone else gets ONLY their own rows, filtered server-side by
# their verified user_id — see the security notes in
# supabase/functions/ai-usage/index.ts.
def log_ai_usage(provider, model, input_tokens, output_tokens, tag=None):
    """Best-effort: upload one AI call's exact usage to the shared ai-usage
    log, so an Admin can later pull a whole-org report. This NEVER raises and
    NEVER blocks a caller on network trouble — it's a convenience mirror of
    the local per-user usage log (which is the source of truth for the
    signed-in user's OWN history and works fully offline); losing one upload
    just means that one call is missing from the admin's cross-user report,
    nothing else degrades. Returns True/False for callers that want to know,
    but nobody is required to check it."""
    if not configured():
        return False
    tok = access_token()
    if not tok:
        return False
    try:
        r = _client().post(_functions_url("ai-usage"),
                           headers={"Authorization": f"Bearer {tok}"},
                           json={"provider": provider, "model": model,
                                 "input_tokens": int(input_tokens or 0),
                                 "output_tokens": int(output_tokens or 0),
                                 "tag": tag},
                           timeout=_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def admin_get_ai_usage(start_date=None, end_date=None):
    """Any signed-in user: fetch raw per-call usage rows, optionally bounded
    to [start_date, end_date] ('YYYY-MM-DD' strings, inclusive). SCOPE is
    decided server-side by the caller's role — an Admin gets rows across
    ALL users; anyone else gets only their OWN rows (the function filters by
    their verified user_id, never by anything this client sends, so there's
    no way to request someone else's data). Returns (ok, rows_or_message).
    Each row is
    {created_at, user_email, provider, model, input_tokens, output_tokens, tag}
    — cost isn't included (computed locally from engine.PRICING so a price
    change never needs a redeploy). The 'Admins only' message below is now
    only reachable for the (separate) whole-org case if the server ever
    rejects it outright; ordinary non-admin reads succeed with their own
    rows instead."""
    if not configured():
        return False, "Auth is not configured."
    tok = access_token()
    if not tok:
        return False, "You’re not signed in."
    params = {}
    if start_date:
        params["start"] = start_date
    if end_date:
        params["end"] = end_date
    try:
        r = _client().get(_functions_url("ai-usage"),
                          headers={"Authorization": f"Bearer {tok}"},
                          params=params, timeout=_TIMEOUT)
    except Exception as ex:
        return False, f"Network error: {ex}"
    if r.status_code == 404:
        return False, ("The ‘ai-usage’ Edge Function isn’t deployed yet — see "
                       "ADMIN_USERS_SETUP.md.")
    if r.status_code == 403:
        return False, "Admins only."
    if r.status_code != 200:
        return False, _friendly(r)
    try:
        return True, (r.json() or {}).get("rows", [])
    except Exception as ex:
        return False, f"Bad response from ai-usage: {ex}"


# ── Remote-run credentials (per-user Supabase Vault — see REMOTE_RUNS.md) ────
def user_id():
    """The signed-in user's auth uid ('' when signed out). Used as
    remote_runs.created_by so the GitHub Actions worker resolves THIS user's
    credentials via worker_get_credentials."""
    with _lock:
        data = _load_session()
    u = (data or {}).get("user") or {}
    return str(u.get("id") or "")


def sync_remote_credentials(azure_org, azure_pat, ai_provider, ai_api_key, ai_model="",
                            gmail_sender="", gmail_sender_name="", gmail_app_pass=""):
    """Upsert the CALLER'S OWN remote-run credentials (rpc set_my_credentials,
    SECURITY DEFINER keyed on auth.uid(); secret values land in Supabase Vault,
    never in readable columns). Empty org/pat/provider/key/gmail_* leave the
    stored value unchanged; ai_model always writes (empty = provider default).
    gmail_sender_name always writes too (empty is a valid choice — falls back
    to the bare address, same as the desktop's own blank-name behavior).
    Returns (ok, message)."""
    if not configured():
        return False, "Supabase isn't configured."
    tok = access_token()
    if not tok:
        return False, "Not signed in."
    try:
        r = _client().post(f"{SUPABASE_URL}/rest/v1/rpc/set_my_credentials",
                           headers={"Authorization": f"Bearer {tok}"},
                           json={"p_azure_org": azure_org or None,
                                 "p_azure_pat": azure_pat or None,
                                 "p_ai_provider": ai_provider or None,
                                 "p_ai_api_key": ai_api_key or None,
                                 "p_ai_model": "" if ai_model is None else str(ai_model),
                                 "p_gmail_sender": gmail_sender or None,
                                 "p_gmail_sender_name": "" if gmail_sender_name is None
                                                        else str(gmail_sender_name),
                                 "p_gmail_app_pass": gmail_app_pass or None},
                           timeout=_TIMEOUT)
    except Exception as ex:
        if _diag: _diag.log("auth_supabase.sync_remote_credentials", ex)
        return False, f"Network error: {str(ex)[:120]}"
    if r.status_code not in (200, 204):
        return False, _friendly(r)
    return True, "Credentials synced — remote runs will execute as you."


def enqueue_remote_run(kind, project, plan_id, story_ids, existing_mode="skip",
                       output_lang="ar", email_recipients=None):
    """INSERT a remote_runs row AS the signed-in user (created_by = auth uid →
    the worker resolves THIS user's vault credentials). The DB trigger
    auto-dispatches the GitHub Actions workflow within seconds. Returns
    (ok, run_id_or_error_message).

    email_recipients — same recipient list the desktop's local-run report
    email uses (Setup's "Report Emails" field); the worker sends the exact
    same build_report_email() report to every address once the run finishes,
    same as a local run does today."""
    if not configured():
        return False, "Supabase isn't configured."
    tok = access_token()
    uid = user_id()
    if not tok or not uid:
        return False, "Not signed in."
    row = {"kind": "titles" if str(kind).startswith("title") else "steps",
           "project": str(project or ""), "plan_id": int(plan_id),
           "story_ids": [int(s) for s in (story_ids or [])],
           "existing_mode": existing_mode or "skip",
           "output_lang": output_lang or "ar",
           "email_recipients": [e.strip() for e in (email_recipients or []) if e.strip()],
           "created_by": uid}
    try:
        r = _client().post(f"{SUPABASE_URL}/rest/v1/remote_runs",
                           headers={"Authorization": f"Bearer {tok}",
                                    "Prefer": "return=representation"},
                           json=row, timeout=_TIMEOUT)
    except Exception as ex:
        if _diag: _diag.log("auth_supabase.enqueue_remote_run", ex)
        return False, f"Network error: {str(ex)[:120]}"
    if r.status_code not in (200, 201):
        return False, _friendly(r)
    try:
        return True, (r.json() or [{}])[0].get("id", "")
    except Exception:
        return True, ""


def list_remote_runs(limit=30):
    """The signed-in user's own remote runs, newest first — RLS already
    scopes this to created_by = auth.uid() (remote_runs_select policy), so
    no explicit filter is needed here; a signed-out/misconfigured caller
    just gets []. Powers the Remote Runs list screen."""
    if not configured():
        return []
    tok = access_token()
    if not tok:
        return []
    try:
        r = _client().get(f"{SUPABASE_URL}/rest/v1/remote_runs",
                          headers={"Authorization": f"Bearer {tok}"},
                          params={"select": "*", "order": "created_at.desc",
                                  "limit": str(int(limit))},
                          timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        return r.json() or []
    except Exception:
        return []


def get_remote_run(run_id):
    """Full row for one run (status/control/summary/timestamps/kind/project/
    plan_id/story_ids/email_recipients) — the detail view's poll target."""
    if not configured() or not run_id:
        return None
    tok = access_token()
    if not tok:
        return None
    try:
        r = _client().get(f"{SUPABASE_URL}/rest/v1/remote_runs",
                          headers={"Authorization": f"Bearer {tok}"},
                          params={"id": f"eq.{run_id}", "select": "*"},
                          timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        rows = r.json()
        return rows[0] if rows else None
    except Exception:
        return None


def get_remote_run_events(run_id, after_seq=0, limit=500):
    """Activity feed for one run, seq > after_seq — the detail view polls
    this every ~2s while the run is live and appends only the new rows,
    same incremental pattern run_worker.py's own control poller uses.
    RLS (remote_run_events_select) already scopes this to events on a run
    this user owns. Returns [] on any failure (never raises into the UI
    poll loop)."""
    if not configured() or not run_id:
        return []
    tok = access_token()
    if not tok:
        return []
    try:
        r = _client().get(f"{SUPABASE_URL}/rest/v1/remote_run_events",
                          headers={"Authorization": f"Bearer {tok}"},
                          params={"run_id": f"eq.{run_id}",
                                  "seq": f"gt.{int(after_seq)}",
                                  "select": "seq,kind,payload,created_at",
                                  # DESC + reverse, NOT asc — see below.
                                  "order": "seq.desc", "limit": str(int(limit))},
                          timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        rows = r.json() or []
        # Return the NEWEST `limit` events (restored to ascending order for
        # display), not the OLDEST. The detail view re-fetches the whole feed
        # every tick with after_seq=0 — it has to, because run_worker collapses
        # a line's lifecycle into ONE row updated in place (hb_id), which an
        # incremental after_seq poll would never see. But with "order=seq.asc"
        # + limit=500 that meant any run producing more than 500 event rows
        # returned the SAME earliest 500 forever: the activity feed froze at an
        # early line (a "Still checking for duplicates… 15s so far" heartbeat,
        # reported live) and never showed later activity or the completion
        # lines — even though the run finished fine and the meta card, which
        # polls get_remote_run separately, kept updating correctly.
        rows.reverse()
        return rows
    except Exception:
        return []


def set_remote_run_control(run_id, control):
    """PATCH remote_runs.control (pause/resume/stop) — the worker's
    _gate()/_control_poller() picks this up within ~2s. RLS
    (remote_runs_update_control) restricts this to the run's own owner, same
    as every other remote_runs write. `control=None` clears it (rarely
    needed from the UI — the worker clears it itself on Resume). Returns
    (ok, message)."""
    if not configured() or not run_id:
        return False, "Not signed in."
    tok = access_token()
    if not tok:
        return False, "Not signed in."
    if control not in ("pause", "resume", "stop", None):
        return False, f"Invalid control value: {control!r}"
    try:
        r = _client().patch(f"{SUPABASE_URL}/rest/v1/remote_runs?id=eq.{run_id}",
                            headers={"Authorization": f"Bearer {tok}",
                                     "Prefer": "return=minimal"},
                            json={"control": control}, timeout=_TIMEOUT)
    except Exception as ex:
        if _diag: _diag.log("auth_supabase.set_remote_run_control", ex)
        return False, f"Network error: {str(ex)[:120]}"
    if r.status_code not in (200, 204):
        return False, _friendly(r)
    return True, ""


def get_remote_run_status(run_id):
    """GET remote_runs.status for one run — used to gate the Start Run button
    against duplicate dispatches of the SAME queued run until it reaches a
    terminal state (done/stopped/error). Returns None on any failure (signed
    out, network blip, row not found) so the caller fails OPEN — a status
    check that can't complete must never permanently lock the button."""
    if not configured() or not run_id:
        return None
    tok = access_token()
    if not tok:
        return None
    try:
        r = _client().get(f"{SUPABASE_URL}/rest/v1/remote_runs",
                          headers={"Authorization": f"Bearer {tok}"},
                          params={"id": f"eq.{run_id}", "select": "status"},
                          timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        rows = r.json()
        return (rows[0].get("status") if rows else None)
    except Exception:
        return None


def remote_credentials_status():
    """Masked status via rpc get_my_credentials_status: {azure_org,
    ai_provider, ai_model, has_pat, has_key, updated_at} — never secret
    values. None when signed out / nothing stored / on any error."""
    if not configured():
        return None
    tok = access_token()
    if not tok:
        return None
    try:
        r = _client().post(f"{SUPABASE_URL}/rest/v1/rpc/get_my_credentials_status",
                           headers={"Authorization": f"Bearer {tok}"},
                           json={}, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        rows = r.json()
        return rows[0] if isinstance(rows, list) and rows else None
    except Exception as ex:
        if _diag: _diag.log("auth_supabase.remote_credentials_status", ex)
        return None
