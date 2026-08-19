-- Organization-scoped shared settings.
--
-- The old public.org_settings table is deliberately left untouched.  Its rows
-- can contain a shared sender credential, and copying that credential into
-- every organization would break tenant isolation.  After deploying the
-- org-settings Edge Function each organization must save its own settings.

create table if not exists public.organization_settings (
  org_id     text not null references public.orgs(id) on delete cascade,
  key        text not null check (char_length(key) between 1 and 128),
  value      jsonb not null,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id) on delete set null,
  primary key (org_id, key)
);

comment on table public.organization_settings is
  'Organization-scoped settings. Only the service-role org-settings Edge Function may access it.';

alter table public.organization_settings enable row level security;

-- No RLS policies are intentionally created. The service role used by the Edge
-- Function bypasses RLS; anon and authenticated clients have no direct access.
revoke all on table public.organization_settings from anon, authenticated;

