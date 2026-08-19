-- Keep RLS-only SECURITY DEFINER helpers out of PostgREST's public API.
-- Existing policies retain their dependency on the same function OIDs when a
-- function changes schema. Authenticated users retain EXECUTE only because
-- PostgreSQL evaluates these functions while enforcing RLS.
create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated, service_role;

alter function public.current_org_id() set schema private;
alter function public.current_user_is_active() set schema private;
alter function public.current_user_has_project_access(uuid) set schema private;

create or replace function private.current_org_id()
returns text language sql stable security definer set search_path = public as $$
  select nullif(auth.jwt() -> 'app_metadata' ->> 'org_id', '');
$$;

create or replace function private.current_user_is_active()
returns boolean language sql stable security definer set search_path = public as $$
  select auth.uid() is not null and not exists (
    select 1 from public.user_lifecycle l
    where l.user_id = auth.uid()
      and (l.status <> 'active'
           or (l.access_expires_at is not null and l.access_expires_at <= now()))
  );
$$;

create or replace function private.current_user_has_project_access(p_project_id uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select p_project_id is null or exists (
    select 1
    from public.project_memberships pm
    join public.organization_projects p on p.id = pm.project_id
    where pm.project_id = p_project_id
      and pm.user_id = auth.uid()
      and p.org_id = private.current_org_id()
      and p.is_active
  );
$$;

revoke all on function private.current_org_id() from public, anon, authenticated;
revoke all on function private.current_user_is_active() from public, anon, authenticated;
revoke all on function private.current_user_has_project_access(uuid) from public, anon, authenticated;
grant execute on function private.current_org_id() to authenticated, service_role;
grant execute on function private.current_user_is_active() to authenticated, service_role;
grant execute on function private.current_user_has_project_access(uuid) to authenticated, service_role;

-- The trigger function remains server-only in public, but must call the
-- helpers at their new private-schema location.
create or replace function public.enforce_remote_run_scope()
returns trigger language plpgsql security definer set search_path = public as $$
declare v_org text := private.current_org_id();
begin
  if not private.current_user_is_active() then
    raise exception 'account access is inactive or expired';
  end if;
  if new.created_by is distinct from auth.uid()::text then
    raise exception 'remote run owner must match authenticated user';
  end if;
  if v_org is null then raise exception 'organization assignment is required'; end if;
  new.org_id := v_org;
  if new.project_id is not null and not private.current_user_has_project_access(new.project_id) then
    raise exception 'no membership for this project';
  end if;
  return new;
end;
$$;
