"""settings.py — Settings screen.

Extracted from main.py (Step-3 modular refactor). screen(app) builds the
Appearance / Data & Diagnostics / Help & Reset cards; the segment builders
(app._lang_segment, app._tool_segment) and action handlers stay on the app.
"""
import flet as ft
import theme as T
import regression
from ui import _ic, card, ghost_btn, danger_btn


def screen(app):
        ro = bool(getattr(app, "readonly", False))   # no 'act.settings' → read-only

        def srow(title, desc, control):
            return ft.Container(
                ft.Row([
                    ft.Column([
                        ft.Text(title, size=13.5, weight=ft.FontWeight.BOLD, color=T.INK),
                        ft.Text(desc, size=12, color=T.INK_3, weight=ft.FontWeight.W_500),
                    ], spacing=2, expand=True),
                    control,
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=16),
                padding=ft.Padding.symmetric(vertical=13))

        def divider():
            return ft.Container(height=1, bgcolor=T.BORDER_2)

        def _theme_seg():
            def seg(label, icon, mode):
                sel = (T.MODE == mode)
                return ft.Container(
                    ft.Row([ft.Icon(icon, size=15,
                                    color=(T.VIOLET_INK if sel else T.INK_2)),
                            ft.Text(label, size=12, weight=ft.FontWeight.BOLD,
                                    color=(T.VIOLET_INK if sel else T.INK_2))],
                           spacing=7, tight=True),
                    height=34, alignment=ft.Alignment.CENTER,
                    padding=ft.Padding.symmetric(vertical=0, horizontal=16),
                    bgcolor=(T.VIOLET_SOFT if sel else None), border_radius=T.R_SM,
                    border=ft.Border.all(1, T.VIOLET if sel else ft.Colors.TRANSPARENT),
                    on_click=(None if ro else (lambda e, m=mode: (None if T.MODE == m
                                                                  else app._toggle_theme()))))
            return ft.Container(
                ft.Row([seg("Light", ft.Icons.LIGHT_MODE_OUTLINED, "light"),
                        seg("Dark", ft.Icons.DARK_MODE_OUTLINED, "dark")],
                       spacing=4, tight=True),
                padding=4, bgcolor=T.CARD_2, border_radius=T.R,
                border=ft.Border.all(1, T.BORDER))

        appearance = card(ft.Column([
            ft.Text("APPEARANCE", size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow("Theme", "Light is the default; dark is easier on the eyes at night.",
                 _theme_seg()),
            divider(),
            srow("Output language", "Default language for newly generated test cases.",
                 app._lang_segment()),
            divider(),
            srow("What to generate",
                 "Default generator for new runs — test-case titles or full steps.",
                 app._tool_segment(compact=True)),
        ], spacing=0))

        perf_switch = ft.Switch(value=regression.perf_on(), active_color=T.VIOLET,
                                disabled=ro,
                                on_change=(None if ro else
                                           (lambda e: app._set_perf(e.control.value))))
        data = card(ft.Column([
            ft.Text("DATA & DIAGNOSTICS", size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow("Regression & sprint caches",
                 "Cached Azure data speeds up re-generating plans. Clear it if "
                 "stories or test cases look out of date.",
                 ghost_btn("Clear caches", icon=_ic("CLEANING_SERVICES_OUTLINED","DELETE_OUTLINE"),
                           on_click=lambda e: app._clear_caches())),
            divider(),
            srow("Performance logging",
                 "Append timing diagnostics to qa_perf.log. Leave on if I'm helping "
                 "you troubleshoot speed.",
                 perf_switch),
        ], spacing=0))

        reset = card(ft.Column([
            ft.Text("HELP & RESET", size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow("Welcome walkthrough",
                 "Replay the first-run guided tour of the app.",
                 ghost_btn("Replay", icon=_ic("SLIDESHOW_OUTLINED","PLAY_ARROW"),
                           on_click=lambda e: app._open_onboarding())),
            divider(),
            srow("Restore default preferences",
                 "Resets theme, language, and logging to defaults. Your saved "
                 "credentials and links are kept.",
                 danger_btn("Reset", icon=ft.Icons.RESTART_ALT,
                            on_click=lambda e: app._reset_prefs())),
        ], spacing=0))

        body = ft.Column([appearance, data, reset], spacing=16,
                         scroll=ft.ScrollMode.AUTO, expand=True)
        return app.shell("Settings", "Preferences for this device", body)

