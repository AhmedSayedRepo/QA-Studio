"""orgs_screen.py — organization-scoped administration for QA Studio.

Create / edit / delete organizations (id, name, optional contact details). The
org `id` is exactly the value stored in each user's app_metadata.org_id; the
Users screen assigns people to an org by NAME, resolving names<->ids through the
directory this screen owns.

Global organization creation/deletion is SuperAdmin-only. Organization Managers
can manage the profile, projects and teams of the organization in their verified
JWT claim. Server-side functions repeat that tenant boundary.
"""
import csv
import json
import os
import threading

import flet as ft
import theme as T
import auth_supabase as auth
import backend_setup
import image_assets
import identity_editor
import platform_caps
import strings
from ui import hover_field, field_label

# IANA identifiers are deliberately stored/displayed unchanged: they are the
# portable timezone contract for Supabase, reports, and remote workers. The
# surrounding label and UI copy are localized through strings.py.
_TIME_ZONES = ["UTC", "Africa/Cairo", "Africa/Johannesburg", "Asia/Riyadh", "Asia/Dubai",
               "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney",
               "Europe/London", "Europe/Paris", "Europe/Berlin", "America/New_York",
               "America/Chicago", "America/Denver", "America/Los_Angeles"]


def _init(app):
    for k, v in (("_orgs_list", None), ("_orgs_loading", False),
                 ("_orgs_list_scope", None),
                 ("_orgs_msg", None), ("_orgs_busy", False),
                 ("_orgs_edit_id", None), ("_orgs_form_open", False),
                 ("_orgs_ferr", {}), ("_orgs_form_vals", {}),
                 ("_sender_audit_rows", None), ("_sender_audit_loading", False),
                 ("_sender_audit_msg", None), ("_sender_audit_filters", {
                     "org_id": "", "actor_id": "", "event": "", "period": "30d"}),
                 ("_sender_change_by_org", {}), ("_sender_health_by_org", {}),
                 ("_tenant_org_id", ""), ("_tenant_overview", None),
                 ("_tenant_loading", False), ("_tenant_msg", None),
                 ("_tenant_audit", []), ("_tenant_edit_project_id", ""),
                 ("_tenant_edit_team_id", ""),
                 ("_tenant_project_source", "manual"),
                 ("_tenant_provider_catalogs", {}),
                 ("_tenant_provider_loading", ""),
                 ("_tenant_provider_error", "")):
        if not hasattr(app, k):
            setattr(app, k, v)


def _directory_scope(app):
    """Identity that determines which organization directory is safe to show.

    The orgs Edge Function returns one organization for an OrgManager and the
    full directory for a SuperAdmin.  Caching only the rows therefore leaked a
    stale one-org view across a logout, role change, or organization switch.
    """
    user = getattr(app, "user", None) or {}
    return (str(user.get("id") or user.get("email") or ""),
            str(user.get("role") or ""), str(user.get("org_id") or ""))


def _refresh_orgs_part(app, part="all"):
    """Refresh a mounted Organizations section without rebuilding the shell.

    Rebuilding the page replaces its outer scrollable Column in this Flet
    version, which produces a visible jump. The screen registers a narrow
    callback while it is active; fall back to the normal renderer only before
    that callback exists (for example during the very first load).
    """
    if getattr(app, "active", None) != "orgs":
        return
    def _go():
        callback = getattr(app, "_orgs_refresh_parts", None)
        if callable(callback):
            callback(part)
        else:
            app.render(preserve_rail=True)
    app.ui_safe(_go)


def _load(app, force=False):
    # Self-initialize so this is safe to call from OTHER screens (the Users
    # screen loads the org directory via _load without ever running screen()/
    # _init). Without this, the first line below raised AttributeError on
    # app._orgs_loading and the Users screen swallowed it — so the org names /
    # dropdown only populated after you visited Organizations (which runs _init).
    _init(app)
    scope = _directory_scope(app)
    if app._orgs_list_scope != scope:
        # Do not briefly show an OrgManager's one-org response after an admin
        # signs in. The next response is accepted only for this exact scope.
        app._orgs_list = None
        app._orgs_msg = None
        force = True
    if app._orgs_loading:
        return
    if app._orgs_list is not None and not force:
        return
    app._orgs_loading = True
    app._orgs_msg = None

    def _work():
        ok, res = auth.list_orgs()
        # A login/logout or role refresh can happen while the request is in
        # flight. Ignore that old response rather than overwriting the new
        # principal's directory with it.
        if _directory_scope(app) != scope:
            app._orgs_loading = False
            return
        app._orgs_loading = False
        if ok:
            app._orgs_list = res
            app._orgs_list_scope = scope
            app._orgs_msg = None
        else:
            app._orgs_list = []
            app._orgs_list_scope = scope
            app._orgs_msg = ("err", res)
        # The Users screen also consumes this list (org dropdowns + name display)
        # via a lazy _load(app); refresh it too, else the dropdown stays empty
        # until you visit Organizations and come back.
        if getattr(app, "active", None) == "orgs":
            _refresh_orgs_part(app, "directory")
        elif getattr(app, "active", None) == "users":
            # Users consumes this directory for organization names and filter
            # options. Refresh only its mounted controls so this companion
            # request cannot rebuild/reset the desktop navigation rail.
            try:
                import users_screen
                users_screen._refresh_users_part(app, "organizations")
            except Exception:
                app.ui_safe(lambda: app.render(preserve_rail=True))
        elif getattr(app, "active", None) == "audit":
            # Audit uses the same token-scoped directory for its organization
            # picker. Patch that one control when the lazy response arrives.
            try:
                import audit_screen
                audit_screen._refresh_audit_directory(app)
            except Exception:
                app.ui_safe(lambda: app.render(preserve_rail=True))
    threading.Thread(target=_work, daemon=True).start()


def _audit_since(period):
    """Return an ISO UTC lower bound for the compact audit date filter."""
    from datetime import datetime, timedelta, timezone
    hours = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}.get(period)
    if not hours:
        return ""
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _load_sender_audit(app, force=False):
    """Load the SuperAdmin-only sender audit through the Edge Function."""
    _init(app)
    if app._sender_audit_loading:
        return
    if app._sender_audit_rows is not None and not force:
        return
    app._sender_audit_loading = True
    app._sender_audit_msg = None
    filters = dict(app._sender_audit_filters or {})

    def _work():
        ok, res = auth.get_org_email_audit_feed(
            org_id=filters.get("org_id", ""),
            actor_id=filters.get("actor_id", ""),
            event=filters.get("event", ""),
            since=_audit_since(filters.get("period", "30d")))
        app._sender_audit_loading = False
        if ok:
            app._sender_audit_rows = res.get("audit", [])
            app._sender_change_by_org = {
                str(row.get("org_id") or ""): str(row.get("updated_at") or "")
                for row in res.get("sender_changes", []) if isinstance(row, dict)
            }
            app._sender_health_by_org = {
                str(row.get("org_id") or ""): row
                for row in res.get("email_health", []) if isinstance(row, dict)
            }
            app._sender_audit_msg = None
        else:
            app._sender_audit_rows = []
            app._sender_audit_msg = ("err", str(res))
        _refresh_orgs_part(app, "audit")
    threading.Thread(target=_work, daemon=True).start()


def _load_tenant_overview(app, org_id, force=False):
    """Load identity, project/team membership and lifecycle data for one org."""
    _init(app)
    org_id = str(org_id or "")
    if not org_id or app._tenant_loading:
        return
    if app._tenant_overview is not None and not force and app._tenant_org_id == org_id:
        return
    app._tenant_loading = True
    app._tenant_org_id = org_id
    def _work():
        ok, res = auth.get_tenant_overview(org_id)
        audit_ok, audit_res = auth.get_admin_audit(org_id)
        app._tenant_loading = False
        app._tenant_overview = res if ok else {}
        app._tenant_msg = None if ok else str(res)
        app._tenant_audit = (audit_res.get("rows", []) if audit_ok and isinstance(audit_res, dict) else [])
        _refresh_orgs_part(app, "tenant")
    threading.Thread(target=_work, daemon=True).start()


def _export_sender_audit(app):
    """Write exactly the rows currently shown by the audit filters to CSV."""
    rows = list(getattr(app, "_sender_audit_rows", None) or [])
    if not rows:
        app._toast(strings.t("org_audit_export_empty"))
        return
    org_names = {str(o.get("id") or ""): str(o.get("name") or o.get("id") or "")
                 for o in (getattr(app, "_orgs_list", None) or [])}

    def _work():
        try:
            import platform_caps as pc
            out_dir = os.path.join(pc.export_base_dir(), "QA Studio", "Organization Audits")
            os.makedirs(out_dir, exist_ok=True)
            from datetime import datetime
            path = os.path.join(out_dir, f"SenderAudit_{datetime.now():%Y%m%d-%H%M%S}.csv")
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "timestamp_utc", "organization_id", "organization_name", "actor_email",
                    "actor_id", "event", "details"])
                writer.writeheader()
                for row in rows:
                    oid = str(row.get("org_id") or "")
                    writer.writerow({
                        "timestamp_utc": row.get("created_at") or "",
                        "organization_id": oid,
                        "organization_name": org_names.get(oid, oid),
                        "actor_email": row.get("actor_email") or "",
                        "actor_id": row.get("actor_id") or "",
                        "event": row.get("event") or "",
                        "details": json.dumps(row.get("details") or {}, ensure_ascii=False),
                    })
            if pc.is_mobile():
                app.ui_safe(lambda: app._mobile_download_popup(
                    path, strings.t("org_audit_exported", path=path)))
            else:
                app.ui_safe(lambda: app._toast(strings.t("org_audit_exported", path=path)))
                pc.open_folder(os.path.dirname(path))
        except Exception as ex:
            app.ui_safe(lambda: app._err(strings.t("org_audit_export_failed", err=ex)))
    threading.Thread(target=_work, daemon=True).start()


def _save(app, fn):
    """Run an org mutation (fn -> (ok,msg)) in the background, then refresh."""
    app._orgs_busy = True
    _refresh_orgs_part(app, "directory")

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
        _refresh_orgs_part(app, "directory")
    threading.Thread(target=_work, daemon=True).start()


def screen(app):
    _init(app)
    _mobile = platform_caps.is_mobile()
    # Do not let an old mounted screen callback mutate newly-created controls
    # while this screen is being assembled.
    app._orgs_refresh_parts = None
    from main import card, sec_head, ghost_btn, green_btn

    me = getattr(app, "user", None)
    _is_super = auth.is_super_admin(me)
    _manager_org_id = str(auth.org_id_of(me) or "")
    if auth.configured() and not auth.can_manage_org_settings(me):
        body = ft.Column([card(ft.Column([
            ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, color=T.INK_3, size=20),
                    ft.Text(strings.t("orgs_super_only"), size=16,
                            weight=ft.FontWeight.W_800, color=T.INK)], spacing=10),
            ft.Container(height=6),
            ft.Text(strings.t("orgs_super_only_body"), size=12.5, color=T.INK_3, no_wrap=False),
        ], spacing=2))], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        return app.shell(strings.t("orgs_title"), strings.t("orgs_subtitle"), body, badge="Or")

    _load(app)
    _load_sender_audit(app)
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

    def _set_form_values(oid=None):
        """Populate the mounted form controls without recreating the screen."""
        source = next((o for o in (app._orgs_list or []) if o.get("id") == oid), {}) or {}
        f_id.value = str(oid or "")
        f_id.disabled = bool(oid)
        f_name.value = str(source.get("name") or "")
        f_cname.value = str(source.get("contact_name") or "")
        f_cemail.value = str(source.get("contact_email") or "")
        f_cphone.value = str(source.get("contact_phone") or "")

    def _open_form(oid=None):
        app._orgs_edit_id = oid
        app._orgs_form_open = True
        app._orgs_ferr = {}
        app._orgs_form_vals = {}
        _set_form_values(oid)
        _refresh_form()

    def _close_form(e=None):
        app._orgs_form_open = False
        app._orgs_edit_id = None
        app._orgs_ferr = {}
        app._orgs_form_vals = {}
        _refresh_form()

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
            _refresh_form()
            return
        app._orgs_ferr = {}
        app._orgs_form_vals = {}
        app._orgs_form_open = False
        app._orgs_edit_id = None
        _refresh_form()
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

    def _form_content():
        if not app._orgs_form_open:
            return ft.Container()
        editing_now = app._orgs_edit_id
        errors = app._orgs_ferr if isinstance(app._orgs_ferr, dict) else {}
        title = strings.t("orgs_form_edit_title") if editing_now else strings.t("orgs_form_add_title")
        locked = (ft.Text(strings.t("orgs_id_locked_hint"), size=10.5,
                          color=T.INK_3, weight=ft.FontWeight.W_600) if editing_now else None)
        return ft.Container(
            ft.Column([
                ft.Text(title, size=13.5, weight=ft.FontWeight.W_800, color=T.INK),
                ft.Container(height=12),
                _labeled(f_id, strings.t("orgs_f_id"), extra=locked, req=True, err=errors.get("id")),
                ft.Container(height=12),
                _labeled(f_name, strings.t("orgs_f_name"), req=True, err=errors.get("name")),
                ft.Container(height=12),
                _labeled(f_cname, strings.t("orgs_f_cname")),
                ft.Container(height=12),
                (ft.Column([
                    _labeled(f_cemail, strings.t("orgs_f_cemail")),
                    ft.Container(height=10),
                    _labeled(f_cphone, strings.t("orgs_f_cphone")),
                ], spacing=0) if _mobile else ft.Row([
                    ft.Container(_labeled(f_cemail, strings.t("orgs_f_cemail")), expand=True),
                    ft.Container(_labeled(f_cphone, strings.t("orgs_f_cphone")), expand=True),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START)),
                ft.Container(height=14),
                ft.Row([ft.Container(expand=True),
                        ghost_btn(strings.t("orgs_cancel"), on_click=_close_form),
                        green_btn(strings.t("orgs_save"), icon=ft.Icons.CHECK, on_click=_save_org)],
                       spacing=10),
            ], spacing=0),
            padding=16, margin=ft.Margin.only(bottom=12), bgcolor=T.CARD_2,
            border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    form_holder = ft.Container(_form_content())

    def _refresh_form():
        try:
            form_holder.content = _form_content()
            form_holder.update()
        except Exception:
            # A transient unmounted-control race should not rebuild the page
            # and move the user. The next normal loader/action refresh will
            # repaint this small form section.
            pass

    # ── One org row ──────────────────────────────────────────────────────────
    def _org_row(o):
        row_busy = bool(getattr(app, "_orgs_busy", False))
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
        sender_changed = str((app._sender_change_by_org or {}).get(oid) or "")
        sender_badge = None
        if sender_changed:
            sender_badge = ft.Container(
                ft.Text(strings.t("orgs_sender_changed", date=sender_changed.replace("T", " ")[:10]),
                        size=9.5, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK),
                padding=ft.Padding.symmetric(vertical=2, horizontal=7),
                bgcolor=T.VIOLET_SOFT, border_radius=999)
        health = (app._sender_health_by_org or {}).get(oid)
        health_badge = None
        if isinstance(health, dict):
            status = str(health.get("status") or "amber")
            health_label, health_color, health_bg = {
                "green": (strings.t("orgs_email_healthy"), T.GREEN, T.GREEN_SOFT),
                "amber": (strings.t("orgs_email_untested"), T.AMBER, T.AMBER_SOFT),
                "red": (strings.t("orgs_email_issue"), T.RED, ft.Colors.with_opacity(0.12, T.RED)),
            }.get(status, (strings.t("orgs_email_untested"), T.AMBER, T.AMBER_SOFT))

            def _show_sender_audit(e=None, org_id=oid):
                app._sender_audit_filters = {
                    "org_id": org_id, "actor_id": "", "event": "", "period": ""}
                app._sender_audit_rows = None
                _load_sender_audit(app, force=True)

            health_badge = ft.Container(
                ft.Text(health_label, size=9.5, weight=ft.FontWeight.BOLD, color=health_color),
                padding=ft.Padding.symmetric(vertical=2, horizontal=7),
                bgcolor=health_bg, border_radius=999, ink=True,
                tooltip=strings.t("org_audit_head"), on_click=_show_sender_audit)
        avatar = ft.Container(ft.Text((name[:1] or "?").upper(), size=13,
                                      weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                              width=34, height=34, bgcolor=T.VIOLET, border_radius=9,
                              alignment=ft.Alignment.CENTER)
        identity_meta = [
            ft.Container(ft.Text(strings.t("orgs_id_label") + ": " + oid, size=10,
                                 weight=ft.FontWeight.BOLD, color=T.INK_3),
                         padding=ft.Padding.symmetric(vertical=1, horizontal=7),
                         bgcolor=T.CARD_2, border_radius=999),
            *([health_badge] if health_badge is not None else []),
            *([sender_badge] if sender_badge is not None else []),
        ]
        identity = (ft.Column([
            ft.Text(name, size=13.5, weight=ft.FontWeight.W_700, color=T.INK,
                    no_wrap=False),
            ft.Row(identity_meta, spacing=6, run_spacing=4, wrap=True),
            ft.Text(contact, size=11, color=T.INK_3, weight=ft.FontWeight.W_500,
                    no_wrap=False),
        ], spacing=4, expand=True) if _mobile else ft.Column([
            ft.Row([ft.Text(name, size=13.5, weight=ft.FontWeight.W_700, color=T.INK,
                            no_wrap=False, expand=True), *identity_meta],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text(contact, size=11, color=T.INK_3, weight=ft.FontWeight.W_500,
                    no_wrap=False, expand=True),
        ], spacing=3, expand=True))
        actions = []
        if _is_super:
            actions = [
                ft.Container(
                    ft.Icon(ft.Icons.EDIT_OUTLINED, size=18, color=T.INK_3),
                    on_click=(None if row_busy else (lambda e, x=oid: _open_form(x))),
                    ink=True, border_radius=8, padding=8, tooltip=strings.t("orgs_edit")),
                ft.Container(
                    ft.Icon(ft.Icons.DELETE_OUTLINE, size=18,
                            color=(T.INK_3 if row_busy else T.RED)),
                    on_click=(None if row_busy else (lambda e, x=oid, n=name: _delete_org(x, n))),
                    ink=True, border_radius=8, padding=8, tooltip=strings.t("orgs_delete")),
            ]
        row_content = (ft.Column([
            ft.Row([avatar, identity], spacing=12,
                   vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Row(actions, spacing=2, alignment=ft.MainAxisAlignment.END),
        ], spacing=6) if (_mobile and actions) else ft.Row(
            [avatar, identity, *actions], spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER))
        return ft.Container(
            row_content,
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

    def _directory_items():
        if app._orgs_loading and app._orgs_list is None:
            return [ft.Container(ft.Row([
                ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=T.VIOLET),
                ft.Text(strings.t("orgs_loading"), size=12.5, color=T.INK_3)], spacing=10), padding=14)]
        if app._orgs_msg and app._orgs_msg[0] == "err":
            return [ft.Container(ft.Row([
                ft.Icon(ft.Icons.ERROR_OUTLINE, color=T.RED, size=18),
                ft.Text(app._orgs_msg[1], size=12.5, color=T.RED, no_wrap=False, expand=True)], spacing=10),
                padding=ft.Padding.symmetric(vertical=12, horizontal=14),
                bgcolor=ft.Colors.with_opacity(0.10, T.RED), border_radius=T.R,
                border=ft.Border.all(1, ft.Colors.with_opacity(0.4, T.RED)))]
        if not (app._orgs_list or []):
            return [ft.Container(ft.Text(strings.t("orgs_none"), size=12.5, color=T.INK_3), padding=14)]
        rows = []
        for item in app._orgs_list:
            rows.extend([_org_row(item), ft.Container(height=8)])
        return rows

    def _refresh_directory():
        try:
            list_holder.controls = _directory_items()
            list_holder.update()
        except Exception:
            pass

    # ── Sender audit (read-only; SuperAdmin-only server enforcement) ────────
    audit_filters = dict(app._sender_audit_filters or {})

    def _set_audit_filter(key):
        def _change(e):
            value = str(getattr(getattr(e, "control", None), "value", "") or "")
            app._sender_audit_filters[key] = value
            app._sender_audit_rows = None
            _load_sender_audit(app, force=True)
        return _change

    org_options = [ft.DropdownOption(key="", text=strings.t("org_audit_all_orgs"))]
    for org in app._orgs_list or []:
        oid = str(org.get("id") or "")
        if oid:
            name = str(org.get("name") or oid)
            org_options.append(ft.DropdownOption(key=oid, text=(name if name == oid else f"{name} ({oid})")))

    user_options = [ft.DropdownOption(key="", text=strings.t("org_audit_all_users"))]
    seen_users = set()
    for row in app._sender_audit_rows or []:
        actor_id = str(row.get("actor_id") or "")
        if not actor_id or actor_id in seen_users:
            continue
        seen_users.add(actor_id)
        email = str(row.get("actor_email") or "")
        user_options.append(ft.DropdownOption(key=actor_id, text=email or actor_id[:8]))

    event_labels = {
        "email_settings_saved": strings.t("org_audit_saved"),
        "email_sender_inherited": strings.t("org_audit_inherited"),
        "email_test_succeeded": strings.t("org_audit_test_ok"),
        "email_test_failed": strings.t("org_audit_test_failed"),
    }
    event_options = [ft.DropdownOption(key="", text=strings.t("org_audit_all_events"))]
    for event, label in event_labels.items():
        event_options.append(ft.DropdownOption(key=event, text=label))
    period_options = [
        ft.DropdownOption(key="", text=strings.t("org_audit_all_time")),
        ft.DropdownOption(key="24h", text=strings.t("org_audit_24h")),
        ft.DropdownOption(key="7d", text=strings.t("org_audit_7d")),
        ft.DropdownOption(key="30d", text=strings.t("org_audit_30d")),
    ]

    def _audit_dropdown(value, options, on_select, tooltip):
        return ft.Dropdown(value=value or "", options=options, on_select=on_select,
                           border_color=T.BORDER, focused_border_color=T.VIOLET,
                           border_radius=T.R, content_padding=ft.Padding.symmetric(
                               vertical=9, horizontal=10), text_size=12.5, expand=True,
                           tooltip=tooltip)

    audit_filters_ui = ft.Column([
        ft.Row([
            _audit_dropdown(audit_filters.get("org_id"), org_options, _set_audit_filter("org_id"), strings.t("tenant_tip_audit_org")),
            _audit_dropdown(audit_filters.get("actor_id"), user_options, _set_audit_filter("actor_id"), strings.t("tenant_tip_audit_user")),
        ], spacing=10),
        ft.Container(height=8),
        ft.Row([
            _audit_dropdown(audit_filters.get("event"), event_options, _set_audit_filter("event"), strings.t("tenant_tip_audit_event")),
            _audit_dropdown(audit_filters.get("period", "30d"), period_options,
                            _set_audit_filter("period"), strings.t("tenant_tip_audit_period")),
        ], spacing=10),
    ], spacing=0)

    org_names = {str(o.get("id") or ""): str(o.get("name") or o.get("id") or "")
                 for o in (app._orgs_list or [])}

    def _audit_row(row):
        event = str(row.get("event") or "")
        title = event_labels.get(event, event.replace("_", " ").title() or "Sender activity")
        created = str(row.get("created_at") or "").replace("T", " ")[:19]
        oid = str(row.get("org_id") or "")
        org_name = org_names.get(oid, oid)
        actor = str(row.get("actor_email") or row.get("actor_id") or "System")
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        detail = ""
        if details.get("source_org_id"):
            detail = f"Source organization: {details['source_org_id']}"
        elif details.get("error"):
            detail = f"Error: {details['error']}"
        return ft.Container(ft.Column([
            ft.Row([
                ft.Text(title, size=12.5, color=T.INK, weight=ft.FontWeight.W_700, expand=True),
                ft.Text(created, size=10.5, color=T.INK_3),
            ], spacing=8),
            ft.Text(f"{org_name}  ·  {actor}", size=11, color=T.INK_3),
            *( [ft.Text(detail, size=10.5, color=T.INK_3)] if detail else [] ),
        ], spacing=3), padding=ft.Padding.symmetric(vertical=10, horizontal=12),
           bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER), border_radius=T.R)

    if app._sender_audit_loading and app._sender_audit_rows is None:
        audit_items = [ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=T.VIOLET),
                              ft.Text(strings.t("org_audit_loading"), size=12.5, color=T.INK_3)], spacing=10)]
    elif app._sender_audit_msg and app._sender_audit_msg[0] == "err":
        audit_items = [ft.Text(app._sender_audit_msg[1], size=12, color=T.RED, no_wrap=False)]
    elif not (app._sender_audit_rows or []):
        audit_items = [ft.Text(strings.t("org_audit_empty"), size=12, color=T.INK_3)]
    else:
        audit_items = []
        for audit_row in app._sender_audit_rows:
            audit_items += [_audit_row(audit_row), ft.Container(height=7)]

    audit_items_holder = ft.Column(audit_items, spacing=0)

    def _audit_items_now():
        if app._sender_audit_loading and app._sender_audit_rows is None:
            return [ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=T.VIOLET),
                            ft.Text(strings.t("org_audit_loading"), size=12.5, color=T.INK_3)], spacing=10)]
        if app._sender_audit_msg and app._sender_audit_msg[0] == "err":
            return [ft.Text(app._sender_audit_msg[1], size=12, color=T.RED, no_wrap=False)]
        if not (app._sender_audit_rows or []):
            return [ft.Text(strings.t("org_audit_empty"), size=12, color=T.INK_3)]
        rows = []
        for audit_row in app._sender_audit_rows:
            rows.extend([_audit_row(audit_row), ft.Container(height=7)])
        return rows

    def _refresh_audit():
        try:
            audit_items_holder.controls = _audit_items_now()
            audit_items_holder.update()
        except Exception:
            pass

    audit_export_btn = ghost_btn(strings.t("org_audit_export_csv"), icon=ft.Icons.DOWNLOAD,
                                 on_click=lambda e: _export_sender_audit(app),
                                 disabled=not bool(app._sender_audit_rows),
                                 tooltip=strings.t("tenant_tip_audit_export"))
    audit_card = card(ft.Column([
        ft.Row([
            sec_head("Au", strings.t("org_audit_head")),
            ft.Container(expand=True),
            audit_export_btn,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=6),
        ft.Text(strings.t("org_audit_help"), size=12, color=T.INK_3, no_wrap=False),
        ft.Container(height=14),
        audit_filters_ui,
        ft.Container(height=14),
        audit_items_holder,
    ], spacing=0))

    # ── Tenant identity + project/team registry ───────────────────────────
    # This stays on the Organizations screen so a SuperAdmin can set up a
    # tenant before inviting people into it. The API itself also supports an
    # OrgManager for their own organization; server-side token scoping is the
    # security boundary in both cases.
    # A manager is pinned to the organization encoded in their verified token.
    # Do this before the async organization directory has returned; otherwise
    # the picker remains empty and no tenant overview (projects/teams) is
    # requested until the screen is rebuilt.
    if not _is_super and _manager_org_id and app._tenant_org_id != _manager_org_id:
        app._tenant_org_id = _manager_org_id
        app._tenant_overview = None
        app._tenant_audit = []
    tenant_org = (_manager_org_id if not _is_super else
                  (app._tenant_org_id or str((app._orgs_list or [{}])[0].get("id") or "")))
    if tenant_org:
        _load_tenant_overview(app, tenant_org)
    tenant = app._tenant_overview if app._tenant_org_id == tenant_org and isinstance(app._tenant_overview, dict) else {}
    profile = tenant.get("profile") if isinstance(tenant.get("profile"), dict) else {}
    tenant_options = [ft.DropdownOption(key=str(o.get("id") or ""), text=str(o.get("name") or o.get("id") or ""))
                      for o in (app._orgs_list or []) if o.get("id")]
    tenant_pick = ft.Dropdown(value=tenant_org or None, options=tenant_options, dense=True,
                              text_size=12.5, border_color=T.BORDER, focused_border_color=T.VIOLET,
                              border_radius=T.R, expand=True, disabled=(not _is_super),
                              tooltip=strings.t("tenant_tip_org"))

    def _logo_preview(url):
        source = str(url or "")
        if source:
            return ft.Image(src=source, width=42, height=42,
                            fit=ft.BoxFit.COVER, border_radius=10)
        return ft.Container(ft.Icon(ft.Icons.BUSINESS, size=20, color=T.VIOLET_INK),
                            width=42, height=42, border_radius=10,
                            bgcolor=T.VIOLET_SOFT, alignment=ft.Alignment.CENTER)

    logo_holder = ft.Container(_logo_preview(profile.get("logo_url")), width=42, height=42,
                               border_radius=10, clip_behavior=ft.ClipBehavior.HARD_EDGE)
    logo_remove_btn = ft.Container(
        ft.Icon(ft.Icons.CLOSE, size=13, color="#FFFFFF"), width=20, height=20,
        alignment=ft.Alignment.CENTER, border_radius=10, bgcolor=T.RED,
        visible=bool(profile.get("logo_url")), tooltip=strings.t("org_logo_tip_remove"),
        right=-5, top=-5, ink=True)
    logo_action = ft.Stack([logo_holder, logo_remove_btn], width=42, height=42,
                           clip_behavior=ft.ClipBehavior.NONE)
    domains = ft.TextField(value=", ".join(profile.get("allowed_domains") or []), dense=True,
                           hint_text="company.com, partner.com", text_size=12.5,
                           border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
                           tooltip=strings.t("tenant_tip_domains"))
    _tz_value = str(profile.get("default_time_zone") or "UTC")
    _tz_options = [_tz_value] + [z for z in _TIME_ZONES if z != _tz_value]
    timezone = ft.Dropdown(value=_tz_value, options=[ft.DropdownOption(key=z, text=z) for z in _tz_options],
                           dense=True, text_size=12.5, border_color=T.BORDER,
                           focused_border_color=T.VIOLET, border_radius=T.R,
                           tooltip=strings.t("tenant_tip_timezone"))
    locale = ft.TextField(value=profile.get("default_locale") or "en", dense=True,
                          hint_text="en", text_size=12.5, border_color=T.BORDER,
                          focused_border_color=T.VIOLET, border_radius=T.R,
                          tooltip=strings.t("tenant_tip_locale"))
    support_email = ft.TextField(value=profile.get("support_email") or "", dense=True,
                                 hint_text="support@example.com", text_size=12.5,
                                 border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
                                 tooltip=strings.t("tenant_tip_support_email"))
    retention = ft.TextField(value=str(profile.get("data_retention_days") or 365), dense=True,
                             hint_text="365", text_size=12.5, border_color=T.BORDER,
                             focused_border_color=T.VIOLET, border_radius=T.R,
                             tooltip=strings.t("tenant_tip_retention"))
    project_key = ft.TextField(dense=True, hint_text=strings.t("tenant_project_key"), text_size=12.5,
                               border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R, expand=True,
                               tooltip=strings.t("tenant_tip_project_key"))
    project_name = ft.TextField(dense=True, hint_text=strings.t("tenant_project_name"), text_size=12.5,
                                border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R, expand=True,
                                tooltip=strings.t("tenant_tip_project_name"))
    project_source = ft.Dropdown(
        value="manual",
        options=[ft.DropdownOption(key="manual", text=strings.t("tenant_project_source_manual"))] + [
            ft.DropdownOption(key=key, text=backend_setup.label_for(key))
            for key, _label, _blurb in backend_setup.BACKENDS
            if auth.can_import_project_source(me, key)
        ],
        dense=True, text_size=12.5, border_color=T.BORDER, focused_border_color=T.VIOLET,
        border_radius=T.R, expand=True,
        tooltip=strings.t("tenant_tip_project_source"))
    provider_project = ft.Dropdown(
        dense=True, text_size=12.5, border_color=T.BORDER, focused_border_color=T.VIOLET,
        border_radius=T.R, expand=True, disabled=True,
        hint_text=strings.t("tenant_provider_project_pick"),
        tooltip=strings.t("tenant_tip_provider_project"))
    provider_note_holder = ft.Container()
    project_actions_holder = ft.Row([], spacing=8)
    team_name = ft.TextField(dense=True, hint_text=strings.t("tenant_team_name"), text_size=12.5,
                             border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R, expand=True,
                             tooltip=strings.t("tenant_tip_team_name"))
    team_actions_holder = ft.Row([], spacing=8)

    def _tenant_change(e):
        app._tenant_org_id = str(e.control.value or "")
        app._tenant_overview = None
        _clear_project_edit()
        _clear_team_edit()
        _load_tenant_overview(app, app._tenant_org_id, force=True)

    tenant_pick.on_select = _tenant_change

    def _source_label(source):
        source = str(source or "manual")
        return (strings.t("tenant_project_source_manual") if source == "manual"
                else backend_setup.label_for(source))

    def _provider_items(source):
        values = (getattr(app, "_tenant_provider_catalogs", {}) or {}).get(source, [])
        return [item for item in values if isinstance(item, dict) and item.get("key")]

    def _refresh_project_source_editor(update_fields=True):
        source = str(getattr(app, "_tenant_project_source", "manual") or "manual")
        items = _provider_items(source)
        # Keep the saved value visible during an edit even when an admin later
        # removes that backend permission. It cannot then be re-imported or
        # changed by this user, but the existing project remains intelligible.
        allowed_sources = {"manual"}
        allowed_sources.update(key for key, _label, _blurb in backend_setup.BACKENDS
                               if auth.can_import_project_source(me, key))
        if source not in allowed_sources:
            allowed_sources.add(source)
        project_source.options = [ft.DropdownOption(
            key="manual", text=strings.t("tenant_project_source_manual"))] + [
            ft.DropdownOption(key=key, text=backend_setup.label_for(key))
            for key, _label, _blurb in backend_setup.BACKENDS if key in allowed_sources
        ]
        project_source.value = source
        project_source.disabled = (source != "manual"
                                   and not auth.can_import_project_source(me, source))
        provider_project.options = [ft.DropdownOption(
            key=str(item.get("key")), text=str(item.get("name") or item.get("key")))
            for item in items]
        provider_project.disabled = (source == "manual"
                                     or not auth.can_import_project_source(me, source))
        project_key.read_only = source != "manual"
        provider_note_holder.content = ft.Text(
            (strings.t("tenant_provider_projects_loading", source=_source_label(source))
             if getattr(app, "_tenant_provider_loading", "") == source else
             str(getattr(app, "_tenant_provider_error", "") or "") if source != "manual" else
             strings.t("tenant_project_source_manual_help")),
            size=10.5, color=T.INK_3, no_wrap=False)
        try:
            controls = [project_source, provider_project, provider_note_holder]
            if update_fields:
                controls = [project_key, project_name, *controls]
            for control in controls:
                control.update()
        except Exception:
            pass

    def _load_provider_projects(source):
        source = str(source or "manual")
        if source == "manual":
            app._tenant_provider_loading = ""
            app._tenant_provider_error = ""
            _refresh_project_source_editor()
            return
        if not auth.can_import_project_source(me, source):
            app._tenant_provider_loading = ""
            app._tenant_provider_error = strings.t("tenant_project_source_denied")
            _refresh_project_source_editor()
            return
        if source != backend_setup.active(getattr(app, "creds", {}) or {}):
            app._tenant_provider_loading = ""
            app._tenant_provider_error = strings.t("tenant_provider_backend_inactive", source=_source_label(source))
            _refresh_project_source_editor()
            return
        app._tenant_provider_loading = source
        app._tenant_provider_error = ""
        _refresh_project_source_editor(update_fields=False)

        def _work():
            try:
                values = backend_setup.list_projects_for_backend(app, source)
                app._tenant_provider_catalogs = {
                    **(getattr(app, "_tenant_provider_catalogs", {}) or {}), source: values}
                app._tenant_provider_error = (strings.t("tenant_provider_projects_empty", source=_source_label(source))
                                               if not values else "")
            except Exception as ex:
                app._tenant_provider_error = strings.t("tenant_provider_projects_error", error=str(ex)[:180])
            finally:
                if getattr(app, "_tenant_provider_loading", "") == source:
                    app._tenant_provider_loading = ""
                app.ui_safe(_refresh_project_source_editor)
        threading.Thread(target=_work, daemon=True).start()

    def _provider_project_change(e):
        key = str(e.control.value or "")
        item = next((value for value in _provider_items(getattr(app, "_tenant_project_source", "manual"))
                     if str(value.get("key") or "") == key), {})
        if not item:
            return
        project_key.value = key
        project_name.value = str(item.get("name") or key)
        _refresh_project_source_editor()

    def _project_source_change(e):
        source = str(e.control.value or "manual")
        if source != "manual" and not auth.can_import_project_source(me, source):
            app._err(strings.t("tenant_project_source_denied"))
            return
        app._tenant_project_source = source
        provider_project.value = None
        project_key.value = ""
        project_name.value = ""
        _refresh_project_source_editor()
        _load_provider_projects(source)

    project_source.on_select = _project_source_change
    provider_project.on_select = _provider_project_change

    def _tenant_save(action, on_success=None, **values):
        # Read the live picker value instead of the value captured when the
        # screen was first mounted. That is essential now that changing tenant
        # no longer rebuilds the whole screen.
        selected_org = str(getattr(tenant_pick, "value", None) or app._tenant_org_id or "")
        def _work():
            ok, result = auth.admin_tenant_action(action, org_id=selected_org, **values)
            if ok:
                app._tenant_overview = None
                # The Users page caches tenant overviews for membership
                # checkboxes. A newly added project must invalidate that cache
                # so it is immediately assignable when the admin opens Users.
                if action in ("upsert_project", "upsert_team"):
                    app._users_tenant_overviews = {}
                    app._users_list_uid = None
                if callable(on_success):
                    app.ui_safe(on_success)
                _load_tenant_overview(app, selected_org, force=True)
                app.ui_safe(lambda: app._toast(strings.t("tenant_saved")))
            else:
                app.ui_safe(lambda: app._err(result))
        threading.Thread(target=_work, daemon=True).start()

    def _save_profile(e=None):
        _tenant_save("upsert_profile",
                     allowed_domains=[p.strip().lower() for p in (domains.value or "").split(",") if p.strip()],
                     default_time_zone=(timezone.value or "UTC").strip(), default_locale=(locale.value or "en").strip(),
                     support_email=(support_email.value or "").strip(), data_retention_days=(retention.value or "365").strip(),
                     # SSO/SCIM requires dashboard/IdP activation; this profile
                     # stores only non-secret configuration intent.
                     sso_provider=profile.get("sso_provider") or "none", scim_enabled=bool(profile.get("scim_enabled")))

    def _open_logo_editor(e=None):
        selected_org = str(getattr(tenant_pick, "value", None) or app._tenant_org_id or "")
        if not selected_org:
            app._err(strings.t("tenant_select_org"))
            return

        messages = {"size": "profile_image_size", "dimensions": "profile_image_dimensions",
                    "format": "profile_image_format", "empty": "profile_image_picker"}

        def rejected(reason):
            app._err(strings.t(messages.get(reason, "profile_image_picker")))

        def save(payload):
            ok, result = auth.upload_organization_logo(selected_org, **payload)
            if not ok:
                return False, strings.t("image_upload_failed", error=str(result)[:160])
            app._tenant_overview = None
            app._users_tenant_overviews = {}
            _load_tenant_overview(app, selected_org, force=True)
            if str(auth.org_id_of(getattr(app, "user", None)) or "") == selected_org:
                app._identity_visuals["organization_logo_url"] = "data:{mime};base64,{body}".format(
                    mime=payload["mime_type"], body=payload["image_base64"])
                app.ui_safe(lambda: (app._refresh_rail_logo(), app.render(preserve_rail=True)))
                app._refresh_identity_visuals()
            app.ui_safe(lambda: app._toast(strings.t("org_logo_saved")))
            return True, result

        def remove():
            ok, result = auth.remove_organization_logo(selected_org)
            if not ok:
                return False, strings.t("image_remove_failed", error=str(result)[:160])
            app._tenant_overview = None
            app._users_tenant_overviews = {}
            _load_tenant_overview(app, selected_org, force=True)
            if str(auth.org_id_of(getattr(app, "user", None)) or "") == selected_org:
                app._identity_visuals["organization_logo_url"] = ""
                app.ui_safe(lambda: (app._refresh_rail_logo(), app.render(preserve_rail=True)))
                app._refresh_identity_visuals()
            app.ui_safe(lambda: app._toast(strings.t("org_logo_removed")))
            return True, result

        latest = app._tenant_overview if app._tenant_org_id == selected_org else {}
        latest_profile = latest.get("profile") if isinstance(latest, dict) and isinstance(latest.get("profile"), dict) else {}
        identity_editor.open_editor(
            app, source=str(latest_profile.get("logo_url") or ""), title=strings.t("org_logo"),
            choose_label=strings.t("org_logo_upload"), save_label=strings.t("avatar_edit_save"),
            close_label=strings.t("avatar_preview_close"), reset_label=strings.t("avatar_edit_reset"),
            remove_tooltip=strings.t("org_logo_tip_remove"), loading_label=strings.t("avatar_edit_loading"),
            failed_label=strings.t("avatar_edit_failed"), drag_hint=strings.t("identity_editor_drag"),
            zoom_label=strings.t("avatar_edit_zoom"), rotate_label=strings.t("avatar_edit_rotate"),
            uploading_label=strings.t("profile_uploading"), on_choose_error=rejected,
            on_save=save, on_remove=(remove if latest_profile.get("logo_url") else None),
            positioned_label=strings.t("identity_editor_positioned"),
            load_failed_label=strings.t("identity_editor_source_unavailable"))

    def _remove_logo(e=None):
        selected_org = str(getattr(tenant_pick, "value", None) or app._tenant_org_id or "")
        if not selected_org:
            app._err(strings.t("tenant_select_org"))
            return

        def _work():
            try:
                ok, result = auth.remove_organization_logo(selected_org)
            except Exception as ex:
                ok, result = False, str(ex)
            if not ok:
                app.ui_safe(lambda: app._err(strings.t("image_remove_failed", error=str(result)[:160])))
                return
            app._tenant_overview = None
            app._users_tenant_overviews = {}
            _load_tenant_overview(app, selected_org, force=True)
            if str(auth.org_id_of(getattr(app, "user", None)) or "") == selected_org:
                app._identity_visuals["organization_logo_url"] = ""
                app.ui_safe(lambda: (app._refresh_rail_logo(),
                                     app.render(preserve_rail=True)))
                app._refresh_identity_visuals()
            app.ui_safe(lambda: app._toast(strings.t("org_logo_removed")))

        threading.Thread(target=_work, daemon=True).start()

    logo_remove_btn.on_click = _remove_logo
    logo_holder.on_click = _open_logo_editor
    logo_holder.ink = True
    logo_holder.tooltip = strings.t("org_logo_tip")

    def _project_action_controls():
        if getattr(app, "_tenant_edit_project_id", ""):
            return [
                green_btn(strings.t("tenant_save_project"), icon=ft.Icons.SAVE,
                          on_click=_add_project, tooltip=strings.t("tenant_tip_save_project")),
                ghost_btn(strings.t("tenant_cancel_project"), icon=ft.Icons.CLOSE,
                          on_click=_clear_project_edit, tooltip=strings.t("tenant_tip_cancel_project")),
            ]
        return [green_btn(strings.t("tenant_add_project"), icon=ft.Icons.ADD, on_click=_add_project,
                          tooltip=strings.t("tenant_tip_add_project"))]

    def _refresh_project_editor(update_fields=True):
        project_actions_holder.controls = _project_action_controls()
        try:
            controls = [project_actions_holder]
            if update_fields:
                controls = [project_key, project_name, *controls]
            for control in controls:
                control.update()
        except Exception:
            pass

    def _clear_project_edit(e=None):
        app._tenant_edit_project_id = ""
        app._tenant_project_source = "manual"
        provider_project.value = None
        project_key.value = ""
        project_name.value = ""
        _refresh_project_source_editor()
        _refresh_project_editor()

    def _start_project_edit(project):
        # Pass the immutable project id to the existing upsert endpoint. It
        # updates the record in place, so project memberships remain attached.
        app._tenant_edit_project_id = str(project.get("id") or "")
        source = str(project.get("source_backend") or "manual")
        app._tenant_project_source = source
        project_key.value = str(project.get("external_key") or "")
        project_name.value = str(project.get("name") or "")
        provider_project.value = (str(project.get("provider_project_key") or project.get("external_key") or "")
                                  if source != "manual" else None)
        _refresh_project_source_editor()
        if source != "manual":
            _load_provider_projects(source)
        _refresh_project_editor()

    def _add_project(e=None):
        source = str(getattr(app, "_tenant_project_source", "manual") or "manual")
        if source != "manual" and not auth.can_import_project_source(me, source):
            app._err(strings.t("tenant_project_source_denied"))
            return
        provider_key = str(provider_project.value or project_key.value or "").strip()
        if source != "manual" and not provider_key:
            app._err(strings.t("tenant_provider_project_required"))
            return
        if not provider_key or not (project_name.value or "").strip():
            app._err(strings.t("tenant_project_required"))
            return
        project_id = str(getattr(app, "_tenant_edit_project_id", "") or "")
        _tenant_save("upsert_project", id=project_id, external_key=provider_key,
                     name=(project_name.value or "").strip(), source_backend=source,
                     provider_project_key=(provider_key if source != "manual" else ""),
                     on_success=_clear_project_edit)

    def _add_team(e=None):
        if not (team_name.value or "").strip():
            app._err(strings.t("tenant_team_required"))
            return
        team_id = str(getattr(app, "_tenant_edit_team_id", "") or "")
        _tenant_save("upsert_team", id=team_id, name=(team_name.value or "").strip(),
                     on_success=_clear_team_edit)

    def _team_action_controls():
        if getattr(app, "_tenant_edit_team_id", ""):
            return [
                green_btn(strings.t("tenant_save_team"), icon=ft.Icons.SAVE,
                          on_click=_add_team, tooltip=strings.t("tenant_tip_save_team")),
                ghost_btn(strings.t("tenant_cancel_team"), icon=ft.Icons.CLOSE,
                          on_click=_clear_team_edit, tooltip=strings.t("tenant_tip_cancel_team")),
            ]
        return [green_btn(strings.t("tenant_add_team"), icon=ft.Icons.GROUP_ADD,
                          on_click=_add_team, tooltip=strings.t("tenant_tip_add_team"))]

    def _refresh_team_editor():
        team_actions_holder.controls = _team_action_controls()
        try:
            team_name.update()
            team_actions_holder.update()
        except Exception:
            pass

    def _clear_team_edit(e=None):
        app._tenant_edit_team_id = ""
        team_name.value = ""
        _refresh_team_editor()

    def _start_team_edit(team):
        # The stable row id keeps every existing membership attached while the
        # human-readable team name is changed.
        app._tenant_edit_team_id = str(team.get("id") or "")
        team_name.value = str(team.get("name") or "")
        _refresh_team_editor()

    project_rows = []
    members_by_project = {}
    for membership in tenant.get("project_memberships", []) if isinstance(tenant.get("project_memberships"), list) else []:
        members_by_project[membership.get("project_id")] = members_by_project.get(membership.get("project_id"), 0) + 1
    for p in tenant.get("projects", []) if isinstance(tenant.get("projects"), list) else []:
        project_rows.append(ft.Text(
            f"{p.get('name')}  ·  {p.get('external_key')}  ·  "
            f"{strings.t('tenant_project_assigned', count=members_by_project.get(p.get('id'), 0))}",
            size=11.5, color=T.INK_3))
    if not project_rows:
        project_rows = [ft.Text(strings.t("tenant_no_projects"), size=11.5, color=T.INK_3)]
    team_rows = []
    members_by_team = {}
    for membership in tenant.get("team_memberships", []) if isinstance(tenant.get("team_memberships"), list) else []:
        members_by_team[membership.get("team_id")] = members_by_team.get(membership.get("team_id"), 0) + 1
    for team in tenant.get("teams", []) if isinstance(tenant.get("teams"), list) else []:
        team_rows.append(ft.Text(f"{team.get('name')}  ·  {members_by_team.get(team.get('id'), 0)} assigned", size=11.5, color=T.INK_3))
    if not team_rows:
        team_rows = [ft.Text(strings.t("tenant_no_teams"), size=11.5, color=T.INK_3)]
    admin_audit_rows = []
    for event in (app._tenant_audit or [])[:12]:
        stamp = str(event.get("created_at") or "").replace("T", " ")[:19]
        action = str(event.get("action") or "").replace(".", " · ")
        admin_audit_rows.append(ft.Text(f"{stamp}  ·  {action}  ·  {str(event.get('entity_id') or '')[:16]}", size=10.5, color=T.INK_3))
    if not admin_audit_rows:
        admin_audit_rows = [ft.Text(strings.t("tenant_audit_empty"), size=10.5, color=T.INK_3)]

    project_rows_holder = ft.Column(project_rows, spacing=5)
    team_rows_holder = ft.Column(team_rows, spacing=5)
    tenant_audit_holder = ft.Column(admin_audit_rows, spacing=4)

    def _project_source_badge(project):
        source = str(project.get("source_backend") or "manual")
        source_text = strings.t("tenant_project_source_badge", source=_source_label(source))
        if source == "manual":
            active_backend = backend_setup.active(getattr(app, "creds", {}) or {})
            current_keys = {str(item) for item in (getattr(app, "_projects", []) or [])}
            if bool(getattr(app, "connected", False)) and str(project.get("external_key") or "") in current_keys:
                source_text = strings.t("tenant_project_matched_badge", source=_source_label(active_backend))
        return ft.Container(
            ft.Text(source_text, size=9.5, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK,
                    no_wrap=True),
            padding=ft.Padding.symmetric(vertical=3, horizontal=7), bgcolor=T.VIOLET_SOFT,
            border_radius=999, tooltip=strings.t("tenant_tip_project_source_badge"))

    def _tenant_rows_now(data):
        projects_out, teams_out, audit_out = [], [], []
        project_counts, team_counts = {}, {}
        for membership in data.get("project_memberships", []) if isinstance(data.get("project_memberships"), list) else []:
            project_counts[membership.get("project_id")] = project_counts.get(membership.get("project_id"), 0) + 1
        for project in data.get("projects", []) if isinstance(data.get("projects"), list) else []:
            label = (f"{project.get('name')}  ·  {project.get('external_key')}  ·  "
                     f"{strings.t('tenant_project_assigned', count=project_counts.get(project.get('id'), 0))}")
            if _mobile:
                projects_out.append(ft.Column([
                    ft.Text(label, size=11.5, color=T.INK_3, no_wrap=False),
                    ft.Row([
                        _project_source_badge(project),
                        ghost_btn(strings.t("tenant_edit_project"), icon=ft.Icons.EDIT_OUTLINED,
                                  on_click=lambda e, p=dict(project): _start_project_edit(p),
                                  tooltip=strings.t("tenant_tip_edit_project")),
                    ], spacing=8),
                ], spacing=5))
            else:
                projects_out.append(ft.Row([
                    ft.Text(label, size=11.5, color=T.INK_3, expand=True),
                    _project_source_badge(project),
                    ghost_btn(strings.t("tenant_edit_project"), icon=ft.Icons.EDIT_OUTLINED,
                              on_click=lambda e, p=dict(project): _start_project_edit(p),
                              tooltip=strings.t("tenant_tip_edit_project")),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        if not projects_out:
            projects_out = [ft.Text(strings.t("tenant_no_projects"), size=11.5, color=T.INK_3)]
        for membership in data.get("team_memberships", []) if isinstance(data.get("team_memberships"), list) else []:
            team_counts[membership.get("team_id")] = team_counts.get(membership.get("team_id"), 0) + 1
        for team in data.get("teams", []) if isinstance(data.get("teams"), list) else []:
            label = f"{team.get('name')}  ·  {team_counts.get(team.get('id'), 0)} assigned"
            if _mobile:
                teams_out.append(ft.Column([
                    ft.Text(label, size=11.5, color=T.INK_3, no_wrap=False),
                    ghost_btn(strings.t("tenant_edit_team"), icon=ft.Icons.EDIT_OUTLINED,
                              on_click=lambda e, t=dict(team): _start_team_edit(t),
                              tooltip=strings.t("tenant_tip_edit_team")),
                ], spacing=5))
            else:
                teams_out.append(ft.Row([
                    ft.Text(label, size=11.5, color=T.INK_3, expand=True),
                    ghost_btn(strings.t("tenant_edit_team"), icon=ft.Icons.EDIT_OUTLINED,
                              on_click=lambda e, t=dict(team): _start_team_edit(t),
                              tooltip=strings.t("tenant_tip_edit_team")),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        if not teams_out:
            teams_out = [ft.Text(strings.t("tenant_no_teams"), size=11.5, color=T.INK_3)]
        for event in (app._tenant_audit or [])[:12]:
            stamp = str(event.get("created_at") or "").replace("T", " ")[:19]
            action = str(event.get("action") or "").replace(".", " · ")
            audit_out.append(ft.Text(f"{stamp}  ·  {action}  ·  {str(event.get('entity_id') or '')[:16]}", size=10.5, color=T.INK_3))
        if not audit_out:
            audit_out = [ft.Text(strings.t("tenant_audit_empty"), size=10.5, color=T.INK_3)]
        return projects_out, teams_out, audit_out

    # The tenant overview may already be cached when this screen mounts. Build
    # its project rows with edit controls immediately instead of waiting for a
    # remote refresh.
    project_rows_holder.controls, team_rows_holder.controls, tenant_audit_holder.controls = _tenant_rows_now(tenant)
    _refresh_project_source_editor(update_fields=False)
    _refresh_project_editor(update_fields=False)
    _refresh_team_editor()

    def _refresh_tenant():
        """Patch the selected tenant's fields and lists in place."""
        selected = (_manager_org_id if not _is_super else
                    str(getattr(tenant_pick, "value", None) or app._tenant_org_id or ""))
        if not selected and _is_super:
            selected = str(((app._orgs_list or [{}])[0].get("id") or ""))
        if selected and app._tenant_org_id != selected:
            app._tenant_org_id = selected
            app._tenant_overview = None
            app._tenant_audit = []
            _load_tenant_overview(app, selected)
        latest = (app._tenant_overview if app._tenant_org_id == selected
                  and isinstance(app._tenant_overview, dict) else {})
        latest_profile = latest.get("profile") if isinstance(latest.get("profile"), dict) else {}
        tenant_pick.options = [ft.DropdownOption(key=str(o.get("id") or ""), text=str(o.get("name") or o.get("id") or ""))
                               for o in (app._orgs_list or []) if o.get("id")]
        tenant_pick.value = selected or None
        domains.value = ", ".join(latest_profile.get("allowed_domains") or [])
        timezone.value = str(latest_profile.get("default_time_zone") or "UTC")
        locale.value = str(latest_profile.get("default_locale") or "en")
        support_email.value = str(latest_profile.get("support_email") or "")
        retention.value = str(latest_profile.get("data_retention_days") or 365)
        logo_holder.content = _logo_preview(latest_profile.get("logo_url"))
        logo_remove_btn.visible = bool(latest_profile.get("logo_url"))
        projects_now, teams_now, _audit_now = _tenant_rows_now(latest)
        project_rows_holder.controls = projects_now
        team_rows_holder.controls = teams_now
        try:
            for control in (tenant_pick, domains, timezone, locale, support_email, retention, logo_holder, logo_remove_btn,
                            project_rows_holder, team_rows_holder):
                control.update()
        except Exception:
            pass

    # The desktop grid makes efficient use of a wide pane. A phone must not
    # use it: three expanded fields and the project row then each receive only
    # a few pixels, producing the vertical character-by-character records in
    # the Android view. Keep the same controls and callbacks, but stack their
    # independent groups when the platform reports a mobile client.
    _tenant_header = (
        ft.Column([
            ft.Row([sec_head("T", strings.t("tenant_head")), ft.Container(expand=True), logo_action],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=8),
            ft.Row([
                ghost_btn(strings.t("org_logo_upload"), icon=ft.Icons.UPLOAD,
                          on_click=_open_logo_editor, tooltip=strings.t("org_logo_tip")),
                tenant_pick,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=0)
        if _mobile else ft.Row([
            sec_head("T", strings.t("tenant_head")), ft.Container(expand=True), logo_action,
            ghost_btn(strings.t("org_logo_upload"), icon=ft.Icons.UPLOAD,
                      on_click=_open_logo_editor, tooltip=strings.t("org_logo_tip")), tenant_pick,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER)
    )
    _profile_primary = (
        ft.Column([
            ft.Column([field_label(strings.t("tenant_domains")), domains], spacing=4),
            ft.Container(height=8),
            ft.Column([field_label(strings.t("tenant_timezone")), timezone], spacing=4),
        ], spacing=0)
        if _mobile else ft.Row([
            ft.Container(ft.Column([field_label(strings.t("tenant_domains")), domains], spacing=4), expand=True),
            ft.Container(ft.Column([field_label(strings.t("tenant_timezone")), timezone], spacing=4), expand=True),
        ], spacing=10)
    )
    _profile_secondary = (
        ft.Column([
            ft.Column([field_label(strings.t("tenant_locale")), locale], spacing=4),
            ft.Container(height=8),
            ft.Column([field_label(strings.t("tenant_support_email")), support_email], spacing=4),
            ft.Container(height=8),
            ft.Column([field_label(strings.t("tenant_retention")), retention], spacing=4),
        ], spacing=0)
        if _mobile else ft.Row([
            ft.Container(ft.Column([field_label(strings.t("tenant_locale")), locale], spacing=4), expand=True),
            ft.Container(ft.Column([field_label(strings.t("tenant_support_email")), support_email], spacing=4), expand=True),
            ft.Container(ft.Column([field_label(strings.t("tenant_retention")), retention], spacing=4), width=130),
        ], spacing=10)
    )
    _project_source_fields = (
        ft.Column([
            ft.Column([field_label(strings.t("tenant_project_source")), project_source], spacing=4),
            ft.Container(height=8),
            ft.Column([field_label(strings.t("tenant_provider_project")), provider_project], spacing=4),
        ], spacing=0)
        if _mobile else ft.Row([
            ft.Container(ft.Column([field_label(strings.t("tenant_project_source")), project_source], spacing=4), expand=True),
            ft.Container(ft.Column([field_label(strings.t("tenant_provider_project")), provider_project], spacing=4), expand=True),
        ], spacing=8)
    )
    _project_editor = (
        ft.Column([project_key, ft.Container(height=8), project_name,
                   ft.Container(height=8), project_actions_holder], spacing=0)
        if _mobile else ft.Row([project_key, project_name, project_actions_holder], spacing=8)
    )
    _team_editor = (
        ft.Column([team_name, ft.Container(height=8), team_actions_holder], spacing=0)
        if _mobile else ft.Row([team_name, team_actions_holder], spacing=8)
    )

    tenant_card = card(ft.Column([
        _tenant_header,
        ft.Container(height=8),
        ft.Text(strings.t("tenant_desc"), size=11.5, color=T.INK_3, no_wrap=False),
        ft.Container(height=12),
        _profile_primary,
        ft.Container(height=8),
        _profile_secondary,
        ft.Container(height=10),
        ft.Row([ft.Container(expand=True), green_btn(strings.t("tenant_save"), icon=ft.Icons.SAVE, on_click=_save_profile,
                                                       tooltip=strings.t("tenant_tip_save"))], alignment=ft.MainAxisAlignment.END),
        ft.Container(height=14),
        ft.Text(strings.t("tenant_projects"), size=12.5, weight=ft.FontWeight.W_800, color=T.INK),
        ft.Container(height=6),
        _project_source_fields,
        ft.Container(height=4), provider_note_holder,
        ft.Container(height=8),
        _project_editor,
        ft.Container(height=8), project_rows_holder,
        ft.Container(height=12),
        ft.Text(strings.t("tenant_teams"), size=12.5, weight=ft.FontWeight.W_800, color=T.INK),
        ft.Container(height=6),
        _team_editor,
        ft.Container(height=8), team_rows_holder,
        ft.Container(height=10),
        ft.Text(strings.t("tenant_sso_note"), size=11, color=T.INK_3, no_wrap=False),
    ], spacing=0))

    add_org_btn = (green_btn(strings.t("orgs_add_btn"), icon=ft.Icons.ADD_BUSINESS,
                              disabled=bool(getattr(app, "_orgs_busy", False)),
                              on_click=(lambda e: _open_form(None))) if _is_super else ft.Container())

    def _refresh_parts(part="all"):
        """Registered with async loaders; never replaces the outer scroller."""
        if part in ("all", "directory"):
            _refresh_directory()
            if _is_super:
                add_org_btn.disabled = bool(getattr(app, "_orgs_busy", False))
            try:
                add_org_btn.update()
            except Exception:
                pass
            # The tenant picker's options depend on the organization directory.
            _refresh_tenant()
        if part in ("all", "tenant"):
            _refresh_tenant()
        if part == "all":
            _refresh_form()

    app._orgs_refresh_parts = _refresh_parts

    directory_header = (ft.Column([
        sec_head("Or", strings.t("orgs_sec_head")),
        ft.Container(height=8),
        add_org_btn,
    ], spacing=0) if _mobile else ft.Row([
        sec_head("Or", strings.t("orgs_sec_head")), ft.Container(expand=True), add_org_btn,
    ], vertical_alignment=ft.CrossAxisAlignment.CENTER))
    body = ft.Column([card(ft.Column([
        directory_header,
        ft.Container(height=6),
        ft.Text(strings.t("orgs_help_line"), size=12, color=T.INK_3,
                weight=ft.FontWeight.BOLD, no_wrap=False),
        ft.Container(height=14),
        form_holder,
        list_holder,
    ], spacing=0)), ft.Container(height=14), tenant_card], spacing=0,
                     scroll=ft.ScrollMode.AUTO, expand=True)

    return app.shell(strings.t("orgs_title"), strings.t("orgs_subtitle"), body, badge="Or")
