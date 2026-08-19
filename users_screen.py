"""users_screen.py — Admin-only "Users" screen for QA Studio.

Lists every account and lets an Admin manage access with fine granularity:
  • set a role preset (Viewer / Member / Admin), and
  • toggle individual capabilities — which nav tabs a user can OPEN and which
    actions they can DO — per user.

All privileged work happens server-side in the 'admin-users' Supabase Edge
Function (it holds the service_role key). See ADMIN_USERS_SETUP.md to deploy it.
"""
import threading
import re
import csv
import os
from datetime import datetime, timedelta, timezone

import flet as ft
import theme as T
import auth_supabase as auth
import platform_caps
import strings
from ui import hover_field

_ROLES = ["Viewer", "Member", "Admin"]
_PAGE = 25   # users per page

# Client-side email shape check so an obviously-bad address is flagged inline
# (under the field) instead of round-tripping and returning as a full-width
# banner over the whole list.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _fmt_last(ts):
    """Format a Supabase ISO timestamp as local 'YYYY-MM-DD HH:MM' (date + time)."""
    if not ts:
        return strings.t("users_never")
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        try:
            dt = dt.astimezone()          # show in the viewer's local time
        except Exception:
            pass
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)[:16].replace("T", " ") or "—"


def _init(app):
    for k, v in (("_users_list", None), ("_users_loading", False),
                 ("_users_msg", None), ("_users_busy", None),
                 ("_users_expanded", set()), ("_users_invite_err", None),
                 ("_users_invite_vals", {}), ("_users_invite_err_name", False),
                 ("_users_invite_err_email", False),
                 ("_users_search", ""), ("_users_page", 0),
                 ("_users_org_filter", ""), ("_users_status_filter", ""),
                 ("_users_tenant_overviews", {}), ("_users_tenant_loading", set()),
                 ("_users_invite_open", False), ("_users_list_uid", None)):
        if not hasattr(app, k):
            setattr(app, k, v)


def _refresh_users_part(app, part="all"):
    """Refresh a mounted Users section without replacing the desktop rail.

    The first Users visit starts two background requests: the authorized user
    directory and the organization directory used by its filters.  Rebuilding
    the full shell when either returns used to detach the rail twice and reset
    its native Flet scroll offset.  The screen registers a narrow refresher
    once its controls are mounted; the fallback is only for the short window
    before that happens and still preserves the rail.
    """
    if getattr(app, "active", None) != "users":
        return

    def _go():
        callback = getattr(app, "_users_refresh_parts", None)
        if callable(callback):
            callback(part)
        else:
            app.render(preserve_rail=True)
    app.ui_safe(_go)


def _load(app, force=False):
    if app._users_loading:
        return
    # Key the cached list to the signed-in user. If a DIFFERENT user is now
    # signed in (e.g. a super admin fetched the full cross-org list, then
    # signed out and a scoped org manager signed in), the previous user's
    # cached rows must NOT be shown — drop them and refetch under the new
    # identity so the server re-scopes the result. Fixes a stale cross-user
    # (and cross-org) list surviving a logout/login within the same process.
    cur_uid = (getattr(app, "user", None) or {}).get("id")
    stale_user = getattr(app, "_users_list_uid", None) != cur_uid
    if app._users_list is not None and not force and not stale_user:
        return
    if stale_user:
        app._users_list = None
        app._users_expanded = set()
        app._users_page = 0
        app._users_search = ""
        app._users_invite_open = False
    app._users_loading = True
    app._users_msg = None

    def _work():
        ok, res = auth.admin_list_users()
        app._users_loading = False
        app._users_list_uid = cur_uid
        if ok:
            app._users_list = res
            # Fetch project/team membership once per visible organization. This
            # is intentionally background work: rendering the Users page must
            # not block on one tenant's administrative API response.
            overviews = {}
            for oid in sorted({str(u.get("org_id") or "") for u in res if u.get("org_id")}):
                got, data = auth.get_tenant_overview(oid)
                if got:
                    overviews[oid] = data
            app._users_tenant_overviews = overviews
            app._users_msg = None
        else:
            app._users_list = []
            app._users_msg = ("err", res)
        _refresh_users_part(app, "directory")
    threading.Thread(target=_work, daemon=True).start()


def screen(app):
    _init(app)
    from main import card, sec_head, ghost_btn, green_btn

    me = getattr(app, "user", None)
    if not auth.can_manage_users(me):
        body = ft.Column([card(ft.Column([
            ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, color=T.INK_3, size=20),
                    ft.Text(strings.t("users_admins_only"), size=16, weight=ft.FontWeight.W_800, color=T.INK)],
                   spacing=10),
            ft.Container(height=6),
            ft.Text(strings.t("users_admins_only_body"), size=12.5,
                    color=T.INK_3, no_wrap=False),
        ], spacing=2))], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        return app.shell(strings.t("users_title"), strings.t("users_subtitle_short"), body)

    _load(app)

    # Which roles this caller may assign. A super admin can set anyone to any
    # role; an org manager may only set OrgManager/Member/Viewer (never Super
    # Admin) — the Edge Function enforces this too, this just scopes the UI.
    _is_super = auth.is_super_admin(me)
    caller_roles = (["Viewer", "Member", "OrgManager", "SuperAdmin"] if _is_super
                    else ["Viewer", "Member", "OrgManager"])

    # Organization directory (id -> name) for the dropdowns + display, from the
    # shared cache the Organizations screen also fills.
    try:
        import orgs_screen as _orgs
        # force a (re)fetch while the directory is still empty so a transient
        # empty/None self-heals; _load self-inits, guards against concurrent
        # fetches, and re-renders this screen on completion.
        _orgs._load(app, force=(not getattr(app, "_orgs_list", None)))
    except Exception:
        pass
    _orgs_dir = app._orgs_list if isinstance(getattr(app, "_orgs_list", None), list) else []
    _org_name = {str(o.get("id")): (o.get("name") or str(o.get("id"))) for o in _orgs_dir}
    _org_options = [ft.DropdownOption(key=str(o.get("id")), text=(o.get("name") or str(o.get("id"))))
                    for o in _orgs_dir if o.get("id") is not None]

    def _refresh_tenant_memberships(org_id):
        """Fetch the current project/team assignments for one organization.

        Membership can be changed by another administrator or another desktop
        session after the Users directory was first loaded.  The former
        once-per-screen cache then showed the misleading "No configured items"
        state even though the database held valid assignments.  Refresh only the
        expanded user's organization and repaint only this list when it lands.
        """
        oid = str(org_id or "")
        if not oid:
            return
        loading = getattr(app, "_users_tenant_loading", set())
        if oid in loading:
            return
        loading.add(oid)
        app._users_tenant_loading = loading

        def _work():
            ok, data = auth.get_tenant_overview(oid)
            loading_now = getattr(app, "_users_tenant_loading", set())
            loading_now.discard(oid)
            app._users_tenant_loading = loading_now
            if ok and isinstance(data, dict):
                cache = getattr(app, "_users_tenant_overviews", {}) or {}
                cache[oid] = data
                app._users_tenant_overviews = cache
            _refresh_users_part(app, "directory")

        threading.Thread(target=_work, daemon=True).start()

    def _toggle_expand(uid):
        s = app._users_expanded
        if uid in s:
            s.discard(uid)
        else:
            s.add(uid)
            user = next((row for row in (app._users_list or [])
                         if str(row.get("id") or "") == str(uid)), {})
            _refresh_tenant_memberships(user.get("org_id") if isinstance(user, dict) else "")
        # Repaint only the list so opening/closing a permission drawer doesn't
        # full-render the page (header, nav, scroll all stay put).
        try:
            _refresh_list()
        except Exception:
            app.ui_safe(lambda: app.render(preserve_rail=True))

    def _save_inline(uid, fn, refresh_users=True, repaint_busy=True):
        """Run an admin mutation but repaint ONLY the user list (see
        _refresh_list) instead of the whole page — so a role / permission / org
        change doesn't flash the header, nav or scroll position and any open
        permission panel stays put. Errors surface as a toast, never as a banner
        that wipes out the list."""
        app._users_busy = uid
        # Membership checkboxes already disable the clicked control directly.
        # Avoid rebuilding even the list while the request is in flight: this
        # preserves the outer page scroll position on Flet Windows.
        if repaint_busy:
            try:
                _refresh_list()
            except Exception:
                app.ui_safe(lambda: app.render(preserve_rail=True))

        def _work():
            ok, msg = fn()
            app._users_busy = None
            if not ok:
                try:
                    app._err(msg)
                except Exception:
                    pass
            else:
                try:
                    app._toast(msg)
                except Exception:
                    pass
            # A membership update changes only the cached tenant overview,
            # not the user directory. Do not re-fetch the directory for it;
            # that was the first of two full page renders after a checkbox
            # click. Role, lifecycle, organization and capability writes keep
            # their existing fresh-directory behavior.
            if refresh_users:
                ok2, res = auth.admin_list_users()
                if ok2:
                    app._users_list = res
                    if app.user:
                        for u in res:
                            if u.get("id") == app.user.get("id"):
                                app.user["role"] = u.get("role")
                                app.user["caps"] = u.get("caps")
                                # Keep the always-visible account chip aligned
                                # with the same managed display name shown in
                                # this editor.  The auth session's user object
                                # otherwise stays stale until its next refresh.
                                app.user["name"] = (u.get("name")
                                                    or u.get("email")
                                                    or app.user.get("email", ""))
                        try:
                            app._refresh_account_identity()
                        except Exception:
                            pass
            if getattr(app, "active", None) == "users":
                app.ui_safe(_refresh_list)
        threading.Thread(target=_work, daemon=True).start()

    def _role_chip(uid, current, busy):
        if current in auth.SUPER_ROLES:
            current = "SuperAdmin"          # show legacy "Admin" as Super Admin
        def chip(role):
            sel = (current == role)
            return ft.Container(
                ft.Text(strings.t("role_" + role.lower()), size=12, weight=ft.FontWeight.W_700,
                        color=(T.VIOLET_INK if sel else T.INK_2)),
                height=30, alignment=ft.Alignment.CENTER,
                padding=ft.Padding.symmetric(horizontal=14),
                bgcolor=(T.VIOLET_SOFT if sel else None), border_radius=T.R_SM,
                border=ft.Border.all(1, T.VIOLET if sel else ft.Colors.TRANSPARENT),
                on_click=(None if (sel or busy)
                          else (lambda e, r=role: _set_role(uid, r))))
        return ft.Container(
            ft.Row([chip(r) for r in caller_roles], spacing=4, tight=True),
            padding=4, bgcolor=T.CARD_2, border_radius=T.R,
            border=ft.Border.all(1, T.BORDER))

    def _set_role(uid, role):
        is_self = bool(me and me.get("id") == uid)
        if is_self and role not in auth.SUPER_ROLES:
            app._confirm(strings.t("users_confirm_role_title"),
                         strings.t("users_confirm_role_body"),
                         lambda: _save_inline(uid, lambda: auth.admin_set_role(uid, role)),
                         yes_label=strings.t("users_confirm_role_yes"))
        else:
            _save_inline(uid, lambda: auth.admin_set_role(uid, role))

    def _revoke(uid, email):
        is_self = bool(me and me.get("id") == uid)
        msg = strings.t("users_revoke_msg", email=email)
        if is_self:
            msg = strings.t("users_revoke_self_prefix") + msg
        app._confirm(strings.t("users_revoke_title"), msg,
                     lambda: _save_inline(uid, lambda: auth.admin_revoke_access(uid)),
                     yes_label=strings.t("users_revoke_yes"), danger=True)

    def _lifecycle(uid, email, action):
        labels = {
            "suspend": ("Suspend account", "Suspend and sign out this user from every device?", True),
            "reactivate": ("Reactivate account", "Restore this user's ability to sign in?", False),
            "force_signout": ("Sign out everywhere", "End this user's active sessions on every device?", True),
        }
        title, message, danger = labels[action]
        def _go():
            def _call():
                ok, res = auth.admin_set_user_lifecycle(uid, action)
                if ok and action == "force_signout":
                    return True, f"Signed {email} out from all devices."
                return (True, f"{title} completed.") if ok else (False, res)
            _save_inline(uid, _call)
        app._confirm(title, message, _go, yes_label=title, danger=danger)

    def _recovery(uid, email):
        def _go():
            def _call():
                ok, res = auth.admin_set_user_lifecycle(uid, "recovery_email")
                temp = (res or {}).get("temp_password") if isinstance(res, dict) else ""
                if ok and temp:
                    # The server invalidated old sessions and created a
                    # temporary password. Deliver it through the same org
                    # sender used for invitations — never a browser reset URL.
                    sent, send_err = _email_temp_password(email, temp, recovery=True)
                    if sent:
                        return True, strings.t("users_recovery_credentials_sent", email=email)
                    app.ui_safe(lambda: _show_temp_pw_dialog(email, temp, send_err, recovery=True))
                    return True, strings.t("users_recovery_credentials_ready")
                return False, res or "Recovery email could not be sent."
            _save_inline(uid, _call)
        app._confirm(strings.t("users_recovery_email_title"),
                     strings.t("users_recovery_email_body", email=email),
                     _go, yes_label=strings.t("users_recovery_email"))

    def _perm_chip(uid, key, label, granted, busy):
        def _do(e):
            eff = set(auth.caps_for({"role": _cur_role[0], "caps": _cur_caps[0]}))
            eff.discard(key) if granted else eff.add(key)
            _save_inline(uid, lambda: auth.admin_set_caps(uid, sorted(eff)))
        return ft.Container(
            ft.Row([ft.Icon(ft.Icons.CHECK if granted else ft.Icons.ADD, size=13,
                            color=("#FFFFFF" if granted else T.INK_3)),
                    ft.Text(strings.t("cap_" + key.replace(".", "_")), size=11.5, weight=ft.FontWeight.W_600,
                            color=("#FFFFFF" if granted else T.INK_2), no_wrap=False)],
                   spacing=6, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(vertical=6, horizontal=10),
            bgcolor=(T.VIOLET if granted else T.CARD_2), border_radius=999,
            border=ft.Border.all(1, T.VIOLET if granted else T.BORDER),
            on_click=(None if busy else _do))

    # captured per-row for the perm chip closure
    _cur_role = [None]
    _cur_caps = [None]

    def _perm_panel(uid, role, caps, busy, org="", name="", status="active"):
        _cur_role[0] = role
        _cur_caps[0] = caps
        eff = auth.caps_for({"role": role, "caps": caps})
        # Display-name editor — available to any admin who can manage this user.
        _nf = ft.TextField(
            value=name or "", hint_text=strings.t("users_name_hint"), dense=True,
            text_size=12.5, border_color=T.BORDER, focused_border_color=T.VIOLET,
            border_radius=T.R_SM, expand=True,
            content_padding=ft.Padding.symmetric(vertical=10, horizontal=12))
        _name_editor = [
            ft.Text(strings.t("users_name_section"), size=11,
                    weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=6),
            ft.Row([hover_field(_nf), ghost_btn(strings.t("users_name_set"),
                    on_click=(None if busy else (lambda e, w=_nf:
                        _save_inline(uid, lambda: auth.admin_set_name(uid, (w.value or "").strip())))))],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=12),
        ]
        nav = [(k, lbl) for k, lbl, kind in auth.CATALOG if kind == "nav"]
        act = [(k, lbl) for k, lbl, kind in auth.CATALOG if kind == "act"]

        def group(title, items):
            chips = [_perm_chip(uid, k, lbl, (k in eff), busy) for k, lbl in items]
            return ft.Column([
                ft.Text(title, size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
                ft.Container(height=6),
                ft.Row(chips, wrap=True, spacing=8, run_spacing=8),
            ], spacing=0)

        custom = isinstance(caps, list)
        _org_editor = []
        if _is_super:
            _of = ft.Dropdown(value=(org or None), options=_org_options,
                              hint_text=strings.t("users_org_pick"),
                              dense=True, text_size=12.5, border_color=T.BORDER,
                              focused_border_color=T.VIOLET, border_radius=T.R_SM,
                              expand=True)
            _org_editor = [
                ft.Text(strings.t("users_org_section"), size=11,
                        weight=ft.FontWeight.BOLD, color=T.INK_3),
                ft.Container(height=6),
                ft.Row([_of, ghost_btn(strings.t("users_org_set"),
                        on_click=(None if busy else (lambda e, w=_of:
                            _save_inline(uid, lambda: auth.admin_set_org(uid, (w.value or "").strip())))))],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=12),
            ]
        lifecycle_panel = [
            ft.Text(strings.t("users_lifecycle"), size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=6),
            ft.Row([
                ghost_btn(strings.t("users_reactivate") if status in ("suspended", "expired") else strings.t("users_suspend"),
                          icon=(ft.Icons.PLAY_CIRCLE_OUTLINE if status in ("suspended", "expired") else ft.Icons.PAUSE_CIRCLE_OUTLINE),
                          on_click=(None if busy else (lambda e: _lifecycle(uid, name or uid, "reactivate" if status in ("suspended", "expired") else "suspend"))),
                          tooltip=strings.t("users_tip_reactivate" if status in ("suspended", "expired") else "users_tip_suspend")),
                ghost_btn(strings.t("users_signout_all"), icon=ft.Icons.LOGOUT,
                          on_click=(None if busy else (lambda e: _lifecycle(uid, name or uid, "force_signout"))),
                          tooltip=strings.t("users_tip_signout_all")),
                ghost_btn(strings.t("users_recovery_email"), icon=ft.Icons.KEY,
                          on_click=(None if busy else (lambda e: _recovery(uid, name or uid))),
                          tooltip=strings.t("users_tip_recovery_email")),
            ], spacing=8, wrap=True),
            ft.Container(height=12),
        ]
        # Memberships are edited here (rather than trusting a project id sent
        # by the client at run time). The tenant-admin function replaces the
        # server-side membership set only after checking every user belongs to
        # the same organization.
        overview = (getattr(app, "_users_tenant_overviews", {}) or {}).get(str(org or ""), {})
        project_memberships = overview.get("project_memberships", []) if isinstance(overview, dict) else []
        team_memberships = overview.get("team_memberships", []) if isinstance(overview, dict) else []

        def _membership_panel(kind, rows, memberships, title):
            controls = [ft.Text(title, size=11, weight=ft.FontWeight.BOLD, color=T.INK_3), ft.Container(height=6)]
            for item in rows or []:
                item_id = str(item.get("id") or "")
                existing = [m for m in memberships if str(m.get(f"{kind}_id") or "") == item_id]
                assigned = any(str(m.get("user_id") or "") == uid for m in existing)
                def _toggle(e, iid=item_id, now=assigned, members=existing):
                    # Ignore a second click while this user's first membership
                    # write is in flight. This prevents stale checkbox values
                    # from replacing a just-saved server-side membership set.
                    if getattr(app, "_users_busy", None) == uid:
                        return
                    try:
                        e.control.disabled = True
                        e.control.update()
                    except Exception:
                        pass
                    next_members = [dict(m) for m in members if str(m.get("user_id") or "") != uid]
                    if not now:
                        next_members.append({"user_id": uid, "access_level": "contributor"} if kind == "project" else {"user_id": uid, "role": "member"})
                    action = "set_project_memberships" if kind == "project" else "set_team_memberships"
                    key = "project_id" if kind == "project" else "team_id"
                    def _call():
                        ok, result = auth.admin_tenant_action(action, org_id=org, **{key: iid, "members": next_members})
                        if ok:
                            # Patch just this project/team in the cached
                            # overview. The final _refresh_list() redraws the
                            # open card in place, without app.render() or a
                            # scroll restore race.
                            cache = getattr(app, "_users_tenant_overviews", {}) or {}
                            current = cache.get(str(org or ""))
                            member_key = f"{kind}_memberships"
                            if isinstance(current, dict):
                                all_members = current.get(member_key, [])
                                retained = [dict(m) for m in all_members
                                            if str(m.get(key) or "") != iid]
                                retained.extend([{**dict(m), key: iid} for m in next_members])
                                current[member_key] = retained
                            return True, f"{title} updated."
                        return False, result
                    _save_inline(uid, _call, refresh_users=False, repaint_busy=False)
                item_name = str(item.get("name") or item.get("external_key") or item_id)
                controls.append(ft.Checkbox(label=item_name, value=assigned,
                                            on_change=(None if busy else _toggle),
                                            tooltip=strings.t("users_tip_project_membership" if kind == "project" else "users_tip_team_membership", name=item_name)))
            if len(controls) == 2:
                controls.append(ft.Text(strings.t("users_no_configured"), size=11, color=T.INK_3))
            controls.append(ft.Container(height=10))
            return controls

        if str(org or "") in getattr(app, "_users_tenant_loading", set()):
            membership_panel = [ft.Row([
                ft.ProgressRing(width=14, height=14, stroke_width=2, color=T.VIOLET),
                ft.Text(strings.t("users_memberships_loading"), size=11, color=T.INK_3),
            ], spacing=8), ft.Container(height=10)]
        else:
            membership_panel = _membership_panel("project", overview.get("projects", []) if isinstance(overview, dict) else [], project_memberships, strings.t("users_project_membership"))
            membership_panel += _membership_panel("team", overview.get("teams", []) if isinstance(overview, dict) else [], team_memberships, strings.t("users_team_membership"))
        return ft.Container(
            ft.Column(_name_editor + _org_editor + lifecycle_panel + membership_panel + [
                ft.Row([
                    ft.Text(strings.t("users_perms_title"), size=12,
                            weight=ft.FontWeight.W_800, color=T.INK),
                    ft.Container(expand=True),
                    (ft.Container(ft.Text(strings.t("users_custom"), size=10, weight=ft.FontWeight.BOLD,
                                          color=T.AMBER),
                                  padding=ft.Padding.symmetric(vertical=1, horizontal=7),
                                  bgcolor=ft.Colors.with_opacity(0.14, T.AMBER),
                                  border_radius=999)
                     if custom else ft.Container(width=0)),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=10),
                group(strings.t("users_group_nav"), nav),
                ft.Container(height=12),
                group(strings.t("users_group_act"), act),
            ], spacing=0),
            padding=14, margin=ft.Margin.only(top=10), bgcolor=T.CARD_2,
            border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    def _row(u):
        uid = u.get("id")
        email = u.get("email") or strings.t("users_no_email")
        role = u.get("role") or "Viewer"
        caps = u.get("caps")
        revoked = isinstance(caps, list) and len(caps) == 0   # custom caps, none granted
        confirmed = u.get("confirmed")
        status = u.get("status") or "active"
        last = _fmt_last(u.get("last_sign_in_at"))
        is_self = bool(me and me.get("id") == uid)
        busy = (app._users_busy == uid)
        expanded = uid in app._users_expanded

        avatar = ft.Container(ft.Text((email[:1] or "?").upper(), size=13,
                                      weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                              width=34, height=34, bgcolor=T.VIOLET, border_radius=17,
                              alignment=ft.Alignment.CENTER)
        identity = ft.Column([
            ft.Row([
                # expand=True: without it, a Row's child Text sizes to its
                # own unwrapped intrinsic width regardless of no_wrap=False
                # (that flag only permits wrapping, it doesn't by itself
                # bound the width that triggers it) — the long "✓ confirmed
                # · last sign-in …" line below was overflowing straight past
                # the phone's right edge and getting visually cut off rather
                # than wrapping, reported live as "last login time not
                # displays". expand=True gives both lines a real width to
                # wrap against; harmless on desktop, where there's already
                # room and nothing changes visually.
                ft.Text(email, size=13.5, weight=ft.FontWeight.W_700, color=T.INK,
                        no_wrap=False, expand=True),
                (ft.Container(ft.Text(strings.t("users_you"), size=10, weight=ft.FontWeight.BOLD,
                                      color=T.VIOLET_INK),
                              padding=ft.Padding.symmetric(vertical=1, horizontal=7),
                              bgcolor=T.VIOLET_SOFT, border_radius=999)
                 if is_self else ft.Container(width=0)),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([
                ft.Text((strings.t("users_confirmed") if confirmed else strings.t("users_not_confirmed"))
                        + strings.t("users_last_signin", ts=last),
                        size=11, color=(T.GREEN if confirmed else T.AMBER),
                        weight=ft.FontWeight.BOLD, no_wrap=False, expand=True),
                (ft.Container(ft.Text(strings.t("users_access_revoked"), size=9.5,
                                      weight=ft.FontWeight.BOLD, color=T.RED),
                              padding=ft.Padding.symmetric(vertical=1, horizontal=7),
                              bgcolor=ft.Colors.with_opacity(0.12, T.RED),
                              border_radius=999)
                 if revoked else ft.Container(width=0)),
                (ft.Container(ft.Text(status.upper(), size=9.5, weight=ft.FontWeight.BOLD,
                                      color=(T.RED if status in ("suspended", "expired") else T.GREEN)),
                              padding=ft.Padding.symmetric(vertical=1, horizontal=7),
                              bgcolor=ft.Colors.with_opacity(0.12, T.RED if status in ("suspended", "expired") else T.GREEN),
                              border_radius=999)
                 if status != "active" else ft.Container(width=0)),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([ft.Icon(ft.Icons.BUSINESS_OUTLINED, size=12, color=T.INK_3),
                    ft.Text(strings.t("users_org_label") + ": "
                            + (_org_name.get(str(u.get("org_id") or "")) or u.get("org_id")
                               or strings.t("users_org_none")),
                            size=10.5, color=T.INK_3, weight=ft.FontWeight.BOLD,
                            no_wrap=False, expand=True)],
                   spacing=5, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=2, expand=True)
        role_or_spinner = (ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2.4,
                                                    color=T.VIOLET)], tight=True)
                           if busy else _role_chip(uid, (None if revoked else role), busy))
        expand_btn = ft.Container(
            ft.Icon(ft.Icons.EXPAND_LESS if expanded else ft.Icons.TUNE,
                    size=18, color=T.INK_3),
            on_click=lambda e, x=uid: _toggle_expand(x), ink=True, border_radius=8,
            padding=8, tooltip=strings.t("users_tip_perms"))
        revoke_btn = ft.Container(
            ft.Icon(ft.Icons.REMOVE_CIRCLE_OUTLINE, size=18,
                    color=(T.INK_3 if (busy or revoked) else T.RED)),
            on_click=(None if (busy or revoked)
                      else (lambda e, x=uid, em=email: _revoke(x, em))),
            ink=True, border_radius=8, padding=8,
            tooltip=(strings.t("users_tip_revoked")
                     if revoked else strings.t("users_revoke_yes")))

        if platform_caps.is_mobile():
            # Desktop's single Row packs avatar + email/status(expand) + the
            # 3-pill role chip + 2 icon buttons side by side — on a ~390px
            # phone the FIXED-width items alone (avatar ~34 + role chip
            # ~190 + 2 icons ~34 each + spacing) already exceed the screen,
            # so the expand=True email column got squeezed to nothing
            # (invisible) and, with no wrap/scroll, the role chip and both
            # action buttons (expand-permissions, revoke/"ban") rendered
            # past the right edge entirely — reported live as "doesn't show
            # the ban action, email, and user". Stacking role+actions on ONE
            # shared row (the first fix) still wasn't enough on its own: the
            # 3-pill role chip alone (~260-270px incl. its own padding/
            # border) plus both icon buttons (~34px each) plus spacing still
            # summed past a ~390px phone's usable width after card/row
            # padding, so the revoke/"ban" button — the LAST item in that
            # row — kept getting clipped off the visible edge, reported live
            # again as the ban button still missing even after the row
            # stopped overlapping the email. Give the role chip its OWN
            # full-width row so it never has to compete with the buttons
            # for space; actions get their own row underneath, pushed right.
            head = ft.Column([
                ft.Row([avatar, identity], spacing=12,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=8),
                role_or_spinner,
                ft.Container(height=8),
                ft.Row([ft.Container(expand=True), expand_btn, revoke_btn],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=0)
        else:
            head = ft.Row([avatar, identity, role_or_spinner, expand_btn, revoke_btn],
                          spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        children = [head]
        if expanded:
            children.append(_perm_panel(uid, role, caps, busy,
                                        org=u.get("org_id") or "",
                                        name=u.get("name") or u.get("email") or "",
                                        status=status))
        return ft.Container(
            ft.Column(children, spacing=0),
            padding=ft.Padding.symmetric(vertical=12, horizontal=14),
            border=ft.Border.all(1, T.BORDER), border_radius=T.R, bgcolor=T.CARD)

    # ── filter (search) + paginate (25/page) ──
    def _compute():
        allu = app._users_list or []
        q = (app._users_search or "").strip().lower()
        org_filter = getattr(app, "_users_org_filter", "")
        status_filter = getattr(app, "_users_status_filter", "")
        inactive_before = datetime.now(timezone.utc) - timedelta(days=30)
        filt = []
        for u in allu:
            if q and q not in (u.get("email") or "").lower() and q not in (u.get("role") or "").lower():
                continue
            if org_filter and str(u.get("org_id") or "") != org_filter:
                continue
            status = str(u.get("status") or "active")
            if status_filter in ("active", "suspended", "expired") and status != status_filter:
                continue
            if status_filter == "never" and u.get("last_sign_in_at"):
                continue
            if status_filter == "inactive":
                try:
                    stamp = datetime.fromisoformat(str(u.get("last_sign_in_at") or "").replace("Z", "+00:00"))
                    if stamp > inactive_before:
                        continue
                except Exception:
                    pass
            filt.append(u)
        tot = max(1, -(-len(filt) // _PAGE))
        p = max(0, min(getattr(app, "_users_page", 0), tot - 1))
        app._users_page = p
        return filt, tot, p, filt[p * _PAGE:(p + 1) * _PAGE]

    def _list_controls():
        if app._users_loading and app._users_list is None:
            return [ft.Container(ft.Row([
                ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=T.VIOLET),
                ft.Text(strings.t("users_loading"), size=12.5, color=T.INK_3)], spacing=10),
                padding=14)]
        if app._users_msg and app._users_msg[0] == "err":
            return [ft.Container(ft.Row([
                ft.Icon(ft.Icons.ERROR_OUTLINE, color=T.RED, size=18),
                ft.Text(app._users_msg[1], size=12.5, color=T.RED, no_wrap=False,
                        expand=True)], spacing=10),
                padding=ft.Padding.symmetric(vertical=12, horizontal=14),
                bgcolor=ft.Colors.with_opacity(0.10, T.RED), border_radius=T.R,
                border=ft.Border.all(1, ft.Colors.with_opacity(0.4, T.RED)))]
        if not (app._users_list or []):
            return [ft.Container(ft.Text(strings.t("users_none"), size=12.5, color=T.INK_3),
                                 padding=14)]
        filt, tot, p, page_u = _compute()
        if not filt:
            return [ft.Container(ft.Row([
                ft.Icon(ft.Icons.SEARCH_OFF, size=18, color=T.INK_3),
                ft.Text(strings.t("users_none_match"), size=12.5, color=T.INK_3)],
                spacing=10), padding=14)]
        out = []
        for u in page_u:
            out.append(_row(u))
            out.append(ft.Container(height=8))
        return out

    def _pager_controls():
        if not (app._users_list or []):
            return ft.Container()
        filt, tot, p, _ = _compute()
        if tot <= 1:
            return ft.Container()
        return ft.Row([
            ghost_btn(strings.t("users_prev"), on_click=(None if p == 0 else (lambda e: _goto(p - 1)))),
            ft.Text(strings.t("users_pager", page=p + 1, total=tot, count=len(filt)), size=12,
                    color=T.INK_3, weight=ft.FontWeight.W_600),
            ghost_btn(strings.t("users_next"), on_click=(None if p >= tot - 1
                                          else (lambda e: _goto(p + 1)))),
        ], alignment=ft.MainAxisAlignment.CENTER, spacing=16)

    # The list sizes to its content and scrolls with the page's own scroller
    # (the card body is wrapped in Column(scroll=AUTO) — see body). A fixed-
    # height inner ListView fought that outer scroll and clipped the last rows
    # and the pager off the bottom of the screen.
    list_view = ft.Column(controls=_list_controls(), spacing=0)
    list_holder = list_view
    pager_holder = ft.Container(_pager_controls(), margin=ft.Margin.only(top=12),
                                alignment=ft.Alignment.CENTER)

    def _refresh_list():
        try:
            list_view.controls = _list_controls()
            list_view.update()
            pager_holder.content = _pager_controls()
            pager_holder.update()
        except Exception:
            app.ui_safe(lambda: app.render(preserve_rail=True))

    def _goto(p):
        app._users_page = p
        _refresh_list()

    def _on_search(e):
        app._users_search = e.control.value or ""
        app._users_page = 0
        _refresh_list()       # in-place so the search box keeps focus while typing

    def _set_filter(which):
        def _change(e):
            setattr(app, which, str(e.control.value or ""))
            app._users_page = 0
            _refresh_list()
        return _change

    def _export_csv(e=None):
        rows, _, _, _ = _compute()
        if not rows:
            app._toast("No users match the current filters.")
            return
        def _work():
            try:
                out_dir = os.path.join(platform_caps.export_base_dir(), "QA Studio", "User Exports")
                os.makedirs(out_dir, exist_ok=True)
                path = os.path.join(out_dir, f"Users_{datetime.now():%Y%m%d-%H%M%S}.csv")
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=["email", "name", "organization", "role", "status", "confirmed", "last_sign_in_at", "access_expires_at", "created_at"])
                    writer.writeheader()
                    for u in rows:
                        writer.writerow({k: u.get(k, "") for k in writer.fieldnames} | {"organization": u.get("org_id", "")})
                app.ui_safe(lambda: app._toast(f"User export saved: {path}"))
                if not platform_caps.is_mobile():
                    platform_caps.open_folder(out_dir)
            except Exception as ex:
                app.ui_safe(lambda: app._err(ex))
        threading.Thread(target=_work, daemon=True).start()

    def _bulk_signout(e=None):
        rows, _, _, _ = _compute()
        target_ids = [str(u.get("id") or "") for u in rows if u.get("id") and u.get("id") != (me or {}).get("id")]
        if not target_ids:
            app._toast("There are no other matching users to sign out.")
            return
        def _go():
            def _work():
                failures = 0
                for uid in target_ids:
                    ok, _ = auth.admin_set_user_lifecycle(uid, "force_signout")
                    failures += 0 if ok else 1
                app.ui_safe(lambda: app._toast(f"Signed out {len(target_ids) - failures} matching user(s)."))
                _load(app, force=True)
            threading.Thread(target=_work, daemon=True).start()
        app._confirm("Sign out filtered users", f"End active sessions for {len(target_ids)} matching user(s)? Your own session is excluded.", _go, yes_label="Sign out users", danger=True)

    search = ft.TextField(
        value=app._users_search or "", hint_text=strings.t("users_search_hint"),
        prefix_icon=ft.Icons.SEARCH, on_change=_on_search, text_size=13, dense=True,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=11, horizontal=12))
    org_filter = (ft.Dropdown(value=getattr(app, "_users_org_filter", "") or "", dense=True,
                              options=[ft.DropdownOption(key="", text=strings.t("users_all_orgs"))] + _org_options,
                              on_select=_set_filter("_users_org_filter"), text_size=12.5,
                              border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
                              tooltip=strings.t("users_tip_org_filter"),
                              expand=True) if _is_super else ft.Container())
    status_filter = ft.Dropdown(value=getattr(app, "_users_status_filter", "") or "", dense=True,
                                options=[ft.DropdownOption(key="", text=strings.t("users_all_statuses")),
                                         ft.DropdownOption(key="active", text=strings.t("users_active")), ft.DropdownOption(key="suspended", text=strings.t("users_suspended")),
                                         ft.DropdownOption(key="expired", text=strings.t("users_expired")), ft.DropdownOption(key="never", text=strings.t("users_never_signin")),
                                         ft.DropdownOption(key="inactive", text=strings.t("users_inactive_30"))],
                                on_select=_set_filter("_users_status_filter"), text_size=12.5,
                                border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
                                tooltip=strings.t("users_tip_status_filter"),
                                expand=True)
    def _seat_counts_text():
        seat_counts = {}
        for user in (app._users_list or []):
            key = str(user.get("org_id") or "Unassigned")
            seat_counts[key] = seat_counts.get(key, 0) + 1
        return " · ".join(f"{_org_name.get(k, k)}: {v}" for k, v in sorted(seat_counts.items())) or "No seats assigned"

    seat_label = ft.Text(strings.t("users_seat_counts", counts=_seat_counts_text()),
                         size=10.5, color=T.INK_3, expand=True)

    # ── Invite a new user into an org ──────────────────────────────────────
    def _toggle_invite(e=None):
        app._users_invite_open = not getattr(app, "_users_invite_open", False)
        app._users_invite_err = None
        app._users_invite_vals = {}
        app._users_invite_err_name = False
        app._users_invite_err_email = False
        app.ui_safe(lambda: app.render(preserve_rail=True))

    # Invite values + validation error live in app state so a validation
    # re-render never wipes what was typed and the message is guaranteed to
    # show. This Flet build's TextField has NO error_text kwarg (it raises
    # TypeError), so the field just goes red-bordered and the message renders as
    # a separate red line below the fields (app-standard border+note pattern).
    _iv = app._users_invite_vals if isinstance(getattr(app, "_users_invite_vals", None), dict) else {}
    _ierr = getattr(app, "_users_invite_err", None)
    _nerr = bool(getattr(app, "_users_invite_err_name", False))
    _eerr = bool(getattr(app, "_users_invite_err_email", False))
    _inv_name = ft.TextField(
        value=_iv.get("name", "") or "",
        hint_text=strings.t("login_full_name_hint") + " *", dense=True, text_size=13,
        border_color=(T.RED if _nerr else T.BORDER),
        focused_border_color=(T.RED if _nerr else T.VIOLET), border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=11, horizontal=12), expand=True)
    _inv_email = ft.TextField(
        value=_iv.get("email", "") or "",
        hint_text=strings.t("users_invite_email_hint") + " *", dense=True, text_size=13,
        border_color=(T.RED if _eerr else T.BORDER),
        focused_border_color=(T.RED if _eerr else T.VIOLET), border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=11, horizontal=12), expand=True)
    _inv_role = ft.Dropdown(
        value=_iv.get("role", "Viewer") or "Viewer", width=180, dense=True, text_size=13,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        options=[ft.DropdownOption(key=r, text=strings.t("role_" + r.lower()))
                 for r in caller_roles])
    _inv_org = (ft.Dropdown(
        value=(_iv.get("org") or None),
        hint_text=strings.t("users_invite_org_pick"), options=_org_options,
        dense=True, text_size=13, border_color=T.BORDER, focused_border_color=T.VIOLET,
        border_radius=T.R, expand=True)
        if _is_super else None)

    def _capture_invite():
        app._users_invite_vals = {
            "name": (_inv_name.value or "").strip(),
            "email": (_inv_email.value or "").strip(),
            "role": _inv_role.value or "Viewer",
            "org": (((_inv_org.value or "").strip() or None) if _inv_org is not None else None),
        }

    def _email_temp_password(to_email, temp, recovery=False):
        """Email the invitee their temp password via the org-shared Gmail sender
        (same pipeline as report emails). Returns (ok, error)."""
        try:
            import engine
            engine.ensure_sender_creds()
            prefix = "users_recovery" if recovery else "users_invite"
            html = (
                "<div style=\"font-family:Segoe UI,Arial,sans-serif;color:#1b1f3a\">"
                "<h2 style=\"margin:0 0 12px\">" + strings.t(prefix + "_email_heading") + "</h2>"
                "<p style=\"margin:0 0 10px\">" + strings.t(prefix + "_email_line1") + "</p>"
                "<p style=\"margin:0 0 14px\">" + strings.t(prefix + "_email_line2") + "</p>"
                "<table style=\"border-collapse:collapse;margin:0 0 14px\">"
                "<tr><td style=\"padding:4px 12px 4px 0;color:#6b7280\">"
                + strings.t(prefix + "_email_email_label") + "</td>"
                "<td style=\"padding:4px 0;font-weight:bold\">" + to_email + "</td></tr>"
                "<tr><td style=\"padding:4px 12px 4px 0;color:#6b7280\">"
                + strings.t(prefix + "_email_pw_label") + "</td>"
                "<td style=\"padding:4px 0;font-weight:bold;font-family:Consolas,monospace\">"
                + temp + "</td></tr>"
                "</table>"
                "<p style=\"margin:0;color:#6b7280;font-size:13px\">"
                + strings.t(prefix + "_email_line3") + "</p></div>"
            )
            ok, err = engine.send_report(to_email, strings.t(prefix + "_email_subject"), html)
            return bool(ok), (err or "")
        except Exception as ex:
            return False, str(ex)

    def _show_temp_pw_dialog(to_email, temp, err, recovery=False):
        """Fallback when the email couldn't be sent: show the temp password to
        the admin (with a Copy button) so they can relay it securely."""
        prefix = "users_recovery" if recovery else "users_invite"
        body = (strings.t(prefix + "_pw_intro", email=to_email, err=(err or "no email sender"))
                + "\n\n" + strings.t(prefix + "_pw_email_label") + ":  " + to_email
                + "\n" + strings.t(prefix + "_pw_label") + ":  " + temp)
        try:
            app._confirm(strings.t(prefix + "_pw_title"), body,
                         lambda: app._copy_text_to_clipboard(temp, strings.t(prefix + "_pw_copied")),
                         yes_label=strings.t(prefix + "_pw_copy"), danger=False,
                         icon=ft.Icons.VPN_KEY)
        except Exception:
            app.ui_safe(lambda: app.render(preserve_rail=True))

    def _run_invite(fn, email):
        # Persist typed values first, then validate — a re-render keeps them.
        # (Add-existing needs only a valid email; the full name is not required
        # here since the target already has an account.)
        _capture_invite()
        app._users_invite_err_name = False
        app._users_invite_err_email = (not _EMAIL_RE.match(email))
        if not _EMAIL_RE.match(email):
            app._users_invite_err = strings.t("users_invite_bad_email")
            app.ui_safe(lambda: app.render(preserve_rail=True))
            return
        app._users_invite_err = None
        app.ui_safe(lambda: app.render(preserve_rail=True))

        def _work():
            ok, msg = fn()
            if not ok:
                # Server rejection (already a member, other org, …) shows under
                # the email field too; panel stays open to fix and retry.
                app._users_invite_err = msg
                app._users_invite_err_email = True
                if getattr(app, "active", None) == "users":
                    app.ui_safe(lambda: app.render(preserve_rail=True))
                return
            app._users_invite_open = False
            app._users_invite_err = None
            app._users_invite_vals = {}
            try:
                app._toast(msg)
            except Exception:
                pass
            ok2, res = auth.admin_list_users()
            if ok2:
                app._users_list = res
            if getattr(app, "active", None) == "users":
                app.ui_safe(lambda: app.render(preserve_rail=True))
        threading.Thread(target=_work, daemon=True).start()

    def _do_invite(e=None):
        email = (_inv_email.value or "").strip()
        name = (_inv_name.value or "").strip()
        role = _inv_role.value or "Viewer"
        org = ((_inv_org.value or "").strip() or None) if _inv_org is not None else None
        _capture_invite()
        # Both the full name and a valid email are mandatory.
        app._users_invite_err_name = (not name)
        app._users_invite_err_email = (not _EMAIL_RE.match(email))
        if not name:
            app._users_invite_err = strings.t("users_invite_name_required")
            app.ui_safe(lambda: app.render(preserve_rail=True))
            return
        if not _EMAIL_RE.match(email):
            app._users_invite_err = strings.t("users_invite_bad_email")
            app.ui_safe(lambda: app.render(preserve_rail=True))
            return
        app._users_invite_err = None
        app._users_invite_err_name = False
        app._users_invite_err_email = False
        app.ui_safe(lambda: app.render(preserve_rail=True))

        def _work():
            ok, res = auth.admin_invite_user(email, role=role, org_id=org, name=name)
            if not ok:
                # Server rejection (already registered, other org, …) inline.
                app._users_invite_err = res if isinstance(res, str) else str(res)
                app._users_invite_err_email = True
                if getattr(app, "active", None) == "users":
                    app.ui_safe(lambda: app.render(preserve_rail=True))
                return
            temp = (res or {}).get("temp_password") or ""
            sent, send_err = _email_temp_password(email, temp)
            app._users_invite_open = False
            app._users_invite_err = None
            app._users_invite_vals = {}
            if sent:
                app.ui_safe(lambda: app._toast(strings.t("users_invite_sent", email=email)))
            else:
                # Couldn't email → show the credentials to the admin to relay.
                app.ui_safe(lambda: _show_temp_pw_dialog(email, temp, send_err))
            ok2, res2 = auth.admin_list_users()
            if ok2:
                app._users_list = res2
            if getattr(app, "active", None) == "users":
                app.ui_safe(lambda: app.render(preserve_rail=True))
        threading.Thread(target=_work, daemon=True).start()

    def _do_add_existing(e=None):
        email = (_inv_email.value or "").strip()
        role = _inv_role.value or "Member"
        org = ((_inv_org.value or "").strip() or None) if _inv_org is not None else None
        _run_invite(lambda: auth.admin_add_existing_user(email, role=role, org_id=org), email)

    _inv_fields = [hover_field(_inv_name), hover_field(_inv_email), _inv_role]
    if _inv_org is not None:
        _inv_fields.append(hover_field(_inv_org))
    invite_panel = (ft.Container(
        ft.Column([
            ft.Text(strings.t("users_invite_title"), size=12,
                    weight=ft.FontWeight.W_800, color=T.INK),
            ft.Container(height=4),
            ft.Text(strings.t("users_addexisting_hint"), size=11, color=T.INK_3,
                    weight=ft.FontWeight.W_500, no_wrap=False),
            ft.Container(height=8),
            ft.Column(_inv_fields, spacing=8),
            (ft.Container(
                ft.Row([ft.Icon(ft.Icons.ERROR_OUTLINE, size=15, color=T.RED),
                        ft.Text(_ierr, size=11, color=T.RED, weight=ft.FontWeight.W_600,
                                no_wrap=False, expand=True)], spacing=6,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                margin=ft.Margin.only(top=8))
             if _ierr else ft.Container(height=0)),
            ft.Container(height=10),
            ft.Row([ft.Container(expand=True),
                    ghost_btn(strings.t("users_invite_cancel"), on_click=_toggle_invite),
                    ghost_btn(strings.t("users_addexisting_btn"), icon=ft.Icons.GROUP_ADD,
                              on_click=_do_add_existing),
                    green_btn(strings.t("users_invite_send"), icon=ft.Icons.SEND,
                              on_click=_do_invite)], spacing=10),
        ], spacing=0),
        padding=14, margin=ft.Margin.only(bottom=12), bgcolor=T.CARD_2,
        border_radius=T.R, border=ft.Border.all(1, T.BORDER))
        if getattr(app, "_users_invite_open", False) else ft.Container())

    # Help line: inline the SAME icon the per-row button uses (ft.Icons.TUNE)
    # in place of the gear-glyph marker the translations carry, so the hint and
    # the actual button can never disagree.
    _hl = strings.t("users_help_line")
    if "\u2699" in _hl:
        _hb, _ha = _hl.split("\u2699", 1)
        help_line = ft.Row([
            ft.Text(_hb.rstrip(), size=12, color=T.INK_3, weight=ft.FontWeight.BOLD),
            ft.Icon(ft.Icons.TUNE, size=15, color=T.INK_3),
            ft.Text(_ha.lstrip(), size=12, color=T.INK_3, weight=ft.FontWeight.BOLD),
        ], spacing=4, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    else:
        help_line = ft.Text(_hl, size=12, color=T.INK_3, weight=ft.FontWeight.BOLD,
                            no_wrap=False)

    def _refresh_mounted(part="all"):
        """Update Users controls in place after either background directory returns."""
        if part in ("all", "organizations", "directory"):
            latest = (getattr(app, "_orgs_list", None)
                      if isinstance(getattr(app, "_orgs_list", None), list) else [])
            _org_name.clear()
            _org_name.update({str(o.get("id")): (o.get("name") or str(o.get("id")))
                              for o in latest if o.get("id") is not None})
            _org_options[:] = [ft.DropdownOption(key=str(o.get("id")),
                                                  text=(o.get("name") or str(o.get("id"))))
                               for o in latest if o.get("id") is not None]
            if _is_super:
                try:
                    org_filter.options = [ft.DropdownOption(
                        key="", text=strings.t("users_all_orgs"))] + list(_org_options)
                    org_filter.update()
                except Exception:
                    pass
                if _inv_org is not None:
                    try:
                        _inv_org.options = list(_org_options)
                        _inv_org.update()
                    except Exception:
                        pass
            try:
                seat_label.value = strings.t("users_seat_counts", counts=_seat_counts_text())
                seat_label.update()
            except Exception:
                pass
        _refresh_list()

    # Register only after every mutable list/filter control exists. The global
    # loader can now refresh a first-open response without reconstructing this
    # screen or the persistent desktop navigation rail.
    app._users_refresh_parts = _refresh_mounted

    body = ft.Column([card(ft.Column([
        ft.Row([sec_head("U", strings.t("users_sec_head")), ft.Container(expand=True),
                green_btn(strings.t("users_invite_btn"), icon=ft.Icons.PERSON_ADD_ALT,
                          on_click=_toggle_invite)],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=6),
        help_line,
        ft.Container(height=14),
        invite_panel,
        hover_field(search),
        ft.Container(height=8),
        # A wrapping Row is implemented by Flutter as Wrap, which cannot host
        # expanded children. Both filters intentionally expand to share the
        # desktop width, so keep this a flex Row (the mobile layout already
        # uses the navigation drawer and can scroll the page horizontally).
        ft.Row([org_filter, status_filter], spacing=8),
        ft.Container(height=8),
        ft.Row([seat_label,
                ghost_btn(strings.t("users_export_csv"), icon=ft.Icons.DOWNLOAD, on_click=_export_csv,
                          tooltip=strings.t("users_tip_export_csv")),
                ghost_btn(strings.t("users_signout_filtered"), icon=ft.Icons.LOGOUT, on_click=_bulk_signout,
                          tooltip=strings.t("users_tip_signout_filtered"))], spacing=8),
        ft.Container(height=12),
        list_holder,
        pager_holder,
    ], spacing=0))], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    return app.shell(strings.t("users_title"),
                     strings.t("users_subtitle"),
                     body, badge="U")
