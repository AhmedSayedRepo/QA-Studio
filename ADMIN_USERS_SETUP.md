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
   set raw_app_meta_data = coalesce(raw_app_meta_data,'{}'::jsonb) || '{"role":"Admin"}'::jsonb;
   ```
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
