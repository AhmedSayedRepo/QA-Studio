# QA Studio — External-User Auth (Supabase Auth, Free tier)

Goal: let **external** users (customers, partners, testers) **sign up** and **sign
in** themselves, with role-based permissions — free, hosted, and with real
server-side security. This complements the internal Entra option (`auth.py`); use
this one when the people signing in are *not* in your Microsoft org.

Implemented in **`auth_supabase.py`** (Phase 1, done & unit-tested). UI wiring is
Phase 2 (snippet below).

---

## 0. TL;DR

- **Provider:** Supabase Auth (the open-source **GoTrue** service), used over its
  plain REST API — no SDK, just `requests` (already a dependency).
- **Self-service:** email/password **sign-up** with email confirmation, **sign-in**,
  **password reset**, **resend confirmation** — all built in.
- **Security:** passwords are hashed and stored by Supabase (never on the device);
  JWTs are signed by your project, so the **role can't be forged** client-side.
- **Authorization:** role read from the signed **`app_metadata.role`** → mapped to
  QA Studio capabilities in `auth_supabase.PERMISSIONS`.
- **Token cache:** access + refresh tokens encrypted at rest with the existing
  Windows DPAPI (`store.py`), refreshed silently.
- **Ships dark:** until `SUPABASE_URL` + `SUPABASE_ANON_KEY` are set,
  `configured()` is False and the app runs exactly as today.
- **Cost:** $0 on the free tier (50,000 monthly active users).

---

## 1. Why Supabase Auth (vs the alternatives) for external users

| Option | Verdict |
|---|---|
| **Supabase Auth (Free)** | ✅ Chosen. Free 50k MAU, hosted, open-source, self-service sign-up + email confirm + reset, signed JWT roles, simple REST from desktop. |
| Firebase Authentication (Free) | Also solid/free; good if you're already in Google's ecosystem. Slightly more SDK-oriented; roles need custom claims via Admin SDK. |
| Auth0 / Clerk (Free tiers) | Great DX but paid beyond modest MAU caps and a second vendor. |
| Entra **External ID** (CIAM) | Microsoft's external-user product; fine if you want one vendor with Entra, usage-priced with a free MAU allowance. |
| Fully local / on-device | Not real security for external users — bypassable on the device, no central account control. |

---

## 2. One-time Supabase setup (you, in the dashboard)

1. Create a free project at supabase.com → note the **Project URL** and the
   **anon public** API key (Settings → API). *Neither is a secret — the anon key is
   designed to ship in clients.*
2. **Authentication → Providers → Email**: enable it. Keep **"Confirm email" ON**
   (recommended) so addresses are verified.
3. **Authentication → URL Configuration**: set a Site URL / redirect (e.g. your
   site or `http://localhost`) for the confirmation + reset links.
4. **Roles:** set each user's role in their **`app_metadata`** (admin-only, signed).
   Easiest ways:
   - Dashboard → Authentication → Users → (user) → edit `app_metadata` →
     `{"role": "Member"}`.
   - Or SQL / an Edge Function on the `auth.users` table for automation.
   Valid roles: `Admin`, `Member`, `Viewer` (see PERMISSIONS). No role → `Viewer`.
5. Put the two values in env vars (or the constants in `auth_supabase.py`):
   `SUPABASE_URL`, `SUPABASE_ANON_KEY`.

That's it — no server to host, no secret on the device.

---

## 3. Sign-up / sign-in flow

```
Sign up:  auth_supabase.sign_up(email, password, name)
            → POST /auth/v1/signup
            → if "Confirm email" is ON: user must click the emailed link first
              (returns ok=True, user=None, "check your email")
            → else: session created immediately

Sign in:  auth_supabase.sign_in(email, password)
            → POST /auth/v1/token?grant_type=password
            → returns (ok, message, user) and caches the session (encrypted)

Resume:   auth_supabase.acquire_silent()        # on startup, no prompt
            → loads the encrypted cache; refreshes the access token if expired
Reset:    auth_supabase.request_password_reset(email)
Resend:   auth_supabase.resend_confirmation(email)
Sign out: auth_supabase.sign_out()              # revokes server-side + wipes cache
```

`sign_up` / `sign_in` return **`(ok, message, user)`** so the UI can show the exact
message (e.g. "check your email", "Incorrect email or password"). `user` is
`{id, email, name, role, confirmed}`.

---

## 4. Authorization (RBAC)

Roles come from the **signed** `app_metadata.role`; capabilities are defined in
`auth_supabase.PERMISSIONS`:

| Capability | Admin | Member | Viewer |
|---|:---:|:---:|:---:|
| `view` / `export` | ✅ | ✅ | ✅ |
| `generate` / `run` | ✅ | ✅ | — |
| `regression` / `sprint` | ✅ | ✅ | — |
| `automation` | ✅ | — | — |
| `edit_providers` (Setup creds) | ✅ | — | — |

`permissions_for(user)` / `has(user, CAP_*)` are the gates. No role → `Viewer`.

---

## 5. UI wiring (Phase 2 — main.py)

Mirror the Entra integration points, swapping `auth` for `auth_supabase`:

```python
import auth_supabase as auth

# startup: resume a session without prompting
if auth.configured():
    self.user = auth.acquire_silent()

# render(): if configured and not signed in, show the sign-in / sign-up screen
if auth.configured() and not getattr(self, "user", None):
    view = auth_screen(self)        # email+password sign in, "create account", "forgot password"
else:
    ... # normal shell

# gate nav items + action handlers
if not auth.has(self.user, auth.CAP_GENERATE):
    return self._err("Not permitted")

# user chip + sign out where the provider chip is
auth.sign_out(); self.user = None; self.render()
```

The screen needs three actions on `auth_supabase`: `sign_in`, `sign_up`,
`request_password_reset` — each returns `(ok, message, …)` to drive a toast.

---

## 6. Security notes

- **Passwords** are hashed + stored by Supabase (bcrypt), never on the device.
- **Anon key is public** by design; it only allows the auth/PostgREST operations
  your Row-Level-Security policies permit — it is **not** the service-role key
  (keep that server-side only, never in the app).
- **Roles are signed** into the JWT/`app_metadata`, so a user editing local state
  can't grant themselves Admin; the value comes from Supabase.
- **Tokens at rest** are DPAPI-encrypted (`store.py`), per-user + machine-bound;
  access tokens are short-lived and refreshed; we never log tokens.
- **Email confirmation** keeps drive-by/typo accounts out; **reset** is self-serve.
- **True enforcement:** as with any client app, the local gate is advisory. For
  hard enforcement, put sensitive operations behind a small service that validates
  the Supabase JWT (`access_token()` gives you the bearer to send) and checks the
  role server-side.

---

## 7. Status

| Phase | Scope | Status |
|---|---|---|
| 1. Module | `auth_supabase.py` (sign up/in/out, refresh, reset, RBAC, encrypted cache) | ✅ Done, unit-tested |
| 2. UI | sign-in/sign-up/forgot screen + nav/action gating + user chip | Next |
| 3. Polish | social/OAuth logins, idle re-auth, audit log | After |
| 4. Hard enforcement | token-checked backend for sensitive calls | When required |
