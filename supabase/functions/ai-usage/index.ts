// ai-usage — Supabase Edge Function for QA Studio's AI usage tracking.
//
// Two operations, both server-side with the project's service_role key
// (injected automatically as SUPABASE_SERVICE_ROLE_KEY — never ships in the
// desktop app):
//
//   POST  — any signed-in user logs ONE of their own AI provider calls
//           (provider, model, input_tokens, output_tokens, optional tag).
//           user_id / user_email are taken from the caller's OWN verified JWT,
//           never from the request body — so nobody can log an event under
//           someone else's identity, and a compromised/forged body can at
//           worst pollute that same user's own usage numbers.
//
//   GET   — Admin-only. Returns raw per-call rows across ALL users for an
//           optional [start, end] date range, so an admin can build a report
//           for the whole org. This is deliberately a HARD role check
//           (caller's app_metadata.role === 'Admin'), not a capability toggle
//           like 'act.export' — this endpoint exposes every user's activity,
//           which is materially more sensitive than the shared settings
//           org-settings gates, so it gets the strictest check available
//           (mirrors org-settings' POST, which is also Admin-only).
//           Cost is intentionally NOT computed here: token counts are exact
//           (read straight from each provider's response by the desktop
//           client before logging), but price tables change over time and
//           are kept client-side so a price update never needs a redeploy.
//
// Deploy (once):
//   supabase functions deploy ai-usage --no-verify-jwt
// The function verifies the caller's JWT itself, so --no-verify-jwt is safe
// here (same reasoning as org-settings). See ADMIN_USERS_SETUP.md §6.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, apikey",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

// Row cap on a single GET — this is an occasional admin report, not a live
// feed. A caller who needs more history should narrow the date range.
const MAX_ROWS = 50000;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

// Keep provider/model/tag bounded and plain — this data gets rendered
// straight into report tables/exports, so reject anything absurd rather than
// silently truncating and storing garbage.
function cleanStr(v: unknown, maxLen: number): string | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  if (!s || s.length > maxLen) return null;
  return s;
}

function cleanInt(v: unknown): number | null {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  const n = Math.trunc(v);
  if (n < 0 || n > 50_000_000) return null; // sanity cap, not a real limit
  return n;
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
    if (req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      const provider = cleanStr(body.provider, 40);
      const model = cleanStr(body.model, 120);
      const input_tokens = cleanInt(body.input_tokens ?? 0);
      const output_tokens = cleanInt(body.output_tokens ?? 0);
      const tag = body.tag == null ? null : cleanStr(body.tag, 60);
      if (!provider || !model || input_tokens === null || output_tokens === null) {
        return json({ error: "Invalid usage payload." }, 400);
      }

      const { error } = await admin.from("ai_usage_events").insert({
        user_id: who.user.id,
        user_email: who.user.email ?? "(no email)",
        provider,
        model,
        input_tokens,
        output_tokens,
        tag,
      });
      if (error) return json({ error: error.message }, 500);
      return json({ ok: true });
    }

    if (req.method === "GET") {
      // SECURITY: hard Admin-only check — see header notes.
      const callerRole = (who.user.app_metadata as Record<string, unknown>)?.role;
      if (callerRole !== "Admin") {
        return json({ error: "You don't have permission to view this report." }, 403);
      }

      const q = new URL(req.url).searchParams;
      const start = q.get("start"); // 'YYYY-MM-DD', inclusive
      const end = q.get("end");     // 'YYYY-MM-DD', inclusive

      let query = admin
        .from("ai_usage_events")
        .select("created_at, user_email, provider, model, input_tokens, output_tokens, tag")
        .order("created_at", { ascending: true })
        .limit(MAX_ROWS);
      if (start) query = query.gte("created_at", `${start}T00:00:00Z`);
      if (end) query = query.lte("created_at", `${end}T23:59:59.999Z`);

      const { data, error } = await query;
      if (error) return json({ error: error.message }, 500);
      return json({ rows: data ?? [], truncated: (data ?? []).length >= MAX_ROWS });
    }

    return json({ error: "Method not allowed." }, 405);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});
