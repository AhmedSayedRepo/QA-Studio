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

_ROLES = ["Viewer", "Member", "Admin"]


def _init(app):
    for k, v in (("_users_list", None), ("_users_loading", False),
                 ("_users_msg", None), ("_users_busy", None),
                 ("_users_expanded", set())):
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
                    ft.Text("Admins only", size=16, weight=ft.FontWeight.W_800, color=T.INK)],
                   spacing=10),
            ft.Container(height=6),
            ft.Text("This screen is available to administrators.", size=12.5,
                    color=T.INK_3, no_wrap=False),
        ], spacing=2))
        return app.shell("Users", "Manage who can access QA Studio", body, badge="U")

    _load(app)

    def _toggle_expand(uid):
        s = app._users_expanded
        s.discard(uid) if uid in s else s.add(uid)
        app.ui_safe(app.render)

    def _role_chip(uid, current, busy):
        def chip(role):
            sel = (current == role)
            return ft.Container(
                ft.Text(role, size=12, weight=ft.FontWeight.W_700,
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
            app._confirm("Change your own role?",
                         "You’re changing your OWN role and will lose admin access "
                         "until another admin restores it. Continue?",
                         lambda: _save(app, uid, lambda: auth.admin_set_role(uid, role)),
                         yes_label="Yes, change it")
        else:
            _save(app, uid, lambda: auth.admin_set_role(uid, role))

    def _perm_chip(uid, key, label, granted, busy):
        def _do(e):
            eff = set(auth.caps_for({"role": _cur_role[0], "caps": _cur_caps[0]}))
            eff.discard(key) if granted else eff.add(key)
            _save(app, uid, lambda: auth.admin_set_caps(uid, sorted(eff)))
        return ft.Container(
            ft.Row([ft.Icon(ft.Icons.CHECK if granted else ft.Icons.ADD, size=13,
                            color=("#FFFFFF" if granted else T.INK_3)),
                    ft.Text(label, size=11.5, weight=ft.FontWeight.W_600,
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
                    ft.Text("Fine-grained permissions", size=12,
                            weight=ft.FontWeight.W_800, color=T.INK),
                    ft.Container(expand=True),
                    (ft.Container(ft.Text("custom", size=10, weight=ft.FontWeight.BOLD,
                                          color=T.AMBER),
                                  padding=ft.Padding.symmetric(vertical=1, horizontal=7),
                                  bgcolor=ft.Colors.with_opacity(0.14, T.AMBER),
                                  border_radius=999)
                     if custom else ft.Container(width=0)),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=10),
                group("Can open (navigation)", nav),
                ft.Container(height=12),
                group("Can do (actions)", act),
            ], spacing=0),
            padding=14, margin=ft.Margin.only(top=10), bgcolor=T.CARD_2,
            border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    def _row(u):
        uid = u.get("id")
        email = u.get("email") or "(no email)"
        role = u.get("role") or "Viewer"
        caps = u.get("caps")
        confirmed = u.get("confirmed")
        last = (u.get("last_sign_in_at") or "")[:10] or "—"
        is_self = bool(me and me.get("id") == uid)
        busy = (app._users_busy == uid)
        expanded = uid in app._users_expanded

        head = ft.Row([
            ft.Container(ft.Text((email[:1] or "?").upper(), size=13,
                                 weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                         width=34, height=34, bgcolor=T.VIOLET, border_radius=17,
                         alignment=ft.Alignment.CENTER),
            ft.Column([
                ft.Row([
                    ft.Text(email, size=13.5, weight=ft.FontWeight.W_700, color=T.INK,
                            no_wrap=False),
                    (ft.Container(ft.Text("you", size=10, weight=ft.FontWeight.BOLD,
                                          color=T.VIOLET_INK),
                                  padding=ft.Padding.symmetric(vertical=1, horizontal=7),
                                  bgcolor=T.VIOLET_SOFT, border_radius=999)
                     if is_self else ft.Container(width=0)),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text(("✓ confirmed" if confirmed else "✗ not confirmed")
                        + f"  ·  last sign-in {last}",
                        size=11, color=(T.GREEN if confirmed else T.AMBER),
                        weight=ft.FontWeight.BOLD),
            ], spacing=2, expand=True),
            (ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2.4,
                                     color=T.VIOLET)], tight=True)
             if busy else _role_chip(uid, role, busy)),
            ft.Container(
                ft.Icon(ft.Icons.EXPAND_LESS if expanded else ft.Icons.TUNE,
                        size=18, color=T.INK_3),
                on_click=lambda e, x=uid: _toggle_expand(x), ink=True, border_radius=8,
                padding=8, tooltip="Per-permission access"),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        children = [head]
        if expanded:
            children.append(_perm_panel(uid, role, caps, busy))
        return ft.Container(
            ft.Column(children, spacing=0),
            padding=ft.Padding.symmetric(vertical=12, horizontal=14),
            border=ft.Border.all(1, T.BORDER), border_radius=T.R, bgcolor=T.CARD)

    if app._users_loading and app._users_list is None:
        rows = [ft.Container(ft.Row([
            ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=T.VIOLET),
            ft.Text("Loading users…", size=12.5, color=T.INK_3)], spacing=10), padding=14)]
    elif app._users_msg and app._users_msg[0] == "err":
        rows = [ft.Container(ft.Row([
            ft.Icon(ft.Icons.ERROR_OUTLINE, color=T.RED, size=18),
            ft.Text(app._users_msg[1], size=12.5, color=T.RED, no_wrap=False, expand=True)],
            spacing=10), padding=ft.Padding.symmetric(vertical=12, horizontal=14),
            bgcolor=ft.Colors.with_opacity(0.10, T.RED), border_radius=T.R,
            border=ft.Border.all(1, ft.Colors.with_opacity(0.4, T.RED)))]
    elif not app._users_list:
        rows = [ft.Container(ft.Text("No users found.", size=12.5, color=T.INK_3), padding=14)]
    else:
        rows = []
        for u in app._users_list:
            rows.append(_row(u))
            rows.append(ft.Container(height=8))

    body = card(ft.Column([
        ft.Row([sec_head("U", "Users & permissions"), ft.Container(expand=True),
                ghost_btn("Refresh", icon=ft.Icons.REFRESH,
                          on_click=lambda e: _load(app, force=True))],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=6),
        ft.Text("Pick a role preset, or tap the ⚙ icon on a user to grant/revoke "
                "individual tabs and actions.", size=12, color=T.INK_3,
                weight=ft.FontWeight.BOLD, no_wrap=False),
        ft.Container(height=14),
        ft.Column(rows, spacing=0),
    ], spacing=0))

    return app.shell("Users",
                     "Manage who can access QA Studio and what they can do",
                     body, badge="U")
