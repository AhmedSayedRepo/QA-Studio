-- Automatic organization sender-sync audit. This follows the already-applied
-- Vault/audit migration; do not fold it into that historical migration.

create or replace function public.log_org_email_auto_sync(
  p_org_id text,
  p_actor_id uuid,
  p_source_org_id text default null
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  -- A sender is refreshed during sign-in and just before report sends. Retain
  -- useful evidence without turning normal application traffic into audit spam.
  if exists (
    select 1
    from public.organization_settings_audit
    where org_id = p_org_id
      and actor_id is not distinct from p_actor_id
      and event = 'email_auto_synced'
      and created_at > now() - interval '1 hour'
  ) then
    return;
  end if;

  insert into public.organization_settings_audit (org_id, actor_id, event, details)
  values (p_org_id, p_actor_id, 'email_auto_synced',
          case when nullif(btrim(coalesce(p_source_org_id, '')), '') is null
               then '{}'::jsonb
               else jsonb_build_object('source_org_id', p_source_org_id) end);
end;
$$;

revoke all on function public.log_org_email_auto_sync(text, uuid, text)
  from public, anon, authenticated;
grant execute on function public.log_org_email_auto_sync(text, uuid, text)
  to service_role;
