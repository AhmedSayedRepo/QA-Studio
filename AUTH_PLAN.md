# QA Studio — Authentication & Authorization Plan (Microsoft Entra ID, Free tier)

Goal: add **sign-in** (and "sign-up" = first sign-in with a work account) and
**role-based permissions** to QA Studio, reusing Microsoft Entra ID (Azure AD)
since the team already lives in the Azure DevOps / Microsoft ecosystem. No
passwords stored by the app, no auth server to run, and $0 on the Entra **Free**
tier for internal users.

---

## 0. TL;DR

- **Identity provider:** Microsoft Entra ID (Free). Users sign in with the work
  account they already use for Azure DevOps. "Sign up" is just the first sign-in
  (account provisioning is handled by your tenant, not by QA Studio).
- **Protocol:** OAuth2 **Authorization Code + PKCE**, the desktop-app pattern, via
  the official **MSAL** library. Public client (no secret on the device).
- **Authorization:** Entra **app roles** (`Admin` / `Member` / `Viewer`) arrive as
  a signed `roles` claim → mapped to QA Studio capabilities in `auth.py`.
- **Token storage:** MSAL token cache, encrypted at rest with the existing
  Windows DPAPI in `store.py` (per-user, machine-bound). Silent refresh.
- **Honest limit:** QA Studio is a local, open-source Python app, so any check it
  runs can be edited out by a determined user. Client-side gating + signed roles
  is *good enough for a trusted internal team*; **true** enforcement requires
  moving the sensitive operations behind a token-checked service (Phase 4).

---

## 1. Why Entra (and not the alternatives)

| Option | Verdict |
|---|---|
| **Entra ID (Free)** | ✅ Chosen. Reuses existing accounts; Microsoft owns password storage, MFA, lockout, revocation; free for internal users; signed roles. |
| Keycloak / Zitadel / Authentik (self-hosted) | Viable but you'd run and patch a server. Rejected per your call. |
| Auth0 / Clerk / Cognito (managed) | Fine, but a paid external dependency and a second identity store. |
| Own backend (FastAPI + DB) | Most work + you own all the security. Overkill for an internal tool. |
| Fully local / on-device | Not real security — bypassable on the device, no central control. Only soft gating. |

---

## 2. Constraints this design respects

- **Desktop, no secret storage.** A distributed app can't hold a client secret, so
  we use a **public client + PKCE** (the code-interception protection that replaces
  the secret).
- **Local-first today.** `store.py` already encrypts creds with DPAPI; the token
  cache reuses that, so nothing new is stored in plaintext.
- **Ship dark.** Until `ENTRA_TENANT_ID` + `ENTRA_CLIENT_ID` are set, `auth.configured()`
  is `False` and QA Studio runs exactly as it does today — no gate, no risk of
  locking anyone out before the tenant is ready.
- **Offline tolerance.** Silent refresh works while a token is valid; the plan
  includes a grace/again-later path so a flaky network doesn't hard-block work.

---

## 3. One-time Azure setup (you, in the Entra admin center)

1. **App registration** → New registration.
   - Name: `QA Studio`. Supported accounts: *Accounts in this org directory only*.
   - Platform: **Mobile and desktop applications**. Redirect URI:
     `http://localhost` (MSAL uses an ephemeral loopback port for the desktop flow).
   - Tick **"Allow public client flows" = Yes** (Authentication → Advanced).
2. **App roles** (App registration → App roles): create
   - `Admin` (value `Admin`), `Member` (value `Member`), `Viewer` (value `Viewer`),
   allowed member types = *Users/Groups*.
3. **Assign people** (Enterprise applications → QA Studio → Users and groups):
   assign each user (or an AD group) to a role.
4. Copy the **Directory (tenant) ID** and **Application (client) ID** into the app's
   env vars (`ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`) or `auth.py`. *Neither is a
   secret.*

No client secret, no certificates, no redirect to a hosted page — the desktop
flow is self-contained.

---

## 4. Sign-in / sign-up flow

```
User clicks "Sign in"
  → MSAL acquire_token_interactive(scopes, prompt="select_account")
      → opens the SYSTEM BROWSER at login.microsoftonline.com
      → user authenticates (password + MFA, all on Microsoft's page)
      → browser redirects to http://localhost:<ephemeral>?code=...
      → MSAL exchanges code (+ PKCE verifier) for tokens at the token endpoint
  → tokens cached (encrypted via store.py DPAPI); id_token_claims parsed
  → roles claim → permissions → QA Studio unlocks the allowed screens
```

- **"Sign up"** is the same call; provisioning/invites are a tenant concern, not
  the app's. (For external/customer self-service sign-up you'd switch to **Entra
  External ID / CIAM** — out of scope here, noted in §9.)
- **Returning users:** on launch, `auth.acquire_silent()` restores the session from
  the encrypted cache with no prompt (refreshing the token if needed).
- **Sign out:** `auth.sign_out()` forgets the account and wipes the cache.

All of the above is already implemented in **`auth.py`** (Phase 1, done).

---

## 5. Authorization model (RBAC)

Roles come from Entra (signed, can't be forged client-side); capabilities are
defined in `auth.py → PERMISSIONS`:

| Capability | Admin | Member | Viewer |
|---|:---:|:---:|:---:|
| `view` (open app, read reports) | ✅ | ✅ | ✅ |
| `export` (Word/Excel/PDF/JSON) | ✅ | ✅ | ✅ |
| `generate` (titles/steps) | ✅ | ✅ | — |
| `run` | ✅ | ✅ | — |
| `regression`, `sprint` plans | ✅ | ✅ | — |
| `automation` | ✅ | — | — |
| `edit_providers` (Setup creds / AI keys) | ✅ | — | — |

- **No role assigned** → least privilege (`Viewer`), never Admin.
- Enforcement points (Phase 2): the nav rail (`rail()` in `main.py`) hides/locks
  screens the user lacks; each action handler re-checks `auth.has(user, CAP_*)`
  before doing work (defence in depth, not just hiding buttons); `setup_screen`'s
  credential fields are read-only without `edit_providers`.

---

## 6. Token & secret handling (security checklist)

- **PKCE** on every auth (MSAL default) — protects the code exchange without a
  secret.
- **System browser, not an embedded webview** — avoids credential phishing and lets
  the user see the real `microsoftonline.com` URL + their MFA.
- **Tokens encrypted at rest** via DPAPI (`store.py`); cache file is
  `~/.qa_tool/auth_cache.bin`, per-user, machine-bound, not portable.
- **Short-lived access tokens + refresh** handled by MSAL; we never log tokens.
- **Least privilege** default role; explicit deny when unauthenticated.
- **Signed claims**: roles are read from the **ID token** (signed by Entra), not
  from any client-writable state.
- **Revocation**: disabling/removing the user or unassigning the role in Entra
  takes effect on the next token refresh (minutes), not "never".
- **No new attack surface on disk**: reuses the existing creds vault; no plaintext
  user database, no homegrown password hashing.
- **Logout / shared machines**: `sign_out()` clears the cache; add an idle-timeout
  re-auth in Phase 3 for shared kiosks.

### Threat model — what this does and does NOT stop
- **Stops:** outsiders without a valid org account; casual use by unauthorized
  people; role mistakes (least-privilege default); password theft (Microsoft holds
  passwords + MFA).
- **Does NOT stop (by itself):** a determined user *on their own machine* editing
  the Python source to bypass the gate, since the app + the Azure PAT/AI keys are
  already on that device. That's the inherent limit of any client-only check.
- **Mitigation / true enforcement (Phase 4):** put the sensitive calls (Azure
  DevOps writes, AI generation) behind a thin service that validates the access
  token and the role server-side, and move the org's Azure/AI secrets there. Then
  a bypassed local gate yields nothing because the *server* enforces authz and
  holds the credentials.

---

## 7. Code integration points

- **`auth.py`** (new, done) — MSAL flow, encrypted cache, roles → permissions.
- **`requirements.txt`** — `msal>=1.28.0` added.
- **`main.py`** (Phase 2):
  - `__init__`/startup: `self.user = auth.acquire_silent()` if `auth.configured()`.
  - `render()`: if `auth.configured()` and no `self.user`, show the **sign-in
    screen** instead of the normal shell (a single "Sign in with your work account"
    button calling `auth.sign_in()` off-thread, then `goto` home).
  - `rail()`: gate nav items by `auth.has(self.user, CAP_*)`; show a small user
    chip (name + role) with a "Sign out" action where the provider chip is.
  - Action handlers (`_start_run`, `_calculate`, automation start, provider saves):
    early `if not auth.has(self.user, CAP_*): return self._err("Not permitted")`.
- **`store.py`** — unchanged; `auth.py` reuses its DPAPI helpers.
- **No changes** to `engine.py` / `regression.py` logic in Phases 1–2.

---

## 8. Phased rollout

| Phase | Scope | Status |
|---|---|---|
| **1. Foundation** | `auth.py` (MSAL flow, encrypted cache, RBAC map), `msal` dep | ✅ Done |
| **2. Gate the UI** | Sign-in screen, startup silent-auth, nav + action gating, user chip + sign out | Next |
| **3. Polish** | Idle re-auth, "session expired" UX, role-aware empty states, audit log of who-ran-what | After |
| **4. True enforcement (optional)** | Move sensitive calls + org secrets behind a token-checked service | When real security is required |

Each phase is independently shippable; Phases 1–2 keep the app fully usable for an
internal team. Auth stays **off** until the tenant/client id are configured.

---

## 9. Open decisions

1. **Roles vs groups** — app roles (simple, in the token) vs AD security groups
   (reuse existing groups, but may need Graph lookup for >~150 groups). Default:
   app roles.
2. **Hard gate vs soft gate** — block the app entirely without sign-in, or allow
   read-only `Viewer` offline? Default: hard gate when `configured()`.
3. **External users** — if customers ever need self-service sign-up, add **Entra
   External ID (CIAM)** as a second authority (usage-priced, free MAU allowance).
4. **Shared machines** — add idle-timeout re-auth (Phase 3) if QA Studio runs on
   kiosks.

---

## 10. Definition of done (Phase 2)

- Configured tenant: launching QA Studio shows the sign-in screen; signing in with
  a work account lands on Setup with the user's name + role shown.
- A `Viewer` sees Reports but `Generate`/`Run`/provider edits are blocked (hidden +
  handler-guarded).
- Sign out returns to the sign-in screen and clears the cache.
- Unconfigured tenant: behaves exactly like today (no gate).
