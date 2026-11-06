"""automation.py — Automation screen (self-healing Selenium generation).

Extracted from main.py (Step-4 modular refactor). screen(app) builds the
automation panel; regression.locked_state gates access.
"""
import os
import flet as ft
import theme as T
import regression
import strings
import backend_setup
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
                 ft.Text(strings.t("auto_stop"), size=14, weight=ft.FontWeight.BOLD,
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
            _pr_label, _pr_icon, _pr_col = strings.t("auto_resume"), ft.Icons.PLAY_ARROW, T.GREEN
            _pr_click = lambda e: app._resume_automation()
        else:
            _pr_label, _pr_icon, _pr_col = strings.t("auto_pause"), ft.Icons.PAUSE_CIRCLE, T.AMBER
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
            strings.t("auto_generate"),
            icon=ft.Icons.AUTO_AWESOME, expand=True, disabled=gen_disabled,
            on_click=lambda e: app._start_automation())

    # Push is enabled ANYTIME a real project folder + Git repo/token are set —
    # so a forgotten/earlier run can be pushed without regenerating — not only
    # right after a generation.
    _proj = (app.auto_local_path or "").strip()
    _can_push = bool(_proj and os.path.isdir(_proj)
                     and app.auto_git_url.strip() and app.auto_git_token.strip())
    push_disabled = app._auto_running or not (_can_push or app._auto_built)
    push_btn = green_btn(strings.t("auto_push"), icon=ft.Icons.CLOUD_UPLOAD_OUTLINED,
                         expand=True, on_click=lambda e: app._push_automation())
    # grey it out visually when disabled
    if push_disabled:
        push_btn = ft.Row([ft.OutlinedButton(
            strings.t("auto_push"), icon=ft.Icons.CLOUD_UPLOAD_OUTLINED, height=42,
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


def _result_banner_content(app):
    """End-of-run banner body from app._auto_result, or None while running /
    before any run."""
    _res = getattr(app, "_auto_result", None)
    if not _res or app._auto_running:
        return None
    import log_view
    _kind, _detail = _res
    _tone = log_view.banner_tone(_kind)
    _fg = {"ok": T.GREEN, "err": T.RED, "warn": T.AMBER}.get(_tone, T.INK_2)
    _bg = {"ok": T.GREEN_SOFT, "err": T.RED_SOFT, "warn": T.AMBER_SOFT}.get(_tone, T.CARD)
    _icon = {"ok": ft.Icons.CHECK_CIRCLE, "err": ft.Icons.ERROR_OUTLINE,
             "warn": ft.Icons.PAUSE_CIRCLE_OUTLINE}.get(_tone, ft.Icons.INFO_OUTLINE)
    _text = strings.t("auto_result_" + _kind, detail=_detail)
    return ft.Container(
        ft.Row([ft.Icon(_icon, size=16, color=_fg),
                ft.Text(_text, size=11.5, weight=ft.FontWeight.BOLD, color=_fg,
                        expand=True, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)],
               spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
        bgcolor=_bg, border_radius=T.R, border=ft.Border.all(1, _fg))


def _log_placeholder():
    return ft.Container(empty_state(
        ft.Icons.TERMINAL, strings.t("auto_no_activity"),
        strings.t("auto_no_activity_hint")), height=320)


def refresh_run_ui(app, starting=False):
    """In-place update for a Generate run's start/end: buttons, spinner, the four
    cards, the result banner and (on start) an emptied log. Returns False when the
    Automation screen isn't showing or hasn't been built — caller falls back to
    app.render()."""
    if getattr(app, "active", None) != "automation":
        return False
    if not _refresh_auto_state(app):
        return False
    counts = app._auto_count()
    for key, ctl in (getattr(app, "_auto_count_ctl", None) or {}).items():
        try:
            ctl.value = str(counts.get(key, ""))
            ctl.update()
        except Exception:
            pass
    rctr = getattr(app, "_auto_result_ctr", None)
    if rctr is not None:
        try:
            body = _result_banner_content(app)
            rctr.content = body
            rctr.visible = body is not None
            rctr.update()
        except Exception:
            pass
    col = getattr(app, "_auto_log_col", None)
    if col is not None:
        try:
            with app._auto_log_ui_lock:
                if starting and not app._auto_log:
                    col.controls = [_log_placeholder()]
                    col._qa_placeholder = True
                else:
                    # Running -> finished changes case auto-collapse.
                    app._apply_log_view(col)
                col.update()
        except Exception:
            pass
    return True


def screen(app):
        # No connect gate: a local CSV/XLSX needs no test-management tool. Only
        # the tracker source does — it's shown disabled with a Setup hint.
        tracker_ok = backend_setup.tracker_available(app)
        regression._auto_init(app)
        # ── left: config form ──
        file_source = backend_setup.automation_uses_file_source(app)
        ready = bool((app.auto_test_case_file or "").strip()) if file_source else bool(
            app._auto_plans_selected and app._auto_selected)

        def _auto_refresh_gen_btn():
            app._auto_buttons_ready = (bool((app.auto_test_case_file or "").strip())
                                        if backend_setup.automation_uses_file_source(app)
                                        else bool(app._auto_plans_selected and app._auto_selected))
            _refresh_auto_state(app)
        app._auto_refresh_gen_btn = _auto_refresh_gen_btn

        setup_hint = None
        if not ready:
            setup_hint = ft.Container(
                ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=T.AMBER),
                        ft.Text(strings.t("auto_file_setup_hint") if file_source else strings.t("auto_setup_hint"),
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
                tooltip=(None if enabled else strings.t("auto_coming_soon")),
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

        def _source_option(key, title, desc, icon, enabled=True):
            active = (("file" if file_source else "backend") == key)
            def _pick(e, value=key):
                app.auto_source_mode = value
                app._auto_invalid = set()
                app._save_git_creds()
                app.render()
            return ft.Container(
                ft.Row([ft.Icon(icon, size=19, color=T.VIOLET if active else T.INK_3),
                        ft.Column([ft.Text(title, size=12.5, weight=ft.FontWeight.BOLD,
                                           color=T.VIOLET_INK if active else T.INK_2),
                                   ft.Text(desc, size=10.5, color=T.INK_3)],
                                  spacing=2, expand=True)], spacing=8),
                padding=ft.Padding.symmetric(horizontal=12, vertical=10), expand=True,
                bgcolor=T.VIOLET_SOFT if active else T.CARD_2,
                border=ft.Border.all(2 if active else 1, T.VIOLET if active else T.BORDER),
                border_radius=T.R, on_click=(_pick if enabled else None),
                opacity=(1 if enabled else 0.55),
                tooltip=(None if enabled else strings.t("auto_source_needs_connect")))

        if file_source:
            selected_file = (getattr(app, "auto_test_case_file", "") or "").strip()
            file_name = os.path.basename(selected_file) if selected_file else strings.t("auto_file_none")
            source_details = ft.Column([
                ft.Text(strings.t("auto_file_label"), size=12.5, weight=ft.FontWeight.BOLD,
                        color=T.INK),
                ft.Container(height=5),
                ft.Row([ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=18, color=T.VIOLET),
                        ft.Text(file_name, size=12, color=T.INK_2, expand=True,
                                no_wrap=True)], spacing=8),
                ft.Container(height=8),
                ft.Row([ghost_btn(strings.t("auto_file_browse"), icon=ft.Icons.UPLOAD_FILE,
                                  on_click=app._browse_auto_test_case_file),
                        ghost_btn(strings.t("auto_copy_prompt"), icon=ft.Icons.CONTENT_COPY,
                                  on_click=app._copy_external_case_prompt)],
                       spacing=8, wrap=True),
                ft.Container(height=7),
                ft.Text(strings.t("auto_file_hint"), size=10.5, color=T.INK_3,
                        weight=ft.FontWeight.W_500),
            ], spacing=0)
        else:
            # Reuse the established plan/story pickers without nesting its
            # source card (and duplicate heading) inside this source switcher.
            _azure_card = regression.automation_source_card(app)
            source_details = ft.Column(list(_azure_card.content.controls[2:]), spacing=0)

        def _flip_only_automation(e):
            # Saved immediately; read by the worker when loading cases (no render).
            app.auto_only_automation = bool(e.control.value)
            try:
                app.creds["auto_only_automation"] = app.auto_only_automation
                import store
                store.save(app.creds)
            except Exception:
                pass

        only_tagged = ([] if file_source else [
            ft.Container(height=10),
            ft.Row([
                ft.Checkbox(value=bool(getattr(app, "auto_only_automation", False)),
                            active_color=T.VIOLET, on_change=_flip_only_automation),
                ft.Text(strings.t("auto_only_tagged"), size=11.5, color=T.INK_2, expand=True),
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ])

        source_card = card(ft.Column([
            sec_head("A", strings.t("auto_sec_source")),
            ft.Container(height=10),
            ft.Row([
                _source_option("backend",
                               backend_setup.label_for(backend_setup.active(app.creds)),
                               (strings.t("auto_source_backend_desc") if tracker_ok
                                else strings.t("auto_source_needs_connect")),
                               ft.Icons.CLOUD_OUTLINED, enabled=tracker_ok),
                _source_option("file", strings.t("auto_source_file"),
                               strings.t("auto_source_file_desc"), ft.Icons.UPLOAD_FILE),
            ], spacing=10),
            ft.Container(height=12),
            source_details,
            *only_tagged,
        ], spacing=0))

        framework_card = card(ft.Column([
            sec_head("B", strings.t("auto_sec_framework")),
            ft.Container(height=10),
            ft.Row([
                _target_opt("selenium", "Selenium", "Java · TestNG"),
                _target_opt("playwright", "Playwright", "JavaScript"),
                _target_opt("cypress", "Cypress", "JavaScript"),
            ], spacing=10),
            ft.Container(height=6),
            ft.Text(strings.t("auto_framework_note"),
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

        site_card = card(ft.Column([
            sec_head("C", strings.t("auto_sec_site")),
            ft.Container(height=10),
            app._auto_field(strings.t("auto_lbl_site_url"), "auto_site_url",
                             "https://your-app.example.com/page", req=True),
            ft.Container(height=12),
            app._auto_field(strings.t("auto_lbl_login_url"), "auto_login_url",
                             "https://your-app.example.com/login (defaults to site URL)"),
            ft.Container(height=6),
            ft.Text(strings.t("auto_site_note"),
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

        def _flip_inspection(e):
            # In place: show/hide the always-built details panel (no render(),
            # so the form keeps its scroll position and typed values).
            app.auto_live_inspection = bool(e.control.value)
            app._auto_live_confirm = False
            app._auto_inspection_confirm_invalid = False
            confirm_box.value = False
            confirm_error.visible = False
            inspection_details.visible = app.auto_live_inspection
            try:
                inspection_details.update()
            except Exception:
                app.render()   # not mounted yet — fall back to a full build

        confirm_error = ft.Text(
            strings.t("auto_err_test_confirm"), size=11, color=T.RED,
            weight=ft.FontWeight.W_500,
            visible=bool(getattr(app, "_auto_inspection_confirm_invalid", False)))

        def _confirm_inspection(e):
            app._auto_live_confirm = bool(e.control.value)
            if app._auto_live_confirm:
                app._auto_inspection_confirm_invalid = False
                confirm_error.visible = False
                try:
                    confirm_error.update()
                except Exception:
                    pass

        def _flip_allow_unverified(e):
            # Saved immediately; nothing else on screen depends on it (no render).
            app.auto_allow_unverified = bool(e.control.value)
            try:
                app.creds["auto_allow_unverified"] = app.auto_allow_unverified
                import store
                store.save(app.creds)
            except Exception:
                pass

        def _flip_env_creds(e):
            # Saved immediately; read by the worker at generation (no render).
            app.auto_env_creds = bool(e.control.value)
            try:
                app.creds["auto_env_creds"] = app.auto_env_creds
                import store
                store.save(app.creds)
            except Exception:
                pass

        def _flip_regenerate(e):
            # Saved immediately; read by the worker at classification (no render).
            app.auto_regenerate = bool(e.control.value)
            try:
                app.creds["auto_regenerate"] = app.auto_regenerate
                import store
                store.save(app.creds)
            except Exception:
                pass

        def _flip_reinspect_all(e):
            app.auto_reinspect_all = bool(e.control.value)
            try:
                app.creds["auto_reinspect_all"] = app.auto_reinspect_all
                import store
                store.save(app.creds)
            except Exception:
                pass

        inspection_enabled = bool(getattr(app, "auto_live_inspection", True))
        inspection_controls = [
            ft.Row([
                ft.Switch(value=inspection_enabled, active_color=T.VIOLET, scale=0.8,
                          on_change=_flip_inspection),
                ft.Text(strings.t("auto_inspection_enable"), size=12.5,
                        weight=ft.FontWeight.BOLD, color=T.INK, expand=True),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=4),
            ft.Text(strings.t("auto_inspection_desc"), size=11, color=T.INK_3,
                    weight=ft.FontWeight.W_500),
        ]
        confirm_box = ft.Checkbox(value=bool(getattr(app, "_auto_live_confirm", False)),
                                  active_color=T.VIOLET, on_change=_confirm_inspection)
        # Always built; the switch toggles .visible in place (see _flip_inspection).
        inspection_details = ft.Column([
                ft.Container(height=12),
                app._auto_field(strings.t("auto_lbl_test_user"), "auto_login_user",
                                "test.user@example.com"),
                ft.Container(height=10),
                app._auto_field(strings.t("auto_lbl_test_pass"), "auto_login_pass",
                                "", password=True),
                ft.Container(height=4),
                ft.Text(strings.t("auto_creds_optional"), size=11, color=T.INK_3,
                        weight=ft.FontWeight.W_500),
                ft.Container(height=10),
                ft.Row([
                    confirm_box,
                    ft.Text(strings.t("auto_inspection_confirm"), size=11.5,
                            color=T.INK_2, expand=True),
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Container(confirm_error, padding=ft.Padding.only(top=3, left=2)),
                ft.Container(height=4),
                ft.Text(strings.t("auto_inspection_note"), size=11, color=T.INK_3,
                        weight=ft.FontWeight.W_500),
                ft.Container(height=10),
                ft.Row([
                    ft.Checkbox(value=bool(getattr(app, "auto_allow_unverified", False)),
                                active_color=T.VIOLET, on_change=_flip_allow_unverified),
                    ft.Text(strings.t("auto_allow_unverified"), size=11.5,
                            color=T.INK_2, expand=True),
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Container(height=6),
                ft.Row([
                    ft.Checkbox(value=bool(getattr(app, "auto_env_creds", False)),
                                active_color=T.VIOLET, on_change=_flip_env_creds),
                    ft.Text(strings.t("auto_env_creds"), size=11.5,
                            color=T.INK_2, expand=True),
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Container(height=6),
                ft.Row([
                    ft.Checkbox(value=bool(getattr(app, "auto_reinspect_all", False)),
                                active_color=T.VIOLET, on_change=_flip_reinspect_all),
                    ft.Column([
                        ft.Text(strings.t("auto_reinspect_all"), size=11.5,
                                color=T.INK_2),
                        ft.Text(strings.t("auto_reinspect_hint"), size=11, color=T.INK_3,
                                weight=ft.FontWeight.W_500),
                    ], spacing=2, expand=True),
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
            ], spacing=0, visible=inspection_enabled)
        inspection_controls.append(inspection_details)
        inspection_card = card(ft.Column([
            sec_head("D", strings.t("auto_sec_inspection")),
            ft.Container(height=10),
            *inspection_controls,
        ], spacing=0))

        git_card = card(ft.Column([
            sec_head("E", strings.t("auto_sec_git_optional")),
            ft.Container(height=10),
            app._auto_field(strings.t("auto_lbl_repo_url"), "auto_git_url",
                             "https://github.com/you/automation-tests.git"),
            ft.Container(height=10),
            ft.Row([
                ft.Container(app._auto_field(strings.t("auto_lbl_branch"), "auto_git_branch", "main"), expand=1),
                ft.Container(app._auto_field(strings.t("auto_lbl_pat"), "auto_git_token",
                                 "ghp_… or Azure PAT", password=True,
                                 info=strings.t("auto_pat_info"),
                                 on_info=lambda e: app._show_help("git_pat")), expand=1),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Container(height=4),
            ft.Text(strings.t("auto_git_note"),
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

        local_card = card(ft.Column([
            sec_head("F", strings.t("auto_sec_folder")),
            ft.Container(height=10),
            app._auto_field(strings.t("auto_lbl_save_folder"), "auto_local_path",
                             r"e.g. C:\Users\you\IdeaProjects\automation-tests"),
            ft.Container(height=8),
            ft.Row([ghost_btn(strings.t("auto_browse"), icon=ft.Icons.FOLDER_OPEN,
                              on_click=app._browse_auto_folder)], spacing=8),
            ft.Container(height=6),
            ft.Text(strings.t("auto_folder_note"),
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
            ft.Container(height=10),
            ft.Row([
                ft.Checkbox(value=bool(getattr(app, "auto_regenerate", False)),
                            active_color=T.VIOLET, on_change=_flip_regenerate),
                ft.Column([
                    ft.Text(strings.t("auto_regen_label"), size=11.5,
                            weight=ft.FontWeight.BOLD, color=T.INK_2),
                    ft.Text(strings.t("auto_regen_hint"), size=11, color=T.INK_3,
                            weight=ft.FontWeight.W_500),
                ], spacing=2, expand=True),
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
        ], spacing=0))

        email_picker = regression.email_recipient_picker(
            app, "_auto_email_to", is_open_key="_auto_email_open", sync_key="auto_emails")
        email_card = card(ft.Column([
            sec_head("G", strings.t("auto_sec_email")),
            ft.Container(height=10),
            email_picker,
            ft.Container(height=6),
            ft.Text(strings.t("auto_email_note"),
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

        # Buttons live in their own Container so a running/paused/finished state
        # change can refresh JUST this (see _refresh_auto_state) instead of a
        # full app.render() — see _build_action_buttons' docstring for why.
        app._auto_buttons_ready = ready
        app._auto_buttons_ctr = ft.Container(_build_action_buttons(app, ready))

        left = ft.Column([
            source_card,
            *([setup_hint] if setup_hint else []),
            framework_card,
            site_card,
            inspection_card,
            git_card,
            local_card,
            email_card,
            ft.Container(
                ft.Row([ft.Icon(ft.Icons.HELP_OUTLINE, size=15, color=T.VIOLET_INK),
                        ft.Text(strings.t("auto_tips_link"), size=12,
                                weight=ft.FontWeight.BOLD, color=T.VIOLET_INK)],
                       spacing=6, tight=True),
                on_click=lambda e: app._show_help("auto_scripts"),
                padding=ft.Padding.symmetric(horizontal=4, vertical=2), border_radius=6),
            app._auto_buttons_ctr,
        ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)

        # ── right: live counters + clean log ──
        # Locked against main.py's _auto_logmsg/upd(), which reads
        # app._auto_log_col (to append just the new line) from a worker
        # thread while THIS rebuild replaces it wholesale from the main/
        # render thread — unsynchronized, those two can race: upd() grabs
        # the OLD column reference a moment before this line swaps in a new
        # one, appends the newest line onto that now-discarded widget
        # (invisible — it's no longer on the page), and nothing else ever
        # catches the NEW column up to match, since upd() thinks it already
        # handled that line. Depending on timing that leaves the Activity
        # log looking stuck a few lines behind, or — if the race lands right
        # after Generate starts, before anything has rendered into the new
        # column yet — completely blank despite a run actively in progress.
        # Same root cause/fix shape as run.py's screen() rebuild.
        #
        # CONFIRMED (flet-dev/flet#6087 — "Large empty blank area when using
        # Column with expand=True and scroll on desktop", Windows, same
        # symptom): a `ft.Column(..., scroll=..., expand=True)` is a known-broken
        # combo on Flet desktop — Column's scroll support is a SingleChildScroll-
        # View wrapped around a plain Column under the hood, and giving that
        # inner Column expand=True while its own parent is unbounded (which is
        # exactly this panel's chain of nested expand=True Containers, never
        # pinned to a concrete pixel height until the outer Row's cross-axis
        # stretch, several levels up) is the textbook Flutter "RenderFlex
        # children have non-zero flex but incoming height constraints are
        # unbounded" conflict — it doesn't crash, it just silently renders
        # blank. Run's own log column (run.py) uses this exact same
        # Column(scroll=, expand=True) shape and is fine ONLY because it's
        # wrapped in a Container with a literal height=380 one level up —
        # never true here, where the log area is meant to fill whatever
        # vertical space is left, not a fixed box.
        #
        # Reverted from ft.ListView back to Column(scroll=..., expand=True) —
        # ListView fixed the blank-panel bug at the time, but that bug's REAL
        # cause (found this session) was a missing expand=True several levels
        # up the ancestor chain, not the Column/ListView choice itself; now
        # that the chain is fixed, Column works fine here too, same as it
        # always has in run.py/report.py. Switching back also fixes text
        # selection/copy: ListView lazily builds only its visible rows, and
        # Flutter's SelectionArea (wrapping the whole screen body, see
        # shell()) can't reliably register selectable text in rows that
        # aren't built yet — this is why the log couldn't be drag-selected or
        # Ctrl+C copied. A plain Column has no such virtualization, matching
        # run.py's/report.py's already-working, already-copyable log panels.
        # `.controls` still works the same way main.py's _auto_logmsg/
        # _clear_auto_log read and mutate it (same API surface as ListView).
        with app._auto_log_ui_lock:
            log_lines = [app._auto_log_line(ln.get("msg", ""), ln.get("tone", "dim"))
                         for ln in app._auto_log]
            if not log_lines:
                # A flex/expand=True ITEM still can't size itself inside a
                # SCROLLING column — that's a hard Flutter constraint (there's
                # no maximum to "expand" to on the scroll axis), unrelated to
                # the ancestor-chain bug above — so the empty-state
                # placeholder keeps its fixed-height wrapper regardless.
                log_lines = [ft.Container(empty_state(
                    ft.Icons.TERMINAL, strings.t("auto_no_activity"),
                    strings.t("auto_no_activity_hint")), height=320)]
            # BACK to ft.ListView — the third and FINAL follow-the-tail
            # mechanism, after every Column-based approach failed on a real,
            # confirmed bug on this exact widget combo (scroll + expand, no
            # fixed height — the shape run.py never has to deal with):
            #   1. offset-based col.scroll_to(offset=10M) — silently never
            #      moved this column at all (the original mechanism; the
            #      first "rail doesn't auto scroll" report was this).
            #   2. auto_scroll=True on the Column — huge blank dead area
            #      mid-log, flet-dev/flet#6087 (confirmed live, screenshot).
            #   3. key-based scroll_to("autolog-tail") — also not honored on
            #      this pinned Flet build (confirmed live).
            # ListView's auto_scroll is implemented INSIDE the widget (its own
            # ScrollController, post-layout), which is why it worked here
            # historically — the long-ago revert to Column was only to improve
            # drag-select copy, and a later session established drag-copy was
            # broken garbage on this panel anyway (lines jammed together, see
            # the "Log copy" entry) with the "Copy entire log" button as the
            # sanctioned mechanism. `.controls`/`.update()` API is identical,
            # so main.py's _auto_logmsg/upd()/_clear_auto_log need no changes.
            app._auto_log_col = ft.ListView(controls=log_lines, spacing=3,
                                            expand=True, auto_scroll=True)
            # Tail key kept — harmless, and lets _auto_log_scroll_end's
            # key/offset fallbacks keep working as belt-and-suspenders.
            app._retag_log_tail(app._auto_log_col)
            # Flag set ONLY while the single control is the empty-state
            # placeholder (zero real log lines). _auto_logmsg's upd() (main.py)
            # checks this to know it must REPLACE the controls list rather than
            # append after the placeholder — a plain len() heuristic can't tell
            # "1 placeholder" from "1 rendered line", and got it wrong whenever
            # two log lines landed before the first upd() ran.
            app._auto_log_col._qa_placeholder = not app._auto_log
            # Case grouping / auto-collapse / All-Issues filter (log_view.py):
            # only `visible` flags on the 1:1 rows built above.
            app._apply_log_view(app._auto_log_col)

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

        # All / Issues filter: two small chips, refreshed in place on toggle.
        def _filter_chips():
            issues = bool(getattr(app, "_auto_log_issues", False))
            def chip(label, active, value):
                return ft.Container(
                    ft.Text(label, size=10.5, weight=ft.FontWeight.BOLD,
                            color=(T.VIOLET_INK if active else T.INK_3)),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                    border_radius=7, bgcolor=(T.VIOLET_SOFT if active else T.CARD),
                    border=ft.Border.all(1, T.VIOLET if active else T.BORDER),
                    on_click=lambda e, v=value: app._set_log_filter(v))
            return ft.Row([chip(strings.t("auto_log_all"), not issues, False),
                           chip(strings.t("auto_log_issues"), issues, True)],
                          spacing=4, tight=True)
        app._auto_log_filter_ctr = ft.Container(_filter_chips())

        def _refresh_log_filter():
            ctr = getattr(app, "_auto_log_filter_ctr", None)
            if ctr is not None:
                try:
                    ctr.content = _filter_chips()
                    ctr.update()
                except Exception:
                    pass
        app._auto_refresh_log_filter = _refresh_log_filter

        # Pinned toolbar: spinner + title + Copy/Clear, fixed at the very top of
        # the log rail (a sibling of the scrollable column, not inside it) so it
        # never scrolls away with the log — only the log content beneath scrolls.
        log_toolbar = ft.Container(
            ft.Row([app._auto_spinner_ctr,
                    ft.Text(strings.t("auto_activity"), size=11, weight=ft.FontWeight.BOLD,
                           color=T.INK_3, expand=True),
                    app._auto_log_filter_ctr,
                    ft.Container(width=4),
                    _log_tool_btn(ft.Icons.COPY_ALL_OUTLINED, strings.t("auto_copy_log"),
                                 app._copy_auto_log),
                    ft.Container(width=6),
                    _log_tool_btn(ft.Icons.DELETE_OUTLINE, strings.t("auto_clear_log"),
                                 app._clear_auto_log, danger=True)],
                   spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.only(bottom=10),
            margin=ft.Margin.only(bottom=10),
            border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER)))

        # ACTUAL root cause, found by comparing against setup.py's _setup_right()
        # — the one other screen that shares Automation's exact `Row([Container
        # (left, expand=True), Container(right, width=N)])` shell shape and is
        # NOT reported broken. run.py (the earlier "mirror" reference) doesn't
        # count as a real precedent for this bug: its body is a bare ft.Column,
        # not a Row with a static side card, so its log card never passes
        # through shell()'s _install_top_gap() Row-handling branch at all.
        # That branch (main.py) re-wraps any Row child whose `.content` isn't
        # itself a bare Column — which is exactly what `card(...)` produces —
        # into a NEW `ft.Column([spacer, inner], expand=True)`. `_setup_right()`
        # passes expand=True on BOTH its inner Column and the outer card()
        # Container; the previous version of `right` here (this session) had
        # neither, so `inner` had no expand of its own to participate correctly
        # as a flex child of that freshly-created expand=True wrapper Column —
        # resolving to zero/collapsed height, rendering nothing at all (not
        # even the card's own border/background), matching every screenshot
        # exactly, including a bright test banner placed as the very first
        # child. Fixed by matching _setup_right()'s exact shape: expand=True
        # on the inner Column AND on the outer card() Container. The log's own
        # fixed height=460 Container is kept — that part was a separate, real
        # fix (ft.ListView needs a bounded-height ancestor).
        # Second real issue, found via the DIAG2 banner: a plain Text/Container
        # child rendered fine as the first item, but everything after it —
        # starting with app._auto_counts_header() — did not. That header
        # returns a bare ft.Row whose two chip() children each carry their
        # own expand=True; placed as a plain (non-expand) child of a Column,
        # that Row has no explicit height and nothing pinning one, same class
        # of "flex child needs a concrete extent from somewhere" issue already
        # solved for the log ListView (height=460) and the empty-state
        # placeholder (height=320) — just not yet applied to this Row or to
        # log_toolbar's Row (which also has an expand=True child, the
        # "ACTIVITY" label). Wrapping both in their own fixed-height
        # Containers, matching the same proven pattern.
        # End-of-run result banner (hidden while a run is active). Always built; refresh_run_ui() swaps its content / visibility in place.
        _banner_body = _result_banner_content(app)
        app._auto_result_ctr = ft.Container(_banner_body, height=48, margin=ft.Margin.only(bottom=10),
                                            visible=_banner_body is not None)

        right = card(ft.Column([
            ft.Container(app._auto_counts_header(), height=76),
            ft.Container(height=12),
            ft.Container(log_toolbar, height=50),
            app._auto_result_ctr,
            # NOT wrapped in its own ft.SelectionArea — shell() already wraps
            # the entire screen body in one outer SelectionArea, so this text
            # is already selectable (drag-select + Ctrl+C), same as run.py/
            # report.py's log panels; a second, nested SelectionArea here
            # would fight the outer one instead of helping.
            # expand=True instead of a fixed height — now that the ancestor
            # chain properly threads expand=True all the way down (see the
            # `right`/counts-header/toolbar fixes above), this can fill
            # whatever space is actually left instead of stopping at a
            # guessed pixel value and leaving a gap at the bottom of the card.
            ft.Container(app._auto_log_col, expand=True, bgcolor=T.CARD_2,
                        border=ft.Border.all(1, T.BORDER), border_radius=T.R,
                        padding=12),
        ], spacing=0, expand=True), expand=True)

        body = ft.Row([ft.Container(left, expand=True),
                       ft.Container(right, width=384)], spacing=22,
                      vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
        if file_source:
            sub = (strings.t("auto_file_selected", name=os.path.basename(app.auto_test_case_file))
                   if (app.auto_test_case_file or "").strip() else strings.t("auto_file_none"))
        else:
            sub = (strings.t("auto_stories_selected", n=len(app._auto_selected)) if app._auto_selected
                   else strings.t("auto_no_stories"))
        return app.shell(strings.t("auto_title"), sub, body)

    # ---- activity panel: live counters + clean, RTL-aware log lines ----
