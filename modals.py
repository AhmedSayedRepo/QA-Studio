"""modals.py — dialog/modal builders (onboarding, create-plan, sprint-summary,
existing-steps). Extracted from main.py (Step-11). Each takes the QAStudio app;
the app keeps thin delegator methods so the call-sites keep working.
"""
import re
import threading
import flet as ft
import theme as T
import engine as E
import platform_caps
from ui import _ic, badge, field_label, ghost_btn, grad, green_btn, hover_field, logo_img, primary_btn, stat_tile
from regression import email_recipient_picker, _id_link


def open_onboarding(app):
    app._onb_i = 0

    def _badge(icon, tone="violet"):
        soft = {"violet": T.VIOLET_SOFT, "green": T.GREEN_SOFT,
                "amber": T.AMBER_SOFT}.get(tone, T.VIOLET_SOFT)
        ink = {"violet": T.VIOLET_INK, "green": T.GREEN,
               "amber": T.AMBER}.get(tone, T.VIOLET_INK)
        return ft.Container(ft.Icon(icon, size=22, color=ink),
                            width=46, height=46, bgcolor=soft, border_radius=13,
                            alignment=ft.Alignment.CENTER)

    def _item(icon, title, desc, tone="violet"):
        return ft.Container(
            ft.Row([_badge(icon, tone),
                    ft.Column([
                        ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=T.INK),
                        ft.Text(desc, size=12, color=T.INK_3, weight=ft.FontWeight.W_500),
                    ], spacing=1, expand=True)],
                   spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(vertical=7))

    def _welcome():
        return ft.Column([
            ft.Text("Generate Azure DevOps test cases with AI, plan regression "
                    "and sprint effort, and build self-healing Selenium tests — "
                    "all from one place.",
                    size=13, color=T.INK_2, weight=ft.FontWeight.W_500),
            ft.Container(height=12),
            _item(ft.Icons.AUTO_AWESOME, "Titles or full steps",
                  "Concise titles or complete step-by-step cases — your choice per run."),
            _item(_ic("LANGUAGE", "DESCRIPTION_OUTLINED"), "English & Arabic",
                  "Produce test content in either language with one toggle.", "green"),
            _item(ft.Icons.DOWNLOAD, "Writes back to Azure DevOps",
                  "Pushes the generated cases straight into your test plans.", "amber"),
        ], spacing=0, tight=True)

    def _how():
        return ft.Column([
            _item(ft.Icons.TUNE, "1 · Setup",
                  "Connect your AI provider and Azure DevOps, then pick a project, "
                  "test plan and stories."),
            _item(ft.Icons.MONITOR_HEART, "2 · Run",
                  "Generate test-case titles or detailed steps in English or Arabic."),
            _item(ft.Icons.DESCRIPTION_OUTLINED, "3 · Report",
                  "Review what was created and email the summary."),
            _item(_ic("CHECKLIST","LAYERS_OUTLINED"), "Plan & automate", "Regression and Sprint "
                  "plans estimate effort; Automation builds Selenium tests.", "green"),
        ], spacing=0, tight=True)

    def _connect():
        return ft.Column([
            ft.Text("On the Setup screen you'll add three credentials "
                    "(stored only on this device):",
                    size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500),
            ft.Container(height=8),
            _item(ft.Icons.AUTO_AWESOME, "AI provider key",
                  "Anthropic, OpenAI, Gemini, and more — powers the generation."),
            _item(ft.Icons.KEY_OUTLINED, "Azure DevOps PAT",
                  "Read/write access to your test plans and work items."),
            _item(ft.Icons.MAIL_OUTLINED, "Gmail app password",
                  "Optional — only needed to email reports.", "amber"),
        ], spacing=0, tight=True)

    def _ready():
        return ft.Column([
            _item(_ic("KEYBOARD_COMMAND_KEY","TERMINAL"), "Command palette",
                  "Press Ctrl/⌘-K anywhere to jump between screens and run actions."),
            _item(ft.Icons.DARK_MODE_OUTLINED, "Light & dark",
                  "Toggle the theme from the sidebar or Settings."),
            _item(ft.Icons.SETTINGS_OUTLINED, "Settings",
                  "Defaults, cache controls, and this walkthrough live there."),
        ], spacing=0, tight=True)

    steps = [
        (_ic("WAVING_HAND_OUTLINED","AUTO_AWESOME"), "violet", "Welcome to QA Studio",
         "Your AI test-engineering workspace.", _welcome),
        (_ic("ACCOUNT_TREE_OUTLINED","LAYERS_OUTLINED"), "violet", "How it works",
         "A simple pipeline, plus planning tools.", _how),
        (ft.Icons.LINK, "green", "Get connected",
         "Three quick credentials and you're set.", _connect),
        (_ic("ROCKET_LAUNCH_OUTLINED","PLAY_ARROW"), "violet", "You're ready",
         "A couple of shortcuts worth knowing.", _ready),
    ]

    import threading
    import platform_caps as _pc_onb0
    # Body height 380 is fine in portrait, but on a LANDSCAPE phone the whole
    # viewport is only ~360-400px tall — a fixed 380px body pushed the footer
    # (dots + Next) off the bottom, unreachable again (reported live). Cap the
    # scrollable body to leave room for header chrome + footer when the
    # viewport is short. The body's content already scrolls (see _paint), so
    # a smaller box just means the steps scroll within it.
    _body_h = 380
    if _pc_onb0.is_mobile():
        try:
            _ph = app.page.height or 0
            if _ph and _ph < 560:
                _body_h = max(210, int(_ph) - 210)
        except Exception:
            pass
    body = ft.Container(
        height=_body_h,
        animate_opacity=ft.Animation(260, ft.AnimationCurve.EASE_OUT),
        animate_offset=ft.Animation(260, ft.AnimationCurve.EASE_OUT),
        clip_behavior=ft.ClipBehavior.HARD_EDGE, border_radius=T.R_DLG)
    dots = ft.Row([], spacing=6)
    back_holder = ft.Container()
    next_holder = ft.Container()

    def _hero_badge(icon):
        # white "glass" tile on the gradient header; pops in via animate_scale
        return ft.Container(
            ft.Icon(icon, size=26, color="#FFFFFF"),
            width=58, height=58, border_radius=16,
            bgcolor=ft.Colors.with_opacity(0.18, "#FFFFFF"),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.30, "#FFFFFF")),
            alignment=ft.Alignment.CENTER,
            scale=0.6, animate_scale=ft.Animation(320, ft.AnimationCurve.EASE_OUT))

    def _paint():
        i = app._onb_i
        icon, tone, title, sub, build = steps[i]
        hg = T.GRAD_GREEN if tone == "green" else T.GRAD_LOGO
        badge_ctl = _hero_badge(icon)
        header = ft.Container(
            ft.Row([badge_ctl,
                    ft.Column([
                        ft.Text(title, size=18, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                        ft.Text(sub, size=12.5, weight=ft.FontWeight.W_500,
                                color=ft.Colors.with_opacity(0.88, "#FFFFFF")),
                    ], spacing=3, expand=True)],
                   spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            gradient=grad(hg),
            padding=ft.Padding.symmetric(vertical=22, horizontal=22),
            # Matches the dialog panel's OWN corner radius (T.R_DLG, set
            # centrally in dialogs.show_dialog) so this gradient header's top
            # corners align seamlessly with the dialog's outer edge instead of
            # leaving a visible seam/notch.
            border_radius=ft.BorderRadius.only(top_left=T.R_DLG, top_right=T.R_DLG))
        content = ft.Container(
            build(), padding=ft.Padding.only(left=22, right=22, top=18, bottom=8))
        body.content = ft.Column([header, content], spacing=0,
                                 scroll=ft.ScrollMode.AUTO, tight=True)
        dots.controls = [
            ft.Container(width=(20 if j == i else 7), height=7,
                         bgcolor=(T.VIOLET if j == i else T.BORDER),
                         border_radius=4,
                         animate=ft.Animation(180, ft.AnimationCurve.EASE_OUT))
            for j in range(len(steps))]
        last = (i == len(steps) - 1)
        back_holder.content = (ghost_btn("Back", on_click=lambda e: _go(-1))
                               if i > 0 else ft.Container(width=0))
        next_holder.content = (
            green_btn("Get started", icon=ft.Icons.ARROW_FORWARD,
                      on_click=lambda e: app._finish_onboarding(goto_setup=True))
            if last else
            primary_btn("Next", icon=ft.Icons.ARROW_FORWARD,
                        on_click=lambda e: _go(1)))
        # set the "from" state (invisible + slid + small badge), then animate to "to"
        body.opacity = 0
        body.offset = ft.Offset(0.06, 0)
        for c in (dots, back_holder, next_holder):
            try: c.update()
            except Exception: pass
        try: body.update()
        except Exception: pass

        def _reveal():
            try:
                badge_ctl.scale = 1.0
                body.opacity = 1
                body.offset = ft.Offset(0, 0)
                app.page.update()
            except Exception:
                pass
        try:
            # Serialized on the session loop (see main.py ui_safe's docstring).
            threading.Timer(0.05, lambda: app.ui_safe(_reveal)).start()
        except Exception:
            _reveal()

    def _go(delta):
        app._onb_i = max(0, min(len(steps) - 1, app._onb_i + delta))
        _paint()

    skip = ft.TextButton("Skip", on_click=lambda e: app._finish_onboarding())
    import platform_caps as _pc_foot
    if _pc_foot.is_mobile():
        # Phone: Skip + Back + Next/Get-started in ONE nav row still overflowed
        # on the LAST step, where "Get started" is much wider than "Next" — it
        # clipped off the right edge (reported live). So put SKIP up on the
        # dots line (far left, balanced by a spacer to keep the dots centred),
        # and give the two nav buttons (Back + Next/Get-started) their OWN
        # right-aligned row with the full card width — they always fit there.
        footer = ft.Container(
            ft.Column([
                ft.Row([skip, ft.Container(expand=True), dots,
                        ft.Container(expand=True), ft.Container(width=52)],
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=10),
                ft.Row([ft.Container(expand=True), back_holder,
                        ft.Container(width=8), next_holder],
                       vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
            ], spacing=0, tight=True),
            padding=ft.Padding.only(left=16, right=16, top=4, bottom=2))
    else:
        footer = ft.Container(
            ft.Row([skip, ft.Container(expand=True), dots,
                    ft.Container(width=14), back_holder, next_holder],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            padding=ft.Padding.only(left=20, right=20, top=4, bottom=2))
    _paint()
    # Fixed width=600 assumes a desktop window. Flet's AlertDialog otherwise
    # sizes itself to content, so on a ~390px phone this forced the whole
    # panel (including the footer's Next/Get started button) wider than the
    # viewport — the button wasn't actually broken, it was rendered past the
    # visible edge with no way to reach it, which is what "Next doesn't
    # work" looked like. Let content size itself to the viewport on mobile.
    import platform_caps as _pc_onb
    # Portrait phone: width=None sizes to the (narrow) viewport — the #71 fix.
    # LANDSCAPE phone / tablet: width=None instead let the card stretch across
    # the whole ~800px+ width, looking awkwardly wide (reported live). Cap it
    # at 560 once the viewport is wide enough that a cap is what we want.
    if _pc_onb.is_mobile():
        _dlg_width = None
        try:
            _pw = app.page.width or 0
            if _pw > 620:
                _dlg_width = 560
        except Exception:
            pass
    else:
        _dlg_width = 600
    dlg = ft.AlertDialog(
        modal=True, bgcolor=T.CARD,
        shape=ft.RoundedRectangleBorder(radius=T.R_LG),
        content=ft.Container(width=_dlg_width, padding=0, content=ft.Column([
            body,
            ft.Container(height=14),
            footer,
            ft.Container(height=6),
        ], spacing=0, tight=True)))
    app._show_dialog(dlg)

# ---- navigation ----
def open_create_plan(app):
    if not app.can("act.create_plan"):
        return app._toast("You don’t have permission to create a test plan.")
    if not app.project:
        app._err("Select a project first."); return

    # Route through the backend seam so this modal works for EVERY backend, not
    # just Azure. Previously it called engine's Azure-only fetch_iterations /
    # create_test_plan directly, so on a Jira/Xray/TestRail backend sprint-load
    # hit dev.azure.com and 404'd ("check the project name spelling"). The
    # per-story "requirement suites" auto-create is Azure-only engine
    # orchestration; other backends get a plain plan.
    import backend_setup as _bs
    _is_azure = _bs.is_azure(getattr(app, "creds", {}) or {})

    name_field = ft.TextField(
        hint_text="e.g. Sprint 24 — Regression",
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
        text_size=13, expand=True)
    iter_dd = ft.Dropdown(
        hint_text="Loading sprints…", options=[],
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
        text_size=13, filled=True, bgcolor=T.CARD, expand=True)
    path_box = ft.Text("—", size=12.5, color=T.VIOLET_INK, weight=ft.FontWeight.BOLD, font_family=T.F_MONO)
    modal_err = ft.Text("", size=12, color=T.RED, weight=ft.FontWeight.BOLD)

    # Auto-create requirement suites for every sprint story (PAT-only, no AI)
    auto_suites = ft.Checkbox(value=_is_azure, disabled=not _is_azure, label="", scale=0.9,
                              active_color=T.VIOLET, check_color="#FFFFFF")

    # ── Story picker: choose WHICH sprint stories get a requirement suite ──
    # Shown only for Azure with auto-suites on. Loads the selected sprint's
    # stories (board order — see engine.fetch_stories_in_iteration) and defaults
    # to ALL selected, so the plan behaves exactly as before unless the user
    # narrows it. do_create passes the checked ids to
    # create_plan_with_sprint_suites(story_ids=…).
    stories_state = {"rows": [], "sel": set(), "path": None}
    story_cbs = {}
    sel_all_cb = ft.Checkbox(value=True, scale=0.85,
                             active_color=T.VIOLET, check_color="#FFFFFF")
    stories_count = ft.Text("Loading sprint stories…", size=11, color=T.INK_3,
                            weight=ft.FontWeight.BOLD)

    def _stories_msg(text, spin=False):
        row = []
        if spin:
            row.append(ft.ProgressRing(width=14, height=14, stroke_width=2,
                                       color=T.VIOLET))
        row.append(ft.Text(text, size=12, color=T.INK_3, italic=not spin))
        return ft.Row(row, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # Start with a loading placeholder so the box never renders as an empty void
    # during the initial sprint fetch (load_iters warms it once sprints land).
    stories_col = ft.Column([_stories_msg("Loading sprint stories…", spin=True)],
                            spacing=1, scroll=ft.ScrollMode.AUTO, height=180)

    def _stories_visible():
        return _is_azure and bool(auto_suites.value)

    def _refresh_count():
        n, m = len(stories_state["sel"]), len(stories_state["rows"])
        stories_count.value = (f"{n} of {m} stories selected" if m
                               else "No stories in this sprint")
        sel_all_cb.value = (m > 0 and n == m)
        try:
            stories_count.update(); sel_all_cb.update()
        except Exception:
            pass

    def _toggle_story(sid):
        def _h(e):
            if e.control.value:
                stories_state["sel"].add(sid)
            else:
                stories_state["sel"].discard(sid)
            _refresh_count()
        return _h

    def _toggle_all(e):
        on = bool(e.control.value)
        stories_state["sel"] = ({s["id"] for s in stories_state["rows"]} if on else set())
        for _sid, _cb in story_cbs.items():
            _cb.value = on
            try: _cb.update()
            except Exception: pass
        _refresh_count()
    sel_all_cb.on_change = _toggle_all

    def _build_story_rows():
        story_cbs.clear()
        ctrls = []
        for s in stories_state["rows"]:
            sid = s["id"]
            cb = ft.Checkbox(value=(sid in stories_state["sel"]), scale=0.8,
                             active_color=T.VIOLET, check_color="#FFFFFF",
                             on_change=_toggle_story(sid))
            story_cbs[sid] = cb
            ctrls.append(ft.Row(
                [cb, ft.Text(f"[{sid}] {(s.get('title') or '')[:70]}", size=12,
                             color=T.INK_2, weight=ft.FontWeight.W_500,
                             expand=True, no_wrap=False)],
                spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        stories_col.controls = ctrls or [
            ft.Text("No stories in this sprint.", size=12, color=T.INK_3,
                    italic=True)]
        try: stories_col.update()
        except Exception: pass

    def load_stories(path):
        if not _stories_visible():
            return
        if not path:
            stories_state["rows"] = []
            stories_state["sel"] = set()
            stories_state["path"] = None
            stories_col.controls = [_stories_msg("Select a sprint to load its stories.")]
            _refresh_count()
            try: stories_col.update()
            except Exception: pass
            return
        stories_state["path"] = path
        stories_col.controls = [_stories_msg("Loading sprint stories…", spin=True)]
        try: stories_col.update()
        except Exception: pass
        try:
            lst = _bs.fetch_stories_in_sprint(app, path) or []
        except Exception:
            lst = []
        # Drop if the sprint changed again while this load was in flight.
        if stories_state["path"] != path:
            return
        stories_state["rows"] = lst
        stories_state["sel"] = {s["id"] for s in lst}     # default: all selected
        _build_story_rows()
        _refresh_count()

    stories_box = ft.Container(
        ft.Column([
            ft.Row([
                ft.Text("STORIES TO INCLUDE", size=10.5, weight=ft.FontWeight.BOLD,
                        color=T.INK_3),
                ft.Container(expand=True),
                ft.Row([sel_all_cb, ft.Text("All", size=11.5, color=T.INK_2,
                                            weight=ft.FontWeight.BOLD)],
                       spacing=2, tight=True),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(
                stories_col,
                padding=ft.Padding.symmetric(vertical=6, horizontal=8),
                bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER),
                border_radius=T.R, margin=ft.Margin.only(top=6)),
            ft.Container(stories_count, padding=ft.Padding.only(top=6)),
        ], spacing=0),
        padding=ft.Padding.only(top=12), visible=_stories_visible())

    def _on_auto_suites(e):
        vis = _stories_visible()
        stories_box.visible = vis
        try: stories_box.update()
        except Exception: pass
        if vis and not stories_state["rows"]:
            app._bg(lambda: load_stories(iter_dd.value))
    auto_suites.on_change = _on_auto_suites

    # In-modal progress UI (design: 8px rounded track + violet gradient fill)
    prog_label = ft.Text("", size=12, color=T.INK_2, weight=ft.FontWeight.BOLD)
    prog_pct = ft.Text("", size=12, color=T.VIOLET_INK, weight=ft.FontWeight.BOLD)
    prog_spin = ft.ProgressRing(width=14, height=14, stroke_width=2, color=T.VIOLET)
    prog_bar = ft.ProgressBar(value=0, color=T.VIOLET, bgcolor="#E9E8F0",
                              bar_height=8, border_radius=99)
    prog_box = ft.Container(
        ft.Column([
            ft.Row([prog_spin, prog_label, ft.Container(expand=True), prog_pct],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            prog_bar,
        ], spacing=8),
        padding=ft.Padding.only(top=10), visible=False)

    iters_cache = {"list": []}
    def load_iters():
        try:
            lst = _bs.fetch_sprints(app, app.project)
            iters_cache["list"] = lst
            iter_dd.options = [ft.DropdownOption(key=it["path"], text=it["path"]) for it in lst]
            if lst:
                iter_dd.value = lst[-1]["path"]
                path_box.value = iter_dd.value
                iter_dd.hint_text = "Select sprint"
            else:
                iter_dd.hint_text = "No sprints found"
            modal_err.value = ""
        except Exception as ex:
            iter_dd.hint_text = "Failed to load"
            modal_err.value = f"Could not load sprints: {str(ex)[:100]}"
        try:
            iter_dd.update()
        except Exception:
            pass
        try:
            app.page.update()
        except Exception:
            pass
        # Warm the story picker for the default sprint (Azure + auto-suites on).
        try:
            if _stories_visible():
                load_stories(iter_dd.value)
        except Exception:
            pass

    def on_iter_change(e):
        path_box.value = iter_dd.value or "—"
        app.page.update()
        # Reload the story picker for the newly chosen sprint.
        app._bg(lambda: load_stories(iter_dd.value))
    iter_dd.on_select = on_iter_change

    def _set_prog(pct, label):
        prog_box.visible = True
        prog_bar.value = max(0.0, min(1.0, pct))
        prog_label.value = label
        prog_pct.value = f"{int(pct*100)}%"
        # hide the spinner once finished
        prog_spin.visible = pct < 1.0
        def _paint():
            try: app.page.update()
            except Exception: pass
        # Single serialized dispatch (see main.py ui_safe's docstring): the
        # old inline call from the worker + run_thread duplicate ran the same
        # whole-page update on two threads at once.
        app.ui_safe(_paint)

    def do_create(e):
        nm = (name_field.value or "").strip()
        pth = (iter_dd.value or "").strip()
        if not nm:
            modal_err.value = "Plan name is required."; app.page.update(); return
        # Iteration/Sprint is an Azure-only concept (a plan is created UNDER an
        # iteration). Non-Azure backends create a plain named plan (TestRail
        # suite / Xray Test Plan / Zephyr folder) with no iteration — stories
        # come from the sprint at run time, decoupled from the plan.
        if _is_azure and not pth:
            modal_err.value = "Select a sprint/iteration."; app.page.update(); return
        # Auto-suites on with stories loaded → require at least one pick, so a plan
        # isn't created with zero suites by an empty selection the user didn't mean.
        if (_is_azure and auto_suites.value and stories_state["rows"]
                and not stories_state["sel"]):
            modal_err.value = ("Pick at least one story, or turn off "
                               "auto-create suites.")
            app.page.update(); return
        modal_err.value = ""
        create_btn.visible = False; cancel_btn.visible = False
        # show the progress bar immediately at 0% (before any slow work begins)
        _set_prog(0.0, "Starting…")

        def work():
            try:
                if not _is_azure:
                    # Non-Azure backends (Jira+Zephyr, Jira+Xray, TestRail): route
                    # plan creation through the backend seam — Zephyr creates a
                    # folder, Xray a test plan, TestRail a suite. The Azure-only
                    # per-story requirement-suite orchestration doesn't apply, so
                    # we create the plan itself (auto_suites is disabled here).
                    _set_prog(0.2, "Creating test plan…")
                    plan = _bs.get_backend(app).create_test_plan(app.project, nm, pth)
                    app.plan_id = ((getattr(plan.ref, "id", "") or
                                    getattr(plan.ref, "key", "")) if plan else None)
                    app.plan_name = nm
                    _set_prog(1.0, "Done")
                    app._load_plans(); app._close_dialog(); app.render()
                    return
                if not auto_suites.value:
                    _set_prog(0.15, "Creating test plan…")
                    new_id = E.create_test_plan(app.project, nm, pth)
                    app.plan_id = new_id; app.plan_name = nm
                    _set_prog(1.0, "Done")
                    app._load_plans(); app._close_dialog(); app.render()
                    return

                def cb(ev, payload):
                    if ev == "plan":
                        _set_prog(0.10, "Plan created · finding sprint stories…")
                    elif ev == "stories":
                        n = payload["total"]
                        if n == 0:
                            _set_prog(1.0, "Plan created · no stories in this sprint")
                        else:
                            _set_prog(0.15, f"Found {n} stories · creating suites…")
                    elif ev == "suite":
                        i, n = payload["done"], payload["total"]
                        frac = 0.15 + 0.85 * (i / n) if n else 1.0
                        _set_prog(frac, f"Creating suite {i} of {n}…")
                    elif ev == "done":
                        app.plan_id = payload["plan_id"]; app.plan_name = nm
                        # NOTE: story IDs field is intentionally left untouched —
                        # the suites are created in Azure, but the user enters the
                        # story IDs they actually want to run themselves.
                        c = payload.get("created", 0); s = payload.get("skipped", 0)
                        f = payload.get("failed", 0)
                        _set_prog(1.0, f"Done · {c} created · {s} existed"
                                  + (f" · {f} failed" if f else ""))

                # Only build suites for the CHECKED stories. Pass the selected ids
                # when the picker has loaded rows; None (all) only if it never
                # populated, preserving the original all-stories behaviour.
                _sel_ids = (list(stories_state["sel"])
                            if stories_state["rows"] else None)
                E.create_plan_with_sprint_suites(app.project, nm, pth, cb=cb,
                                                 story_ids=_sel_ids)
                import time as _t; _t.sleep(0.4)
                app._load_plans(); app._close_dialog(); app.render()
            except Exception as ex:
                create_btn.visible = True; cancel_btn.visible = True
                prog_box.visible = False
                modal_err.value = f"Create failed: {str(ex)[:140]}"
                try: app.page.update()
                except Exception: pass
        app._bg(work)

    cancel_btn = ghost_btn("Cancel", on_click=lambda e: app._close_dialog())
    create_btn = green_btn("Create plan", icon=ft.Icons.ADD, on_click=do_create,
                           height=46)
    btn_row = ft.Row([cancel_btn, create_btn],
                     alignment=ft.MainAxisAlignment.END, spacing=10,
                     vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _plan_modal_body():
        """Fields shown in the Create-plan modal. Iteration/Sprint, the
        'Will be created at' path, and the requirement-suites checkbox are
        Azure-only concepts (a plan is created UNDER an iteration and can
        auto-spawn per-story suites). Non-Azure backends create a plain named
        plan in the write target (TestRail suite / Xray Test Plan / Zephyr
        folder), so they show just the name + a one-line note instead of the
        Azure iteration machinery."""
        rows = [
            field_label("Plan name", req=True),
            ft.Container(hover_field(name_field),
                         padding=ft.Padding.only(top=4, bottom=14)),
        ]
        if _is_azure:
            rows += [
                field_label("Iteration / Sprint", req=True),
                ft.Container(hover_field(iter_dd),
                             padding=ft.Padding.only(top=4, bottom=10)),
                ft.Text("Will be created at", size=11, color=T.INK_3,
                        weight=ft.FontWeight.BOLD),
                ft.Container(
                    path_box,
                    padding=ft.Padding.symmetric(vertical=11, horizontal=13),
                    bgcolor=T.VIOLET_SOFT, border_radius=T.R,
                    border=ft.Border.all(1, "#E0DAFF"),
                    margin=ft.Margin.only(top=5), width=9999),
                ft.Container(
                    ft.Row([
                        auto_suites,
                        ft.Column([
                            ft.Text("Add requirement suites for sprint stories",
                                    size=12.5, color=T.INK, weight=ft.FontWeight.BOLD),
                            ft.Text("Creates one suite per User Story in the sprint "
                                    "(Azure only — no AI).",
                                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
                        ], spacing=1, expand=True),
                    ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.Padding.only(top=12)),
                stories_box,
            ]
        else:
            rows += [
                ft.Container(
                    ft.Row([
                        ft.Icon(ft.Icons.INFO_OUTLINE, size=15, color=T.INK_3),
                        ft.Text(f"Creates a new plan in {_bs.case_store_label(app)}. "
                                f"User stories are picked separately from your sprint "
                                f"when you run.", size=11.5, color=T.INK_2,
                                weight=ft.FontWeight.W_500, expand=True),
                    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START),
                    padding=ft.Padding.symmetric(vertical=11, horizontal=13),
                    bgcolor=T.CARD_2, border_radius=T.R,
                    border=ft.Border.all(1, T.BORDER), margin=ft.Margin.only(top=2)),
            ]
        rows += [
            prog_box,
            ft.Container(modal_err, padding=ft.Padding.only(top=8)),
        ]
        return rows

    def _plan_modal_content():
        # Keep Cancel/Create ALWAYS visible: the field area scrolls inside a
        # viewport-capped height (the story checklist can make this tall), and the
        # button row is PINNED below it — so on a short window the actions are
        # never pushed off-screen. Short (non-Azure) forms size naturally.
        _rows = _plan_modal_body()
        _mobile = platform_caps.is_mobile()
        _ph = getattr(getattr(app, "page", None), "height", None) or 800
        _cap = max(240, min(440, int(_ph) - 300)) if _is_azure else None
        fields = ft.Column(_rows, spacing=0, tight=True,
                           scroll=(ft.ScrollMode.AUTO if _cap else None))
        footer = ft.Container(btn_row, padding=ft.Padding.only(top=14))
        return ft.Container(
            width=(None if _mobile else 470),
            content=ft.Column([ft.Container(fields, height=_cap), footer],
                              spacing=0, tight=True))

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Container(ft.Icon(ft.Icons.ADD, size=18, color=T.GREEN),
                         width=34, height=34, bgcolor=T.GREEN_SOFT, border_radius=9,
                         alignment=ft.Alignment.CENTER),
            ft.Column([
                ft.Text("Create test plan", size=16, weight=ft.FontWeight.W_800, color=T.INK),
                ft.Text(("Created under the selected iteration in this project."
                         if _is_azure else
                         f"Creates a new plan in {_bs.case_store_label(app)}."),
                        size=11, color=T.INK_2, weight=ft.FontWeight.W_500),
            ], spacing=1, expand=True),
        ], spacing=10),
        content=_plan_modal_content(),
    )
    app._show_dialog(dlg)
    app._bg(load_iters)


# ── Sprint summary report (read-only, before a run) ────────────────────
def open_sprint_summary(app):
    if not app.can("act.sprint_summary"):
        return app._toast("You don’t have permission to generate the sprint summary.")
    if not (app.project and app.plan_id):
        app._toast("Select a test plan first.")
        return

    # State → brand color/soft-bg mapping for status chips
    def _state_kind(state):
        s = (state or "").lower()
        if s in ("done", "closed", "completed", "resolved"):
            return "green"
        if s in ("active", "in progress", "committed", "doing"):
            return "violet"
        if s in ("new", "to do", "proposed", "open"):
            return "amber"
        return "grey"

    # animated 'scanning' loading state — motion while the summary is generated
    _sum_status = ft.Text("Connecting to Azure DevOps…", size=12.5, color=T.INK_2,
                          weight=ft.FontWeight.W_500)
    _scan = ft.Column([
        ft.Container(ft.Stack([
            ft.ProgressRing(width=76, height=76, stroke_width=3,
                            color=ft.Colors.with_opacity(0.85, T.VIOLET)),
            ft.Container(ft.Icon(ft.Icons.SUMMARIZE_OUTLINED, size=26, color=T.VIOLET),
                         width=54, height=54, bgcolor=T.VIOLET_SOFT, border_radius=16,
                         alignment=ft.Alignment.CENTER, left=11, top=11),
        ], width=76, height=76), width=76, height=76, alignment=ft.Alignment.CENTER),
        ft.Container(height=18),
        ft.Text("Generating sprint summary", size=16, weight=ft.FontWeight.BOLD,
                color=T.INK),
        ft.Container(height=5),
        _sum_status,
    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
       alignment=ft.MainAxisAlignment.CENTER, spacing=0)
    body_col = ft.Column(
        [ft.Container(_scan, alignment=ft.Alignment.CENTER, height=380, expand=True)],
        spacing=12, tight=True, scroll=ft.ScrollMode.AUTO)

    # cycle the status line every ~0.9s until the data arrives
    app._sum_loading = True
    def _cycle_status():
        msgs = ["Connecting to Azure DevOps…", "Fetching sprint results…",
                "Counting test cases…", "Summarizing stories…"]
        ev = threading.Event()
        i = 0
        while getattr(app, "_sum_loading", False):
            ev.wait(0.9)
            if not getattr(app, "_sum_loading", False):
                break
            i += 1
            def upd(m=msgs[i % len(msgs)]):
                _sum_status.value = m
                try: _sum_status.update()
                except Exception: pass
            app.ui_safe(upd)
    app._bg(_cycle_status)

    # email recipients (asked each time) + status text — uses the same
    # searchable multiselect picker as the Sprint Plan / Regression Plan email
    # sections (with the send button beside it, not stacked below), instead
    # of a bare comma-separated TextField, so all three "email this" surfaces
    # look and behave the same way. Seeded from the Report screen's own email
    # list once when the dialog opens, then kept independent (this picker
    # writes to its own state key, not app.emails, so picking recipients here
    # doesn't silently change what the Report screen has queued).
    app._sum_data = None
    app._sum_email_to = app.emails or ""
    email_status = ft.Text("", size=11.5, weight=ft.FontWeight.BOLD, visible=False)

    def do_email(e=None):
        if not app._sum_data:
            return
        if not E.GMAIL_APP_PASS:
            email_status.value = "Set a Gmail App Password in Setup → Connection first."
            email_status.color = T.AMBER
            email_status.visible = True
            try: email_status.update()
            except Exception: app.render()
            return
        to = [x.strip() for x in re.split(r"[,\s;]+", (app._sum_email_to or "")) if x.strip()]
        if not to:
            email_status.value = "Enter at least one recipient."
            email_status.color = T.RED
            email_status.visible = True
            try: email_status.update()
            except Exception: app.render()
            return
        email_status.value = "Sending…"; email_status.color = T.INK_2
        email_status.visible = True
        try: email_status.update()
        except Exception: app.render()

        def work():
            html = E.build_sprint_summary_email(app._sum_data)
            plan = app._sum_data.get("plan_name", "")
            ok, err = E.send_report(to, f"QA Studio — Sprint Summary — {plan}", html)
            def show():
                if ok:
                    email_status.value = f"Summary emailed to {', '.join(to)}"
                    email_status.color = T.GREEN
                else:
                    email_status.value = f"Email failed — {err}"
                    email_status.color = T.RED
                email_status.visible = True
                try: email_status.update()
                except Exception: app.render()
            app.ui_safe(show)
        app._bg(work)

    email_btn = green_btn("Email summary", icon=ft.Icons.MAIL_OUTLINED,
                          on_click=do_email)
    close_btn = ghost_btn("Close", on_click=lambda e: app._close_dialog())

    email_picker = email_recipient_picker(
        app, "_sum_email_to", is_open_key="_sum_email_open",
        sync_key="sum_emails", trailing=email_btn)

    email_bar = ft.Column([
        ft.Container(height=1, bgcolor=T.BORDER_2),
        ft.Container(height=6),
        ft.Text("EMAIL THIS SUMMARY", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
        ft.Container(height=5),
        email_picker,
        email_status,
    ], spacing=0, tight=True)
    email_bar.visible = False  # shown only after data loads

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([ft.Container(logo_img(32, ft.Icons.SUMMARIZE_OUTLINED, T.VIOLET_INK),
                                   width=46, height=46, bgcolor=T.VIOLET_SOFT,
                                   border_radius=12, alignment=ft.Alignment.CENTER),
                      ft.Text("Sprint Summary", weight=ft.FontWeight.W_800, size=16,
                              color=T.INK)],
                     spacing=10, tight=True),
        content=ft.Container(
            ft.Column([ft.Container(body_col, expand=True), email_bar],
                      spacing=4, tight=False),
            # width=820 forced this whole dialog — including the recipient
            # picker's Row inside email_bar — to squeeze into a ~390px phone
            # viewport, which is what turned "selected" into a vertical
            # one-letter-per-line column (not enough width left for even a
            # single character to lay out normally). Let it size to the
            # viewport on mobile; see email_recipient_picker's own mobile
            # handling in regression.py for the picker Row itself.
            width=(None if platform_caps.is_mobile() else 820), height=580),
        actions=[close_btn],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    app._show_dialog(dlg)

    def load():
        # ADR-002: the Sprint Summary generates via the Azure engine only. On a
        # non-Azure connection, say so honestly instead of calling E.sprint_summary
        # and surfacing its misleading "Not found (404) — check project spelling".
        import backend_setup as _bs
        if not _bs.sprint_reports_available(app):
            app._sum_loading = False
            _label = _bs.plan_link_label(app) if hasattr(_bs, "plan_link_label") else "this backend"
            def show_unavail():
                body_col.controls = [ft.Row([
                    ft.Icon(ft.Icons.INFO_OUTLINE, color=T.INK_3, size=20),
                    ft.Text(f"The Sprint Summary is currently available on Azure DevOps "
                            f"only — not on {_label}.",
                            size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500, expand=True)],
                    spacing=8)]
                try: body_col.update()
                except Exception: app.render()
            app.ui_safe(show_unavail)
            return
        try:
            data = E.sprint_summary(app.project, app.plan_id)
        except Exception as ex:
            app._sum_loading = False
            # Capture the message NOW, not inside show_err(). `except X as ex`
            # is implicitly `del`-ed by Python the instant this except suite
            # finishes (to avoid a traceback/frame reference cycle) — and
            # app.ui_safe() dispatches via page.run_thread(), which SCHEDULES
            # show_err() to run later on the page thread rather than calling
            # it inline. So by the time show_err() actually ran, `ex` was
            # already gone, and referencing it as a free variable crashed
            # with "NameError: cannot access free variable 'ex'" — a real,
            # deterministic crash confirmed from a live `python main.py` run,
            # not a network/message-content issue at all.
            err_msg = str(ex)[:160]
            def show_err():
                body_col.controls = [ft.Row([
                    ft.Icon(ft.Icons.ERROR_OUTLINE, color=T.RED, size=20),
                    ft.Text(f"Could not load summary: {err_msg}",
                            size=12.5, color=T.RED, weight=ft.FontWeight.W_500, expand=True)],
                    spacing=8)]
                try: body_col.update()
                except Exception: app.render()
            app.ui_safe(show_err)
            return

        # Inline delete, mirroring Sprint Plan's own _delete_story exactly:
        # confirm first, then remove from the LOCAL list and recalculate —
        # this never touches Azure DevOps, only trims what this summary
        # (and, if sent, its email) shows.
        def _delete_story(sid):
            def _do():
                data["stories"] = [s for s in data["stories"] if s["id"] != sid]
                render_summary()
                try:
                    app._toast(f"Removed story {sid} from the summary.")
                except Exception:
                    pass
            def _d(e):
                if getattr(app, "readonly", False):
                    return app._toast("Read-only — your role can't modify the summary.")
                app._confirm(
                    "Remove story?",
                    f"Remove story {sid} from this summary and recalculate the "
                    "totals? This doesn't change anything in Azure DevOps.",
                    _do, yes_label="Remove")
            return _d

        def render_summary():
            app._sum_data = data
            app._sum_loading = False
            # Recomputed from data["stories"] every render (not read from the
            # fetch-time totals) so an inline delete's recalculation actually
            # shows up — mirrors Sprint Plan's _delete_story, which also
            # recalculates its totals from the live row list.
            total = len(data["stories"])
            total_tc = sum(s.get("test_cases", 0) for s in data["stories"])
            by_state = {}
            for s in data["stories"]:
                by_state[s["state"]] = by_state.get(s["state"], 0) + 1

            # Header line
            header = ft.Column([
                ft.Row([ft.Container(
                    ft.Text("SPRINT SNAPSHOT", size=10, weight=ft.FontWeight.BOLD,
                            color=T.VIOLET_INK),
                    bgcolor=T.VIOLET_SOFT, border_radius=20,
                    padding=ft.Padding.symmetric(vertical=4, horizontal=11))], tight=True),
                ft.Container(height=8),
                ft.Text(data["plan_name"], size=18, weight=ft.FontWeight.BOLD, color=T.INK),
                ft.Text(data["iteration"] or "—", size=11, color=T.INK_3,
                        weight=ft.FontWeight.BOLD, font_family=T.F_MONO),
            ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.START)

            # Stat tiles: total stories + total test cases
            _tiles = [
                stat_tile("Stories", total, tone="violet"),
                stat_tile("Test Cases", total_tc, tone="green"),
                stat_tile("Statuses", len(by_state), tone="amber"),
            ]
            if platform_caps.is_mobile():
                # Inside this dialog the usable width is only ~300 logical px on
                # a phone, so three expand=True tiles got ~93px each and
                # stat_tile's no_wrap+ELLIPSIS label truncated to "St…" / "Te…" /
                # "St…" — unreadable. Give each tile a width that actually fits
                # its label and scroll the row sideways instead of compressing
                # the content (same rule as the generated-plan KPI cards).
                for _t in _tiles:
                    try:
                        _t.expand = None
                        _t.width = 132
                    except Exception:
                        pass
                tiles = ft.Row(_tiles, spacing=10, scroll=ft.ScrollMode.AUTO,
                               tight=True,
                               vertical_alignment=ft.CrossAxisAlignment.START)
            else:
                tiles = ft.Row(_tiles, spacing=10)

            # Status breakdown — give EACH status its own distinct colour so the
            # cards are visually separable (most states otherwise collapsed to the
            # same grey). Colours are assigned from a rotating palette, ordered by
            # count, and reused for the distribution bar so the two line up.
            #
            # SECURITY/THEME NOTE: this used to be a list of (fg, bg) pairs with
            # bg as a flat pastel hex tuned for the LIGHT theme only (e.g.
            # "#ECE8FF"), so in dark mode the cards rendered as bright white-ish
            # boxes clashing with the surrounding dark surface. bg is now derived
            # from fg via with_opacity() — a translucent tint over whatever
            # surface is actually behind it — so it reads correctly in both
            # themes automatically, the same way this function's own card border
            # already computed its color (with_opacity(0.30, fg), one line
            # below), instead of adding a second hardcoded light/dark table.
            _PALETTE_FG = [
                T.VIOLET_INK, T.GREEN, "#1C80E0", T.AMBER,
                "#0E8A8A", T.RED, "#6A33A8", "#C2860C",
            ]
            _sorted_states = sorted(by_state.items(), key=lambda x: -x[1])
            _state_color = {st: _PALETTE_FG[i % len(_PALETTE_FG)]
                            for i, (st, _c) in enumerate(_sorted_states)}
            # Card width is computed from how many status cards there are so
            # they all fit across one row of the (now-820px-wide) dialog
            # instead of truncating each label to "Product O…" — the full
            # status name wraps onto 2 lines instead. Cards shrink toward
            # _MIN_W as more statuses appear; if there are still too many to
            # fit even at the floor width (a LOT of distinct states), the
            # Row's wrap=True is the fallback so they flow onto a second row
            # rather than overflowing the dialog horizontally.
            _MIN_W, _MAX_W = 92, 150
            _n_cards = len(_sorted_states)
            _card_w = _MAX_W
            _m_status = platform_caps.is_mobile()
            if _m_status:
                # The width maths below assumes the 820px DESKTOP dialog. On a
                # phone the dialog is only ~300 logical px wide, so that formula
                # handed back cards up to 150px and the row overflowed — the
                # status breakdown rendered clipped mid-card. Use one readable
                # fixed width and scroll the row sideways instead (same rule as
                # the stat tiles above and the generated-plan KPI cards).
                _card_w = 132
            elif _n_cards:
                _avail = 760  # dialog content width (820) minus outer padding
                _raw = (_avail - (10 * (_n_cards - 1))) / _n_cards
                _card_w = max(_MIN_W, min(_MAX_W, _raw))
            def _status_card(label, count, fg, width):
                return ft.Container(
                    ft.Column([
                        ft.Text(str(count), size=22, weight=ft.FontWeight.BOLD, color=fg),
                        ft.Text(label, size=11, weight=ft.FontWeight.BOLD, color=T.INK_2,
                                max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                                text_align=ft.TextAlign.CENTER),
                    ], spacing=1, horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True),
                    bgcolor=ft.Colors.with_opacity(0.14, fg), border_radius=T.R,
                    border=ft.Border.all(1, ft.Colors.with_opacity(0.30, fg)),
                    padding=ft.Padding.symmetric(vertical=12, horizontal=10),
                    width=width, tooltip=f"{label}: {count}")
            state_cards = []
            for st, cnt in _sorted_states:
                state_cards.append(_status_card(st, cnt, _state_color[st], _card_w))
            status_row = ft.Row(state_cards, wrap=not _m_status, spacing=10,
                                run_spacing=10,
                                scroll=(ft.ScrollMode.AUTO if _m_status else None),
                                tight=bool(_m_status)) \
                if state_cards else ft.Text("No stories in this sprint.",
                                            size=12, color=T.INK_3, weight=ft.FontWeight.W_500)
            dist_bar = ft.Container(
                ft.Row([ft.Container(expand=max(1, c),
                                     bgcolor=_state_color[stt],
                                     tooltip=f"{stt}: {c}")
                        for stt, c in _sorted_states],
                       spacing=2),
                height=10, border_radius=6,
                clip_behavior=ft.ClipBehavior.HARD_EDGE) if by_state else ft.Container()

            # Per-story rows — only the "#id" is a clickable link to Azure
            # DevOps, using the SAME helper Sprint Plan uses (identical
            # styling + properly-escaped URL) instead of a hand-built one;
            # the rest of the row is inert. Rows are zebra-striped and carry
            # an inline delete button, both copied from Sprint Plan's table
            # so the two screens match.
            # Column widths shared between the header row and every story row
            # so labels line up with the cells underneath (badge()/Text() are
            # both auto-width, so without a fixed Container around each one
            # the header wouldn't actually align with anything below it).
            # STORY used to be expand=True, which only works with a bounded
            # parent — fine when this table sat directly inside the dialog's
            # (previously fixed-820px) Row, but on a ~390px phone the four
            # OTHER fixed columns (118+54+110+34=316px) already ate nearly
            # the whole width, so STORY's remaining space collapsed to
            # ~0px and its own "STORY" label wrapped one character per line
            # (confirmed live: "S\nT\nO\nR\nY"). Fixed-width like every other
            # column instead, and the whole table (below) now sits inside a
            # horizontally-scrolling wrapper — same pattern already used for
            # AI Usage's own results table — so columns keep their natural
            # size and a phone just scrolls sideways to see the rest,
            # instead of every column being squeezed to fit.
            _COL_STORY, _COL_ASSIGNED, _COL_TC, _COL_STATE, _COL_DEL = 200, 118, 54, 110, 34
            table_header = ft.Row([
                ft.Container(ft.Text("STORY", size=10, weight=ft.FontWeight.BOLD,
                                     color=T.INK_3), width=_COL_STORY),
                ft.Container(ft.Text("ASSIGNED", size=10, weight=ft.FontWeight.BOLD,
                                     color=T.INK_3), width=_COL_ASSIGNED),
                ft.Container(ft.Text("TC", size=10, weight=ft.FontWeight.BOLD,
                                     color=T.INK_3, text_align=ft.TextAlign.CENTER),
                             width=_COL_TC, alignment=ft.Alignment.CENTER),
                ft.Container(ft.Text("STATUS", size=10, weight=ft.FontWeight.BOLD,
                                     color=T.INK_3, text_align=ft.TextAlign.CENTER),
                             width=_COL_STATE, alignment=ft.Alignment.CENTER),
                ft.Container(width=_COL_DEL),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

            story_rows = []
            for i, s in enumerate(data["stories"]):
                rtl = any('\u0600' <= c <= '\u06ff' for c in s["title"])
                id_link = ft.Row([
                    _id_link(app, s["id"], color=T.VIOLET_INK,
                             weight=ft.FontWeight.BOLD, size=10.5,
                             font_family=T.F_MONO),
                    ft.Icon(ft.Icons.OPEN_IN_NEW, size=11, color=T.VIOLET_INK),
                ], spacing=3, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                assigned_name = s.get("assigned_to") or "Unassigned"
                assigned_cell = ft.Container(
                    ft.Row([
                        ft.Icon(ft.Icons.PERSON_OUTLINE, size=13,
                                color=(T.INK_3 if assigned_name == "Unassigned" else T.INK_2)),
                        ft.Text(assigned_name, size=11.5,
                               weight=ft.FontWeight.W_600,
                               color=(T.INK_3 if assigned_name == "Unassigned" else T.INK),
                               max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                               tooltip=assigned_name),
                    ], spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    width=_COL_ASSIGNED)
                del_btn = ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE, icon_size=18, icon_color=T.RED,
                    tooltip="Remove from this summary",
                    on_click=_delete_story(s["id"]),
                    width=_COL_DEL, height=34,
                    style=ft.ButtonStyle(padding=ft.Padding.all(0),
                                         shape=ft.RoundedRectangleBorder(radius=8)))
                story_rows.append(ft.Container(
                    ft.Row([
                        ft.Column([
                            ft.Text(s["title"] or "(no title)", size=12.5,
                                    weight=ft.FontWeight.BOLD, color=T.INK,
                                    font_family=(T.F_AR if rtl else None),
                                    text_align=(ft.TextAlign.RIGHT if rtl else ft.TextAlign.LEFT),
                                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                            id_link,
                        ], spacing=2, width=_COL_STORY),
                        assigned_cell,
                        ft.Container(badge(f"{s['test_cases']} TC", "grey"),
                                    width=_COL_TC, alignment=ft.Alignment.CENTER),
                        ft.Container(badge(s["state"], _state_kind(s["state"])),
                                    width=_COL_STATE, alignment=ft.Alignment.CENTER),
                        del_btn,
                    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.Padding.symmetric(vertical=6, horizontal=12),
                    bgcolor=(T.CARD if i % 2 == 0 else T.CARD_2),
                    border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER_2))))
            if not story_rows:
                story_rows = [ft.Text("No user stories found in this sprint.",
                                      size=12, color=T.INK_3, weight=ft.FontWeight.W_500)]

            body_col.controls = [
                header,
                ft.Container(height=4),
                tiles,
                ft.Container(height=10),
                dist_bar,
                ft.Container(height=8),
                ft.Text("STATUS BREAKDOWN", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
                status_row,
                ft.Container(height=6),
                ft.Text("STORIES", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
                ft.Container(
                    ft.Row([ft.Column([
                                ft.Container(table_header,
                                            padding=ft.Padding.symmetric(vertical=6, horizontal=12),
                                            bgcolor=T.CARD_2,
                                            border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER))),
                                ft.Column(story_rows, spacing=0, scroll=ft.ScrollMode.AUTO,
                                         height=240),
                             ], spacing=0, tight=True)], scroll=ft.ScrollMode.AUTO),
                    bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER),
                    border_radius=T.R, padding=ft.Padding.symmetric(vertical=2, horizontal=4),
                    clip_behavior=ft.ClipBehavior.HARD_EDGE),
            ]
            email_bar.visible = True
            try:
                body_col.update(); email_bar.update()
            except Exception:
                app.render()

        app.ui_safe(render_summary)

    app._bg(load)

def open_existing_steps_modal(app, have, total, on_choice):
    chosen = {"mode": "evaluate"}

    def opt(title, desc, key, icon, recommended=False):
        sel = (chosen["mode"] == key)
        head = [ft.Icon(icon, size=15, color=(T.VIOLET_INK if key == "evaluate" else T.INK_2)),
                ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=T.INK)]
        if recommended:
            head.append(badge("Recommended", "violet"))
        box = ft.Container(
            ft.Row([
                ft.Container(width=16, height=16, border_radius=10,
                             border=ft.Border.all(2, (T.VIOLET if sel else T.BORDER)),
                             bgcolor=(T.VIOLET if sel else None)),
                ft.Column([ft.Row(head, spacing=7),
                           ft.Text(desc, size=11.5, color=T.INK_2, weight=ft.FontWeight.W_500)],
                          spacing=4, expand=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
            padding=12, border_radius=T.R,
            border=ft.Border.all(1, (T.VIOLET if sel else T.BORDER)),
            bgcolor=(T.VIOLET_SOFT if sel else T.CARD_2),
            on_click=lambda e, k=key: select(k))
        return box

    body = ft.Column(spacing=10)
    def select(k):
        chosen["mode"] = k
        rebuild()
    def rebuild():
        body.controls = [
            opt("Skip existing steps",
                "Leave them untouched and only fill the empty ones. Fast — uses no AI credits on cases that already have steps.",
                "skip", ft.Icons.CHECK),
            opt("Evaluate with AI",
                "Checks each existing test case against the requirements and regenerates only the inadequate ones. Flagged cases appear in the report.",
                "evaluate", ft.Icons.AUTO_AWESOME, recommended=True),
        ]
        app.page.update()
    rebuild()

    def cont(e):
        app._close_dialog()
        on_choice(chosen["mode"])

    dlg = ft.AlertDialog(
        modal=True,
        content=ft.Container(
            width=(None if platform_caps.is_mobile() else 496),
            content=ft.Column([
            ft.Row([
                ft.Container(ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=18, color=T.AMBER),
                             width=34, height=34, bgcolor=T.AMBER_SOFT, border_radius=9,
                             alignment=ft.Alignment.CENTER),
                ft.Column([
                    ft.Text("Some test cases already have steps", size=16,
                            weight=ft.FontWeight.W_800, color=T.INK),
                    ft.Text(f"{have} of {total} test cases in this plan already contain steps. Choose how to handle them.",
                            size=13, color=T.INK_2, weight=ft.FontWeight.W_500),
                ], spacing=1, expand=True),
            ], spacing=10),
            ft.Container(height=14),
            body,
        ], spacing=0, tight=True)),
        actions=[
            # Single Row so Flet's action bar doesn't stack them (Cancel was
            # floating above a full-width Continue). Compact, right-aligned pair.
            ft.Row([
                ghost_btn("Cancel", on_click=lambda e: app._close_dialog()),
                primary_btn("Continue", icon=ft.Icons.ARROW_FORWARD, on_click=cont),
            ], alignment=ft.MainAxisAlignment.END, spacing=10, tight=True),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=T.R_LG),
    )
    app._show_dialog(dlg)

# ═══════════════════════════════════════════════════════════════════════════
#  RUN — start + live screen
# ═══════════════════════════════════════════════════════════════════════════
