"""log_panel.py - the toolbar pieces every activity log shares (Generate,
Automation, …): the small Copy / Save / Clear icon buttons and the All / Issues
filter chips. The plain-data side (grouping, filters, copy / save text) lives
in log_view.py, which has no Flet."""
import flet as ft
import theme as T
import strings


def tool_btn(icon, tip, cb, danger=False):
    """Small rounded icon-button chip; red for a destructive action (Clear)."""
    return ft.Container(
        ft.IconButton(
            icon, icon_size=15,
            icon_color=(T.RED if danger else T.INK_3),
            tooltip=tip, on_click=cb, width=26, height=26,
            style=ft.ButtonStyle(padding=0,
                                 shape=ft.RoundedRectangleBorder(radius=7))),
        bgcolor=(T.RED_SOFT if danger else T.CARD),
        border=ft.Border.all(1, (T.RED_SOFT if danger else T.BORDER)),
        border_radius=8)


def filter_chips(issues_only, n_issues, on_set):
    """All / Issues chips; the Issues chip shows how many warnings and errors
    the log has. `on_set(True|False)` switches the filter."""
    def chip(label, active, value):
        return ft.Container(
            ft.Text(label, size=10.5, weight=ft.FontWeight.BOLD, no_wrap=True,
                    color=(T.VIOLET_INK if active else T.INK_3)),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border_radius=7, bgcolor=(T.VIOLET_SOFT if active else T.CARD),
            border=ft.Border.all(1, T.VIOLET if active else T.BORDER),
            on_click=lambda e, v=value: on_set(v))
    issues = strings.t("auto_log_issues") + (" %d" % n_issues if n_issues else "")
    return ft.Row([chip(strings.t("auto_log_all"), not issues_only, False),
                   chip(issues, bool(issues_only), True)],
                  spacing=4, tight=True)


def save_btn(cb):
    return tool_btn(ft.Icons.DOWNLOAD, strings.t("log_save_tip"), cb)
