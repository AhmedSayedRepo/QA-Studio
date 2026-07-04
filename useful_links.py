"""useful_links.py — Useful Links screen.

Extracted from main.py (Step-2 modular refactor). Exposes screen(app),
where `app` is the QAStudio instance; state and helpers (app._links,
app.shell, app._toast, ...) are read straight off it.
"""
import flet as ft
import theme as T
from ui import card, field_label, green_btn, hover_field


def screen(app):
        if not hasattr(app, "_links"):
            app._links = app._load_links()

        name_field = ft.TextField(
            hint_text="e.g. Azure DevOps", text_size=14, border_color=T.BORDER,
            focused_border_color=T.VIOLET, border_radius=T.R, bgcolor=T.CARD_2,
            content_padding=ft.Padding.symmetric(vertical=11, horizontal=13))
        url_field = ft.TextField(
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
            app._links.append({"name": nm, "url": u})
            app._save_links()
            app.render()
        url_field.on_submit = _add

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
                    app._links.pop(idx)
                except Exception:
                    pass
                app._save_links(); app.render()
            return _d

        def _open_btn(u):
            return ft.FilledButton(
                "Open", icon=ft.Icons.OPEN_IN_NEW, on_click=_open(u), height=40,
                style=ft.ButtonStyle(
                    bgcolor={"": T.VIOLET}, color={"": "#FFFFFF"}, elevation=0,
                    shape=ft.RoundedRectangleBorder(radius=T.R),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=0)))

        add_card = card(ft.Column([
            ft.Row([
                ft.Container(ft.Icon(ft.Icons.ADD, size=16, color=T.VIOLET), width=30,
                             height=30, bgcolor=T.VIOLET_SOFT, border_radius=9,
                             alignment=ft.Alignment.CENTER),
                ft.Text("Add a link", size=16, weight=ft.FontWeight.BOLD, color=T.INK),
            ], spacing=11),
            ft.Container(height=16),
            ft.Row([
                ft.Column([field_label("App name"),
                           ft.Container(hover_field(name_field), width=230,
                                        padding=ft.Padding.only(top=4))],
                          spacing=0, tight=True),
                ft.Column([field_label("URL"),
                           ft.Container(hover_field(url_field), padding=ft.Padding.only(top=4))],
                          spacing=0, expand=True),
                green_btn("Add link", icon=ft.Icons.ADD, on_click=_add, height=44),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.END),
        ], spacing=0))

        palette = ["#4d5ad6", "#0f9586", "#7c45d4", "#C2860C", "#1C80E0", "#E0474D"]
        rows = []
        for i, l in enumerate(app._links):
            nm = (l.get("name") or l.get("url") or "?")
            init = nm.strip()[:1].upper() if nm.strip() else "?"
            col = palette[sum(ord(c) for c in nm) % len(palette)]
            rows.append(ft.Container(
                ft.Row([
                    ft.Container(ft.Text(init, size=15, weight=ft.FontWeight.BOLD,
                                         color="#FFFFFF"), width=40, height=40,
                                 bgcolor=col, border_radius=11,
                                 alignment=ft.Alignment.CENTER),
                    ft.Column([
                        ft.Text(nm, size=14.5, weight=ft.FontWeight.BOLD, color=T.INK,
                                no_wrap=True),
                        ft.Text(l.get("url", ""), size=12.5, color=T.INK_2,
                                font_family=T.F_MONO, no_wrap=True),
                    ], spacing=1, tight=True, expand=True),
                    _open_btn(l.get("url", "")),
                    ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=18,
                                  icon_color=T.INK_3, tooltip="Remove", on_click=_del(i),
                                  style=ft.ButtonStyle(
                                      shape=ft.RoundedRectangleBorder(radius=8))),
                ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
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
