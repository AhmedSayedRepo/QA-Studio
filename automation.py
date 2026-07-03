"""automation.py — Automation screen (self-healing Selenium generation).

Extracted from main.py (Step-4 modular refactor). screen(app) builds the
automation panel; regression.locked_state gates access.
"""
import flet as ft
import theme as T
import regression
from ui import card, empty_state, sec_head, _btn_shadow, primary_btn, green_btn


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

        site_card = card(ft.Column([
            sec_head("A", "Target site"),
            ft.Container(height=10),
            app._auto_field("Site URL", "auto_site_url",
                             "https://your-app.example.com/page", req=True),
            ft.Container(height=12),
            ft.Text("LOGIN (required to reach the pages)", size=10.5,
                    weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=8),
            app._auto_field("Login page URL", "auto_login_url",
                             "https://your-app.example.com/login (defaults to site URL)"),
            ft.Container(height=10),
            ft.Row([
                ft.Container(app._auto_field("Username", "auto_login_user", "user@example.com"), expand=1),
                ft.Container(app._auto_field("Password", "auto_login_pass", "••••••••", password=True), expand=1),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ], spacing=0))

        git_card = card(ft.Column([
            sec_head("B", "Git destination (IntelliJ syncs this)"),
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
            sec_head("C", "Local copy (optional)"),
            ft.Container(height=10),
            app._auto_field("Save project to folder", "auto_local_path",
                             r"e.g. C:\Users\you\IdeaProjects\automation-tests"),
            ft.Container(height=4),
            ft.Text("If set, the generated Maven project is also copied here so you can "
                    "open it directly in IntelliJ. Leave blank to use a temp folder.",
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

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

        push_disabled = app._auto_running or not app._auto_built
        push_btn = green_btn("Push to Git", icon=ft.Icons.CLOUD_UPLOAD_OUTLINED,
                             expand=True, on_click=lambda e: app._push_automation())
        # grey it out visually when disabled
        if push_disabled:
            push_btn = ft.Row([ft.OutlinedButton(
                "Push to Git", icon=ft.Icons.CLOUD_UPLOAD_OUTLINED, height=42,
                disabled=True, expand=True,
                style=ft.ButtonStyle(color=T.INK_3, side=ft.BorderSide(1, T.BORDER),
                    shape=ft.RoundedRectangleBorder(radius=T.R)))], spacing=0)

        left = ft.Column([
            *([setup_hint] if setup_hint else []),
            site_card,
            git_card,
            local_card,
            ft.Row([gen_btn], spacing=0),
            ft.Row([push_btn], spacing=0),
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
        right = ft.Column([
            card(ft.Column([
                ft.Row([spinner, ft.Text("ACTIVITY", size=11, weight=ft.FontWeight.BOLD,
                                         color=T.INK_3)], spacing=8),
                ft.Container(height=12),
                app._auto_counts_header(),
                ft.Container(height=12),
                ft.Container(ft.SelectionArea(content=app._auto_log_col), expand=True, bgcolor=T.CARD_2,
                             border=ft.Border.all(1, T.BORDER), border_radius=T.R, padding=12),
            ], spacing=0, expand=True), expand=True),
        ], spacing=14, expand=True)

        body = ft.Row([ft.Container(left, expand=True),
                       ft.Container(right, width=384)], spacing=22,
                      vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
        sub = (f"{len(app.story_ids)} stories selected" if app.story_ids else "no stories selected")
        return app.shell("Automation", sub, body)

    # ---- activity panel: live counters + clean, RTL-aware log lines ----
