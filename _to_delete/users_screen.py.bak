"""users_screen.py — Admin-only "Users" screen for QA Studio.

Lists every account and lets an Admin manage access with fine granularity:
  • set a role preset (Viewer / Member / Admin), and
  • toggle individual capabilities — which nav tabs a user can OPEN and which
    actions they can DO — per user.

All privileged work happens server-side in the 'admin-users' Supabase Edge
Function (it holds the service_role key). See ADMIN_USERS_SETUP.md to deploy it.
"""
import threading

import flet as ft
import theme as T
import auth_supabase as auth
import platform_caps
import strings
from ui import hover_field

_ROLES = ["Viewer", "Member", "Admin"]
_PAGE = 25   # users per page


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
                 ("_users_expanded", set()),
                 ("_users_search", ""), ("_users_page", 0)):
        if not hasattr(app, k):
            setattr(app, k, v)


def _load(app, force=False):
    if app._users_loading:
        return
    if app._users_list is not None and not force:
        return
    app._users_loading = True
    app._users_msg = None

    def _work():
        ok, res = auth.admin_list_users()
        app._users_loading = False
        if ok:
            app._users_list = res
            app._users_msg = None
        else:
            app._users_list = []
            app._users_msg = ("err", res)
        if getattr(app, "active", None) == "users":
            app.ui_safe(app.render)
    threading.Thread(target=_work, daemon=True).start()


def _save(app, user_id, fn):
    """Run an admin mutation (fn → (ok,msg)) in the background with a busy state."""
    app._users_busy = user_id
    app.ui_safe(app.render)

    def _work():
        ok, msg = fn()
        app._users_busy = None
        if not ok:
            app._users_msg = ("err", msg)
        else:
            try:
                app._toast(msg)
            except Exception:
                pass
        # refresh the row's data from the server
        ok2, res = auth.admin_list_users()
        if ok2:
            app._users_list = res
            if app.user:
                for u in res:
                    if u.get("id") == app.user.get("id"):
                        app.user["role"] = u.get("role")
                        app.user["caps"] = u.get("caps")
        if getattr(app, "active", None) == "users":
            app.ui_safe(app.render)
    threading.Thread(target=_work, daemon=True).start()


def screen(app):
    _init(app)
    from main import card, sec_head, ghost_btn

    me = getattr(app, "user", None)
    if not auth.is_admin(me):
        body = card(ft.Column([
            ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, color=T.INK_3, size=20),
                    ft.Text(strings.t("users_admins_only"), size=16, weight=ft.FontWeight.W_800, color=T.INK)],
                   spacing=10),
            ft.Container(height=6),
            ft.Text(strings.t("users_admins_only_body"), size=12.5,
                    color=T.INK_3, no_wrap=False),
        ], spacing=2))
        return app.shell(strings.t("users_title"), strings.t("users_subtitle_short"), body)

    _load(app)

    def _toggle_expand(uid):
        s = app._users_expanded
        s.discard(uid) if uid in s else s.add(uid)
        app.ui_safe(app.render)

    def _role_chip(uid, current, busy):
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
            ft.Row([chip(r) for r in _ROLES], spacing=4, tight=True),
            padding=4, bgcolor=T.CARD_2, border_radius=T.R,
            border=ft.Border.all(1, T.BORDER))

    def _set_role(uid, role):
        is_self = bool(me and me.get("id") == uid)
        if is_self and role != "Admin":
            app._confirm(strings.t("users_confirm_role_title"),
                         strings.t("users_confirm_role_body"),
                         lambda: _save(app, uid, lambda: auth.admin_set_role(uid, role)),
                         yes_label=strings.t("users_confirm_role_yes"))
        else:
            _save(app, uid, lambda: auth.admin_set_role(uid, role))

    def _revoke(uid, email):
        is_self = bool(me and me.get("id") == uid)
        msg = strings.t("users_revoke_msg", email=email)
        if is_self:
            msg = strings.t("users_revoke_self_prefix") + msg
        app._confirm(strings.t("users_revoke_title"), msg,
                     lambda: _save(app, uid, lambda: auth.admin_revoke_access(uid)),
                     yes_label=strings.t("users_revoke_yes"), danger=True)

    def _perm_chip(uid, key, label, granted, busy):
        def _do(e):
            eff = set(auth.caps_for({"role": _cur_role[0], "caps": _cur_caps[0]}))
            eff.discard(key) if granted else eff.add(key)
            _save(app, uid, lambda: auth.admin_set_caps(uid, sorted(eff)))
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

    def _perm_panel(uid, role, caps, busy):
        _cur_role[0] = role
        _cur_caps[0] = caps
        eff = auth.caps_for({"role": role, "caps": caps})
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
        return ft.Container(
            ft.Column([
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
            children.append(_perm_panel(uid, role, caps, busy))
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

    # scrollable list with its own scrollbar (bounded height). Taller container —
    # fills more of the screen (was leaving a large empty gap below the list).
    _h = max(440, int((getattr(app.page, "height", None) or 800) - 270))
    list_view = ft.ListView(controls=_list_controls(), spacing=0, padding=0, expand=True)
    list_holder = ft.Container(list_view, height=_h)
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

    body = card(ft.Column([
        ft.Row([sec_head("U", strings.t("users_sec_head")), ft.Container(expand=True),
                ghost_btn(strings.t("users_refresh"), icon=ft.Icons.REFRESH,
                          on_click=lambda e: _load(app, force=True))],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=6),
        ft.Text(strings.t("users_help_line"), size=12, color=T.INK_3,
                weight=ft.FontWeight.BOLD, no_wrap=False),
        ft.Container(height=14),
        hover_field(search),
        ft.Container(height=12),
        list_holder,
        pager_holder,
    ], spacing=0))

    return app.shell(strings.t("users_title"),
                     strings.t("users_subtitle"),
                     body, badge="U")
