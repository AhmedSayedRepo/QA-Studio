-- Private identity assets. Clients never receive Storage write permission:
-- the authenticated tenant-admin Edge Function validates and stores images
-- using the service role, then returns short-lived signed read URLs.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('qa-studio-images', 'qa-studio-images', false, 2097152,
        array['image/jpeg', 'image/png', 'image/webp']::text[])
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

-- ``storage.objects`` is already RLS-protected and owned by Supabase's
-- internal storage role, so application migrations must not alter that table.
-- No client-facing policies are added: upload, replacement and signed reads
-- are authorized by the tenant-admin Edge Function using the service role.
