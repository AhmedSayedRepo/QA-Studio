"""automation.py — Automation screen (self-healing Selenium generation).

Extracted from main.py (Step-4 modular refactor). screen(app) builds the
automation panel; regression.locked_state gates access.
"""
import os
import flet as ft
import theme as T
import regression
from ui import card, empty_state, sec_head, _btn_shadow, primary_btn, green_btn, ghost_btn


def _build_action_buttons(app, ready):
    """Build the Generate/Push button column fresh from current state.

    Extracted out of screen() so a running/finished/paused state change (e.g.
    during a Git push) can refresh JUST these buttons via _refresh_auto_state()
    below, instead of a full app.render(). A full render() rebuilds
    app._auto_log_col from scratch every time, which resets the Activity log's
    scroll position — doing that at the start AND end of every push (plus again
    on a Force-push retry) was making the log rail visibly jump/scroll-to-top
    several times over the course of a single push."""
    gen_disabled = app._auto_running or not ready
    if app._auto_running:
        # While running, show Stop + Pause/Resume side by side (matching shadow)
        _stop_btn = ft.FilledButton(
            content=ft.Row(
                [ft.Icon(ft.Icons.STOP_CIRCLE, size=18, color="#FFFFFF"),
                 ft.Text("Stop", size=14, weight=ft.FontWeight.BOLD,
                         color="#FFFFFF")],
                spacing=8, tight=True,
                alignment=ft.MainAxisAlignment.CENTER),
            height=46, expand=True, on_click=lambda e: app._stop_automation(),
            style=ft.ButtonStyle(
                bgcolor={"": T.RED}, color={"": "#FFFFFF"}, elevation=0,
                shape=ft.RoundedRectangleBorder(radius=T.R),
                padding=ft.Padding.symmetric(horizontal=14, vertical=0)))
        _paused = bool(getattr(app, "_auto_paused", False))
        if _paused:
            _pr_label, _pr_icon, _pr_col = "Resume", ft.Icons.PLAY_ARROW, T.GREEN
            _pr_click = lambda e: app._resume_automation()
        else:
            _pr_label, _pr_icon, _pr_col = "Pause", ft.Icons.PAUSE_CIRCLE, T.AMBER
            _pr_click = lambda e: app._pause_automation()
        _pr_btn = ft.FilledButton(
            content=ft.Row(
                [ft.Icon(_pr_icon, size=18, color="#FFFFFF"),
                 ft.Text(_pr_label, size=14, weight=ft.FontWeight.BOLD,
                         color="#FFFFFF")],
                spacing=8, tight=True,
                alignment=ft.MainAxisAlignment.CENTER),
            height=46, expand=True, on_click=_pr_click,
            style=ft.ButtonStyle(
                bgcolor={"": _pr_col}, color={"": "#FFFFFF"}, elevation=0,
                shape=ft.RoundedRectangleBorder(radius=T.R),
                padding=ft.Padding.symmetric(horizontal=14, vertical=0)))
        _stop_w = ft.Container(_stop_btn, border_radius=T.R,
                               shadow=_btn_shadow(T.RED, 0.55), expand=True)
        _pr_w = ft.Container(_pr_btn, border_radius=T.R,
                             shadow=_btn_shadow(_pr_col, 0.55), expand=True)
        gen_btn = ft.Row([_stop_w, _pr_w], spacing=10)
    else:
        gen_btn = primary_btn(
            "Generate automation scripts",
            icon=ft.Icons.AUTO_AWESOME, expand=True, disabled=gen_disabled,
            on_click=lambda e: app._start_automation())

    # Push is enabled ANYTIME a real project folder + Git repo/token are set —
    # so a forgotten/earlier run can be pushed without regenerating — not only
    # right after a generation.
    _proj = (app.auto_local_path or "").strip()
    _can_push = bool(_proj and os.path.isdir(_proj)
                     and app.auto_git_url.strip() and app.auto_git_token.strip())
    push_disabled = app._auto_running or not (_can_push or app._auto_built)
    push_btn = green_btn("Push to Git", icon=ft.Icons.CLOUD_UPLOAD_OUTLINED,
                         expand=True, on_click=lambda e: app._push_automation())
    # grey it out visually when disabled
    if push_disabled:
        push_btn = ft.Row([ft.OutlinedButton(
            "Push to Git", icon=ft.Icons.CLOUD_UPLOAD_OUTLINED, height=42,
            disabled=True, expand=True,
            style=ft.ButtonStyle(color=T.INK_3, side=ft.BorderSide(1, T.BORDER),
                shape=ft.RoundedRectangleBorder(radius=T.R)))], spacing=0)

    return ft.Column([
        ft.Row([gen_btn], spacing=0),
        ft.Row([push_btn], spacing=0),
    ], spacing=14)


def _refresh_auto_state(app):
    """Scoped update of just the action buttons + Activity spinner, driven by
    app._auto_running / app._auto_paused. Returns True if it found live refs to
    update (screen() has run at least once); False means the caller should fall
    back to a full app.render() (e.g. the very first time)."""
    ctr = getattr(app, "_auto_buttons_ctr", None)
    if ctr is None:
        return False
    try:
        ctr.content = _build_action_buttons(app, getattr(app, "_auto_buttons_ready", False))
        ctr.update()
    except Exception:
        return False
    sctr = getattr(app, "_auto_spinner_ctr", None)
    if sctr is not None:
        try:
            sctr.content = (ft.ProgressRing(width=15, height=15, stroke_width=2, color=T.VIOLET)
                            if app._auto_running else ft.Icon(ft.Icons.TERMINAL, size=15, color=T.INK_3))
            sctr.update()
        except Exception:
            pass
    return True


def screen(app):
        if not app.readonly and not (app.connected and app.project and app.plan_id and app.story_ids):
            return regression.locked_state(
                app, "Automation",
                "Generate self-healing Selenium tests from your stories",
                "Connect your account, then pick a project, test plan, and user "
                "stories on the Setup screen — automation runs on that same "
                "selection.")
        # ── left: config form ──
        ready = bool(app.story_ids and app.project and app.plan_id)
        setup_hint = None
        if not ready:
            setup_hint = ft.Container(
                ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=T.AMBER),
                        ft.Text("Select a project, test plan, and user stories on the Setup "
                                "screen first — automation uses the same selection.",
                                size=12, color=T.AMBER, weight=ft.FontWeight.W_500, expand=True)],
                       spacing=8),
                padding=12, bgcolor=T.AMBER_SOFT, border_radius=T.R,
                border=ft.Border.all(1, "#EAD9A8"), margin=ft.Margin.only(bottom=14))

        def _target_opt(key, label, sub, enabled=True):
            active = (getattr(app, "_auto_target", "selenium") == key)
            def _pick(e, k=key):
                app._auto_target = k
                app.render()
            cont = ft.Container(
                ft.Column([
                    ft.Text(label, size=13, weight=ft.FontWeight.BOLD,
                            color=(T.VIOLET if active else T.INK_2)),
                    ft.Text(sub, size=10.5, color=T.INK_3, weight=ft.FontWeight.W_500),
                ], spacing=2),
                padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                border=ft.Border.all(2 if active else 1, T.VIOLET if active else T.BORDER),
                border_radius=T.R, expand=1, bgcolor=T.CARD_2,
                opacity=(1 if enabled else 0.5),
                tooltip=(None if enabled else "Coming soon"),
                animate_scale=ft.Animation(110, ft.AnimationCurve.EASE_OUT),
                on_click=(_pick if enabled else None))
            if enabled and not active:
                # hover: tint the frame violet + a subtle lift (no shadow halo)
                def _hover(e, _c=cont):
                    on = e.data in (True, "true", "True")
                    _c.border = ft.Border.all(2 if on else 1,
                                              T.VIOLET if on else T.BORDER)
                    _c.scale = 1.02 if on else 1.0
                    _c.update()
                cont.on_hover = _hover
            return cont

        framework_card = card(ft.Column([
            sec_head("A", "Test framework"),
            ft.Container(height=10),
            ft.Row([
                _target_opt("selenium", "Selenium", "Java · TestNG"),
                _target_opt("playwright", "Playwright", "JavaScript"),
                _target_opt("cypress", "Cypress", "JavaScript"),
            ], spacing=10),
            ft.Container(height=6),
            ft.Text("All targets share the same AI-generated steps and self-healing "
                    "locators — only the emitted project differs.",
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

        site_card = card(ft.Column([
            sec_head("B", "Target site"),
            ft.Container(height=10),
            app._auto_field("Site URL", "auto_site_url",
                             "https://your-app.example.com/page", req=True),
            ft.Container(height=12),
            app._auto_field("Login page URL", "auto_login_url",
                             "https://your-app.example.com/login (defaults to site URL)"),
            ft.Container(height=6),
            ft.Text("Both URLs seed the generated project's config. Login credentials "
                    "are NOT entered here — the tests read APP_USER / APP_PASS from "
                    "their .env / config.properties at run time (see the project README).",
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

        git_card = card(ft.Column([
            sec_head("C", "Git destination (your IDE syncs this)"),
            ft.Container(height=10),
            app._auto_field("Repository URL", "auto_git_url",
                             "https://github.com/you/automation-tests.git", req=True),
            ft.Container(height=10),
            ft.Row([
                ft.Container(app._auto_field("Branch", "auto_git_branch", "main"), expand=1),
                ft.Container(app._auto_field("Access token (PAT)", "auto_git_token",
                                 "ghp_… or Azure PAT", password=True, req=True,
                                 info="How to create a Git access token (PAT)",
                                 on_info=lambda e: app._show_help("git_pat")), expand=1),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Container(height=4),
            ft.Text("The token is used only to push and is stored locally like your other "
                    "credentials. It is scrubbed from logs.",
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

        local_card = card(ft.Column([
            sec_head("D", "Project folder (needed to Push to Git)"),
            ft.Container(height=10),
            app._auto_field("Save project to folder", "auto_local_path",
                             r"e.g. C:\Users\you\IdeaProjects\automation-tests"),
            ft.Container(height=8),
            ft.Row([ghost_btn("Browse…", icon=ft.Icons.FOLDER_OPEN,
                              on_click=app._browse_auto_folder)], spacing=8),
            ft.Container(height=6),
            ft.Text("The generated project is written here so you can open it in your "
                    "IDE. Pick an EXISTING project folder to Push a forgotten run to Git "
                    "without regenerating. Leave blank to use a temp folder.",
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

        # Buttons live in their own Container so a running/paused/finished state
        # change can refresh JUST this (see _refresh_auto_state) instead of a
        # full app.render() — see _build_action_buttons' docstring for why.
        app._auto_buttons_ready = ready
        app._auto_buttons_ctr = ft.Container(_build_action_buttons(app, ready))

        left = ft.Column([
            *([setup_hint] if setup_hint else []),
            framework_card,
            site_card,
            git_card,
            local_card,
            app._auto_buttons_ctr,
        ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)

        # ── right: live counters + clean log ──
        log_lines = [app._auto_log_line(ln.get("msg", ""), ln.get("tone", "dim"))
                     for ln in app._auto_log]
        if not log_lines:
            log_lines = [empty_state(
                ft.Icons.TERMINAL, "No activity yet",
                "Fill in the site and Git details, then Generate — "
                "each step shows up here live.")]
        app._auto_log_col = ft.Column(log_lines, spacing=3, scroll=ft.ScrollMode.AUTO,
                                       expand=True, auto_scroll=True)

        spinner = (ft.ProgressRing(width=15, height=15, stroke_width=2, color=T.VIOLET)
                   if app._auto_running else ft.Icon(ft.Icons.TERMINAL, size=15, color=T.INK_3))
        app._auto_spinner_ctr = ft.Container(spinner)

        def _log_tool_btn(icon, tip, cb, danger=False):
            # Small square icon-button "chip": rounded like the delete button on
            # Useful Links, tinted red for the destructive Clear action so it
            # reads differently from the neutral Copy action at a glance.
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

        # Pinned toolbar: spinner + title + Copy/Clear, fixed at the very top of
        # the log rail (a sibling of the scrollable column, not inside it) so it
        # never scrolls away with the log — only the log content beneath scrolls.
        log_toolbar = ft.Container(
            ft.Row([app._auto_spinner_ctr,
                    ft.Text("ACTIVITY", size=11, weight=ft.FontWeight.BOLD,
                           color=T.INK_3, expand=True),
                    _log_tool_btn(ft.Icons.COPY_ALL_OUTLINED, "Copy entire log",
                                 app._copy_auto_log),
                    ft.Container(width=6),
                    _log_tool_btn(ft.Icons.DELETE_OUTLINE, "Clear log",
                                 app._clear_auto_log, danger=True)],
                   spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.only(bottom=10),
            margin=ft.Margin.only(bottom=10),
            border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER)))

        right = ft.Column([
            card(ft.Column([
                app._auto_counts_header(),
                ft.Container(height=12),
                ft.Container(
                    ft.Column([
                        log_toolbar,
                        ft.Container(ft.SelectionArea(content=app._auto_log_col), expand=True),
                    ], spacing=0, expand=True),
                    expand=True, bgcolor=T.CARD_2,
                    border=ft.Border.all(1, T.BORDER), border_radius=T.R, padding=12),
            ], spacing=0, expand=True), expand=True),
        ], spacing=14, expand=True)

        body = ft.Row([ft.Container(left, expand=True),
                       ft.Container(right, width=384)], spacing=22,
                      vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
        sub = (f"{len(app.story_ids)} stories selected" if app.story_ids else "no stories selected")
        return app.shell("Automation", sub, body)

    # ---- activity panel: live counters + clean, RTL-aware log lines ----
