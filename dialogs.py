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
    # Stack depth (see close_all_dialogs): Flet 0.85 stacks dialogs, so opening
    # one over another must be counted — `_dialog` alone only remembers the top.
    app._dialog_depth = int(getattr(app, "_dialog_depth", 0) or 0) + 1
    # Make the dialog BODY text selectable/copyable (the action buttons live in
    # dlg.actions, so they stay outside the selection region — buttons excluded).
    # Also wrap it in the same click-away GestureDetector used for the main
    # screen body (see shell()'s SelectionArea(GestureDetector(...))): any
    # AlertDialog renders on a separate overlay layer, outside that wrapper,
    # so a picker opened INSIDE a dialog (e.g. Sprint Summary's email
    # recipient picker) never saw empty-space taps and never got the chance
    # to close itself — only the main screen's inline pickers did. Nesting
    # the same "child GestureDetector wins over the SelectionArea ancestor
    # for empty space, while checkboxes/buttons keep their own taps" trick
    # here fixes click-away-to-close for every dialog in one place instead of
    # each modal needing its own copy of this wiring.
    try:
        c = getattr(dlg, "content", None)
        if c is not None and not isinstance(c, ft.SelectionArea):
            dlg.content = ft.SelectionArea(
                content=ft.GestureDetector(content=c, on_tap=app._close_dropdowns))
    except Exception:
        pass
    # Keep action buttons on ONE ROW. Flet's AlertDialog stacks multiple `actions`
    # vertically (and often reverses them). Wrapping them in a single right-aligned
    # Row fixes button layout for EVERY modal in one place.
    try:
        acts = getattr(dlg, "actions", None)
        if acts and len(acts) > 1:
            dlg.actions = [ft.Row(list(acts), alignment=ft.MainAxisAlignment.END,
                                  spacing=10, tight=True)]
    except Exception:
        pass
    # Refined popup chrome — ONE consistent look for every AlertDialog in the
    # app (14 call sites across 8 files previously each set their own bgcolor/
    # shape/padding ad hoc, some not at all, so panels drifted: different
    # corner radii, no border on some, default Material grey scrim behind
    # others). Applying it here means every dialog gets it automatically,
    # with no per-call-site upkeep. Individual dialogs keep full control over
    # their OWN title/content/icon colors — this only normalizes the outer
    # card (surface, border, corner radius, shadow, backdrop scrim, spacing).
    try:
        dlg.bgcolor = T.CARD
        dlg.shape = ft.RoundedRectangleBorder(
            radius=T.R_DLG, side=ft.BorderSide(1, T.BORDER))
        dlg.elevation = 0
        dlg.shadow_color = ft.Colors.with_opacity(0.4, "#05060F")
        dlg.surface_tint_color = ft.Colors.TRANSPARENT
        dlg.barrier_color = ft.Colors.with_opacity(0.55, "#05060F")
        dlg.title_padding = ft.Padding.only(left=26, right=26, top=24, bottom=6)
        dlg.content_padding = ft.Padding.symmetric(horizontal=26, vertical=6)
        dlg.actions_padding = ft.Padding.only(left=26, right=26, top=16, bottom=22)
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

def close_all_dialogs(app, limit=8):
    """Pop EVERY open dialog, not just the top one.

    ROOT CAUSE this fixes: Flet 0.85's dialog API is a STACK — `pop_dialog()`
    removes exactly ONE entry, and `app._dialog` only ever tracks the most
    recently shown one. So when two are stacked (the idle-logout warning shown
    ON TOP of an already-open "Create test plan" modal), sign-out's single
    `close_dialog()` popped the warning and left the create-plan modal sitting
    over the login screen — reported live as "the modal is still open after auto
    logout", with its stale error still visible.

    Drains via the depth counter kept by `show_dialog`/`close_dialog`, and is
    additionally bounded so a Flet build that never signals "empty" can't spin.
    Safe to call when nothing is open (it's a no-op).
    """
    for _ in range(max(1, int(limit))):
        if int(getattr(app, "_dialog_depth", 0) or 0) <= 0:
            break
        try:
            close_dialog(app)
        except Exception:
            break
    app._dialog_depth = 0
    app._dialog = None


def close_dialog(app):
    app._sum_loading = False
    # Track stack depth so close_all_dialogs() knows how many are open (see its
    # docstring). Floored at 0 — some paths call close on an already-empty stack.
    app._dialog_depth = max(0, int(getattr(app, "_dialog_depth", 0) or 0) - 1)
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
                          ft.Text(title, size=16, weight=ft.FontWeight.W_800,
                                  color=T.INK)], spacing=9),
            content=ft.Container(width=380, content=ft.Text(
                message, size=13, color=T.INK_2, weight=ft.FontWeight.W_500)),
            actions=[
                ghost_btn("Cancel", on_click=lambda e: app._close_dialog()),
                _yes_btn(yes_label, on_click=lambda e: (app._close_dialog(),
                                                        on_yes())),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=T.R_LG))
        app._show_dialog(dlg)
