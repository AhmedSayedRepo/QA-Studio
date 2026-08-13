"""setup.py — Setup screen (connection + generation config assembler).

Extracted from main.py (Step-5 modular refactor). screen(app) assembles the
connection/config cards; the heavy sub-builders (dropdowns, story picker,
fetch/estimate handlers) stay as methods on the QAStudio instance.
"""
import flet as ft
import theme as T
import engine as E
from ui import card, sec_head


def screen(app):
        # Clear validation-red for any required field that's now filled.
        _inv = getattr(app, "_invalid", None)
        if _inv:
            if app.project: _inv.discard("project")
            if app.plan_id: _inv.discard("plan")
            if app.story_ids: _inv.discard("stories")
        # default provider choice
        if not hasattr(app, "_provider_choice"):
            names = list(E.AI_CONFIG.keys())
            app._provider_choice = names[0] if names else "anthropic"

        # ── Connection card ──
        app.err_text = ft.Text(getattr(app, "_err_msg", "") or "", size=12, color=T.RED, weight=ft.FontWeight.BOLD)

        if app.connected:
            conn_body = app._connection_saved()
        else:
            conn_body = app._connection_edit()

        connection_card = card(ft.Column([
            sec_head("1", "Connection",
                     ft.Text("set once · reused every run", size=11, color=T.INK_3, weight=ft.FontWeight.BOLD)),
            ft.Container(height=12),
            conn_body,
        ], spacing=0))

        # ── Tool selector card ──
        # Output-language description as a STABLE cell so the language picker
        # can refresh it in place (like the provider/model pickers) — no render.
        def _lang_desc_ctrl():
            _lw = E.LANGUAGES.get(getattr(app, "run_lang", app.lang), E.LANGUAGES["en"])["name"]
            _txt = (f"Adds detailed {_lw} steps — precondition · action · expected — to existing test cases."
                    if app.tool == "steps" else
                    f"Reads each user story and writes {_lw} test-case titles into the plan, skipping duplicates.")
            return ft.Text(_txt, size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500)
        app._setup_desc_build = _lang_desc_ctrl
        app._setup_desc_cell = ft.Container(_lang_desc_ctrl())
        tool_card = card(ft.Column([
            sec_head("2", "What to generate",
                     ft.Text("one app · two generators", size=11, color=T.INK_3, weight=ft.FontWeight.BOLD)),
            ft.Container(height=12),
            # persist=False: Setup is a per-run override of Settings' saved
            # default, not another way to redefine it — see main.py's
            # _set_tool/_set_lang docstrings. Settings' own toggle (which
            # reuses these same segment builders with the default
            # persist=True) is the only thing meant to change what future
            # sessions start with.
            app._tool_segment(persist=False),
            ft.Container(height=14),
            # Output language toggle
            ft.Row([
                ft.Text("Output language", size=12, weight=ft.FontWeight.BOLD, color=T.INK_2),
                ft.Container(expand=True),
                app._lang_segment(persist=False),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=10),
            app._setup_desc_cell,
        ], spacing=0))

        # ── Task card (gated) ──
        # Read-only Viewers see the real task card (empty pickers, disabled) instead
        # of the "locked" placeholder — consistent with view-only content elsewhere.
        # Its story loader is gated on plan_id (Viewers have none), so it won't fetch.
        task_card = app._task_card() if (app.connected or app.readonly) else app._task_locked()

        app._left_scroll = ft.Column(
            [connection_card, tool_card, task_card],
            spacing=14, scroll=ft.ScrollMode.AUTO, expand=True,
            key="setup_scroll", on_scroll=app._track_scroll)
        left = app._left_scroll

        right = app._setup_right()

        import platform_caps as _pc
        if _pc.is_mobile():
            # Phone (mobile Phase 2): the 290px sidebar doesn't fit a ~390px
            # screen, BUT _setup_right() is not merely supplementary — before
            # connecting it IS the "Connect & load projects" button (the only
            # way to validate the PAT and load Azure DevOps projects/plans),
            # and once connected it's the run-summary + Start button. Dropping
            # it left no way to connect on mobile at all. Insert it as the
            # FIRST item of the same scrolling column instead of a separate
            # expand=True region — right's card was built for a Row's cross-
            # axis stretch (fills the row's height); in a scrolling list it
            # must size to its own content, so the stretch flags are cleared.
            right.expand = False
            if isinstance(right.content, ft.Column):
                right.content.expand = False
                right.content.tight = True
            app._left_scroll.controls.insert(0, right)
            app._left_scroll.controls.insert(1, ft.Container(height=14))
            body = left
        else:
            body = ft.Row([
                ft.Container(left, expand=True),
                ft.Container(right, width=290),
            ], spacing=22, vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)

        sub = "1 of 2 — configure & run" if app.connected else "1 of 2 — connect first"
        right_tag = ft.Container(
            ft.Row([ft.Icon(ft.Icons.SHIELD_OUTLINED, size=13, color=T.INK_2),
                    ft.Text("Credentials saved on this device", size=11, color=T.INK_2, weight=ft.FontWeight.BOLD)],
                   spacing=5, tight=True),
            padding=ft.Padding.symmetric(vertical=10, horizontal=5), bgcolor=T.CARD_2, border_radius=20,
            border=ft.Border.all(1, T.BORDER))
        return app.shell("Setup", sub, body, right_tag)
