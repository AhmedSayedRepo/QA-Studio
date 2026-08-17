// orgs — Supabase Edge Function: organization directory + super-admin CRUD.
//
//   * SuperAdmin (app_metadata.role in {SuperAdmin, Admin}) — create / edit /
//     delete orgs, and read the full directory (incl. contact details).
//   * OrgManager — may READ only their OWN org (id + name, for display).
//
// Runs server-side with the service_role key (never shipped in the app). THIS
// FUNCTION IS THE SECURITY BOUNDARY. Deploy: supabase functions deploy orgs --no-verify-jwt

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, apikey",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};
const SUPER_ROLES = ["SuperAdmin", "Admin"];

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
}
function roleOf(u) { const am = (u && u.app_metadata) || {}; return am.role || "Viewer"; }
function orgOf(u) { const am = (u && u.app_metadata) || {}; const o = am.org_id; return (typeof o === "string" && o) ? o : ""; }
const isSuper = (r) => SUPER_ROLES.includes(r);

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });

  const url = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY");

  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "").trim();
  if (!token) return json({ error: "Missing access token." }, 401);
  const caller = createClient(url, anonKey);
  const { data: who, error: whoErr } = await caller.auth.getUser(token);
  if (whoErr || !who || !who.user) return json({ error: "Invalid or expired token." }, 401);

  const callerRole = roleOf(who.user);
  const callerOrg = orgOf(who.user);
  const callerIsSuper = isSuper(callerRole);
  const callerIsManager = callerRole === "OrgManager";
  if (!callerIsSuper && !callerIsManager) return json({ error: "Admins only." }, 403);

  const admin = createClient(url, serviceKey, { auth: { autoRefreshToken: false, persistSession: false } });

  try {
    if (req.method === "GET") {
      // Manager: only their own org (name for display). Super: the whole directory.
      if (callerIsManager) {
        if (!callerOrg) return json({ orgs: [] });
        const { data, error } = await admin.from("orgs").select("id,name").eq("id", callerOrg);
        if (error) return json({ error: error.message }, 500);
        return json({ orgs: data || [] });
      }
      const { data, error } = await admin.from("orgs")
        .select("id,name,contact_name,contact_email,contact_phone,created_at").order("name");
      if (error) return json({ error: error.message }, 500);
      return json({ orgs: data || [] });
    }

    if (req.method === "POST") {
      if (!callerIsSuper) return json({ error: "Only a super admin can manage organizations." }, 403);
      const body = await req.json().catch(() => ({}));
      const op = (body.op || "upsert").toString();

      if (op === "delete") {
        const id = (body.id || "").toString().trim();
        if (!id) return json({ error: "Organization id is required." }, 400);
        // Block deletion while users are still assigned to this org.
        const { data: list, error: lErr } = await admin.auth.admin.listUsers({ page: 1, perPage: 1000 });
        if (lErr) return json({ error: lErr.message }, 500);
        const inUse = (list.users || []).filter((u) => {
          const am = (u.app_metadata) || {};
          return (typeof am.org_id === "string" ? am.org_id : "") === id;
        }).length;
        if (inUse > 0) {
          return json({ error: inUse + " user(s) are still in this organization. Reassign them first." }, 409);
        }
        const { error } = await admin.from("orgs").delete().eq("id", id);
        if (error) return json({ error: error.message }, 500);
        return json({ ok: true, id, deleted: true });
      }

      // upsert (create or edit)
      const id = (body.id || "").toString().trim();
      const name = (body.name || "").toString().trim();
      if (!id) return json({ error: "Organization id is required." }, 400);
      if (!name) return json({ error: "Organization name is required." }, 400);
      const row = {
        id,
        name,
        contact_name: (body.contact_name || "").toString().trim() || null,
        contact_email: (body.contact_email || "").toString().trim() || null,
        contact_phone: (body.contact_phone || "").toString().trim() || null,
        updated_at: new Date().toISOString(),
      };
      const { error } = await admin.from("orgs").upsert(row, { onConflict: "id" });
      if (error) return json({ error: error.message }, 500);
      return json({ ok: true, id, name });
    }

    return json({ error: "Method not allowed." }, 405);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});
