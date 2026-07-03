"""dialogs.py — dialog / confirm helpers.

Extracted from main.py (Step-6 modular refactor). show_dialog / close_dialog /
confirm take the QAStudio app; the app keeps thin delegator methods so the
many self._show_dialog / self._close_dialog call-sites keep working.
"""
import flet as ft
import theme as T
from ui import danger_btn, green_btn, ghost_btn


def show_dialog(app, dlg):
    app._dialog = dlg
    # Make the dialog BODY text selectable/copyable (the action buttons live in
    # dlg.actions, so they stay outside the selection region — buttons excluded).
    try:
        c = getattr(dlg, "content", None)
        if c is not None and not isinstance(c, ft.SelectionArea):
            dlg.content = ft.SelectionArea(content=c)
    except Exception:
        pass
    # Flet 0.85 uses page.show_dialog(); 0.24-0.79 uses page.open(); older sets page.dialog
    if hasattr(app.page, "show_dialog"):
        app.page.show_dialog(dlg)
    elif hasattr(app.page, "open"):
        app.page.open(dlg)
    else:
        app.page.dialog = dlg
        dlg.open = True
        app.page.update()

def close_dialog(app):
    app._sum_loading = False
    dlg = getattr(app, "_dialog", None)
    # Flet 0.85 uses page.pop_dialog(); older uses page.close(dlg)
    if hasattr(app.page, "pop_dialog"):
        try:
            app.page.pop_dialog()
            return
        except Exception:
            pass
    if dlg is not None and hasattr(app.page, "close"):
        try:
            app.page.close(dlg)
            return
        except Exception:
            pass
    if dlg is not None:
        dlg.open = False
        app.page.update()

def confirm(app, title, message, on_yes, yes_label="Remove", danger=True,
             icon=ft.Icons.HELP_OUTLINE):
    """Lightweight confirm via a floating snackbar with an action button. Shows
    INSTANTLY even over a heavy table — a modal AlertDialog has to render over
    (and re-lay-out) the whole page behind its barrier, which made it lag; the
    floating snackbar is the same fast path as the toasts. Calls on_yes() only
    when the user taps the action."""
    try:
        sb = ft.SnackBar(
            content=ft.Row([
                ft.Icon(icon, color="#FFFFFF", size=18),
                ft.Text(message, color="#FFFFFF", size=13,
                        weight=ft.FontWeight.W_600, expand=True),
            ], spacing=10),
            bgcolor=T.INK, duration=7000,
            behavior=ft.SnackBarBehavior.FLOATING,
            shape=ft.RoundedRectangleBorder(radius=12),
            margin=ft.Margin.all(16),
            padding=ft.Padding.symmetric(vertical=12, horizontal=16),
            action=yes_label,
            action_color=(T.RED if danger else T.GREEN),
            # Run the action directly on the page thread. (Deferring it through
            # ui_safe -> page.run_thread executed the heavy delete + re-render
            # OFF the UI thread, which wedged Flet's event loop — the whole app
            # stopped responding after a couple of deletes.)
            on_action=lambda e: on_yes())
        # Properly dismiss any showing snackbar first (Flet shows one at a time;
        # just dropping it from the overlay left its state "open" and blocked
        # the next one), then mount + open this one.
        for c in list(app.page.overlay):
            if isinstance(c, ft.SnackBar):
                try:
                    c.open = False
                except Exception:
                    pass
        app.page.overlay[:] = [c for c in app.page.overlay
                                if not isinstance(c, ft.SnackBar)]
        app.page.overlay.append(sb)
        sb.open = True
        app.page.update()
    except Exception:
        # If the action snackbar isn't supported, fall back to the modal dialog.
        _yes_btn = danger_btn if danger else green_btn
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([ft.Icon(icon, size=20, color=(T.RED if danger else T.VIOLET)),
                          ft.Text(title, size=15, weight=ft.FontWeight.BOLD,
                                  color=T.INK)], spacing=9),
            content=ft.Container(width=380, content=ft.Text(
                message, size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500)),
            actions=[
                ghost_btn("Cancel", on_click=lambda e: app._close_dialog()),
                _yes_btn(yes_label, on_click=lambda e: (app._close_dialog(),
                                                        on_yes())),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=T.R_LG))
        app._show_dialog(dlg)
