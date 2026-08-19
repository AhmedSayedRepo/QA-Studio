// set-password — Supabase Edge Function for QA Studio self-service password set.
//
// Any AUTHENTICATED user calls this to set a new password for THEIR OWN account
// and clear the forced-reset flag (app_metadata.must_reset). It is the endpoint
// behind the "you must change your temporary password" screen shown on first
// sign-in after an admin invite.
//
// The caller is identified ONLY from their JWT — there is no target-user param,
// so a user can never change anyone else's password. Runs with the service_role
// key (never shipped in the app) so it can update the protected app_metadata.
//
// Deploy: supabase functions deploy set-password --no-verify-jwt
//   (the function verifies the caller's JWT itself.)

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, apikey",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

function meetsPasswordPolicy(password: string) {
  return password.length >= 12
    && !/\s/.test(password)
    && /[a-z]/.test(password)
    && /[A-Z]/.test(password)
    && /\d/.test(password)
    && /[^A-Za-z0-9]/.test(password);
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "POST only." }, 405);

  const url = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY");

  // -- verify caller (own account only) ----------------------------------------
  const authHeader = req.headers.get("Authorization") || "";
  const token = authHeader.replace(/^Bearer\s+/i, "").trim();
  if (!token) return json({ error: "Missing access token." }, 401);

  const caller = createClient(url, anonKey);
  const { data: who, error: whoErr } = await caller.auth.getUser(token);
  if (whoErr || !who || !who.user) return json({ error: "Invalid or expired token." }, 401);

  let body = {};
  try { body = await req.json(); } catch { body = {}; }
  const password = (body.password || "").toString();
  if (!meetsPasswordPolicy(password)) {
    return json({ error: "Password must be at least 12 characters and include uppercase, lowercase, a number, and a symbol." }, 400);
  }

  const admin = createClient(url, serviceKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });

  // Preserve the user's existing app_metadata (role / org_id / caps); only clear
  // the forced-reset flag alongside the new password.
  const existing = (who.user.app_metadata) || {};
  const meta = { ...existing };
  delete meta.must_reset;
  const { error: upErr } = await admin.auth.admin.updateUserById(who.user.id, {
    password,
    app_metadata: { ...meta, must_reset: false },
  });
  if (upErr) return json({ error: upErr.message }, 500);
  return json({ ok: true });
});
