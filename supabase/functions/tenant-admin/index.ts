// tenant-admin — authenticated, server-side organization administration.
// The desktop client never decides its effective organization: that value is
// derived from the verified Supabase user token below on every request.
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, content-type, apikey", "Access-Control-Allow-Methods": "GET, POST, OPTIONS" };
const SUPER = new Set(["SuperAdmin", "Admin"]);
const ADMINS = new Set(["SuperAdmin", "Admin", "OrgManager"]);
const PROJECT_SOURCES = new Set(["manual", "azure", "jira_zephyr", "xray", "testrail", "azure_testrail", "jira_testrail"]);
const ASSET_BUCKET = "qa-studio-images";
const ASSET_TYPES: Record<string, string> = {
  "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
};
const PROJECT_SOURCE_CAPS: Record<string, string> = {
  azure: "act.import_azure_projects",
  jira_zephyr: "act.import_jira_zephyr_projects",
  xray: "act.import_xray_projects",
  testrail: "act.import_testrail_projects",
  azure_testrail: "act.import_azure_testrail_projects",
  jira_testrail: "act.import_jira_testrail_projects",
};
type User = { id: string; email?: string; app_metadata?: Record<string, unknown>; user_metadata?: Record<string, unknown> };
type Body = Record<string, unknown>;
const out = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
const roleOf = (u: User) => String(u.app_metadata?.role ?? "Viewer");
const orgOf = (u: User) => String(u.app_metadata?.org_id ?? "").trim();
const capsOf = (u: User): string[] | null => Array.isArray(u.app_metadata?.caps)
  ? u.app_metadata!.caps.filter((cap): cap is string => typeof cap === "string") : null;
const canImportProjectSource = (u: User, source: string) => {
  if (source === "manual") return true;
  const caps = capsOf(u);
  // Accounts without custom capabilities follow their role preset.  A custom
  // capability list is intentionally restrictive and must opt in explicitly.
  // The former coarse key remains a safe compatibility path for accounts that
  // have not yet been saved with the provider-specific permission model.
  return caps === null ? ADMINS.has(roleOf(u))
    : caps.includes("act.manage_project_sources") || caps.includes(PROJECT_SOURCE_CAPS[source] ?? "");
};
const str = (v: unknown, max = 500) => typeof v === "string" ? v.trim().slice(0, max) : "";
const obj = (v: unknown): Record<string, unknown> => v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};

type ImageDimensions = { width: number; height: number };
const u16be = (b: Uint8Array, o: number) => o + 2 <= b.length ? (b[o] << 8) | b[o + 1] : 0;
const u16le = (b: Uint8Array, o: number) => o + 2 <= b.length ? b[o] | (b[o + 1] << 8) : 0;
const u24le = (b: Uint8Array, o: number) => o + 3 <= b.length ? b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) : 0;
const u32be = (b: Uint8Array, o: number) => o + 4 <= b.length
  ? ((b[o] * 0x1000000) + (b[o + 1] << 16) + (b[o + 2] << 8) + b[o + 3]) : 0;
const u32le = (b: Uint8Array, o: number) => o + 4 <= b.length
  ? (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] * 0x1000000)) : 0;
const ascii = (b: Uint8Array, o: number, n: number) => String.fromCharCode(...b.slice(o, o + n));

function pngDimensions(bytes: Uint8Array): ImageDimensions | null {
  const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (bytes.length < 45 || signature.some((value, index) => bytes[index] !== value)) return null;
  let offset = 8, hasHeader = false, hasPixels = false, ended = false, dimensions: ImageDimensions | null = null;
  while (offset + 12 <= bytes.length) {
    const length = u32be(bytes, offset), type = ascii(bytes, offset + 4, 4), start = offset + 8, end = start + length;
    if (end + 4 > bytes.length || !/^[A-Za-z]{4}$/.test(type)) return null;
    if (!hasHeader) {
      if (type !== "IHDR" || length !== 13) return null;
      dimensions = { width: u32be(bytes, start), height: u32be(bytes, start + 4) };
      const bitDepth = bytes[start + 8], colorType = bytes[start + 9];
      if (![1, 2, 4, 8, 16].includes(bitDepth) || ![0, 2, 3, 4, 6].includes(colorType)
          || bytes[start + 10] !== 0 || bytes[start + 11] !== 0 || bytes[start + 12] > 1) return null;
      hasHeader = true;
    } else if (type === "IDAT") {
      hasPixels = true;
    } else if (type === "IEND") {
      if (length !== 0 || !hasPixels || end + 4 !== bytes.length) return null;
      ended = true;
      break;
    }
    offset = end + 4;
  }
  return ended ? dimensions : null;
}

function jpegDimensions(bytes: Uint8Array): ImageDimensions | null {
  if (bytes.length < 16 || bytes[0] !== 0xff || bytes[1] !== 0xd8
      || bytes[bytes.length - 2] !== 0xff || bytes[bytes.length - 1] !== 0xd9) return null;
  let offset = 2, dimensions: ImageDimensions | null = null;
  while (offset + 1 < bytes.length - 2) {
    if (bytes[offset] !== 0xff) return dimensions; // entropy-coded scan; final EOI was checked above.
    while (offset < bytes.length && bytes[offset] === 0xff) offset += 1;
    const marker = bytes[offset++];
    if (marker === 0xd9) return dimensions;
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    const segment = u16be(bytes, offset);
    if (segment < 2 || offset + segment > bytes.length) return null;
    if ((marker >= 0xc0 && marker <= 0xc3) || (marker >= 0xc5 && marker <= 0xc7)
        || (marker >= 0xc9 && marker <= 0xcb) || (marker >= 0xcd && marker <= 0xcf)) {
      if (segment < 8) return null;
      dimensions = { width: u16be(bytes, offset + 5), height: u16be(bytes, offset + 3) };
    }
    if (marker === 0xda) return dimensions; // start of scan; only entropy bytes remain until EOI.
    offset += segment;
  }
  return dimensions;
}

function webpDimensions(bytes: Uint8Array): ImageDimensions | null {
  if (bytes.length < 30 || ascii(bytes, 0, 4) !== "RIFF" || ascii(bytes, 8, 4) !== "WEBP"
      || u32le(bytes, 4) + 8 !== bytes.length) return null;
  let offset = 12, imageChunks = 0, dimensions: ImageDimensions | null = null;
  const allowed = new Set(["VP8X", "ICCP", "ALPH", "VP8 ", "VP8L"]);
  while (offset + 8 <= bytes.length) {
    const type = ascii(bytes, offset, 4), length = u32le(bytes, offset + 4), start = offset + 8, end = start + length;
    if (!allowed.has(type) || end > bytes.length) return null;
    if (type === "VP8X") {
      if (length !== 10 || dimensions) return null;
      dimensions = { width: u24le(bytes, start + 4) + 1, height: u24le(bytes, start + 7) + 1 };
    } else if (type === "VP8 " && length >= 10) {
      if (imageChunks++ || bytes[start + 3] !== 0x9d || bytes[start + 4] !== 0x01 || bytes[start + 5] !== 0x2a) return null;
      // VP8 frame dimensions are little-endian 14-bit values. Pillow emits this
      // form for the normalized WebP uploaded by the desktop client.
      dimensions = dimensions ?? { width: u16le(bytes, start + 6) & 0x3fff, height: u16le(bytes, start + 8) & 0x3fff };
    } else if (type === "VP8L" && length >= 5) {
      if (imageChunks++ || bytes[start] !== 0x2f) return null;
      dimensions = dimensions ?? { width: 1 + bytes[start + 1] + ((bytes[start + 2] & 0x3f) << 8), height: 1 + (bytes[start + 2] >> 6) + (bytes[start + 3] << 2) + ((bytes[start + 4] & 0x0f) << 10) };
    } else if (type !== "ICCP" && type !== "ALPH") return null;
    offset = end + (length % 2);
  }
  return offset === bytes.length && imageChunks === 1 ? dimensions : null;
}

function imageDimensions(bytes: Uint8Array, mime: string): ImageDimensions | null {
  if (mime === "image/png") return pngDimensions(bytes);
  if (mime === "image/jpeg") return jpegDimensions(bytes);
  if (mime === "image/webp") return webpDimensions(bytes);
  return null;
}

function imagePayload(body: Body): { bytes: Uint8Array; mime: string; extension: string } | null {
  const mime = str(body.mime_type, 60).toLowerCase();
  const extension = str(body.extension, 8).toLowerCase();
  if (!ASSET_TYPES[mime] || ASSET_TYPES[mime] !== extension) return null;
  const encoded = str(body.image_base64, 3_000_000).replace(/^data:[^;]+;base64,/i, "");
  if (!encoded || encoded.length > 2_800_000) return null;
  try {
    const raw = atob(encoded);
    if (raw.length === 0 || raw.length > 2 * 1024 * 1024) return null;
    const bytes = Uint8Array.from(raw, (c) => c.charCodeAt(0));
    const dimensions = imageDimensions(bytes, mime);
    // The desktop normalizes every accepted image to a square at most 2048px.
    // Recheck a bounded source envelope here because clients can call the Edge
    // Function directly and bypass the Pillow-based desktop normalization.
    if (!dimensions || dimensions.width < 128 || dimensions.height < 128
        || dimensions.width > 2048 || dimensions.height > 2048
        || dimensions.width * dimensions.height > 4_194_304) return null;
    return { bytes, mime, extension };
  } catch (_) { return null; }
}

async function signedAsset(admin: any, path: unknown): Promise<string> {
  const clean = str(path, 500);
  if (!clean) return "";
  const { data, error } = await admin.storage.from(ASSET_BUCKET).createSignedUrl(clean, 3600);
  return error ? "" : str(data?.signedUrl, 2000);
}
// Matches the invite-password policy: enough entropy and the required upper,
// lower, number and symbol characters for the desktop's temporary-password flow.
function temporaryPassword() {
  const upper = "ABCDEFGHJKLMNPQRSTUVWXYZ", lower = "abcdefghijkmnopqrstuvwxyz";
  const digit = "23456789", symbol = "!@#$%";
  const pick = (chars: string) => chars[crypto.getRandomValues(new Uint32Array(1))[0] % chars.length];
  return pick(upper) + pick(lower) + pick(digit) + pick(symbol)
    + Array.from({ length: 12 }, () => pick(upper + lower + digit + symbol)).join("");
}

async function writeAudit(admin: any, orgId: string, actor: User, action: string, type: string, id: string, before: unknown = {}, after: unknown = {}, details: unknown = {}) {
  // User-scoped actions have an affected account as well as an actor.  Persist
  // that relationship so audit readers can name the user after the fact.
  const targetUserId = type === "user" ? id : "";
  await admin.rpc("record_admin_audit", { p_org_id: orgId || null, p_actor_id: actor.id, p_target_user_id: targetUserId || null, p_action: action, p_entity_type: type, p_entity_id: id || null, p_before: obj(before), p_after: obj(after), p_details: obj(details) });
}
async function active(admin: any, user: User): Promise<Response | null> {
  const { data } = await admin.from("user_lifecycle").select("status,access_expires_at").eq("user_id", user.id).maybeSingle();
  if (data?.status && data.status !== "active") return out({ error: "Your account is not active." }, 403);
  if (data?.access_expires_at && Date.parse(data.access_expires_at) <= Date.now()) return out({ error: "Your account access has expired." }, 403);
  return null;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const url = Deno.env.get("SUPABASE_URL")!, anon = Deno.env.get("SUPABASE_ANON_KEY")!, service = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
  const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "").trim();
  if (!token) return out({ error: "Missing access token." }, 401);
  const client: any = createClient(url, anon);
  const { data: who, error: whoErr } = await client.auth.getUser(token);
  const caller = who?.user as User | undefined;
  if (whoErr || !caller) return out({ error: "Invalid or expired token." }, 401);
  const callerRole = roleOf(caller), callerOrg = orgOf(caller), isSuper = SUPER.has(callerRole), isAdmin = ADMINS.has(callerRole);
  const query = new URL(req.url).searchParams;
  const body: Body = req.method === "POST" ? await req.json().catch(() => ({})) : {};
  const requestedAction = str(body.action, 80);
  const selfAvatarAction = ["upload_avatar", "remove_avatar"].includes(requestedAction);
  // A tenant member may read only their assigned project scope. Administration
  // mutations remain administrator-only, except that every active user may
  // replace only their own profile picture.
  if (((req.method !== "GET") && !isAdmin && !selfAvatarAction) || (!isSuper && !callerOrg)) return out({ error: "Organization administration is restricted to this organization." }, 403);
  const admin: any = createClient(url, service, { auth: { autoRefreshToken: false, persistSession: false } });
  const denied = await active(admin, caller); if (denied) return denied;
  const requestedOrg = str(req.method === "GET" ? query.get("org_id") : body.org_id, 120);
  const orgId = isSuper ? requestedOrg : callerOrg;

  try {
    if (req.method === "GET") {
      if (query.get("view") === "visuals") {
        const { data: profile, error } = callerOrg
          ? await admin.from("organization_profiles").select("logo_url").eq("org_id", callerOrg).maybeSingle()
          : { data: null, error: null };
        if (error) return out({ error: error.message }, 500);
        const metadata = obj(caller.user_metadata);
        return out({
          organization_logo_url: await signedAsset(admin, profile?.logo_url),
          avatar_url: await signedAsset(admin, metadata.avatar_path),
        });
      }
      if (query.get("view") === "audit") {
        if (!isAdmin) return out({ error: "Organization audit is restricted to admins." }, 403);
        let q = admin.from("admin_audit_events").select("*").order("created_at", { ascending: false }).limit(500);
        const auditOrg = isSuper ? requestedOrg : callerOrg; if (auditOrg) q = q.eq("org_id", auditOrg);
        const action = str(query.get("action"), 80); if (action) q = q.eq("action", action);
        const { data, error } = await q;
        if (error) return out({ error: error.message }, 500);

        // Audit rows intentionally store immutable UUID references. Resolve
        // only the people and organization names actually present in the
        // authorized result, so the desktop feed can be understood without
        // exposing a directory or leaking IDs from another organization.
        const rows: Record<string, unknown>[] = (data ?? []) as Record<string, unknown>[];
        const userIds = [...new Set(rows.flatMap((row) => [row.actor_id, row.target_user_id])
          .filter((id): id is string => typeof id === "string" && id.length > 0))];
        const orgIds = [...new Set(rows.map((row) => row.org_id)
          .filter((id): id is string => typeof id === "string" && id.length > 0))];
        const people = new Map<string, { email: string; name: string }>();
        if (userIds.length) {
          const { data: directory } = await admin.auth.admin.listUsers({ page: 1, perPage: 1000 });
          for (const person of directory?.users ?? []) {
            if (!userIds.includes(person.id)) continue;
            const metadata = obj(person.user_metadata);
            people.set(person.id, {
              email: typeof person.email === "string" ? person.email : "",
              name: str(metadata.full_name ?? metadata.name, 160),
            });
          }
        }
        const orgNames = new Map<string, string>();
        if (orgIds.length) {
          const { data: orgRows, error: orgError } = await admin.from("orgs").select("id,name").in("id", orgIds);
          if (orgError) return out({ error: orgError.message }, 500);
          for (const org of orgRows ?? []) {
            if (typeof org.id === "string") orgNames.set(org.id, str(org.name, 240));
          }
        }
        return out({ rows: rows.map((row) => {
          const actorId = typeof row.actor_id === "string" ? row.actor_id : "";
          // Older profile-picture events kept the user only as entity_id.
          // Treat that immutable user entity as the target while returning the
          // feed, so existing history is as understandable as new events.
          const targetId = typeof row.target_user_id === "string" && row.target_user_id
            ? row.target_user_id
            : (row.entity_type === "user" && typeof row.entity_id === "string" ? row.entity_id : "");
          const orgId = typeof row.org_id === "string" ? row.org_id : "";
          return {
            ...row,
            actor_email: people.get(actorId)?.email ?? "",
            actor_name: people.get(actorId)?.name ?? "",
            target_email: people.get(targetId)?.email ?? "",
            target_name: people.get(targetId)?.name ?? "",
            org_name: orgNames.get(orgId) ?? "",
          };
        }) });
      }
      if (!orgId) return out({ error: "Select an organization." }, 400);
      const lifecycleRows = admin.from("user_lifecycle").select("*").eq("org_id", orgId);
      if (!isAdmin) {
        lifecycleRows.eq("user_id", caller.id);
      }
      const [profile, projects, teams, lifecycle] = await Promise.all([
        admin.from("organization_profiles").select("*").eq("org_id", orgId).maybeSingle(),
        admin.from("organization_projects").select("*").eq("org_id", orgId).order("name"),
        admin.from("organization_teams").select("*").eq("org_id", orgId).order("name"),
        lifecycleRows,
      ]);
      for (const result of [profile, projects, teams, lifecycle]) if (result.error) return out({ error: result.error.message }, 500);
      const pids = new Set((projects.data ?? []).map((v: any) => v.id)), tids = new Set((teams.data ?? []).map((v: any) => v.id));
      // Build explicit scoped membership queries after the organization rows
      // have been loaded. Chaining a filter onto a saved query builder is
      // version-sensitive; this form guarantees a Member receives only their
      // memberships for this tenant, while administrators receive every
      // membership needed for the assignment editor.
      let projectMembers: any = admin.from("project_memberships").select("project_id,user_id,access_level,created_at");
      let teamMembers: any = admin.from("team_memberships").select("team_id,user_id,role,created_at");
      if (pids.size) projectMembers = projectMembers.in("project_id", [...pids]);
      if (tids.size) teamMembers = teamMembers.in("team_id", [...tids]);
      if (!isAdmin) {
        projectMembers = projectMembers.eq("user_id", caller.id);
        teamMembers = teamMembers.eq("user_id", caller.id);
      }
      const [pms, tms] = await Promise.all([projectMembers, teamMembers]);
      for (const result of [pms, tms]) if (result.error) return out({ error: result.error.message }, 500);
      const projectMemberships = pms.data ?? [];
      const teamMemberships = tms.data ?? [];
      const visibleProjectIds = new Set(projectMemberships.map((v: any) => v.project_id));
      const visibleTeamIds = new Set(teamMemberships.map((v: any) => v.team_id));
      const profileData = profile.data ?? {};
      return out({ profile: { ...profileData, logo_url: await signedAsset(admin, profileData.logo_url) }, projects: isAdmin ? projects.data ?? [] : (projects.data ?? []).filter((v: any) => visibleProjectIds.has(v.id)), teams: isAdmin ? teams.data ?? [] : (teams.data ?? []).filter((v: any) => visibleTeamIds.has(v.id)), lifecycle: lifecycle.data ?? [], project_memberships: projectMemberships, team_memberships: teamMemberships });
    }
    if (req.method !== "POST") return out({ error: "Method not allowed." }, 405);
    const action = requestedAction;
    // User lifecycle actions are targeted by a user id. For a SuperAdmin with
    // no currently selected organization, resolve the organization from that
    // verified target account below; requiring a UI org picker here prevented
    // valid recovery/sign-out/suspend actions from ever reaching that branch.
    // `recovery_link` remains for older desktop clients, but both values use
    // QA Studio's desktop-compatible temporary-password recovery flow. A
    // browser reset link cannot complete inside this desktop application.
    const userAction = ["suspend", "reactivate", "force_signout", "recovery_email", "recovery_link", "upload_avatar", "remove_avatar"].includes(action);
    if (!orgId && !userAction) return out({ error: "Select an organization." }, 400);
    if (action === "upsert_profile") {
      const before = (await admin.from("organization_profiles").select("*").eq("org_id", orgId).maybeSingle()).data ?? {};
      const domains = Array.isArray(body.allowed_domains) ? body.allowed_domains.map((v) => str(v, 120).toLowerCase()).filter(Boolean) : [];
      const row = { org_id: orgId, allowed_domains: [...new Set(domains)], logo_url: str(before.logo_url, 500) || null, default_locale: str(body.default_locale, 20) || "en", default_time_zone: str(body.default_time_zone, 80) || "UTC", support_name: str(body.support_name, 120) || null, support_email: str(body.support_email, 254).toLowerCase() || null, data_retention_days: Math.max(30, Math.min(3650, Number(body.data_retention_days) || 365)), sso_provider: ["none", "saml", "oidc"].includes(str(body.sso_provider, 20)) ? str(body.sso_provider, 20) : "none", sso_metadata_url: str(body.sso_metadata_url, 1000) || null, scim_enabled: body.scim_enabled === true, enterprise_notes: str(body.enterprise_notes, 2000) || null, updated_at: new Date().toISOString(), updated_by: caller.id };
      const { error } = await admin.from("organization_profiles").upsert(row, { onConflict: "org_id" }); if (error) return out({ error: error.message }, 500);
      await writeAudit(admin, orgId, caller, "organization.profile.updated", "organization_profile", orgId, before, row); return out({ ok: true });
    }
    if (action === "upload_avatar") {
      const image = imagePayload(body); if (!image) return out({ error: "Use a JPG, PNG, or WebP image up to 2 MB." }, 400);
      const path = `avatars/${caller.id}.${image.extension}`;
      const before = obj(caller.user_metadata);
      const { error: uploadError } = await admin.storage.from(ASSET_BUCKET).upload(path, image.bytes, { contentType: image.mime, upsert: true, cacheControl: "3600" });
      if (uploadError) return out({ error: uploadError.message }, 500);
      const after = { ...before, avatar_path: path };
      const { error: updateError } = await admin.auth.admin.updateUserById(caller.id, { user_metadata: after });
      if (updateError) return out({ error: updateError.message }, 500);
      await writeAudit(admin, callerOrg, caller, "user.avatar.uploaded", "user", caller.id, { avatar_path: before.avatar_path ?? null }, { avatar_path: path });
      return out({ ok: true, avatar_url: await signedAsset(admin, path) });
    }
    if (action === "remove_avatar") {
      const before = obj(caller.user_metadata);
      const path = str(before.avatar_path, 500);
      if (path) {
        const { error: removeError } = await admin.storage.from(ASSET_BUCKET).remove([path]);
        if (removeError) return out({ error: removeError.message }, 500);
      }
      const after = { ...before };
      delete after.avatar_path;
      const { error: updateError } = await admin.auth.admin.updateUserById(caller.id, { user_metadata: after });
      if (updateError) return out({ error: updateError.message }, 500);
      await writeAudit(admin, callerOrg, caller, "user.avatar.removed", "user", caller.id,
        { avatar_path: path || null }, { avatar_path: null });
      return out({ ok: true, avatar_url: "" });
    }
    if (action === "upload_organization_logo") {
      const image = imagePayload(body); if (!image) return out({ error: "Use a JPG, PNG, or WebP image up to 2 MB." }, 400);
      const path = `organizations/${orgId}/logo.${image.extension}`;
      const before = (await admin.from("organization_profiles").select("*").eq("org_id", orgId).maybeSingle()).data ?? {};
      const { error: uploadError } = await admin.storage.from(ASSET_BUCKET).upload(path, image.bytes, { contentType: image.mime, upsert: true, cacheControl: "3600" });
      if (uploadError) return out({ error: uploadError.message }, 500);
      const { error: profileError } = await admin.from("organization_profiles").upsert({ org_id: orgId, logo_url: path, updated_at: new Date().toISOString(), updated_by: caller.id }, { onConflict: "org_id" });
      if (profileError) return out({ error: profileError.message }, 500);
      await writeAudit(admin, orgId, caller, "organization.logo.uploaded", "organization_profile", orgId, { logo_path: before.logo_url ?? null }, { logo_path: path });
      return out({ ok: true, logo_url: await signedAsset(admin, path) });
    }
    if (action === "remove_organization_logo") {
      const before = (await admin.from("organization_profiles").select("*").eq("org_id", orgId).maybeSingle()).data ?? {};
      const path = str(before.logo_url, 500);
      if (path) {
        const { error: removeError } = await admin.storage.from(ASSET_BUCKET).remove([path]);
        if (removeError) return out({ error: removeError.message }, 500);
      }
      const { error: profileError } = await admin.from("organization_profiles").upsert({
        org_id: orgId, logo_url: null, updated_at: new Date().toISOString(), updated_by: caller.id,
      }, { onConflict: "org_id" });
      if (profileError) return out({ error: profileError.message }, 500);
      await writeAudit(admin, orgId, caller, "organization.logo.removed", "organization_profile", orgId,
        { logo_path: path || null }, { logo_path: null });
      return out({ ok: true, logo_url: "" });
    }
    if (action === "upsert_project") {
      const id = str(body.id, 80), external_key = str(body.external_key, 160), name = str(body.name, 240); if (!external_key || !name) return out({ error: "Project key and name are required." }, 400);
      const before = id ? (await admin.from("organization_projects").select("*").eq("id", id).eq("org_id", orgId).maybeSingle()).data ?? {} : {};
      const previousSource = str(before.source_backend, 40) || "manual";
      const source_backend = str(body.source_backend, 40) || previousSource;
      if (!PROJECT_SOURCES.has(source_backend)) return out({ error: "Unsupported project backend." }, 400);
      const provider_project_key = source_backend === "manual" ? null : (str(body.provider_project_key, 160) || external_key);
      if (source_backend !== "manual" && provider_project_key !== external_key) {
        return out({ error: "Provider project key must match the organization project key." }, 400);
      }
      const sourceChanged = source_backend !== previousSource
        || (source_backend !== "manual" && provider_project_key !== (str(before.provider_project_key, 160) || external_key));
      // Moving an imported project back to Manual also changes its protected
      // source. Require the permission of the source being removed in that
      // case, not merely the universally available Manual scope.
      const controlledSource = source_backend === "manual" ? previousSource : source_backend;
      if (sourceChanged && !canImportProjectSource(caller, controlledSource)) {
        return out({ error: "You do not have permission to select or import backend projects." }, 403);
      }
      const row = { org_id: orgId, external_key, name, description: str(body.description, 2000) || null,
        is_active: body.is_active !== false, source_backend, provider_project_key,
        updated_at: new Date().toISOString() };
      const { data, error } = await admin.from("organization_projects").upsert(id ? { ...row, id } : row, { onConflict: id ? "id" : "org_id,external_key" }).select().single(); if (error) return out({ error: error.message }, 500);
      await writeAudit(admin, orgId, caller, "project.upserted", "project", data.id, before, data); return out({ ok: true, project: data });
    }
    if (action === "set_project_memberships") {
      const projectId = str(body.project_id, 80), project = (await admin.from("organization_projects").select("id,org_id").eq("id", projectId).maybeSingle()).data;
      if (!project || project.org_id !== orgId) return out({ error: "Project not found in this organization." }, 404);
      const members = (Array.isArray(body.members) ? body.members : []).map((m: any) => ({ project_id: projectId, user_id: str(m?.user_id, 80), access_level: ["viewer", "contributor", "manager"].includes(str(m?.access_level, 20)) ? str(m.access_level, 20) : "contributor" })).filter((m: any) => m.user_id);
      const before = (await admin.from("project_memberships").select("user_id,access_level").eq("project_id", projectId)).data ?? [];
      const { data: users } = await admin.auth.admin.listUsers({ page: 1, perPage: 1000 }); const allowed = new Set((users?.users ?? []).filter((u: User) => orgOf(u) === orgId).map((u: User) => u.id));
      if (members.some((m: any) => !allowed.has(m.user_id))) return out({ error: "Every project member must belong to this organization." }, 400);
      await admin.from("project_memberships").delete().eq("project_id", projectId); if (members.length) { const { error } = await admin.from("project_memberships").insert(members); if (error) return out({ error: error.message }, 500); }
      await writeAudit(admin, orgId, caller, "project.memberships.updated", "project", projectId, { members: before }, { members }); return out({ ok: true });
    }
    if (action === "upsert_team") {
      const id = str(body.id, 80), name = str(body.name, 240); if (!name) return out({ error: "Team name is required." }, 400);
      const before = id ? (await admin.from("organization_teams").select("*").eq("id", id).eq("org_id", orgId).maybeSingle()).data ?? {} : {};
      const { data, error } = await admin.from("organization_teams").upsert(id ? { id, org_id: orgId, name, description: str(body.description, 2000) || null } : { org_id: orgId, name, description: str(body.description, 2000) || null }, { onConflict: id ? "id" : "org_id,name" }).select().single(); if (error) return out({ error: error.message }, 500);
      await writeAudit(admin, orgId, caller, "team.upserted", "team", data.id, before, data); return out({ ok: true, team: data });
    }
    if (action === "set_team_memberships") {
      const teamId = str(body.team_id, 80), team = (await admin.from("organization_teams").select("id,org_id").eq("id", teamId).maybeSingle()).data;
      if (!team || team.org_id !== orgId) return out({ error: "Team not found in this organization." }, 404);
      const members = (Array.isArray(body.members) ? body.members : []).map((m: any) => ({ team_id: teamId, user_id: str(m?.user_id, 80), role: str(m?.role, 20) === "lead" ? "lead" : "member" })).filter((m: any) => m.user_id);
      const before = (await admin.from("team_memberships").select("user_id,role").eq("team_id", teamId)).data ?? [];
      const { data: users } = await admin.auth.admin.listUsers({ page: 1, perPage: 1000 }); const allowed = new Set((users?.users ?? []).filter((u: User) => orgOf(u) === orgId).map((u: User) => u.id));
      if (members.some((m: any) => !allowed.has(m.user_id))) return out({ error: "Every team member must belong to this organization." }, 400);
      await admin.from("team_memberships").delete().eq("team_id", teamId); if (members.length) { const { error } = await admin.from("team_memberships").insert(members); if (error) return out({ error: error.message }, 500); }
      await writeAudit(admin, orgId, caller, "team.memberships.updated", "team", teamId, { members: before }, { members }); return out({ ok: true });
    }
    if (["suspend", "reactivate", "force_signout", "recovery_email", "recovery_link"].includes(action)) {
      const userId = str(body.user_id, 80), { data: userData } = await admin.auth.admin.getUserById(userId), target = userData?.user as User | undefined;
      if (!target || (!isSuper && (orgOf(target) !== callerOrg || SUPER.has(roleOf(target))))) return out({ error: "That user is outside your administration scope." }, 403);
      // GoTrueAdminApi.signOut() accepts a *session JWT*, not a user id. An
      // administrator does not possess another user's session JWT, so passing
      // `userId` here caused GoTrue to reject the UUID as a malformed JWT.
      // Do not silently reset a password for this separate action: direct the
      // administrator to the explicit recovery flow, which safely changes the
      // password and consequently ends that user's active Supabase session.
      if (action === "force_signout") {
        return out({ error: "Use Send recovery credentials to end this user's sessions securely." }, 409);
      }
      if (action === "recovery_email" || action === "recovery_link") {
        // Password changes terminate the target user's Supabase session. Do
        // not call admin.signOut(userId): that API expects a session JWT and a
        // UUID would be sent to GoTrue as a malformed token.
        const tempPassword = temporaryPassword();
        const metadata = { ...(target.app_metadata ?? {}), must_reset: true };
        const { error: resetError } = await admin.auth.admin.updateUserById(userId, {
          password: tempPassword,
          app_metadata: metadata,
        });
        if (resetError) return out({ error: resetError.message }, 500);
        // Never record the temporary password in audit data or persistent app
        // state. It exists only in this authenticated function response.
        await writeAudit(admin, orgOf(target), caller, "user.recovery_credentials_issued", "user", userId, {}, { must_reset: true }, { email: target.email });
        return out({ ok: true, recovery_credentials: true, temp_password: tempPassword });
      }
      const before = (await admin.from("user_lifecycle").select("*").eq("user_id", userId).maybeSingle()).data ?? {};
      const expires = str(body.access_expires_at, 80) || null; if (expires && Number.isNaN(Date.parse(expires))) return out({ error: "Access expiry must be a valid timestamp." }, 400);
      const status = action === "suspend" ? "suspended" : "active", row = { user_id: userId, org_id: orgOf(target), status, access_expires_at: expires, suspended_at: status === "suspended" ? new Date().toISOString() : null, suspended_by: status === "suspended" ? caller.id : null, suspension_reason: status === "suspended" ? str(body.reason, 500) || null : null, updated_at: new Date().toISOString(), updated_by: caller.id };
      const { error } = await admin.from("user_lifecycle").upsert(row, { onConflict: "user_id" }); if (error) return out({ error: error.message }, 500);
      const { error: authError } = await admin.auth.admin.updateUserById(userId, { ban_duration: status === "suspended" ? "876000h" : "none" }); if (authError) return out({ error: authError.message }, 500);
      if (status === "suspended") await admin.auth.admin.signOut(userId, "global"); await writeAudit(admin, orgOf(target), caller, `user.lifecycle.${status}`, "user", userId, before, row, { reason: row.suspension_reason }); return out({ ok: true });
    }
    return out({ error: "Unsupported administration action." }, 400);
  } catch (error) { return out({ error: String(error) }, 500); }
});
