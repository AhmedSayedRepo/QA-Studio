# Tenant administration deployment

## What this release enforces

- Every new remote run is stamped with the caller's verified organization. Its
  `project_id` must be an active project membership; the database rejects a
  forged cross-tenant or cross-project REST request.
- AI usage is written and read through the caller's verified organization
  claim. Organization managers see their organization only; members/viewers
  see their own activity only.
- Lifecycle state (`active`, `suspended`, `expired`) is checked by the tenant
  API and remote-run RLS. Suspending also globally signs the user out.
- Organization profiles, projects, teams, memberships and all administrative
  mutations have a server-side audit record. Secret values are not written to
  audit data.

## Deploy in order

Run these from `D:\qa-studio` after reviewing the migration:

```powershell
supabase db push
deno check .\supabase\functions\tenant-admin\index.ts
deno check .\supabase\functions\admin-users\index.ts
deno check .\supabase\functions\ai-usage\index.ts
supabase functions deploy tenant-admin --no-verify-jwt
supabase functions deploy admin-users --no-verify-jwt
supabase functions deploy ai-usage --no-verify-jwt
& C:\Users\pc\AppData\Local\Programs\Python\Python312\python.exe .\_sync_to_install.py
```

Start the installed app, open **Organizations**, select each organization,
save its identity/policy, add its Azure project keys, and assign users to the
project through the tenant administration API/UI rollout. A remote run for an
organization with tenant administration available will refuse an unmapped
project rather than run outside the project model.

## Access expiry scheduling

The migration schedules `qa-studio-expire-user-access` every five minutes if
Supabase Cron (`pg_cron`) is enabled. If it is not enabled, access still fails
closed immediately through RLS and Edge Functions at `access_expires_at`; enable
Cron in Supabase Dashboard → Integrations → Cron to record automatic status
transitions as well.

## SSO / SAML and SCIM

The organization profile persists non-secret configuration intent (provider
type, metadata URL and whether SCIM is planned). It intentionally does **not**
implement a fake IdP or store a SCIM bearer token in the desktop app.

To activate SSO, first configure a real SAML or OIDC provider in Supabase Auth
and provide its issuer, metadata and callback URLs. To activate SCIM, use an
enterprise provider/plan that supports it, store the provisioning token in
Supabase secrets, and point the IdP at a protected server endpoint. This keeps
the tenant isolation and lifecycle checks above in the authorization path rather
than bypassing them through external provisioning.
