-- Distinguish manually scoped projects from projects imported from a provider.
-- No provider credentials are stored here: discovery stays on the authorized
-- administrator's device and this registry retains only a provider identity
-- and stable external project key.
alter table public.organization_projects
  add column if not exists source_backend text not null default 'manual',
  add column if not exists provider_project_key text;

update public.organization_projects
set source_backend = 'manual'
where source_backend is null or source_backend = '';

alter table public.organization_projects
  drop constraint if exists organization_projects_source_backend_check;
alter table public.organization_projects
  add constraint organization_projects_source_backend_check
  check (source_backend in (
    'manual', 'azure', 'jira_zephyr', 'xray', 'testrail',
    'azure_testrail', 'jira_testrail'
  ));

create index if not exists organization_projects_org_source_idx
  on public.organization_projects (org_id, source_backend, is_active, name);
