# QA Studio — Full Security Review

**Date:** 2026-07-24 · **Repo:** github.com/AhmedSayedRepo/QA-Studio
**Reviewers:** Fable 5 (lead) + security-advisor agent (independent verification)
**App:** Python/Flet desktop + Android app; Supabase (auth, Postgres/RLS, 3 edge functions); GitHub Actions remote-run worker; Azure DevOps + AI-provider integrations.
**Method:** Static review of the working tree, full git history (141 release tags), CI workflow, edge functions, crypto, and the published APK. Non-destructive — no code executed, nothing sent, no data modified.

## Overall verdict

**Strong security posture. No critical or high findings.** No credential leak, no injection, no insecure deserialization, no disabled TLS. Authorization is correctly enforced server-side. The findings below are all **Low / Informational** hardening items.

The codebase shows deliberate, documented security work: server-side capability checks, an RLS lockdown migration, a fail-closed self-updater, and DPAPI-backed credential storage.

## Findings

### None — Critical / High

### Low

**L1 · `gh_token.txt` not in `.gitignore`.** `engine.py:8666-8674` reads a GitHub token from env (`QA_STUDIO_GH_TOKEN`/`GITHUB_TOKEN`) or a `gh_token.txt` file next to the app. That filename isn't gitignored, so a developer who follows the documented convention in the repo root could commit a live token. *Fix: add `gh_token.txt` (and `*.log`, incl. the committed `build.log`) to `.gitignore`.*

**L2 · Zip-slip surface in the source-update path.** `engine.py:9118` calls `zipfile.extractall(tmp)` without validating member paths. Mitigated: the archive is GitHub's zipball pinned to an immutable commit SHA resolved immediately before download, fetched over TLS — so exploitation needs a GitHub compromise or broken TLS. *Fix (defense in depth: reject members whose resolved path escapes `tmp`.)*

**L3 · Fernet fallback stores key alongside ciphertext (non-Windows only).** `store.py` uses Windows DPAPI (per-user, machine-bound) as the primary at-rest protection — good. The non-Windows fallback (`_fernet_key`, ~line 114-138) writes a locally generated Fernet key to disk near the encrypted blob, so on those platforms at-rest encryption doesn't defend against an attacker who can already read the user's files. The module comment acknowledges this. Acceptable for a Windows-primary app; note it if Linux/macOS becomes a first-class target.

### Informational

**I1 · Self-update integrity is hash/commit-pinned, not code-signed.** `_verify_download` (engine.py:8924) **fails closed** — a release with no published `SHA256SUMS`/`*.sha256` is rejected — and the source path pins to an immutable commit SHA. This defeats transport tampering and corruption, but not a compromised repo/release (an attacker who can push a release could publish a matching hash). A code-signing certificate would be the stronger control. Reasonable as-is.

**I2 · Edge functions use `Access-Control-Allow-Origin: *`.** Safe here: auth is via explicit `Authorization: Bearer` tokens, not cookies, so there's no ambient authority for a browser to abuse. Fine for a native-app backend.

**I3 · Publishable Supabase key hardcoded** in `auth_supabase.py:46-49` (`sb_publishable_…`). By design and safe — this is the client-safe anon/publishable key, gated by RLS. Your security therefore rests on RLS policies + edge-function authz being correct (they are, per below); worth a periodic RLS review.

## What was checked and found clean

- **Credentials / secrets** — no live keys, tokens, passwords, PATs, or private keys in the tree, across all 141 tags/history, in the workflow, edge functions, or the 105 MB published APK. Server secrets (`SUPABASE_SERVICE_ROLE_KEY`, `AZURE_PAT`, `QA_AI_API_KEY`) come only from env / GitHub Secrets with no hardcoded fallback. (Full detail: `SECURITY_SCAN_CREDENTIALS.md`.)
- **Server-side authorization (the real security boundary)** — all 3 edge functions verify the caller's JWT server-side and enforce role/capability *before* using the service-role key: `admin-users` Admin-only; `org-settings` GET gated on `act.export` cap + table locked to service-role via RLS, POST Admin-only; `ai-usage` derives identity from the verified JWT and hard-filters non-admins to their own `user_id`. No trust in client-supplied identity or scope.
- **Injection** — no `eval`/`exec`/`os.system`/`shell=True`; all `subprocess` calls use list-argument form. No raw SQL string-building (Supabase PostgREST client is parameterized; DB access via RPC/`.from().eq()`). PowerShell shortcut string interpolates only app-derived paths, not user input.
- **Deserialization / parsing** — no `pickle`/`yaml.load`/`marshal`; no untrusted XML parsing (no XXE).
- **Transport** — no `verify=False`, no disabled cert checks; downloads over HTTPS.
- **Crypto** — DPAPI primary (per-user, `CRYPTPROTECT_LOCAL_MACHINE` not set); Fernet (AES-128-CBC+HMAC) fallback with a randomly generated key. md5/sha1 appear only for non-security cache keys / dev file-compare, never for passwords or integrity.

## Remediation status (2026-07-24)

- **L1 — FIXED.** Added `gh_token.txt` and `*.log` to `.gitignore`, and untracked the
  four previously-committed logs (`build.log`, `diagnostics_copy.log`, `qa_perf.log`,
  `qa_perf_installed.log`) via `git rm --cached` (local copies kept). `git check-ignore`
  confirms they're now ignored.
- **L2 — FIXED.** `engine.py` update path now validates every zip member resolves inside
  the temp dir before `extractall`, failing closed on any `../`/absolute path. Guard
  unit-tested against traversal/absolute-path payloads (all pass).
- **L3 — accepted risk, no code change.** The Fernet fallback's key-beside-ciphertext is
  inherent to a local symmetric store without an OS keystore; the app is Windows-primary
  and uses DPAPI there. Revisit only if Linux/macOS becomes a first-class target (would
  need a platform keychain integration).

## Remaining recommendations (not code-fixable here)

1. Periodically review Supabase RLS policies + the `worker_get_credentials` RPC grants —
   that vault RPC is the crown jewel, and `DEV_ROADMAP.md` records one past (self-caught,
   remediated) over-permissive grant to `anon`.
2. Consider code-signing for releases (I1) if/when you attach built binaries.

*No live secret was found exposed, so no emergency rotation is indicated. If any Actions secret was ever pasted into a commit or log historically, rotate it as routine hygiene.*
