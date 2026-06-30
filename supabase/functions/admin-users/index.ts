// admin-users — Supabase Edge Function for QA Studio's in-app user management.
//
// Lets a signed-in **Admin** (app_metadata.role === "Admin") list all users and
// change any user's role. Runs server-side with the project's service_role key
// (injected automatically as SUPABASE_SERVICE_ROLE_KEY) — that key never ships in
// the desktop app, so this is the secure place to do privileged user admin.
//
// Deploy (once), self-verifying so it works with a publishable anon key:
//   supabase functions deploy admin-users --no-verify-jwt
//
// The function verifies the caller's JWT itself and rejects non-Admins, so
// --no-verify-jwt is safe here.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, apikey",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

const ROLES = ["Admin", "Member", "Viewer"];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });

  const url = Deno.env.get("SUPABASE_URL")!;
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY")!;

  // ── verify the caller and require Admin ──────────────────────────────────
  const authHeader = req.headers.get("Authorization") || "";
  const token = authHeader.replace(/^Bearer\s+/i, "").trim();
  if (!token) return json({ error: "Missing access token." }, 401);

  const caller = createClient(url, anonKey);
  const { data: who, error: whoErr } = await caller.auth.getUser(token);
  if (whoErr || !who?.user) return json({ error: "Invalid or expired token." }, 401);
  const callerRole = (who.user.app_metadata as Record<string, unknown>)?.role;
  if (callerRole !== "Admin") return json({ error: "Admins only." }, 403);

  // ── privileged client (service_role) for the actual admin operations ─────
  const admin = createClient(url, serviceKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });

  try {
    if (req.method === "GET") {
      const { data, error } = await admin.auth.admin.listUsers({ page: 1, perPage: 200 });
      if (error) return json({ error: error.message }, 500);
      const users = data.users.map((u) => {
        const am = (u.app_metadata as Record<string, unknown>) || {};
        return {
          id: u.id,
          email: u.email,
          role: am.role || "Viewer",
          caps: Array.isArray(am.caps) ? am.caps : null,
          created_at: u.created_at,
          last_sign_in_at: u.last_sign_in_at,
          confirmed: Boolean(u.email_confirmed_at || (u as Record<string, unknown>).confirmed_at),
        };
      });
      return json({ users });
    }

    if (req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      const userId = body.user_id as string | undefined;
      const newRole = body.role as string | undefined;
      const caps = body.caps as unknown;
      if (!userId) return json({ error: "Provide user_id." }, 400);

      const meta: Record<string, unknown> = {};
      if (newRole !== undefined) {
        if (!ROLES.includes(newRole)) {
          return json({ error: "Invalid role (Admin/Member/Viewer)." }, 400);
        }
        meta.role = newRole;
      }
      if (caps !== undefined) {
        if (!Array.isArray(caps)) return json({ error: "caps must be an array." }, 400);
        meta.caps = caps;
      }
      if (Object.keys(meta).length === 0) {
        return json({ error: "Provide a role and/or caps to update." }, 400);
      }
      const { data, error } = await admin.auth.admin.updateUserById(userId, {
        app_metadata: meta,
      });
      if (error) return json({ error: error.message }, 500);
      return json({ ok: true, id: data.user.id, ...meta });
    }

    return json({ error: "Method not allowed." }, 405);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});
