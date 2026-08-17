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
                 ("_users_invite_open", False), ("_users_list_uid", None)):
        if not hasattr(app, k):
            setattr(app, k, v)


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
            app._users_msg = None
        else:
            app._users_list = []
            app._users_msg = ("err", res)
        if getattr(app, "active", None) == "users":
            app.ui_safe(app.render)
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

    def _toggle_expand(uid):
        s = app._users_expanded
        s.discard(uid) if uid in s else s.add(uid)
        # Repaint only the list so opening/closing a permission drawer doesn't
        # full-render the page (header, nav, scroll all stay put).
        try:
            _refresh_list()
        except Exception:
            app.ui_safe(app.render)

    def _save_inline(uid, fn):
        """Run an admin mutation but repaint ONLY the user list (see
        _refresh_list) instead of the whole page — so a role / permission / org
        change doesn't flash the header, nav or scroll position and any open
        permission panel stays put. Errors surface as a toast, never as a banner
        that wipes out the list."""
        app._users_busy = uid
        try:
            _refresh_list()
        except Exception:
            app.ui_safe(app.render)

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
            ok2, res = auth.admin_list_users()
            if ok2:
                app._users_list = res
                if app.user:
                    for u in res:
                        if u.get("id") == app.user.get("id"):
                            app.user["role"] = u.get("role")
                            app.user["caps"] = u.get("caps")
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

    def _perm_panel(uid, role, caps, busy, org="", name=""):
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
        return ft.Container(
            ft.Column(_name_editor + _org_editor + [
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
            children.append(_perm_panel(uid, role, caps, busy, org=u.get("org_id") or "", name=u.get("name") or ""))
        return ft.Container(
            ft.Column(children, spacing=0),
            padding=ft.Padding.symmetric(vertical=12, horizontal=14),
            border=ft.Border.all(1, T.BORDER), border_radius=T.R, bgcolor=T.CARD)

    # ── filter (search) + paginate (25/page) ──
    def _compute():
        allu = app._users_list or []
        q = (app._users_search or "").strip().lower()
        if q:
            filt = [u for u in allu
                    if q in (u.get("email") or "").lower()
                    or q in (u.get("role") or "").lower()]
        else:
            filt = allu
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
            app.ui_safe(app.render)

    def _goto(p):
        app._users_page = p
        _refresh_list()

    def _on_search(e):
        app._users_search = e.control.value or ""
        app._users_page = 0
        _refresh_list()       # in-place so the search box keeps focus while typing

    search = ft.TextField(
        value=app._users_search or "", hint_text=strings.t("users_search_hint"),
        prefix_icon=ft.Icons.SEARCH, on_change=_on_search, text_size=13, dense=True,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=11, horizontal=12))

    # ── Invite a new user into an org ──────────────────────────────────────
    def _toggle_invite(e=None):
        app._users_invite_open = not getattr(app, "_users_invite_open", False)
        app._users_invite_err = None
        app._users_invite_vals = {}
        app._users_invite_err_name = False
        app._users_invite_err_email = False
        app.ui_safe(app.render)

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

    def _email_temp_password(to_email, temp):
        """Email the invitee their temp password via the org-shared Gmail sender
        (same pipeline as report emails). Returns (ok, error)."""
        try:
            import engine
            engine.ensure_sender_creds()
            subj = strings.t("users_invite_email_subject")
            html = (
                "<div style=\"font-family:Segoe UI,Arial,sans-serif;color:#1b1f3a\">"
                "<h2 style=\"margin:0 0 12px\">" + strings.t("users_invite_email_heading") + "</h2>"
                "<p style=\"margin:0 0 10px\">" + strings.t("users_invite_email_line1") + "</p>"
                "<p style=\"margin:0 0 14px\">" + strings.t("users_invite_email_line2") + "</p>"
                "<table style=\"border-collapse:collapse;margin:0 0 14px\">"
                "<tr><td style=\"padding:4px 12px 4px 0;color:#6b7280\">"
                + strings.t("users_invite_email_email_label") + "</td>"
                "<td style=\"padding:4px 0;font-weight:bold\">" + to_email + "</td></tr>"
                "<tr><td style=\"padding:4px 12px 4px 0;color:#6b7280\">"
                + strings.t("users_invite_email_pw_label") + "</td>"
                "<td style=\"padding:4px 0;font-weight:bold;font-family:Consolas,monospace\">"
                + temp + "</td></tr>"
                "</table>"
                "<p style=\"margin:0;color:#6b7280;font-size:13px\">"
                + strings.t("users_invite_email_line3") + "</p></div>"
            )
            ok, err = engine.send_report(to_email, subj, html)
            return bool(ok), (err or "")
        except Exception as ex:
            return False, str(ex)

    def _show_temp_pw_dialog(to_email, temp, err):
        """Fallback when the email couldn't be sent: show the temp password to
        the admin (with a Copy button) so they can relay it securely."""
        body = (strings.t("users_invite_pw_intro", email=to_email, err=(err or "no email sender"))
                + "\n\n" + strings.t("users_invite_pw_email_label") + ":  " + to_email
                + "\n" + strings.t("users_invite_pw_label") + ":  " + temp)
        try:
            app._confirm(strings.t("users_invite_pw_title"), body,
                         lambda: app._copy_text_to_clipboard(temp, strings.t("users_invite_pw_copied")),
                         yes_label=strings.t("users_invite_pw_copy"), danger=False,
                         icon=ft.Icons.VPN_KEY)
        except Exception:
            app.ui_safe(app.render)

    def _run_invite(fn, email):
        # Persist typed values first, then validate — a re-render keeps them.
        # (Add-existing needs only a valid email; the full name is not required
        # here since the target already has an account.)
        _capture_invite()
        app._users_invite_err_name = False
        app._users_invite_err_email = (not _EMAIL_RE.match(email))
        if not _EMAIL_RE.match(email):
            app._users_invite_err = strings.t("users_invite_bad_email")
            app.ui_safe(app.render)
            return
        app._users_invite_err = None
        app.ui_safe(app.render)

        def _work():
            ok, msg = fn()
            if not ok:
                # Server rejection (already a member, other org, …) shows under
                # the email field too; panel stays open to fix and retry.
                app._users_invite_err = msg
                app._users_invite_err_email = True
                if getattr(app, "active", None) == "users":
                    app.ui_safe(app.render)
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
                app.ui_safe(app.render)
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
            app.ui_safe(app.render)
            return
        if not _EMAIL_RE.match(email):
            app._users_invite_err = strings.t("users_invite_bad_email")
            app.ui_safe(app.render)
            return
        app._users_invite_err = None
        app._users_invite_err_name = False
        app._users_invite_err_email = False
        app.ui_safe(app.render)

        def _work():
            ok, res = auth.admin_invite_user(email, role=role, org_id=org, name=name)
            if not ok:
                # Server rejection (already registered, other org, …) inline.
                app._users_invite_err = res if isinstance(res, str) else str(res)
                app._users_invite_err_email = True
                if getattr(app, "active", None) == "users":
                    app.ui_safe(app.render)
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
                app.ui_safe(app.render)
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
        ft.Container(height=12),
        list_holder,
        pager_holder,
    ], spacing=0))], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    return app.shell(strings.t("users_title"),
                     strings.t("users_subtitle"),
                     body, badge="U")
