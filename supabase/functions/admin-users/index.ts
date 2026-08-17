// admin-users — Supabase Edge Function for QA Studio's in-app user management.
//
// Two-tier admin model:
//   * SuperAdmin (app_metadata.role === "SuperAdmin", or legacy "Admin"):
//       manages ALL users across every org; assigns org_id; may set any role.
//   * OrgManager (app_metadata.role === "OrgManager"):
//       manages ONLY users in their OWN org (app_metadata.org_id). May invite
//       users into that org and set them to OrgManager / Member / Viewer.
//       May NEVER touch a user in another org, promote anyone to SuperAdmin,
//       modify a SuperAdmin, or move a user to a different org.
//
// Runs server-side with the service_role key (never shipped in the app). THIS
// FUNCTION IS THE SECURITY BOUNDARY — the desktop client is untrusted, so every
// scope/escalation rule below is enforced here, not in the app.
//
// Deploy: supabase functions deploy admin-users --no-verify-jwt
//   (the function verifies the caller's JWT itself and rejects the unauthorized)

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, apikey",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

// Every assignable role. "Admin" is accepted as a legacy alias of SuperAdmin.
const ROLES = ["SuperAdmin", "OrgManager", "Member", "Viewer"];
const SUPER_ROLES = ["SuperAdmin", "Admin"];
// Roles an OrgManager is allowed to hand out (never SuperAdmin/Admin).
const MANAGER_ASSIGNABLE = ["OrgManager", "Member", "Viewer"];

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

function roleOf(u) {
  const am = (u && u.app_metadata) || {};
  return am.role || "Viewer";
}
function orgOf(u) {
  const am = (u && u.app_metadata) || {};
  const o = am.org_id;
  return (typeof o === "string" && o) ? o : "";
}
const isSuper = (role) => SUPER_ROLES.includes(role);
const isManager = (role) => role === "OrgManager";

// Strong, human-friendly temporary password (skips ambiguous chars like O/0/l/1)
// using the Web Crypto RNG. 16 chars across 4 classes so it satisfies any
// Supabase password policy; the invitee is forced to change it on first sign-in.
function genTempPassword() {
  const upper = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const lower = "abcdefghijkmnopqrstuvwxyz";
  const digit = "23456789";
  const sym = "!@#$%*?";
  const all = upper + lower + digit + sym;
  const r = (n) => crypto.getRandomValues(new Uint32Array(1))[0] % n;
  const pick = (set) => set[r(set.length)];
  const out = [pick(upper), pick(lower), pick(digit), pick(sym)];
  for (let i = 0; i < 12; i++) out.push(pick(all));
  for (let i = out.length - 1; i > 0; i--) {   // Fisher–Yates shuffle
    const j = r(i + 1);
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out.join("");
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });

  const url = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY");

  // -- verify caller -----------------------------------------------------------
  const authHeader = req.headers.get("Authorization") || "";
  const token = authHeader.replace(/^Bearer\s+/i, "").trim();
  if (!token) return json({ error: "Missing access token." }, 401);

  const caller = createClient(url, anonKey);
  const { data: who, error: whoErr } = await caller.auth.getUser(token);
  if (whoErr || !who || !who.user) return json({ error: "Invalid or expired token." }, 401);

  const callerRole = roleOf(who.user);
  const callerOrg = orgOf(who.user);
  const callerIsSuper = isSuper(callerRole);
  const callerIsManager = isManager(callerRole);
  if (!callerIsSuper && !callerIsManager) {
    return json({ error: "User management is restricted to admins." }, 403);
  }
  // An OrgManager with no org can't scope anything — refuse rather than leak.
  if (callerIsManager && !callerOrg) {
    return json({ error: "Your account has no organization assigned. Ask a super admin to set it." }, 403);
  }

  const admin = createClient(url, serviceKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });

  // May the caller act on this target user?
  const inScope = (targetUser) => {
    if (callerIsSuper) return true;
    if (isSuper(roleOf(targetUser))) return false;   // manager can't touch a super
    return orgOf(targetUser) === callerOrg;           // ...only own-org users
  };

  try {
    if (req.method === "GET") {
      const { data, error } = await admin.auth.admin.listUsers({ page: 1, perPage: 200 });
      if (error) return json({ error: error.message }, 500);
      let users = data.users.map((u) => {
        const am = (u.app_metadata) || {};
        return {
          id: u.id,
          email: u.email,
          role: am.role || "Viewer",
          org_id: (typeof am.org_id === "string" ? am.org_id : "") || "",
          caps: Array.isArray(am.caps) ? am.caps : null,
          created_at: u.created_at,
          last_sign_in_at: u.last_sign_in_at,
          confirmed: Boolean(u.email_confirmed_at || u.confirmed_at),
        };
      });
      if (callerIsManager) users = users.filter((u) => u.org_id === callerOrg);
      return json({ users, caller: { role: callerRole, org_id: callerOrg } });
    }

    if (req.method === "POST") {
      const body = await req.json().catch(() => ({}));

      // -- ADD an ALREADY signed-up user to an org (no email invite) ----------
      // A manager may pull in an ORG-LESS signed-up user (their own org only),
      // never one already in another org (no poaching) and never a super admin.
      const addEmail = (body.add_existing_email || "").toString().trim().toLowerCase();
      if (addEmail) {
        const { data: list, error: lErr } = await admin.auth.admin.listUsers({ page: 1, perPage: 1000 });
        if (lErr) return json({ error: lErr.message }, 500);
        const target = (list.users || []).find((u) => (u.email || "").toLowerCase() === addEmail);
        if (!target) return json({ error: "No signed-up user with that email was found." }, 404);
        const tOrg = orgOf(target);
        let addRole = (body.role || "Member").toString();
        if (callerIsManager) {
          if (isSuper(roleOf(target))) return json({ error: "You can't manage that user." }, 403);
          if (tOrg === callerOrg) return json({ error: "That user is already in your organization." }, 409);
          if (tOrg) return json({ error: "That user is already in another organization." }, 409);
          if (!MANAGER_ASSIGNABLE.includes(addRole)) addRole = "Member";
          const { error } = await admin.auth.admin.updateUserById(target.id, { app_metadata: { org_id: callerOrg, role: addRole } });
          if (error) return json({ error: error.message }, 500);
          return json({ ok: true, id: target.id, email: addEmail, org_id: callerOrg, role: addRole });
        }
        // super admin: assign to a named org (blank leaves org unchanged)
        if (!ROLES.includes(addRole)) addRole = "Member";
        const sMeta = { role: addRole };
        const sOrg = (body.org_id || "").toString().trim();
        if (sOrg) sMeta.org_id = sOrg;
        const { error } = await admin.auth.admin.updateUserById(target.id, { app_metadata: sMeta });
        if (error) return json({ error: error.message }, 500);
        return json({ ok: true, id: target.id, email: addEmail, org_id: sOrg, role: addRole });
      }

      // -- INVITE: create a confirmed account with a temp password -----------
      // The desktop app signs in with email+password (there's no web magic-link
      // landing), so instead of Supabase's invite link we CREATE a confirmed
      // account with a generated temporary password and flag must_reset, so the
      // invitee is forced to set their own password on first sign-in. The temp
      // password is returned so the app can email it to them.
      const inviteEmail = (body.invite_email || "").toString().trim().toLowerCase();
      if (inviteEmail) {
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(inviteEmail)) {
          return json({ error: "Enter a valid email address." }, 400);
        }
        const role = (body.role || "Viewer").toString();
        if (!ROLES.includes(role)) return json({ error: "Invalid role." }, 400);
        if (callerIsManager && !MANAGER_ASSIGNABLE.includes(role)) {
          return json({ error: "You can only invite Org Manager, Member or Viewer." }, 403);
        }
        // target org: a super admin may name it; a manager forces their own
        const org = callerIsSuper ? ((body.org_id || "").toString().trim()) : callerOrg;
        const fullName = (body.name || "").toString().trim();
        const tempPw = genTempPassword();
        const { data: created, error: cErr } = await admin.auth.admin.createUser({
          email: inviteEmail,
          password: tempPw,
          email_confirm: true,
          app_metadata: { role, org_id: org, must_reset: true },
          user_metadata: fullName ? { name: fullName, full_name: fullName } : {},
        });
        if (cErr || !created || !created.user) {
          const m = (cErr && cErr.message) || "";
          if (/registered|already|exists|duplicate/i.test(m)) {
            return json({ error: "That email already has an account. Use 'Add existing' to add them to an org." }, 409);
          }
          return json({ error: m || "Could not create the account." }, 500);
        }
        return json({ ok: true, id: created.user.id, invited: inviteEmail, role, org_id: org, name: fullName, temp_password: tempPw });
      }

      // -- UPDATE an existing user (role / caps / org) ------------------------
      const userId = (body.user_id || "").toString();
      if (!userId) return json({ error: "Provide user_id or invite_email." }, 400);

      const { data: tgt, error: tgtErr } = await admin.auth.admin.getUserById(userId);
      if (tgtErr || !tgt || !tgt.user) return json({ error: "User not found." }, 404);
      if (!inScope(tgt.user)) return json({ error: "That user isn't in your organization." }, 403);

      const meta = {};
      if (body.role !== undefined) {
        const newRole = body.role.toString();
        if (!ROLES.includes(newRole)) return json({ error: "Invalid role." }, 400);
        if (callerIsManager && !MANAGER_ASSIGNABLE.includes(newRole)) {
          return json({ error: "You can't grant that role." }, 403);
        }
        meta.role = newRole;
      }
      if (body.caps !== undefined) {
        if (!Array.isArray(body.caps)) return json({ error: "caps must be an array." }, 400);
        meta.caps = body.caps;
      }
      // org: only a super admin may (re)assign; a manager may never move a user
      if (body.org_id !== undefined) {
        if (!callerIsSuper) return json({ error: "Only a super admin can change a user's organization." }, 403);
        meta.org_id = (body.org_id || "").toString().trim();
      } else if (callerIsManager) {
        meta.org_id = callerOrg;   // defensive: pin org on every manager write
      }
      if (Object.keys(meta).length === 0) {
        return json({ error: "Provide a role, caps and/or org_id to update." }, 400);
      }
      const { data: upd, error: updErr } = await admin.auth.admin.updateUserById(userId, { app_metadata: meta });
      if (updErr) return json({ error: updErr.message }, 500);
      return json({ ok: true, id: upd.user.id, ...meta });
    }

    return json({ error: "Method not allowed." }, 405);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});
