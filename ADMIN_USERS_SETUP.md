# QA Studio — Admin "Users" tab setup

The in-app **Users** tab (visible only to Admins) lists every account and lets an
Admin change each user's role (Viewer / Member / Admin). The privileged work runs
in a small **Supabase Edge Function** (`admin-users`) that holds the project's
`service_role` key server-side — that key never ships in the desktop app, so this
is the secure way to manage users.

You deploy the function **once**. After that the tab just works.

---

# Option A — Deploy from the Dashboard (no CLI, recommended)

1. Open your [Supabase Dashboard](https://supabase.com/dashboard) → your project →
   **Edge Functions** (left sidebar).
2. Click **Deploy a new function** → **Via Editor**.
3. Name it exactly **`admin-users`** (the app calls `/functions/v1/admin-users`).
4. Delete the template code and paste the entire contents of
   `supabase/functions/admin-users/index.ts` from this repo.
5. **Turn off "Verify JWT"** for this function (it's a toggle in the editor's
   settings / function settings). The function verifies the caller itself and
   requires an Admin, and you're using a publishable key — so platform JWT
   verification should be off. *(If you can't find the toggle, deploy anyway and
   only come back to disable it if calls fail with 401.)*
6. Click **Deploy function** and wait for the success message.

That's it — skip to **§4 Use it** below.

---

# Option B — Deploy with the Supabase CLI

Note: a global `npm install -g supabase` is **not** supported, and there's no
`supabase` on your PATH yet — use Scoop, the prebuilt binary, or `npx`.

## 1. Install the Supabase CLI (once)

- Windows (with Scoop): `scoop install supabase`
- or npm: `npm install -g supabase`
- or download from https://github.com/supabase/cli/releases

Check it: `supabase --version`

## 2. Log in and link your project

```
supabase login
supabase link --project-ref psiyktcrggmgralyswua
```

(`psiyktcrggmgralyswua` is your project ref — the subdomain of your Project URL.)

## 3. Deploy the function

The function source is already in this repo at
`supabase/functions/admin-users/index.ts`. From the `qa-studio` folder:

```
supabase functions deploy admin-users --no-verify-jwt
```

Why `--no-verify-jwt`: the function **verifies the caller itself** (it checks the
JWT and requires `app_metadata.role === "Admin"`), which also makes it work with
the publishable/anon key. The `service_role` key is injected automatically as an
environment variable — you don't set any secrets.

## 4. Use it

1. Make sure your own account is **Admin** (SQL editor, once):
   ```sql
   update auth.users
   set raw_app_meta_data = coalesce(raw_app_meta_data,'{}'::jsonb) || '{"role":"Admin"}'::jsonb
   where email = 'you@yourdomain.com';  -- ⚠️ REQUIRED: replace with YOUR OWN email
   ```
   **⚠️ Do not omit the `WHERE` clause.** Without it, this statement grants
   Admin to **every** row in `auth.users` — including any self-registered
   external users already in the project — not just your own account. Double-
   check the `where` clause matches exactly one row before running it, e.g.
   `select email from auth.users where email = 'you@yourdomain.com';` first.
   Then sign out / in.
2. In QA Studio, the **Users** tab appears in the sidebar (Admins only).
3. Click a role chip (Viewer / Member / Admin) on any row to change that user's
   role. It takes effect the next time that user signs in (or their token
   refreshes).

---

## Security notes

- The Edge Function rejects anyone who isn't an Admin (HTTP 403), so a Member or
  Viewer calling it directly gets nothing.
- The desktop app only ever sends the **Admin's own access token** — never the
  service_role key.
- Changing your *own* role away from Admin asks for confirmation (you'd lose
  access to this tab until another admin restores it).

## Troubleshooting

- **"Edge Function isn't deployed yet" in the app** → run step 3.
- **403 Admins only** → your account isn't Admin yet (step 4.1).
- **CLI can't link** → double-check the project ref and that you ran
  `supabase login`.

---

# 5. The `org-settings` function (shared email config)

The Report screen's "send" feature uses one org-wide shared setting (the Gmail
sender / App Password) so every signed-in user's install sends the same way,
configured once by an Admin instead of per-machine. This is a **separate**
Edge Function from `admin-users` above, with its own table and its own
Admin-only write / capability-gated read.

**⚠️ Security note:** an earlier version of this function let *any* signed-in
user read the shared App Password, including a self-registered Viewer with no
other permissions. `supabase/functions/org-settings/index.ts` in this repo now
requires the `act.export` capability (Admin or Member role, or a custom caps
list that includes it) to read it — matching the desktop app's own gating. If
you deployed an org-settings function before this fix, **redeploy it** using
the steps below so the server actually enforces this, not just the app's UI.

## 5.1 Create the table (SQL editor, once)

For new projects, apply `supabase/migrations/20260818000000_org_scoped_settings.sql`
with the Supabase CLI. It creates `organization_settings`, scoped by `org_id`.
It intentionally leaves the old global `org_settings` data untouched rather
than copying a shared sender credential across organizations. Each organization
manager must save its own sender after the migration.

```sql
create table if not exists public.organization_settings (
  org_id      text not null references public.orgs(id) on delete cascade,
  key         text not null check (char_length(key) between 1 and 128),
  value       jsonb not null,
  updated_at  timestamptz not null default now(),
  updated_by  uuid references auth.users(id) on delete set null,
  primary key (org_id, key)
);
-- No RLS policies are added on purpose: this table is only ever touched by the
-- Edge Function using the service_role key, which bypasses RLS. Client code
-- never talks to this table directly.
alter table public.organization_settings enable row level security;
revoke all on table public.organization_settings from anon, authenticated;
```

## 5.2 Deploy the function

- **Dashboard:** Edge Functions → Deploy a new function → Via Editor → name it
  exactly **`org-settings`** → paste the contents of
  `supabase/functions/org-settings/index.ts` → turn off "Verify JWT" (same
  reasoning as `admin-users` in step 2 above) → Deploy.
- **CLI:** `supabase functions deploy org-settings --no-verify-jwt`

## 5.3 Use it

In QA Studio's Settings screen, an Organization Manager sets that organization's
sender address, name, and Gmail App Password once. Only users in that same
organization with export permission receive it on their next sign-in. Viewers
never receive it, from either the app or the server.

# 6. The `ai-usage` function (per-user usage, whole-org report for Admins)

Every AI call QA Studio makes is logged with its EXACT token usage (read
straight from the provider's own response, never estimated) to a local
per-user file on that machine, and — when Supabase sign-in is configured —
also mirrored to this table so it's visible from any machine that user signs
into, and so an **Admin** can pull a report across every signed-in user, not
just their own. Cost is deliberately **not** computed server-side: the
desktop app applies its own price table (`engine.PRICING`) to the exact token
counts, so a price change never needs a redeploy.

**Security model:** every signed-in user can read usage through this
function, but the SCOPE is decided server-side by their role — never by
anything the desktop app sends:
- A **Member or Viewer** gets rows filtered to their own verified `user_id`
  only — they can see their own AI Usage tab, but can't read anyone else's
  activity through this endpoint no matter what they send.
- An **Admin** gets rows across every user (a **hard role check**, not a
  capability toggle like `org-settings`' read gate — this data is materially
  more sensitive than a shared setting).

Writing (logging your own call) is open to any signed-in user, but the
function derives `user_id`/`user_email` from the caller's own verified token
— never from the request body — so nobody can log a call under someone
else's identity.

## 6.1 Create the table (SQL editor, once)

```sql
create table if not exists public.ai_usage_events (
  id            bigint generated always as identity primary key,
  user_id       uuid not null references auth.users(id) on delete cascade,
  user_email    text not null,
  created_at    timestamptz not null default now(),
  provider      text not null,
  model         text not null,
  input_tokens  integer not null default 0,
  output_tokens integer not null default 0,
  tag           text
);
create index if not exists ai_usage_events_created_at_idx on public.ai_usage_events (created_at);
create index if not exists ai_usage_events_user_idx on public.ai_usage_events (user_id, created_at);
-- No RLS policies, same reasoning as org_settings: only the Edge Function
-- (service_role) touches this table. Client code never talks to it directly.
alter table public.ai_usage_events enable row level security;
```

## 6.2 Deploy the function

- **Dashboard:** Edge Functions → Deploy a new function → Via Editor → name it
  exactly **`ai-usage`** → paste the contents of
  `supabase/functions/ai-usage/index.ts` → turn off "Verify JWT" (same
  reasoning as `admin-users`/`org-settings` above — the function verifies the
  caller's JWT itself) → Deploy.
- **CLI:** `supabase functions deploy ai-usage --no-verify-jwt`

## 6.3 Use it

Every signed-in user's AI calls are logged automatically in the background —
nothing to configure per user. Every signed-in user (Admin, Member, or
Viewer) sees an **AI Usage** tab, picks a date range, and generates a report
grouped by date/provider/model with an estimated cost, exportable as
JSON/Excel/Word/PDF or emailed directly from the app:
- A **Member or Viewer** sees only their own usage (no User column — every
  row is already theirs).
- An **Admin** sees the whole-org report — every signed-in user's usage,
  grouped by date/user/provider/model — same as before.

The scope is enforced server-side (not just a hidden button or a different
client query): a non-admin calling the endpoint directly still only ever gets
their own rows back, no matter what they send.
