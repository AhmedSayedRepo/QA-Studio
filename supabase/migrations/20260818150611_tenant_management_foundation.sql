-- QA Studio tenant-management foundation.
--
-- This migration is deliberately additive: existing users, credentials, runs,
-- and usage rows are backfilled from the trusted auth.users app_metadata org
-- claim. New writes are constrained by the authenticated JWT as well as the
-- application Edge Functions, so a client cannot choose another tenant by
-- changing a request body field.

create table if not exists public.organization_profiles (
  org_id text primary key references public.orgs(id) on delete cascade,
  allowed_domains text[] not null default '{}',
  logo_url text,
  default_locale text not null default 'en',
  default_time_zone text not null default 'UTC',
  support_name text,
  support_email text,
  data_retention_days integer not null default 365
    check (data_retention_days between 30 and 3650),
  sso_provider text not null default 'none'
    check (sso_provider in ('none', 'saml', 'oidc')),
  sso_metadata_url text,
  scim_enabled boolean not null default false,
  enterprise_notes text,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id)
);

create table if not exists public.organization_projects (
  id uuid primary key default gen_random_uuid(),
  org_id text not null references public.orgs(id) on delete cascade,
  external_key text not null,
  name text not null,
  description text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, external_key)
);

create table if not exists public.organization_teams (
  id uuid primary key default gen_random_uuid(),
  org_id text not null references public.orgs(id) on delete cascade,
  name text not null,
  description text,
  created_at timestamptz not null default now(),
  unique (org_id, name)
);

create table if not exists public.team_memberships (
  team_id uuid not null references public.organization_teams(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'member' check (role in ('lead', 'member')),
  created_at timestamptz not null default now(),
  primary key (team_id, user_id)
);

create table if not exists public.project_memberships (
  project_id uuid not null references public.organization_projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  access_level text not null default 'contributor'
    check (access_level in ('viewer', 'contributor', 'manager')),
  created_at timestamptz not null default now(),
  primary key (project_id, user_id)
);

create table if not exists public.user_lifecycle (
  user_id uuid primary key references auth.users(id) on delete cascade,
  org_id text references public.orgs(id) on delete set null,
  status text not null default 'active'
    check (status in ('active', 'suspended', 'expired')),
  access_expires_at timestamptz,
  suspended_at timestamptz,
  suspended_by uuid references auth.users(id),
  suspension_reason text,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id)
);

create table if not exists public.admin_audit_events (
  id bigint generated always as identity primary key,
  org_id text references public.orgs(id) on delete set null,
  actor_id uuid references auth.users(id) on delete set null,
  target_user_id uuid references auth.users(id) on delete set null,
  action text not null,
  entity_type text not null,
  entity_id text,
  before_value jsonb not null default '{}'::jsonb,
  after_value jsonb not null default '{}'::jsonb,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists admin_audit_events_org_created_idx
  on public.admin_audit_events (org_id, created_at desc);
create index if not exists admin_audit_events_target_created_idx
  on public.admin_audit_events (target_user_id, created_at desc);
create index if not exists organization_projects_org_idx
  on public.organization_projects (org_id, is_active, name);
create index if not exists project_memberships_user_idx
  on public.project_memberships (user_id, project_id);
create index if not exists user_lifecycle_org_status_idx
  on public.user_lifecycle (org_id, status, access_expires_at);

-- Backfill only where the authoritative server-side auth record carries an
-- organization. Rows with a historic no-org user remain inaccessible until an
-- administrator assigns that account to an organization.
insert into public.user_lifecycle (user_id, org_id)
select u.id, nullif(u.raw_app_meta_data ->> 'org_id', '')
from auth.users u
where nullif(u.raw_app_meta_data ->> 'org_id', '') is not null
on conflict (user_id) do update set org_id = excluded.org_id;

alter table public.ai_usage_events add column if not exists org_id text references public.orgs(id) on delete set null;
alter table public.ai_usage_events add column if not exists project_id uuid references public.organization_projects(id) on delete set null;
alter table public.remote_runs add column if not exists org_id text references public.orgs(id) on delete set null;
alter table public.remote_runs add column if not exists project_id uuid references public.organization_projects(id) on delete set null;
alter table public.user_credentials add column if not exists org_id text references public.orgs(id) on delete set null;

update public.ai_usage_events e set org_id = nullif(u.raw_app_meta_data ->> 'org_id', '')
from auth.users u where e.user_id = u.id and e.org_id is null;
update public.remote_runs r set org_id = nullif(u.raw_app_meta_data ->> 'org_id', '')
from auth.users u where r.created_by = u.id::text and r.org_id is null;
update public.user_credentials c set org_id = nullif(u.raw_app_meta_data ->> 'org_id', '')
from auth.users u where c.user_id = u.id and c.org_id is null;

create index if not exists ai_usage_events_org_created_idx
  on public.ai_usage_events (org_id, created_at desc);
create index if not exists ai_usage_events_project_created_idx
  on public.ai_usage_events (project_id, created_at desc);
create index if not exists remote_runs_org_created_idx
  on public.remote_runs (org_id, created_at desc);
create index if not exists remote_runs_project_created_idx
  on public.remote_runs (project_id, created_at desc);

-- Project-specific secrets are separated from the original per-user defaults.
-- A project record may use the user's existing default credentials until an
-- explicit project credential record is configured.
create table if not exists public.project_user_credentials (
  project_id uuid not null references public.organization_projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  org_id text not null references public.orgs(id) on delete cascade,
  azure_org text not null default '',
  ai_provider text not null default 'anthropic',
  ai_model text not null default '',
  azure_pat_secret_id uuid,
  ai_key_secret_id uuid,
  gmail_sender text,
  gmail_sender_name text,
  gmail_app_pass_secret_id uuid,
  updated_at timestamptz not null default now(),
  last_used_at timestamptz,
  primary key (project_id, user_id)
);

-- The worker prefers explicitly configured project credentials, then falls
-- back to the user's existing personal default. The project id comes from the
-- server-validated remote_runs row, never from the worker environment.
create or replace function public.worker_get_credentials(p_user_id uuid, p_project_id uuid)
returns table(azure_org text, azure_pat text, ai_provider text, ai_api_key text,
              ai_model text, gmail_sender text, gmail_sender_name text, gmail_app_pass text)
language plpgsql security definer set search_path = public, vault as $$
begin
  update public.user_credentials set last_used_at = now() where user_id = p_user_id;
  update public.project_user_credentials set last_used_at = now()
    where user_id = p_user_id and project_id = p_project_id;
  return query
  select coalesce(pc.azure_org, c.azure_org, ''),
         coalesce((select ds.decrypted_secret from vault.decrypted_secrets ds where ds.id = pc.azure_pat_secret_id),
                  (select ds.decrypted_secret from vault.decrypted_secrets ds where ds.id = c.azure_pat_secret_id), ''),
         coalesce(pc.ai_provider, c.ai_provider, 'anthropic'),
         coalesce((select ds.decrypted_secret from vault.decrypted_secrets ds where ds.id = pc.ai_key_secret_id),
                  (select ds.decrypted_secret from vault.decrypted_secrets ds where ds.id = c.ai_key_secret_id), ''),
         coalesce(pc.ai_model, c.ai_model, ''),
         coalesce(pc.gmail_sender, c.gmail_sender, ''),
         coalesce(pc.gmail_sender_name, c.gmail_sender_name, ''),
         coalesce((select ds.decrypted_secret from vault.decrypted_secrets ds where ds.id = pc.gmail_app_pass_secret_id),
                  (select ds.decrypted_secret from vault.decrypted_secrets ds where ds.id = c.gmail_app_pass_secret_id), '')
    from public.user_credentials c
    left join public.project_user_credentials pc
      on pc.user_id = c.user_id and pc.project_id = p_project_id
   where c.user_id = p_user_id;
end;
$$;
revoke all on function public.worker_get_credentials(uuid, uuid) from public;
grant execute on function public.worker_get_credentials(uuid, uuid) to service_role;

-- Helpers are SECURITY DEFINER and granted only to the service role (except
-- the two predicates that RLS calls under the authenticated user context).
create or replace function public.current_org_id()
returns text language sql stable security definer set search_path = public as $$
  select nullif(auth.jwt() -> 'app_metadata' ->> 'org_id', '');
$$;

create or replace function public.current_user_is_active()
returns boolean language sql stable security definer set search_path = public as $$
  select auth.uid() is not null and not exists (
    select 1 from public.user_lifecycle l
    where l.user_id = auth.uid()
      and (l.status <> 'active'
           or (l.access_expires_at is not null and l.access_expires_at <= now()))
  );
$$;

create or replace function public.current_user_has_project_access(p_project_id uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select p_project_id is null or exists (
    select 1
    from public.project_memberships pm
    join public.organization_projects p on p.id = pm.project_id
    where pm.project_id = p_project_id
      and pm.user_id = auth.uid()
      and p.org_id = public.current_org_id()
      and p.is_active
  );
$$;

create or replace function public.record_admin_audit(
  p_org_id text, p_actor_id uuid, p_target_user_id uuid, p_action text,
  p_entity_type text, p_entity_id text, p_before jsonb default '{}'::jsonb,
  p_after jsonb default '{}'::jsonb, p_details jsonb default '{}'::jsonb
) returns void language plpgsql security definer set search_path = public as $$
begin
  insert into public.admin_audit_events
    (org_id, actor_id, target_user_id, action, entity_type, entity_id, before_value, after_value, details)
  values
    (nullif(trim(p_org_id), ''), p_actor_id, p_target_user_id, left(trim(p_action), 80),
     left(trim(p_entity_type), 80), nullif(left(trim(p_entity_id), 200), ''),
     coalesce(p_before, '{}'::jsonb), coalesce(p_after, '{}'::jsonb), coalesce(p_details, '{}'::jsonb));
end;
$$;

-- Detect expiry even when no scheduled job has run; the schedule below merely
-- records an audit-friendly status transition ahead of the next request.
create or replace function public.expire_due_user_access()
returns integer language plpgsql security definer set search_path = public as $$
declare v_count integer;
begin
  update public.user_lifecycle
     set status = 'expired', updated_at = now()
   where status = 'active' and access_expires_at is not null and access_expires_at <= now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

-- Enforce the verified caller's org and project membership for direct REST
-- remote-run writes. This prevents a forged org_id/project_id in a desktop
-- request from crossing tenant boundaries.
create or replace function public.enforce_remote_run_scope()
returns trigger language plpgsql security definer set search_path = public as $$
declare v_org text := public.current_org_id();
begin
  if not public.current_user_is_active() then
    raise exception 'account access is inactive or expired';
  end if;
  if new.created_by is distinct from auth.uid()::text then
    raise exception 'remote run owner must match authenticated user';
  end if;
  if v_org is null then raise exception 'organization assignment is required'; end if;
  new.org_id := v_org;
  if new.project_id is not null and not public.current_user_has_project_access(new.project_id) then
    raise exception 'no membership for this project';
  end if;
  return new;
end;
$$;

drop trigger if exists remote_runs_enforce_scope on public.remote_runs;
create trigger remote_runs_enforce_scope before insert on public.remote_runs
for each row execute function public.enforce_remote_run_scope();

drop policy if exists remote_runs_insert on public.remote_runs;
drop policy if exists remote_runs_select on public.remote_runs;
drop policy if exists remote_runs_update_control on public.remote_runs;
create policy remote_runs_insert on public.remote_runs for insert to authenticated
  with check (created_by = auth.uid()::text and org_id = public.current_org_id()
              and public.current_user_is_active()
              and public.current_user_has_project_access(project_id));
create policy remote_runs_select on public.remote_runs for select to authenticated
  using (org_id = public.current_org_id() and public.current_user_is_active()
         and (created_by = auth.uid()::text or public.current_user_has_project_access(project_id)));
create policy remote_runs_update_control on public.remote_runs for update to authenticated
  using (created_by = auth.uid()::text and org_id = public.current_org_id() and public.current_user_is_active())
  with check (created_by = auth.uid()::text and org_id = public.current_org_id());

drop policy if exists remote_run_events_select on public.remote_run_events;
create policy remote_run_events_select on public.remote_run_events for select to authenticated
  using (exists (
    select 1 from public.remote_runs r where r.id = remote_run_events.run_id
      and r.org_id = public.current_org_id() and public.current_user_is_active()
      and (r.created_by = auth.uid()::text or public.current_user_has_project_access(r.project_id))
  ));

drop policy if exists ai_usage_org_scoped_read on public.ai_usage_events;
create policy ai_usage_org_scoped_read on public.ai_usage_events for select to authenticated
  using (org_id = public.current_org_id() and public.current_user_is_active()
         and user_id = auth.uid());

alter table public.organization_profiles enable row level security;
alter table public.organization_projects enable row level security;
alter table public.organization_teams enable row level security;
alter table public.team_memberships enable row level security;
alter table public.project_memberships enable row level security;
alter table public.project_user_credentials enable row level security;
alter table public.user_lifecycle enable row level security;
alter table public.admin_audit_events enable row level security;

revoke all on table public.organization_profiles, public.organization_projects,
  public.organization_teams, public.team_memberships, public.project_memberships,
  public.project_user_credentials, public.user_lifecycle, public.admin_audit_events
  from anon, authenticated;
grant all on table public.organization_profiles, public.organization_projects,
  public.organization_teams, public.team_memberships, public.project_memberships,
  public.project_user_credentials, public.user_lifecycle, public.admin_audit_events to service_role;
revoke all on function public.record_admin_audit(text, uuid, uuid, text, text, text, jsonb, jsonb, jsonb) from public;
revoke all on function public.expire_due_user_access() from public;
grant execute on function public.record_admin_audit(text, uuid, uuid, text, text, text, jsonb, jsonb, jsonb) to service_role;
grant execute on function public.expire_due_user_access() to service_role;
grant execute on function public.current_org_id(), public.current_user_is_active(),
  public.current_user_has_project_access(uuid) to authenticated, service_role;

-- Supabase Cron is optional at migration time. If the extension is enabled,
-- transition expired accounts every five minutes; runtime authorization still
-- fails closed immediately even if no cron job is configured.
do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    execute 'select cron.unschedule(jobid) from cron.job where jobname = ''qa-studio-expire-user-access''';
    execute $cron$select cron.schedule('qa-studio-expire-user-access', '*/5 * * * *',
      'select public.expire_due_user_access();')$cron$;
  end if;
exception when undefined_table or undefined_function or insufficient_privilege then
  null;
end;
$$;
