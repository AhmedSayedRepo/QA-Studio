# QA Studio — Credential Leakage Scan

**Date:** 2026-07-24 · **Repo:** github.com/AhmedSayedRepo/QA-Studio
**Scope:** Working tree, full git history, all 141 release tags, GitHub Actions workflow, Supabase edge functions, and the published Android APK.

## Verdict

**No credential leak found.** No live API keys, service-role tokens, passwords, PATs, or private keys are committed anywhere in the code, history, release assets, or the APK. Server-side secrets are correctly sourced from environment variables / GitHub Secrets.

## What was scanned

| Surface | Method | Result |
|---|---|---|
| Tracked files (115) | Regex scan for OpenAI/Anthropic/AWS/GitHub/Slack/Google/GitLab keys, JWTs, private keys | Clean |
| Password/token/secret literal assignments | Grep for hardcoded `secret=`, `password=`, `api_key=` values | Clean (all read from env/keyring) |
| Git history — all refs, code files | `git log -p -S` for secret tokens (image blobs excluded) | Clean |
| 141 release tags (`install.bat`) | Scanned each tagged asset | Clean |
| `.github/workflows/remote-run.yml` | Secret handling review | Correct — all via `${{ secrets.* }}` |
| `supabase/functions/*` (3 edge functions) | Hardcoded-key grep | Clean |
| Published `app-release.apk` (105 MB) | `strings` scan + archive listing for service_role/sb_secret/AI keys/bundled `.env` | Clean |

## Notes (informational, not leaks)

**Hardcoded Supabase project URL + anon key** — `auth_supabase.py:46-49` ships a default
`SUPABASE_URL` (`psiyktcrggmgralyswua.supabase.co`) and an `sb_publishable_...` key.
This is **by design and safe**: publishable/anon keys are meant to be embedded in clients and
are protected by Supabase Row-Level Security. The code comment says as much. Caveat: this makes
your security posture depend entirely on RLS policies + edge-function authorization being correct
server-side — something this scan can't verify from the client repo. Worth a periodic RLS review.

**Server-side secrets are handled correctly.** `run_worker.py` reads
`SUPABASE_SERVICE_ROLE_KEY`, `AZURE_PAT`, and `QA_AI_API_KEY` from `os.environ` with **no
hardcoded fallbacks** — they only exist in GitHub repository secrets, never in the tree or the
shipped desktop/mobile builds.

**`.gitignore` correctly excludes** `creds.dat`, `*.dat`, `.qa_tool/`, and `apk-out/`.

## Scan limitations

- GitHub's REST API and web UI weren't reachable from this environment, so release *assets* were
  verified via their versioned source (git tags) rather than by downloading each published binary.
  The two custom asset types (`install.bat`, CI-built APK) were both checked directly.
- One regex hit (`AKIaquaqiuZ5NVEz20js`) was a **false positive** — mixed-case, not a valid AWS
  key format, and not present in the working tree (a substring of base64 blob data).

## Recommendation

If any of the *live* secrets in GitHub Actions (`SUPABASE_SERVICE_ROLE_KEY`, `AZURE_PAT`,
`QA_AI_API_KEY`) have ever been pasted into a commit, chat, or CI log historically, rotate them as
a precaution — but nothing in this scan indicates that happened. Otherwise, no action required.
