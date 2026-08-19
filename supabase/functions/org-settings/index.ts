// org-settings — Supabase Edge Function for QA Studio's organization-scoped settings
// (currently one key, "email": the Gmail sender / App Password used by the Report
// screen's "send" feature). The password itself lives in Supabase Vault; this
// function is the only authorised path that decrypts it for a user who can send.
// It runs server-side with the project's service_role key
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
// DEFENCE IN DEPTH (2026-07, security review): the `org_settings` TABLE is now
// also locked at the database layer. It previously had an RLS policy
// `org_settings_select_authenticated USING (true)` plus a direct SELECT grant,
// which let any signed-in user read the Gmail App Password DIRECTLY via
// `GET /rest/v1/org_settings`, bypassing this function's capability check
// entirely. That policy + grants were dropped (migration
// `lock_org_settings_to_service_role`): RLS stays enabled with NO permissive
// policy for authenticated/anon => the table is reachable ONLY through this
// service-role function. So the capability check here is now the true and only
// gate, not just the intended one.
//
// Reads and writes normally use the caller's verified app_metadata.org_id. A
// SuperAdmin may explicitly select an existing organization; that override is
// checked in this function and is never trusted merely because a client sent it.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, apikey",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

// Mirrors auth_supabase.py's ROLE_PRESETS for the one capability this function
// cares about: Admin and Member presets both include "act.export"; Viewer's
// preset does not (see auth_supabase.py CATALOG / ROLE_PRESETS). A per-user
// custom caps list (app_metadata.caps), if set, normally wins — except a
// SuperAdmin retains access so they can manage sender settings across orgs.
function hasExportCap(user: { app_metadata?: Record<string, unknown> }): boolean {
  const am = user.app_metadata ?? {};
  const caps = am.caps;
  if (Array.isArray(caps)) return caps.includes("act.export");
  const role = am.role;
  return role === "Admin" || role === "SuperAdmin" || role === "OrgManager" || role === "Member";
}

function orgOf(user: { app_metadata?: Record<string, unknown> }): string {
  const orgId = user.app_metadata?.org_id;
  return typeof orgId === "string" ? orgId.trim() : "";
}

function canWriteSettings(user: { app_metadata?: Record<string, unknown> }): boolean {
  const role = user.app_metadata?.role;
  return role === "Admin" || role === "SuperAdmin" || role === "OrgManager";
}

function isSuperAdmin(user: { app_metadata?: Record<string, unknown> }): boolean {
  const role = user.app_metadata?.role;
  return role === "Admin" || role === "SuperAdmin";
}

async function resolveTargetOrgId(
  // Keep this intentionally structural. The SDK's createClient() overloads
  // infer different schema generics for the anon and service-role clients, but
  // this helper needs only the query chain below.
  admin: { from: (table: string) => any },
  user: { app_metadata?: Record<string, unknown> },
  requestedOrgId: string,
): Promise<{ orgId: string } | { error: Response }> {
  const requested = requestedOrgId.trim();
  if (!requested) {
    const callerOrgId = orgOf(user);
    if (!callerOrgId) {
      return { error: json({ error: "Select an organization before managing its settings." }, 403) };
    }
    return { orgId: callerOrgId };
  }

  if (!isSuperAdmin(user)) {
    return { error: json({ error: "Only Super Admins may select another organization." }, 403) };
  }

  // Check existence before the service-role client reads or writes settings.
  const { data, error } = await admin
    .from("orgs")
    .select("id")
    .eq("id", requested)
    .maybeSingle();
  if (error) return { error: json({ error: error.message }, 500) };
  if (!data) return { error: json({ error: "Organization not found." }, 404) };
  return { orgId: requested };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

function asEmailSetting(row: Record<string, unknown> | null | undefined) {
  return {
    sender: typeof row?.sender === "string" ? row.sender : "",
    sender_name: typeof row?.sender_name === "string" ? row.sender_name : "",
    app_password: typeof row?.app_password === "string" ? row.app_password : "",
    inherited_from_org_id: typeof row?.inherited_from_org_id === "string"
      ? row.inherited_from_org_id : "",
  };
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
      // SECURITY: members need the export capability to read the SMTP secret.
      // Organization Managers already have the stronger server-side right to
      // manage this setting, so a customised caps list must not let them write
      // a sender they can never read back. SuperAdmins manage across orgs.
      if (!hasExportCap(who.user) && !canWriteSettings(who.user)) {
        return json({ error: "You don't have permission to view these settings." }, 403);
      }
      const requestUrl = new URL(req.url);
      // Cross-organization audit access is a SuperAdmin-only administration
      // surface. It intentionally reads through this function's service-role
      // client instead of exposing the audit table to the Data API.
      if (requestUrl.searchParams.get("audit_feed") === "1") {
        if (!isSuperAdmin(who.user)) {
          return json({ error: "Only Super Admins may view the sender audit." }, 403);
        }
        const orgId = (requestUrl.searchParams.get("org_id") ?? "").trim();
        const actorId = (requestUrl.searchParams.get("actor_id") ?? "").trim();
        const event = (requestUrl.searchParams.get("event") ?? "").trim();
        const since = (requestUrl.searchParams.get("since") ?? "").trim();
        if (since && Number.isNaN(Date.parse(since))) {
          return json({ error: "Invalid audit start date." }, 400);
        }
        let query = admin
          .from("organization_settings_audit")
          .select("org_id, actor_id, event, details, created_at")
          // Routine credential refresh is operational noise, not an
          // administrator action. It is kept out of all visible audit views.
          .neq("event", "email_auto_synced")
          .order("created_at", { ascending: false })
          .limit(200);
        if (orgId) query = query.eq("org_id", orgId);
        if (actorId) query = query.eq("actor_id", actorId);
        if (event) query = query.eq("event", event);
        if (since) query = query.gte("created_at", since);
        const { data, error } = await query;
        if (error) return json({ error: error.message }, 500);

        const emails = new Map<string, string>();
        const actorIds = [...new Set((data ?? [])
          .map((row: { actor_id?: unknown }) => typeof row.actor_id === "string" ? row.actor_id : "")
          .filter(Boolean))];
        // One bounded directory lookup avoids issuing a separate Auth request
        // for every audit row. We expose only emails belonging to actual rows.
        if (actorIds.length) {
          const { data: userList } = await admin.auth.admin.listUsers({ page: 1, perPage: 1000 });
          for (const user of userList?.users ?? []) {
            if (actorIds.includes(user.id) && user.email) emails.set(user.id, user.email);
          }
        }
        const audit = (data ?? []).map((row: Record<string, unknown>) => ({
          ...row,
          actor_email: typeof row.actor_id === "string" ? (emails.get(row.actor_id) ?? "") : "",
        }));
        // A small per-organization summary powers the Organization list badge.
        // It is intentionally independent of the audit filters, so filtering
        // the table never makes a badge look stale or disappear.
        const { data: senderChanges, error: senderChangesError } = await admin
          .from("organization_settings")
          .select("org_id, updated_at, value")
          .eq("key", "email");
        if (senderChangesError) return json({ error: senderChangesError.message }, 500);
        const { data: allOrgs, error: allOrgsError } = await admin.from("orgs").select("id");
        if (allOrgsError) return json({ error: allOrgsError.message }, 500);
        const { data: tests, error: testsError } = await admin
          .from("organization_settings_audit")
          .select("org_id, event, created_at")
          .in("event", ["email_test_succeeded", "email_test_failed"])
          .order("created_at", { ascending: false })
          .limit(500);
        if (testsError) return json({ error: testsError.message }, 500);

        const senderByOrg = new Map<string, Record<string, unknown>>();
        for (const row of senderChanges ?? []) {
          if (typeof row.org_id === "string" && row.value && typeof row.value === "object") {
            senderByOrg.set(row.org_id, row.value as Record<string, unknown>);
          }
        }
        const latestTestByOrg = new Map<string, { event: string; created_at: string }>();
        for (const row of tests ?? []) {
          if (typeof row.org_id === "string" && !latestTestByOrg.has(row.org_id)) {
            latestTestByOrg.set(row.org_id, {
              event: typeof row.event === "string" ? row.event : "",
              created_at: typeof row.created_at === "string" ? row.created_at : "",
            });
          }
        }
        const hasConfiguredSender = (org: string, depth = 0): boolean => {
          if (depth > 3) return false;
          const value = senderByOrg.get(org);
          if (!value) return false;
          const sender = typeof value.sender === "string" ? value.sender.trim() : "";
          const secretId = typeof value.app_password_secret_id === "string"
            ? value.app_password_secret_id.trim() : "";
          if (sender && secretId) return true;
          const source = typeof value.inherit_from_org_id === "string"
            ? value.inherit_from_org_id.trim() : "";
          return source && source !== org ? hasConfiguredSender(source, depth + 1) : false;
        };
        const staleBefore = Date.now() - 30 * 24 * 60 * 60 * 1000;
        const emailHealth = (allOrgs ?? []).map((org: { id?: unknown }) => {
          const orgId = typeof org.id === "string" ? org.id : "";
          const test = latestTestByOrg.get(orgId);
          let status = "amber";
          if (!hasConfiguredSender(orgId)) status = "red";
          else if (test?.event === "email_test_failed") status = "red";
          else if (!test || !test.created_at || Date.parse(test.created_at) < staleBefore) status = "amber";
          else status = "green";
          return { org_id: orgId, status, last_test_at: test?.created_at ?? "" };
        });
        // Strip the internal JSON setting value: the client needs only its
        // timestamp for the lightweight 'last sender change' badge.
        const senderChangeSummary = (senderChanges ?? []).map((row: Record<string, unknown>) => ({
          org_id: row.org_id, updated_at: row.updated_at,
        }));
        return json({ audit, sender_changes: senderChangeSummary, email_health: emailHealth });
      }
      const target = await resolveTargetOrgId(
        admin, who.user, requestUrl.searchParams.get("org_id") ?? "",
      );
      if ("error" in target) return target.error;
      const { data, error } = await admin
        .from("organization_settings")
        .select("key, value")
        .eq("org_id", target.orgId)
        .neq("key", "email");
      if (error) return json({ error: error.message }, 500);
      const settings: Record<string, unknown> = {};
      for (const row of data ?? []) settings[row.key as string] = row.value;
      // Vault decryption happens inside a service-role-only database function.
      // Never read a password from the JSON settings table directly.
      const { data: emailRows, error: emailError } = await admin.rpc(
        "get_org_email_settings", { p_org_id: target.orgId },
      );
      if (emailError) return json({ error: emailError.message }, 500);
      const emailSetting = asEmailSetting((emailRows ?? [])[0]);
      settings.email = emailSetting;
      if (requestUrl.searchParams.get("audit") === "1") {
        if (!canWriteSettings(who.user)) {
          return json({ error: "Organization managers only." }, 403);
        }
        const { data: audit, error: auditError } = await admin.rpc(
          // Ask for enough history to hide old automatic-sync noise while
          // still returning five useful human configuration/test events.
          "get_org_email_audit", { p_org_id: target.orgId, p_limit: 20 },
        );
        if (auditError) return json({ error: auditError.message }, 500);
        // Existing automatic-sync rows are deliberately hidden as well. Only
        // human configuration changes and explicit sender tests are useful in
        // the Setup activity panel.
        return json({ settings, audit: (audit ?? []).filter(
          (row: Record<string, unknown>) => row.event !== "email_auto_synced",
        ) });
      }
      return json({ settings });
    }

    if (req.method === "POST") {
      if (!canWriteSettings(who.user)) {
        return json({ error: "Organization managers only." }, 403);
      }

      const body = await req.json().catch(() => ({}));
      if (body.org_id !== undefined && typeof body.org_id !== "string") {
        return json({ error: "Organization id must be text." }, 400);
      }
      const target = await resolveTargetOrgId(admin, who.user, body.org_id ?? "");
      if ("error" in target) return target.error;
      const action = typeof body.action === "string" ? body.action : "save_email";
      if (action === "email_test") {
        const success = typeof body.success === "boolean" ? body.success : false;
        const errorText = typeof body.error === "string" ? body.error : null;
        const { error } = await admin.rpc("log_org_email_test", {
          p_org_id: target.orgId, p_actor_id: who.user.id,
          p_success: success, p_error: errorText,
        });
        if (error) return json({ error: error.message }, 500);
        return json({ ok: true });
      }
      if (action !== "save_email") return json({ error: "Unsupported action." }, 400);
      const value = body.value;
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return json({ error: "Provide email settings." }, 400);
      }
      const sender = typeof value.sender === "string" ? value.sender : "";
      const senderName = typeof value.sender_name === "string" ? value.sender_name : "";
      // Empty/missing passwords preserve the existing Vault secret. This makes
      // editing an address safe when the password is intentionally masked.
      const appPassword = typeof value.app_password === "string" ? value.app_password : null;
      const { error } = await admin.rpc("set_org_email_settings", {
        p_org_id: target.orgId,
        p_sender: sender,
        p_sender_name: senderName,
        p_app_password: appPassword,
        p_actor_id: who.user.id,
      });
      if (error) return json({ error: error.message }, 500);
      return json({ ok: true });
    }

    return json({ error: "Method not allowed." }, 405);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});
