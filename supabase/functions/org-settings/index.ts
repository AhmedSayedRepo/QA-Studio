// org-settings — Supabase Edge Function for QA Studio's shared, org-wide settings
// (currently one key, "email": the Gmail sender / App Password used by the Report
// screen's "send" feature). Runs server-side with the project's service_role key
// (injected automatically as SUPABASE_SERVICE_ROLE_KEY) — that key never ships in
// the desktop app.
//
// Deploy (once), self-verifying so it works with a publishable anon key:
//   supabase functions deploy org-settings --no-verify-jwt
//
// The function verifies the caller's JWT itself, so --no-verify-jwt is safe here.
// See ADMIN_USERS_SETUP.md §5 for the one-time table + deploy steps.
//
// SECURITY (this replaces an earlier version of this function that let ANY
// signed-in user, including a zero-capability self-registered "Viewer", read the
// shared Gmail App Password): GET now requires the caller to hold the
// 'act.export' capability — the same capability that gates the desktop app's
// Report screen send/export actions, the only feature that actually needs this
// credential. A Viewer's own client already skips calling this endpoint (see
// main.py's _refresh_org_settings), but that is a UI-level convenience, not a
// security boundary — a Viewer still holds a valid access token for their own
// session and could call this endpoint directly with a bare HTTP request. The
// capability check below is what actually closes that gap: it is enforced here,
// server-side, regardless of what the client does or doesn't do.
//
// Writes (POST) remain Admin-only, unchanged.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, apikey",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

// Mirrors auth_supabase.py's ROLE_PRESETS for the one capability this function
// cares about: Admin and Member presets both include "act.export"; Viewer's
// preset does not (see auth_supabase.py CATALOG / ROLE_PRESETS). A per-user
// custom caps list (app_metadata.caps), if set, always wins — same as the
// Python client's caps_for().
function hasExportCap(user: { app_metadata?: Record<string, unknown> }): boolean {
  const am = user.app_metadata ?? {};
  const caps = am.caps;
  if (Array.isArray(caps)) return caps.includes("act.export");
  const role = am.role;
  return role === "Admin" || role === "Member";
}

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

  // ── verify the caller (every method needs a valid session) ───────────────
  const authHeader = req.headers.get("Authorization") || "";
  const token = authHeader.replace(/^Bearer\s+/i, "").trim();
  if (!token) return json({ error: "Missing access token." }, 401);

  const caller = createClient(url, anonKey);
  const { data: who, error: whoErr } = await caller.auth.getUser(token);
  if (whoErr || !who?.user) return json({ error: "Invalid or expired token." }, 401);

  // ── privileged client (service_role) for the actual reads/writes ─────────
  const admin = createClient(url, serviceKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });

  try {
    if (req.method === "GET") {
      // SECURITY: capability-gated, not just "any signed-in user" — see notes above.
      if (!hasExportCap(who.user)) {
        return json({ error: "You don't have permission to view these settings." }, 403);
      }
      const { data, error } = await admin.from("org_settings").select("key, value");
      if (error) return json({ error: error.message }, 500);
      const settings: Record<string, unknown> = {};
      for (const row of data ?? []) settings[row.key as string] = row.value;
      return json({ settings });
    }

    if (req.method === "POST") {
      const callerRole = (who.user.app_metadata as Record<string, unknown>)?.role;
      if (callerRole !== "Admin") return json({ error: "Admins only." }, 403);

      const body = await req.json().catch(() => ({}));
      const key = body.key as string | undefined;
      const value = body.value;
      if (!key || typeof key !== "string") return json({ error: "Provide a key." }, 400);
      if (value === undefined) return json({ error: "Provide a value." }, 400);

      const { error } = await admin
        .from("org_settings")
        .upsert({ key, value, updated_at: new Date().toISOString(), updated_by: who.user.id });
      if (error) return json({ error: error.message }, 500);
      return json({ ok: true });
    }

    return json({ error: "Method not allowed." }, 405);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});
