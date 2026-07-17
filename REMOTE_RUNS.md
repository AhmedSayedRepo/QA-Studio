# Remote Runs — server-side execution on GitHub Actions (free tier)

Runs QA Studio's Titles/Steps generation **without the desktop app running**,
on GitHub-hosted runners, with Supabase (the project the app already uses) as
the control plane. This is the executor leg of MOBILE_PLAN.md Phase 3: the
desktop/mobile apps become viewers/controllers of runs that execute elsewhere.

## How it fits together
```
app (desktop/mobile)                Supabase (psiyktcrggmgralyswua)          GitHub Actions
────────────────────                ────────────────────────────────          ──────────────
enqueue: INSERT remote_runs  ──►    remote_runs (queue + control + status)
dispatch workflow (API)      ────────────────────────────────────────►  ◄──  remote-run.yml
watch:  Realtime on          ◄──    remote_run_events (the cb feed)     ◄──  run_worker.py
control: UPDATE control=            control: pause / resume / stop  ────►    (polls every 2s)
        pause|resume|stop
```
- `run_worker.py` executes `E.run_titles` / `E.run_steps` unchanged — same
  activity feed (`cb` events, verbatim into `remote_run_events`), same
  pause-on-provider-error behavior, same Stop semantics.
- Budget: private repos get 2,000 free Linux minutes/month; a run bills its
  actual wall-clock, so ~10–20 min/run ≈ 100–200 free runs/month.

## One-time setup
1. **Database** — already applied (migration `remote_runs_control_plane` on the
   QA Studio Supabase project): tables `remote_runs` + `remote_run_events`,
   RLS (authenticated app users read/write runs + read events; the worker uses
   the service-role key), both tables in the Realtime publication.
2. **Repo secrets** (GitHub → Settings → Secrets and variables → Actions), on
   `AhmedSayedRepo/qa-studio` — only TWO are required:
   | Secret | Value |
   |---|---|
   | `SUPABASE_URL` | `https://psiyktcrggmgralyswua.supabase.co` |
   | `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Project Settings → API → service_role |
   | `AZURE_ORG`/`AZURE_PAT`/`QA_AI_*` | OPTIONAL — env fallback used only for runs enqueued WITHOUT `created_by` (debugging) |
3. **Push** this folder (the workflow only exists on GitHub once
   `.github/workflows/remote-run.yml` is pushed to `main`).

## Executing a run (manual smoke test, before any app integration)
1. Enqueue (Supabase SQL editor):
   ```sql
   insert into remote_runs (kind, project, plan_id, story_ids, existing_mode)
   values ('steps', 'YourProject', 12345, '[101046, 101049]', 'skip')
   returning id;
   ```
2. Dispatch with the returned uuid:
   ```
   gh workflow run remote-run.yml -f run_id=<uuid>
   ```
   (or POST to `/repos/AhmedSayedRepo/qa-studio/actions/workflows/remote-run.yml/dispatches`
   with `{"ref":"main","inputs":{"run_id":"<uuid>"}}` and a token with `actions:write`.)
3. Watch: the Actions job log shows every cb event as JSON lines, and
   `select * from remote_run_events where run_id='<uuid>' order by seq` shows
   the same feed. Live pause/stop:
   ```sql
   update remote_runs set control='pause' where id='<uuid>';   -- holds between items
   update remote_runs set control='resume' where id='<uuid>';
   update remote_runs set control='stop'  where id='<uuid>';
   ```
4. Final state lands on the row: `status` (`done|stopped|error`), `summary`,
   `finished_at`.

## Per-user credentials (migration `user_credentials_vault` — applied)
Every user runs with THEIR OWN Azure PAT and AI provider/key, entered once in
the app (mobile or desktop) and synced via Supabase:
- **Storage**: secret values live in **Supabase Vault** (encrypted at rest);
  `public.user_credentials` holds only vault secret IDs + non-secret metadata
  (org, provider, model, timestamps).
- **Access model (verified against pg catalogs after applying)**: the table
  has NO client grants at all (RLS on; ACL = postgres + service_role only).
  Clients use two SECURITY DEFINER RPCs — `set_my_credentials(...)` (writes
  the caller's own row only, via `auth.uid()`) and
  `get_my_credentials_status()` (masked: org/provider/model + has_pat/has_key
  booleans, never values). The decrypting `worker_get_credentials(uuid)` is
  executable by **service_role only** — unreachable from any app client.
- **Worker resolution**: a run enqueued with `created_by = auth user id` makes
  the worker fetch that user's credentials at start, and RE-FETCH them on
  every Resume after a provider-error pause — so switching provider or
  topping up credits in the app, then setting `control='resume'`, rescues a
  paused remote run with the new credentials.
- **Trust statement**: the service-role key (repo secret) can decrypt stored
  credentials, and repo admins control the workflow that holds it — protect
  `main` with branch protection; that key + repo write access is the root of
  trust for this system.
- **App integration**: the Setup/credentials screen calls
  `rpc/set_my_credentials` with whatever the user typed (empty fields leave
  the stored value unchanged) and renders state from
  `rpc/get_my_credentials_status`. Runs are enqueued with
  `created_by = <auth uid>`.

## Semantics & limits (deliberate)
- **Pause-on-provider-error** works exactly like the desktop (engine
  `on_ai_error`): status flips to `paused`, the run waits for
  `control='resume'` (retries the failed item) or `stop`. Note: switching the
  AI provider for a REMOTE run means updating repo secrets — resume without a
  change mostly makes sense after a rate-limit window or credit top-up.
- **No report email from the worker yet** — the `done` event carries the
  summary; wiring `GMAIL_*` secrets + the engine's email builders is a small
  follow-up if wanted.
- **Event feed is best-effort**: a failed batch insert is logged to the job
  output and dropped — telemetry must never kill a run. The Actions job log
  always has the complete raw feed.
- **Concurrency**: one job per run id (workflow-level `concurrency` group);
  parallel DIFFERENT runs are allowed and each consumes its own minutes.

## Next steps (not yet built)
- Desktop app: "Run remotely" toggle on the Run screen — INSERT + dispatch +
  subscribe instead of local execution (the viewer can reuse the existing
  activity-log rendering as-is, since events are verbatim cb payloads).
- Mobile (MOBILE_PLAN.md Phase 3): the same viewer + control buttons.
- Push notification on done/paused via a Supabase webhook → FCM/APNs.
