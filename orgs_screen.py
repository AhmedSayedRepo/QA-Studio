"""orgs_screen.py — super-admin "Organizations" screen for QA Studio.

Create / edit / delete organizations (id, name, optional contact details). The
org `id` is exactly the value stored in each user's app_metadata.org_id; the
Users screen assigns people to an org by NAME, resolving names<->ids through the
directory this screen owns.

All privileged work runs server-side in the 'orgs' Supabase Edge Function (it
holds the service_role key and re-checks the caller is a super admin).
"""
import threading

import flet as ft
import theme as T
import auth_supabase as auth
import strings
from ui import hover_field, field_label


def _init(app):
    for k, v in (("_orgs_list", None), ("_orgs_loading", False),
                 ("_orgs_msg", None), ("_orgs_busy", False),
                 ("_orgs_edit_id", None), ("_orgs_form_open", False),
                 ("_orgs_ferr", {}), ("_orgs_form_vals", {})):
        if not hasattr(app, k):
            setattr(app, k, v)


def _load(app, force=False):
    # Self-initialize so this is safe to call from OTHER screens (the Users
    # screen loads the org directory via _load without ever running screen()/
    # _init). Without this, the first line below raised AttributeError on
    # app._orgs_loading and the Users screen swallowed it — so the org names /
    # dropdown only populated after you visited Organizations (which runs _init).
    _init(app)
    if app._orgs_loading:
        return
    if app._orgs_list is not None and not force:
        return
    app._orgs_loading = True
    app._orgs_msg = None

    def _work():
        ok, res = auth.list_orgs()
        app._orgs_loading = False
        if ok:
            app._orgs_list = res
            app._orgs_msg = None
        else:
            app._orgs_list = []
            app._orgs_msg = ("err", res)
        # The Users screen also consumes this list (org dropdowns + name display)
        # via a lazy _load(app); refresh it too, else the dropdown stays empty
        # until you visit Organizations and come back.
        if getattr(app, "active", None) in ("orgs", "users"):
            app.ui_safe(app.render)
    threading.Thread(target=_work, daemon=True).start()


def _save(app, fn):
    """Run an org mutation (fn -> (ok,msg)) in the background, then refresh."""
    app._orgs_busy = True
    app.ui_safe(app.render)

    def _work():
        ok, msg = fn()
        app._orgs_busy = False
        if not ok:
            # Mutation errors (e.g. delete blocked while users are assigned) show
            # as a toast, not a banner that replaces the whole org list.
            try:
                app._err(msg)
            except Exception:
                pass
        else:
            try:
                app._toast(msg)
            except Exception:
                pass
        ok2, res = auth.list_orgs()
        if ok2:
            app._orgs_list = res
        if getattr(app, "active", None) == "orgs":
            app.ui_safe(app.render)
    threading.Thread(target=_work, daemon=True).start()


def screen(app):
    _init(app)
    from main import card, sec_head, ghost_btn, green_btn

    me = getattr(app, "user", None)
    if not auth.is_super_admin(me):
        body = ft.Column([card(ft.Column([
            ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, color=T.INK_3, size=20),
                    ft.Text(strings.t("orgs_super_only"), size=16,
                            weight=ft.FontWeight.W_800, color=T.INK)], spacing=10),
            ft.Container(height=6),
            ft.Text(strings.t("orgs_super_only_body"), size=12.5, color=T.INK_3, no_wrap=False),
        ], spacing=2))], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        return app.shell(strings.t("orgs_title"), strings.t("orgs_subtitle"), body, badge="Or")

    _load(app)
    busy = bool(app._orgs_busy)
    editing = app._orgs_edit_id
    form_open = bool(app._orgs_form_open)
    _cur = next((o for o in (app._orgs_list or []) if o.get("id") == editing), None)

    def _tf(value, hint, disabled=False, err=None):
        # `hint` is placeholder text (shown only while the field is empty). Each
        # field's persistent identity comes from a field_label ABOVE it (see
        # _labeled) — a floating `label=` overlapped the dense field's top border.
        # `err` truthiness turns the frame red; the message itself is rendered
        # as a separate red line under the field by _labeled. (This Flet build's
        # TextField has NO error_text kwarg — passing it raises TypeError — so we
        # follow the app's border+note pattern, driven from app._orgs_ferr so it
        # SURVIVES the re-render, like the Regression screen's mandatory fields.)
        return ft.TextField(
            value=value or "", hint_text=hint, disabled=disabled, dense=True, text_size=13,
            border_color=(T.RED if err else T.BORDER),
            focused_border_color=(T.RED if err else T.VIOLET), border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=11, horizontal=12))

    # In-progress form values persist in app state so a validation re-render
    # never wipes what the user already typed; fall back to the edited org.
    _fe = app._orgs_ferr if isinstance(getattr(app, "_orgs_ferr", None), dict) else {}
    _fv = app._orgs_form_vals if isinstance(getattr(app, "_orgs_form_vals", None), dict) else {}
    f_id = _tf(_fv.get("id", editing or ""), strings.t("orgs_f_id"),
               disabled=bool(editing), err=_fe.get("id"))
    f_name = _tf(_fv.get("name", (_cur or {}).get("name", "")), strings.t("orgs_f_name"),
                 err=_fe.get("name"))
    f_cname = _tf(_fv.get("contact_name", (_cur or {}).get("contact_name", "")),
                  strings.t("orgs_f_cname"))
    f_cemail = _tf(_fv.get("contact_email", (_cur or {}).get("contact_email", "")),
                   strings.t("orgs_f_cemail"))
    f_cphone = _tf(_fv.get("contact_phone", (_cur or {}).get("contact_phone", "")),
                   strings.t("orgs_f_cphone"))

    def _open_form(oid=None):
        app._orgs_edit_id = oid
        app._orgs_form_open = True
        app._orgs_ferr = {}
        app._orgs_form_vals = {}
        app.ui_safe(app.render)

    def _close_form(e=None):
        app._orgs_form_open = False
        app._orgs_edit_id = None
        app._orgs_ferr = {}
        app._orgs_form_vals = {}
        app.ui_safe(app.render)

    def _save_org(e=None):
        vals = {"id": (f_id.value or "").strip(),
                "name": (f_name.value or "").strip(),
                "contact_name": (f_cname.value or "").strip(),
                "contact_email": (f_cemail.value or "").strip(),
                "contact_phone": (f_cphone.value or "").strip()}
        # Commit typed values to app state so re-render keeps them, then check
        # the mandatory fields (id + name — both required server-side too).
        app._orgs_form_vals = vals
        ferr = {}
        if not vals["id"]:
            ferr["id"] = strings.t("bset_field_required", field=strings.t("orgs_f_id"))
        if not vals["name"]:
            ferr["name"] = strings.t("bset_field_required", field=strings.t("orgs_f_name"))
        if ferr:
            # Persisted in app state → the fields render red WITH the message
            # under them (an inline error_text.update() alone was getting wiped
            # by the very next render, so nothing showed).
            app._orgs_ferr = ferr
            app.ui_safe(app.render)
            return
        app._orgs_ferr = {}
        app._orgs_form_vals = {}
        app._orgs_form_open = False
        app._orgs_edit_id = None
        _save(app, lambda: auth.admin_upsert_org(
            vals["id"], vals["name"], vals["contact_name"],
            vals["contact_email"], vals["contact_phone"]))

    def _delete_org(oid, name):
        app._confirm(strings.t("orgs_delete_title"),
                     strings.t("orgs_delete_body", name=(name or oid)),
                     lambda: _save(app, lambda: auth.admin_delete_org(oid)),
                     yes_label=strings.t("orgs_delete_yes"), danger=True)

    # ── Add / edit form ──────────────────────────────────────────────────────
    def _labeled(field, label_text, extra=None, req=False, err=None):
        # Persistent identity label ABOVE the field (app-standard field_label),
        # replacing the floating label that overlapped the dense field border.
        # req=True adds the red * that marks a mandatory field (id + name).
        # err renders a red validation line UNDER the field (see _tf note).
        kids = [field_label(label_text, req=req), ft.Container(height=4), hover_field(field)]
        if err:
            kids += [ft.Container(height=4),
                     ft.Text(err, size=11, color=T.RED, weight=ft.FontWeight.W_600,
                             no_wrap=False)]
        if extra is not None:
            kids += [ft.Container(height=3), extra]
        return ft.Column(kids, spacing=0)

    form_panel = ft.Container()
    if form_open:
        _title = strings.t("orgs_form_edit_title") if editing else strings.t("orgs_form_add_title")
        _locked = (ft.Text(strings.t("orgs_id_locked_hint"), size=10.5,
                           color=T.INK_3, weight=ft.FontWeight.W_600) if editing else None)
        form_panel = ft.Container(
            ft.Column([
                ft.Text(_title, size=13.5, weight=ft.FontWeight.W_800, color=T.INK),
                ft.Container(height=12),
                _labeled(f_id, strings.t("orgs_f_id"), extra=_locked, req=True, err=_fe.get("id")),
                ft.Container(height=12),
                _labeled(f_name, strings.t("orgs_f_name"), req=True, err=_fe.get("name")),
                ft.Container(height=12),
                _labeled(f_cname, strings.t("orgs_f_cname")),
                ft.Container(height=12),
                ft.Row([ft.Container(_labeled(f_cemail, strings.t("orgs_f_cemail")), expand=True),
                        ft.Container(_labeled(f_cphone, strings.t("orgs_f_cphone")), expand=True)],
                       spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Container(height=14),
                ft.Row([ft.Container(expand=True),
                        ghost_btn(strings.t("orgs_cancel"), on_click=_close_form),
                        green_btn(strings.t("orgs_save"), icon=ft.Icons.CHECK, on_click=_save_org)],
                       spacing=10),
            ], spacing=0),
            padding=16, margin=ft.Margin.only(bottom=12), bgcolor=T.CARD_2,
            border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    # ── One org row ──────────────────────────────────────────────────────────
    def _org_row(o):
        oid = o.get("id") or ""
        name = o.get("name") or oid
        bits = []
        if o.get("contact_name"):
            bits.append(o["contact_name"])
        if o.get("contact_email"):
            bits.append(o["contact_email"])
        if o.get("contact_phone"):
            bits.append(o["contact_phone"])
        contact = "  ·  ".join(bits) if bits else strings.t("orgs_no_contact")
        avatar = ft.Container(ft.Text((name[:1] or "?").upper(), size=13,
                                      weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                              width=34, height=34, bgcolor=T.VIOLET, border_radius=9,
                              alignment=ft.Alignment.CENTER)
        identity = ft.Column([
            ft.Row([ft.Text(name, size=13.5, weight=ft.FontWeight.W_700, color=T.INK,
                            no_wrap=False, expand=True),
                    ft.Container(ft.Text(strings.t("orgs_id_label") + ": " + oid, size=10,
                                         weight=ft.FontWeight.BOLD, color=T.INK_3),
                                 padding=ft.Padding.symmetric(vertical=1, horizontal=7),
                                 bgcolor=T.CARD_2, border_radius=999)],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text(contact, size=11, color=T.INK_3, weight=ft.FontWeight.W_500,
                    no_wrap=False, expand=True),
        ], spacing=3, expand=True)
        edit_btn = ft.Container(
            ft.Icon(ft.Icons.EDIT_OUTLINED, size=18, color=T.INK_3),
            on_click=(None if busy else (lambda e, x=oid: _open_form(x))),
            ink=True, border_radius=8, padding=8, tooltip=strings.t("orgs_edit"))
        del_btn = ft.Container(
            ft.Icon(ft.Icons.DELETE_OUTLINE, size=18, color=(T.INK_3 if busy else T.RED)),
            on_click=(None if busy else (lambda e, x=oid, n=name: _delete_org(x, n))),
            ink=True, border_radius=8, padding=8, tooltip=strings.t("orgs_delete"))
        return ft.Container(
            ft.Row([avatar, identity, edit_btn, del_btn], spacing=12,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(vertical=12, horizontal=14),
            border=ft.Border.all(1, T.BORDER), border_radius=T.R, bgcolor=T.CARD)

    # ── List ─────────────────────────────────────────────────────────────────
    if app._orgs_loading and app._orgs_list is None:
        list_items = [ft.Container(ft.Row([
            ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=T.VIOLET),
            ft.Text(strings.t("orgs_loading"), size=12.5, color=T.INK_3)], spacing=10), padding=14)]
    elif app._orgs_msg and app._orgs_msg[0] == "err":
        list_items = [ft.Container(ft.Row([
            ft.Icon(ft.Icons.ERROR_OUTLINE, color=T.RED, size=18),
            ft.Text(app._orgs_msg[1], size=12.5, color=T.RED, no_wrap=False, expand=True)], spacing=10),
            padding=ft.Padding.symmetric(vertical=12, horizontal=14),
            bgcolor=ft.Colors.with_opacity(0.10, T.RED), border_radius=T.R,
            border=ft.Border.all(1, ft.Colors.with_opacity(0.4, T.RED)))]
    elif not (app._orgs_list or []):
        list_items = [ft.Container(ft.Text(strings.t("orgs_none"), size=12.5, color=T.INK_3), padding=14)]
    else:
        list_items = []
        for o in app._orgs_list:
            list_items.append(_org_row(o))
            list_items.append(ft.Container(height=8))

    # Sizes to content and scrolls with the page's outer scroller (body is a
    # Column(scroll=AUTO)); a fixed-height inner ListView clipped the bottom.
    list_holder = ft.Column(controls=list_items, spacing=0)

    body = ft.Column([card(ft.Column([
        ft.Row([sec_head("Or", strings.t("orgs_sec_head")), ft.Container(expand=True),
                green_btn(strings.t("orgs_add_btn"), icon=ft.Icons.ADD_BUSINESS,
                          disabled=busy, on_click=(lambda e: _open_form(None)))],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=6),
        ft.Text(strings.t("orgs_help_line"), size=12, color=T.INK_3,
                weight=ft.FontWeight.BOLD, no_wrap=False),
        ft.Container(height=14),
        form_panel,
        list_holder,
    ], spacing=0))], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    return app.shell(strings.t("orgs_title"), strings.t("orgs_subtitle"), body, badge="Or")
