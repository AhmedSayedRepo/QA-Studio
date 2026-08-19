-- Security Advisor hardening
--
-- SECURITY DEFINER functions receive EXECUTE from PUBLIC when they are
-- created unless that default is explicitly revoked. Keep user-facing helper
-- functions available only to authenticated sessions because RLS policies
-- invoke them. All credential, audit and lifecycle maintenance functions are
-- server-only and must be called through a service-role Edge Function.

-- RLS predicates: authenticated callers require EXECUTE while evaluating the
-- policies on remote runs, events and AI usage. They never accept an arbitrary
-- user or organization id; identity comes only from the verified JWT.
revoke all on function public.current_org_id() from public, anon, authenticated;
revoke all on function public.current_user_is_active() from public, anon, authenticated;
revoke all on function public.current_user_has_project_access(uuid) from public, anon, authenticated;
grant execute on function public.current_org_id() to authenticated, service_role;
grant execute on function public.current_user_is_active() to authenticated, service_role;
grant execute on function public.current_user_has_project_access(uuid) to authenticated, service_role;

-- Server-only helpers. The desktop client must use the authorized Edge
-- Functions; it can never call these database functions directly.
revoke all on function public.worker_get_credentials(uuid) from public, anon, authenticated;
revoke all on function public.worker_get_credentials(uuid, uuid) from public, anon, authenticated;
revoke all on function public.record_admin_audit(text, uuid, uuid, text, text, text, jsonb, jsonb, jsonb)
  from public, anon, authenticated;
revoke all on function public.expire_due_user_access() from public, anon, authenticated;
revoke all on function public.enforce_remote_run_scope() from public, anon, authenticated;

grant execute on function public.worker_get_credentials(uuid) to service_role;
grant execute on function public.worker_get_credentials(uuid, uuid) to service_role;
grant execute on function public.record_admin_audit(text, uuid, uuid, text, text, text, jsonb, jsonb, jsonb)
  to service_role;
grant execute on function public.expire_due_user_access() to service_role;

-- Credential self-service RPCs are deliberately available to authenticated
-- users only. Reassert this boundary so future migrations cannot reopen them
-- to anonymous callers.
revoke all on function public.get_my_credentials_status() from public, anon;
revoke all on function public.set_my_credentials(text, text, text, text, text, text, text, text)
  from public, anon;
grant execute on function public.get_my_credentials_status() to authenticated, service_role;
grant execute on function public.set_my_credentials(text, text, text, text, text, text, text, text)
  to authenticated, service_role;
