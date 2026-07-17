"""useful_links.py — Useful Links screen.

Extracted from main.py (Step-2 modular refactor). Exposes screen(app),
where `app` is the QAStudio instance; state and helpers (app._links,
app.shell, app._toast, ...) are read straight off it.
"""
import flet as ft
import theme as T
import platform_caps
from ui import card, field_label, green_btn, ghost_btn, hover_field, badge

# Built-in links shown to every signed-in user, regardless of their own saved
# links (main.py._load_links injects these — see its docstring). Only an
# admin can edit or remove one (main.py._hide_static_link / _update_static_link);
# everyone else sees it read-only, same idea as any other admin-managed setting.
STATIC_LINKS = [
    {"id": "qa_studio_site", "name": "QA Studio",
     "url": "https://ahmedsayedrepo.github.io/QA-Studio/"},
]


def screen(app):
        if not hasattr(app, "_links"):
            app._links = app._load_links()

        is_admin = app._is_admin()
        edit_id = getattr(app, "_link_edit_id", None) if is_admin else None
        editing = None
        if edit_id:
            editing = next((l for l in app._links if l.get("id") == edit_id), None)
            if not editing:
                edit_id = None
                app._link_edit_id = None

        name_field = ft.TextField(
            value=(editing.get("name", "") if editing else ""),
            hint_text="e.g. Azure DevOps", text_size=14, border_color=T.BORDER,
            focused_border_color=T.VIOLET, border_radius=T.R, bgcolor=T.CARD_2,
            content_padding=ft.Padding.symmetric(vertical=11, horizontal=13))
        url_field = ft.TextField(
            value=(editing.get("url", "") if editing else ""),
            hint_text="https://dev.azure.com/your-org", text_size=14,
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            bgcolor=T.CARD_2, expand=True,
            content_padding=ft.Padding.symmetric(vertical=11, horizontal=13))

        def _add(e=None):
            u = (url_field.value or "").strip()
            if not u:
                app._toast("Enter a URL."); return
            if not u.lower().startswith(("http://", "https://")):
                u = "https://" + u
            nm = (name_field.value or "").strip() or u
            if editing:
                app._update_static_link(editing["id"], nm, u)
                app._link_edit_id = None
            else:
                app._links.append({"name": nm, "url": u})
                app._save_links()
            app.render()
        url_field.on_submit = _add

        def _cancel_edit(e=None):
            app._link_edit_id = None
            app.render()

        def _edit_static(link_id):
            def _e(e):
                app._link_edit_id = link_id
                app.render()
            return _e

        def _open(u):
            def _o(e):
                try:
                    app._open_url(u)      # opens in front (over the app)
                except Exception:
                    app._toast("Couldn't open the link.")
            return _o

        def _del(idx):
            def _d(e):
                try:
                    link = app._links[idx]
                except Exception:
                    link = None
                if link and link.get("static"):
                    if not is_admin:
                        return
                    app._hide_static_link(link.get("id"))
                    if app._link_edit_id == link.get("id"):
                        app._link_edit_id = None
                else:
                    try:
                        app._links.pop(idx)
                    except Exception:
                        pass
                    app._save_links()
                app.render()
            return _d

        def _open_btn(u):
            return ft.FilledButton(
                "Open", icon=ft.Icons.OPEN_IN_NEW, on_click=_open(u), height=40,
                style=ft.ButtonStyle(
                    bgcolor={"": T.VIOLET}, color={"": "#FFFFFF"}, elevation=0,
                    shape=ft.RoundedRectangleBorder(radius=T.R),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=0)))

        _mobile = platform_caps.is_mobile()
        name_block = ft.Column([field_label("App name"),
                                 ft.Container(hover_field(name_field),
                                              width=(None if _mobile else 230),
                                              padding=ft.Padding.only(top=4))],
                                spacing=0, tight=(not _mobile))
        url_block = ft.Column([field_label("URL"),
                                ft.Container(hover_field(url_field),
                                             padding=ft.Padding.only(top=4))],
                               spacing=0, expand=True)
        save_btn = green_btn("Save changes" if editing else "Add link",
                             icon=ft.Icons.CHECK if editing else ft.Icons.ADD,
                             on_click=_add, height=44)

        if _mobile:
            # Desktop packs App name(230px fixed) + URL(expand) + Add-link
            # button on one Row — the fixed name field alone plus the
            # button's own width already exceeds a ~390px phone, pushing
            # the button off the right edge (confirmed live: "+ Ad…" cut
            # off). Stack each field on its own full-width row on mobile.
            form_items = [name_block, ft.Container(height=12), url_block,
                          ft.Container(height=12)]
            if editing:
                form_items.append(ghost_btn("Cancel", on_click=_cancel_edit, height=44))
                form_items.append(ft.Container(height=8))
            form_items.append(save_btn)
            form_col = ft.Column(form_items, spacing=0)
        else:
            form_row = [name_block, url_block]
            if editing:
                form_row.append(ghost_btn("Cancel", on_click=_cancel_edit, height=44))
            form_row.append(save_btn)
            form_col = ft.Row(form_row, spacing=12, vertical_alignment=ft.CrossAxisAlignment.END)

        add_card = card(ft.Column([
            ft.Row([
                ft.Container(ft.Icon(ft.Icons.EDIT if editing else ft.Icons.ADD,
                                     size=16, color=T.VIOLET), width=30,
                             height=30, bgcolor=T.VIOLET_SOFT, border_radius=9,
                             alignment=ft.Alignment.CENTER),
                ft.Text("Edit link" if editing else "Add a link", size=16,
                        weight=ft.FontWeight.BOLD, color=T.INK),
            ], spacing=11),
            ft.Container(height=16),
            form_col,
        ], spacing=0))

        palette = ["#4d5ad6", "#0f9586", "#7c45d4", "#C2860C", "#1C80E0", "#E0474D"]
        rows = []
        for i, l in enumerate(app._links):
            nm = (l.get("name") or l.get("url") or "?")
            init = nm.strip()[:1].upper() if nm.strip() else "?"
            col = palette[sum(ord(c) for c in nm) % len(palette)]
            is_static = bool(l.get("static"))

            name_row = [ft.Text(nm, size=14.5, weight=ft.FontWeight.BOLD, color=T.INK,
                                 no_wrap=True)]
            if is_static:
                name_row.append(badge("Official", kind="violet"))

            trailing = [_open_btn(l.get("url", ""))]
            # A static (built-in) link is read-only for everyone except admins —
            # regular Members/Viewers just get the Open button, no edit/delete.
            if not is_static or is_admin:
                if is_static:
                    trailing.append(ft.IconButton(
                        ft.Icons.EDIT_OUTLINED, icon_size=18, icon_color=T.INK_3,
                        tooltip="Edit", on_click=_edit_static(l.get("id")),
                        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))))
                trailing.append(ft.IconButton(
                    ft.Icons.DELETE_OUTLINE, icon_size=18, icon_color=T.INK_3,
                    tooltip="Remove", on_click=_del(i),
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))))

            avatar = ft.Container(ft.Text(init, size=15, weight=ft.FontWeight.BOLD,
                                          color="#FFFFFF"), width=40, height=40,
                                  bgcolor=col, border_radius=11,
                                  alignment=ft.Alignment.CENTER)
            identity = ft.Column([
                ft.Row(name_row, spacing=8,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text(l.get("url", ""), size=12.5, color=T.INK_2,
                        font_family=T.F_MONO, no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ], spacing=1, tight=True, expand=True)

            if platform_caps.is_mobile():
                # avatar(40) + Open button(~100) + edit/delete IconButtons
                # (~48px each, Material's minimum touch target) left almost
                # nothing for the expand=True name/url column on a ~390px
                # phone — reported live as the Open button rendering
                # overlapping the name text rather than beside it (the
                # squeezed column had no real width left to lay out into).
                # Identity gets its own full-width row; actions go on a
                # second row below, same pattern used for Users/Task
                # Manager rows this session.
                row_content = ft.Column([
                    ft.Row([avatar, identity], spacing=14,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Container(height=10),
                    ft.Row([ft.Container(expand=True), *trailing],
                           spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ], spacing=0)
            else:
                row_content = ft.Row([avatar, identity, *trailing], spacing=14,
                                     vertical_alignment=ft.CrossAxisAlignment.CENTER)

            rows.append(ft.Container(
                row_content,
                padding=ft.Padding.symmetric(vertical=12, horizontal=16),
                bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER), border_radius=14))

        if rows:
            listing = ft.Column(rows, spacing=10)
        else:
            listing = ft.Container(
                ft.Column([
                    ft.Container(ft.Icon(ft.Icons.LINK, size=22, color=T.VIOLET),
                                 width=48, height=48, bgcolor=T.VIOLET_SOFT,
                                 border_radius=13, alignment=ft.Alignment.CENTER),
                    ft.Container(height=14),
                    ft.Text("No links yet", size=15, weight=ft.FontWeight.BOLD,
                            color=T.INK),
                    ft.Text("Add a name and URL above — they're saved on this device "
                            "and open in your browser.", size=13, color=T.INK_2,
                            text_align=ft.TextAlign.CENTER),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=3),
                alignment=ft.Alignment.CENTER,
                padding=ft.Padding.symmetric(vertical=46, horizontal=20),
                bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER), border_radius=16)

        body = ft.Column([
            add_card,
            ft.Container(height=24),
            ft.Row([ft.Text("SAVED LINKS", size=10.5, weight=ft.FontWeight.BOLD,
                            color=T.INK_3),
                    ft.Container(expand=True),
                    ft.Text(f"{len(app._links)} "
                            + ("link" if len(app._links) == 1 else "links"),
                            size=12, color=T.INK_3, weight=ft.FontWeight.W_500)],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=12),
            listing,
        ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

        return app.shell(
            "Useful Links",
            "Save links to the boards & apps you use — open them in one click", body)

    # ---- settings ----
