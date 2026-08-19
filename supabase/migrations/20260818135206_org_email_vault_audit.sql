-- Protect organization SMTP credentials with Supabase Vault and retain a
-- server-only audit history. The desktop client always talks to these helpers
-- through the authenticated org-settings Edge Function, which uses service_role.

create table if not exists public.organization_settings_audit (
  id         bigint generated always as identity primary key,
  org_id     text not null references public.orgs(id) on delete cascade,
  actor_id   uuid,
  event      text not null check (char_length(event) between 1 and 80),
  details    jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

comment on table public.organization_settings_audit is
  'Server-only audit history for organization settings. Never contains SMTP passwords.';

alter table public.organization_settings_audit enable row level security;
revoke all on table public.organization_settings_audit from anon, authenticated;
create index if not exists organization_settings_audit_org_created_idx
  on public.organization_settings_audit (org_id, created_at desc);

-- Convert the legacy JSON app_password value into an encrypted Vault secret.
-- The migration is intentionally idempotent for interrupted/retried deploys:
-- an existing secret id is retained and a plaintext key is simply removed.
do $$
declare
  r record;
  v_secret_id uuid;
  v_password text;
begin
  for r in
    select org_id, value
    from public.organization_settings
    where key = 'email'
    for update
  loop
    v_password := nullif(btrim(coalesce(r.value ->> 'app_password', '')), '');
    v_secret_id := nullif(r.value ->> 'app_password_secret_id', '')::uuid;

    if v_secret_id is null and v_password is not null then
      v_secret_id := vault.create_secret(
        v_password,
        'qa_studio_org_email:' || r.org_id,
        'QA Studio organization Gmail App Password'
      );
    end if;

    update public.organization_settings
    set value = (r.value - 'app_password') || case
      when v_secret_id is null then '{}'::jsonb
      else jsonb_build_object('app_password_secret_id', v_secret_id::text)
    end,
        updated_at = now()
    where org_id = r.org_id and key = 'email';
  end loop;
end $$;

-- These helpers intentionally have no authenticated/anon grants. The Edge
-- Function is the authorization boundary and calls them as service_role only.
create or replace function public.get_org_email_settings(p_org_id text)
returns table (
  sender text,
  sender_name text,
  app_password text,
  inherited_from_org_id text
)
language plpgsql
security definer
set search_path = public, vault, pg_temp
as $$
declare
  v_value jsonb := '{}'::jsonb;
  v_secret_id uuid;
  v_source_org_id text;
begin
  select value into v_value
  from public.organization_settings
  where org_id = p_org_id and key = 'email';

  v_source_org_id := nullif(v_value ->> 'inherit_from_org_id', '');
  if v_source_org_id is not null and v_source_org_id <> p_org_id then
    select value into v_value
    from public.organization_settings
    where org_id = v_source_org_id and key = 'email';
  else
    v_source_org_id := null;
  end if;

  sender := coalesce(v_value ->> 'sender', '');
  sender_name := coalesce(v_value ->> 'sender_name', '');
  v_secret_id := nullif(v_value ->> 'app_password_secret_id', '')::uuid;
  if v_secret_id is not null then
    select decrypted_secret into app_password
    from vault.decrypted_secrets
    where id = v_secret_id;
  else
    app_password := '';
  end if;
  inherited_from_org_id := v_source_org_id;
  return next;
end;
$$;

create or replace function public.set_org_email_settings(
  p_org_id text,
  p_sender text,
  p_sender_name text,
  p_app_password text,
  p_actor_id uuid
)
returns void
language plpgsql
security definer
set search_path = public, vault, pg_temp
as $$
declare
  v_value jsonb := '{}'::jsonb;
  v_secret_id uuid;
  v_new_value jsonb;
begin
  if nullif(btrim(p_org_id), '') is null then
    raise exception 'Organization id is required';
  end if;

  select value into v_value
  from public.organization_settings
  where org_id = p_org_id and key = 'email'
  for update;
  v_secret_id := nullif(v_value ->> 'app_password_secret_id', '')::uuid;

  -- An omitted or empty password preserves the existing Vault secret. This
  -- prevents an address/name edit from accidentally disabling report email.
  if nullif(btrim(coalesce(p_app_password, '')), '') is not null then
    if v_secret_id is null then
      v_secret_id := vault.create_secret(
        btrim(p_app_password),
        'qa_studio_org_email:' || p_org_id,
        'QA Studio organization Gmail App Password'
      );
    else
      perform vault.update_secret(v_secret_id, btrim(p_app_password));
    end if;
  end if;

  v_new_value := jsonb_build_object(
    'sender', btrim(coalesce(p_sender, '')),
    'sender_name', btrim(coalesce(p_sender_name, ''))
  ) || case
    when v_secret_id is null then '{}'::jsonb
    else jsonb_build_object('app_password_secret_id', v_secret_id::text)
  end;

  insert into public.organization_settings (org_id, key, value, updated_at, updated_by)
  values (p_org_id, 'email', v_new_value, now(), p_actor_id)
  on conflict (org_id, key) do update
  set value = excluded.value,
      updated_at = excluded.updated_at,
      updated_by = excluded.updated_by;

  insert into public.organization_settings_audit (org_id, actor_id, event, details)
  values (p_org_id, p_actor_id, 'email_settings_saved', jsonb_build_object(
    'sender', btrim(coalesce(p_sender, '')),
    'has_app_password', v_secret_id is not null
  ));
end;
$$;

create or replace function public.inherit_org_email_settings(
  p_org_id text,
  p_source_org_id text,
  p_actor_id uuid
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if nullif(btrim(p_org_id), '') is null or nullif(btrim(p_source_org_id), '') is null then
    raise exception 'Both organization ids are required';
  end if;
  if p_org_id = p_source_org_id then
    raise exception 'An organization cannot inherit its own sender';
  end if;
  if not exists (select 1 from public.orgs where id = p_source_org_id) then
    raise exception 'Source organization not found';
  end if;

  insert into public.organization_settings (org_id, key, value, updated_at, updated_by)
  values (p_org_id, 'email', jsonb_build_object('inherit_from_org_id', p_source_org_id), now(), p_actor_id)
  on conflict (org_id, key) do update
  set value = excluded.value,
      updated_at = excluded.updated_at,
      updated_by = excluded.updated_by;

  insert into public.organization_settings_audit (org_id, actor_id, event, details)
  values (p_org_id, p_actor_id, 'email_sender_inherited',
          jsonb_build_object('source_org_id', p_source_org_id));
end;
$$;

create or replace function public.log_org_email_test(
  p_org_id text,
  p_actor_id uuid,
  p_success boolean,
  p_error text default null
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  insert into public.organization_settings_audit (org_id, actor_id, event, details)
  values (p_org_id, p_actor_id,
          case when p_success then 'email_test_succeeded' else 'email_test_failed' end,
          case when p_success then '{}'::jsonb
               else jsonb_build_object('error', left(coalesce(p_error, 'Unknown error'), 500)) end);
end;
$$;

create or replace function public.get_org_email_audit(p_org_id text, p_limit integer default 5)
returns table (created_at timestamptz, actor_id uuid, event text, details jsonb)
language sql
security definer
set search_path = public, pg_temp
as $$
  select a.created_at, a.actor_id, a.event, a.details
  from public.organization_settings_audit a
  where a.org_id = p_org_id
  order by a.created_at desc
  limit greatest(1, least(coalesce(p_limit, 5), 20));
$$;

revoke all on function public.get_org_email_settings(text) from public, anon, authenticated;
revoke all on function public.set_org_email_settings(text, text, text, text, uuid) from public, anon, authenticated;
revoke all on function public.inherit_org_email_settings(text, text, uuid) from public, anon, authenticated;
revoke all on function public.log_org_email_test(text, uuid, boolean, text) from public, anon, authenticated;
revoke all on function public.get_org_email_audit(text, integer) from public, anon, authenticated;
grant execute on function public.get_org_email_settings(text) to service_role;
grant execute on function public.set_org_email_settings(text, text, text, text, uuid) to service_role;
grant execute on function public.inherit_org_email_settings(text, text, uuid) to service_role;
grant execute on function public.log_org_email_test(text, uuid, boolean, text) to service_role;
grant execute on function public.get_org_email_audit(text, integer) to service_role;
