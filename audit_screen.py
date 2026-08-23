"""Dedicated, read-only administration audit screen.

The Organization screen is intentionally for configuring a tenant.  This
screen presents the resulting administrative and sender events separately,
using the same protected Edge Function paths as the existing UI.
"""
import threading
from datetime import datetime, timedelta, timezone

import flet as ft
import auth_supabase as auth
import strings
import theme as T
from ui import badge


_ACTION_LABELS = {
    "user.updated": "audit_action_user_updated",
    "user.organization_assigned": "audit_action_user_assigned",
    "user.invited": "audit_action_user_invited",
    "user.signed_out_globally": "audit_action_user_signed_out",
    "user.recovery_credentials_issued": "audit_action_user_recovery",
    "user.lifecycle.suspended": "audit_action_user_suspended",
    "user.lifecycle.active": "audit_action_user_reactivated",
    "project.upserted": "audit_action_project_upserted",
    "project.memberships.updated": "audit_action_project_memberships",
    "team.upserted": "audit_action_team_upserted",
    "team.memberships.updated": "audit_action_team_memberships",
    "organization.profile.updated": "audit_action_organization_profile",
    "organization.logo.uploaded": "audit_action_organization_logo",
    "user.avatar.uploaded": "audit_action_user_avatar",
    "organization.logo.removed": "audit_action_organization_logo_removed",
    "user.avatar.removed": "audit_action_user_avatar_removed",
    "email_settings_saved": "audit_action_sender_saved",
    "email_sender_inherited": "audit_action_sender_inherited",
    "email_test_succeeded": "audit_action_sender_test_succeeded",
    "email_test_failed": "audit_action_sender_test_failed",
}

_FIELD_LABELS = {
    "role": "audit_field_role", "org_id": "audit_field_organization",
    "name": "audit_field_name", "caps": "audit_field_permissions",
    "status": "audit_field_status",
}


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _person(item, prefix):
    """Prefer an authorized, human directory value; never show UUIDs in UI."""
    name = str(item.get(f"{prefix}_name") or "").strip()
    email = str(item.get(f"{prefix}_email") or "").strip()
    return name or email or strings.t("audit_person_unavailable")


def _organization(item):
    name = str(item.get("org_name") or "").strip()
    if name:
        return name
    # The older endpoint did not provide a name.  Keep its internal ID out of
    # the visible feed rather than making an audit card harder to read.
    return strings.t("audit_organization_unavailable")


def _count_members(value):
    if isinstance(value, dict):
        value = value.get("members")
    return len(value) if isinstance(value, list) else 0


def _short_value(value, field=""):
    if field == "role" and isinstance(value, str):
        role_key = "role_" + value.strip().lower().replace(" ", "")
        localized = strings.t(role_key)
        if localized != role_key:
            return localized
    if field == "status" and isinstance(value, str):
        status_key = "users_" + value.strip().lower()
        localized = strings.t(status_key)
        if localized != status_key:
            return localized
    if isinstance(value, list):
        return strings.t("audit_permissions_custom") if value else strings.t("audit_none")
    if value is None or value == "":
        return strings.t("audit_none")
    return str(value)


def _summary(item):
    """Create a short, safe explanation from the immutable audit payload."""
    action = str(item.get("action") or item.get("event") or "")
    before, after, details = (_as_dict(item.get("before_value")),
                              _as_dict(item.get("after_value")),
                              _as_dict(item.get("details")))
    if action in ("project.memberships.updated", "team.memberships.updated"):
        scope = strings.t("audit_project") if action.startswith("project") else strings.t("audit_team")
        return strings.t("audit_summary_memberships", scope=scope,
                         before=_count_members(before), after=_count_members(after))
    if action in ("project.upserted", "team.upserted"):
        scope = strings.t("audit_project") if action.startswith("project") else strings.t("audit_team")
        name = str(after.get("name") or before.get("name") or "").strip()
        created = not bool(before)
        return strings.t("audit_summary_created" if created else "audit_summary_updated_named",
                         scope=scope, name=name or strings.t("audit_record"))
    if action == "user.invited":
        return strings.t("audit_summary_invited", user=str(after.get("email") or _person(item, "target")))
    if action in ("user.updated", "user.organization_assigned"):
        changed = []
        for field, label in _FIELD_LABELS.items():
            if before.get(field) != after.get(field):
                changed.append(strings.t("audit_summary_changed", field=strings.t(label),
                                         before=_short_value(before.get(field), field),
                                         after=_short_value(after.get(field), field)))
        return " · ".join(changed[:3]) or strings.t("audit_summary_user_updated")
    if action == "organization.profile.updated":
        return strings.t("audit_summary_organization_profile")
    if action in ("email_settings_saved", "email_sender_inherited"):
        sender = str(details.get("sender") or "").strip()
        return strings.t("audit_summary_sender", sender=sender or strings.t("audit_none"))
    if action in ("email_test_succeeded", "email_test_failed"):
        return strings.t("audit_summary_sender_test")
    if action.startswith("user."):
        return strings.t("audit_summary_user", user=_person(item, "target"))
    return strings.t("audit_summary_generic")


def _event_title(item):
    action = str(item.get("action") or item.get("event") or "")
    key = _ACTION_LABELS.get(action)
    return strings.t(key) if key else strings.t("audit_action_generic")


def _as_utc(value):
    """Parse an audit timestamp safely; malformed legacy rows stay visible."""
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _filter_rows(rows, filters, now=None):
    """Filter only rows already authorized and fetched for the selected org."""
    filters = filters or {}
    event = str(filters.get("event") or "")
    actor_id = str(filters.get("actor_id") or "")
    target_id = str(filters.get("target_id") or "")
    query = str(filters.get("query") or "").strip().casefold()
    period = str(filters.get("period") or "30d")
    days = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}.get(period)
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)) if days else None

    kept = []
    for item in rows or []:
        action = str(item.get("action") or item.get("event") or "")
        if event and action != event:
            continue
        if actor_id and str(item.get("actor_id") or "") != actor_id:
            continue
        if target_id and str(item.get("target_id") or "") != target_id:
            continue
        stamp = _as_utc(item.get("created_at"))
        if cutoff and stamp and stamp < cutoff:
            continue
        if query:
            searchable = " ".join((action, _event_title(item), _summary(item),
                                    _person(item, "actor"), _person(item, "target"),
                                    _organization(item))).casefold()
            if query not in searchable:
                continue
        kept.append(item)
    return kept


def _init(app):
    for key, value in (("_audit_rows", None), ("_audit_loading", False),
                       ("_audit_error", ""), ("_audit_org_id", ""),
                       ("_audit_page", 0), ("_audit_page_size", 25),
                       ("_audit_filters", {"event": "", "actor_id": "",
                                             "target_id": "", "query": "",
                                             "period": "30d"})):
        if not hasattr(app, key):
            setattr(app, key, value)


def _refresh_audit_directory(app):
    """Refresh only the mounted organization picker after its directory loads."""
    if getattr(app, "active", "") != "audit":
        return

    def _go():
        callback = getattr(app, "_audit_refresh_directory", None)
        if callable(callback):
            callback()
        else:
            # This can occur only during the initial construction race. Keep
            # the persistent desktop rail intact in that fallback as well.
            app.render(preserve_rail=True)

    app.ui_safe(_go)


def _load(app, org_id="", force=False):
    if getattr(app, "_audit_loading", False):
        return
    if app._audit_rows is not None and not force and app._audit_org_id == org_id:
        return
    app._audit_loading = True
    app._audit_error = ""
    app._audit_org_id = org_id

    def work():
        ok, payload = auth.get_admin_audit(org_id or None)
        rows = []
        if ok and isinstance(payload, dict):
            for item in payload.get("rows", []) or []:
                if isinstance(item, dict):
                    rows.append({**item, "_kind": "admin"})
        else:
            app._audit_error = str(payload or "")

        # Cross-organization sender audit is restricted to SuperAdmins.  An
        # organization manager can still see their own sender history.
        if auth.is_super_admin(getattr(app, "user", None)):
            sender_ok, sender = auth.get_org_email_audit_feed(org_id=org_id)
            sender_rows = sender.get("audit", []) if sender_ok and isinstance(sender, dict) else []
        else:
            sender_ok, sender_rows = auth.get_org_email_audit(org_id or None)
            if not sender_ok:
                sender_rows = []
        for item in sender_rows:
            if isinstance(item, dict):
                rows.append({**item, "_kind": "sender", "action": item.get("event") or "sender.activity"})
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        app._audit_rows = rows
        app._audit_loading = False
        if getattr(app, "active", "") == "audit":
            app.ui_safe(lambda: app.render(preserve_rail=True))

    threading.Thread(target=work, daemon=True).start()


def screen(app):
    _init(app)
    # Prevent a late directory response from updating controls that belonged
    # to a previous instance of this screen.
    app._audit_refresh_directory = None
    from main import card, ghost_btn, sec_head

    user = getattr(app, "user", None)
    if not auth.can_manage_users(user):
        body = ft.Column([card(ft.Column([
            ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, color=T.INK_3, size=20),
                    ft.Text(strings.t("audit_admins_only"), size=16,
                            weight=ft.FontWeight.W_800, color=T.INK)], spacing=10),
            ft.Container(height=6),
            ft.Text(strings.t("audit_admins_only_body"), size=12.5, color=T.INK_3),
        ], spacing=0))], expand=True)
        return app.shell(strings.t("audit_title"), strings.t("audit_subtitle"), body, badge="Au")

    super_admin = auth.is_super_admin(user)
    # The org directory endpoint is safely scoped by the verified token: a
    # manager receives only their organization. Load it for both roles so the
    # fixed manager picker can display the human organization name, not its ID.
    try:
        import orgs_screen
        orgs_screen._load(app)
    except Exception:
        pass
    organizations = getattr(app, "_orgs_list", None) or []
    if super_admin:
        options = [ft.DropdownOption(key="", text=strings.t("audit_all_orgs"))]
        options += [ft.DropdownOption(key=str(item.get("id") or ""),
                                      text=str(item.get("name") or item.get("id") or ""))
                    for item in organizations if item.get("id")]
        selected = str(getattr(app, "_audit_org_id", "") or "")
    else:
        selected = str(auth.org_id_of(user) or "")
        org_name = next((str(item.get("name") or "").strip()
                         for item in organizations
                         if str(item.get("id") or "") == selected), "")
        options = [ft.DropdownOption(key=selected, text=org_name or selected)] if selected else []

    _load(app, selected)

    def change_org(e):
        chosen = str(getattr(e.control, "value", "") or "")
        app._audit_rows = None
        app._audit_page = 0
        _load(app, chosen, force=True)

    # A wrapping Row becomes Flutter's Wrap widget. Its children must not use
    # ``expand`` (Flex parent data), otherwise Flet 0.85 raises
    # WrapParentData/FlexParentData and replaces the screen with a gray pane.
    org_picker = ft.Dropdown(value=selected or None, options=options, on_select=change_org,
                             disabled=not super_admin, width=280, dense=True, text_size=12.5,
                             border_color=T.BORDER, focused_border_color=T.VIOLET,
                             border_radius=T.R, tooltip=strings.t("audit_tip_org"))

    def _refresh_directory():
        """Patch the organization name after the lazy directory request ends."""
        current_orgs = getattr(app, "_orgs_list", None) or []
        if super_admin:
            org_picker.options = [ft.DropdownOption(key="", text=strings.t("audit_all_orgs"))]
            org_picker.options += [
                ft.DropdownOption(key=str(item.get("id") or ""),
                                  text=str(item.get("name") or item.get("id") or ""))
                for item in current_orgs if item.get("id")
            ]
            org_picker.value = str(getattr(app, "_audit_org_id", "") or "") or None
        else:
            manager_org = str(auth.org_id_of(user) or "")
            manager_name = next((str(item.get("name") or "").strip()
                                 for item in current_orgs
                                 if str(item.get("id") or "") == manager_org), "")
            org_picker.options = ([ft.DropdownOption(key=manager_org,
                                                      text=manager_name or manager_org)]
                                  if manager_org else [])
            org_picker.value = manager_org or None
        try:
            org_picker.update()
        except Exception:
            pass

    app._audit_refresh_directory = _refresh_directory
    refresh = ghost_btn(strings.t("audit_refresh"), icon=ft.Icons.REFRESH,
                        on_click=lambda e: _load(app, selected, force=True),
                        tooltip=strings.t("audit_tip_refresh"))

    filters = dict(getattr(app, "_audit_filters", {}) or {})
    filters.setdefault("event", "")
    filters.setdefault("actor_id", "")
    filters.setdefault("target_id", "")
    filters.setdefault("query", "")
    filters.setdefault("period", "30d")
    app._audit_filters = filters

    def _directory_options(field):
        seen, options = set(), [ft.DropdownOption(key="", text=strings.t("audit_all_people"))]
        for item in app._audit_rows or []:
            person_id = str(item.get(f"{field}_id") or "")
            label = _person(item, field)
            if not person_id or label == strings.t("audit_person_unavailable") or person_id in seen:
                continue
            seen.add(person_id)
            options.append(ft.DropdownOption(key=person_id, text=label))
        return options

    def _event_options():
        seen = set()
        options = [ft.DropdownOption(key="", text=strings.t("audit_all_events"))]
        for item in app._audit_rows or []:
            action = str(item.get("action") or item.get("event") or "")
            if not action or action in seen:
                continue
            seen.add(action)
            options.append(ft.DropdownOption(key=action, text=_event_title(item)))
        return options

    events_holder = ft.Column(spacing=0)
    pager_holder = ft.Container()

    def _filtered_rows():
        return _filter_rows(app._audit_rows or [], app._audit_filters)

    def _refresh_filtered_view(reset_page=True):
        if reset_page:
            app._audit_page = 0
        events_holder.controls = _event_controls()
        pager_holder.content = _pager_control()
        try:
            events_holder.update()
            pager_holder.update()
        except Exception:
            pass

    def _set_filter(key):
        def apply(e):
            app._audit_filters[key] = str(getattr(e.control, "value", "") or "")
            _refresh_filtered_view()
        return apply

    event_filter = ft.Dropdown(label=strings.t("audit_filter_event"), value=filters["event"] or None,
                               options=_event_options(), on_select=_set_filter("event"),
                               dense=True, text_size=12.5, width=210, border_color=T.BORDER,
                               focused_border_color=T.VIOLET, border_radius=T.R,
                               tooltip=strings.t("audit_tip_filter_event"))
    actor_filter = ft.Dropdown(label=strings.t("audit_filter_actor"), value=filters["actor_id"] or None,
                               options=_directory_options("actor"), on_select=_set_filter("actor_id"),
                               dense=True, text_size=12.5, width=210, border_color=T.BORDER,
                               focused_border_color=T.VIOLET, border_radius=T.R,
                               tooltip=strings.t("audit_tip_filter_actor"))
    target_filter = ft.Dropdown(label=strings.t("audit_filter_target"), value=filters["target_id"] or None,
                                options=_directory_options("target"), on_select=_set_filter("target_id"),
                                dense=True, text_size=12.5, width=210, border_color=T.BORDER,
                                focused_border_color=T.VIOLET, border_radius=T.R,
                                tooltip=strings.t("audit_tip_filter_target"))
    period_filter = ft.Dropdown(label=strings.t("audit_filter_period"), value=filters["period"],
                                options=[ft.DropdownOption(key="24h", text=strings.t("audit_24h")),
                                         ft.DropdownOption(key="7d", text=strings.t("audit_7d")),
                                         ft.DropdownOption(key="30d", text=strings.t("audit_30d")),
                                         ft.DropdownOption(key="90d", text=strings.t("audit_90d")),
                                         ft.DropdownOption(key="", text=strings.t("audit_all_time"))],
                                on_select=_set_filter("period"), dense=True, text_size=12.5,
                                width=175, border_color=T.BORDER, focused_border_color=T.VIOLET,
                                border_radius=T.R, tooltip=strings.t("audit_tip_filter_period"))
    search_filter = ft.TextField(label=strings.t("audit_filter_search"), value=filters["query"],
                                 prefix_icon=ft.Icons.SEARCH, on_change=_set_filter("query"),
                                 dense=True, text_size=12.5, width=260, border_color=T.BORDER,
                                 focused_border_color=T.VIOLET, border_radius=T.R,
                                 tooltip=strings.t("audit_tip_filter_search"))

    def _clear_filters(e=None):
        app._audit_filters = {"event": "", "actor_id": "", "target_id": "", "query": "", "period": "30d"}
        event_filter.value = actor_filter.value = target_filter.value = None
        period_filter.value = "30d"
        search_filter.value = ""
        _refresh_filtered_view()
        for control in (event_filter, actor_filter, target_filter, period_filter, search_filter):
            try:
                control.update()
            except Exception:
                pass

    filters_ui = ft.Column([
        ft.Row([org_picker, event_filter, period_filter], spacing=10, wrap=True),
        ft.Row([actor_filter, target_filter, search_filter,
                ghost_btn(strings.t("audit_clear_filters"), icon=ft.Icons.FILTER_ALT_OFF,
                          on_click=_clear_filters, tooltip=strings.t("audit_tip_clear_filters"))],
               spacing=10, wrap=True),
    ], spacing=8)

    def _event_controls():
        """Build one page only; audit history can grow without bloating Flet's tree."""
        if app._audit_loading and app._audit_rows is None:
            return [ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=T.VIOLET),
                            ft.Text(strings.t("audit_loading"), size=12, color=T.INK_3)], spacing=9)]
        if app._audit_error:
            return [ft.Text(app._audit_error, size=12, color=T.RED, no_wrap=False)]
        rows = _filtered_rows()
        if not rows:
            return [ft.Container(ft.Column([
                ft.Icon(ft.Icons.FACT_CHECK_OUTLINED, size=26, color=T.INK_3),
                ft.Container(height=6),
                ft.Text(strings.t("audit_empty"), size=12, color=T.INK_3),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                alignment=ft.Alignment.CENTER, padding=ft.Padding.symmetric(vertical=22))]
        size = max(1, int(getattr(app, "_audit_page_size", 25) or 25))
        total = max(1, (len(rows) + size - 1) // size)
        app._audit_page = min(max(0, int(getattr(app, "_audit_page", 0) or 0)), total - 1)
        start = app._audit_page * size
        events = []
        for item in rows[start:start + size]:
            stamp = str(item.get("created_at") or "").replace("T", " ")[:19]
            actor = _person(item, "actor")
            target = _person(item, "target") if (item.get("target_email") or item.get("target_name")) else ""
            org = _organization(item)
            kind = strings.t("audit_sender_event") if item.get("_kind") == "sender" else strings.t("audit_admin_event")
            meta = [badge(f"{strings.t('audit_actor')}: {actor}", "grey")]
            if target:
                meta.append(badge(f"{strings.t('audit_target')}: {target}", "violet"))
            meta.append(badge(f"{strings.t('audit_organization')}: {org}", "grey"))
            events.extend([ft.Container(ft.Column([
                ft.Row([ft.Text(_event_title(item), size=13, weight=ft.FontWeight.W_700,
                                color=T.INK, expand=True, no_wrap=True,
                                overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(stamp, size=10.5, color=T.INK_3)], spacing=8),
                ft.Text(_summary(item), size=11.5, color=T.INK_2, no_wrap=False),
                ft.Row([badge(kind, "amber"), *meta], spacing=6, wrap=True),
            ], spacing=6), padding=ft.Padding.symmetric(vertical=11, horizontal=14),
                bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER), border_radius=T.R),
                ft.Container(height=7)])
        return events

    def _pager_control():
        rows = _filtered_rows()
        if not rows or app._audit_error:
            return ft.Container()
        size = max(1, int(getattr(app, "_audit_page_size", 25) or 25))
        total = max(1, (len(rows) + size - 1) // size)
        page = min(max(0, int(getattr(app, "_audit_page", 0) or 0)), total - 1)

        def _set_page(target):
            app._audit_page = min(max(0, target), total - 1)
            events_holder.controls = _event_controls()
            pager_holder.content = _pager_control()
            try:
                events_holder.update()
                pager_holder.update()
            except Exception:
                pass

        return ft.Row([
            ghost_btn(strings.t("audit_prev"), icon=ft.Icons.CHEVRON_LEFT,
                      disabled=page <= 0, on_click=lambda e: _set_page(page - 1),
                      tooltip=strings.t("audit_tip_prev")),
            ft.Container(ft.Text(strings.t("audit_pager", page=page + 1, total=total,
                                             count=len(rows)), size=11.5, color=T.INK_3), expand=True,
                         alignment=ft.Alignment.CENTER),
            ghost_btn(strings.t("audit_next"), icon=ft.Icons.CHEVRON_RIGHT,
                      disabled=page >= total - 1, on_click=lambda e: _set_page(page + 1),
                      tooltip=strings.t("audit_tip_next")),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    pager_holder.content = _pager_control()
    events_holder.controls = _event_controls()

    body = ft.Column([card(ft.Column([
        ft.Row([sec_head("Au", strings.t("audit_head")), ft.Container(expand=True), refresh],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=6),
        ft.Text(strings.t("audit_help"), size=12, color=T.INK_3, no_wrap=False),
        ft.Container(height=12), filters_ui,
        ft.Container(height=14), events_holder,
        ft.Container(height=6), pager_holder,
    ], spacing=0))], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    return app.shell(strings.t("audit_title"), strings.t("audit_subtitle"), body, badge="Au")
