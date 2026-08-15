"""run.py — Run screen (generation progress / results).

Extracted from main.py (Step-4 modular refactor). screen(app) reads run
state off the app; handlers stay on the QAStudio instance.
"""
import flet as ft
import theme as T
import strings
from ui import card, _btn_shadow, primary_btn, stat_tile


def screen(app):
        s = getattr(app, "_stats", {"total": 0, "stories_done": 0, "total_stories": 0,
                                     "done": 0, "skipped": 0, "errors": 0, "created": 0})
        p = getattr(app, "_progress", {"pct": 0, "label": "Starting…"})
        if not hasattr(app, "_story_prog"):
            app._story_prog = {}
        # NOTE: do NOT reset _run_finished / _run_started / _emailed_to here.
        # run_screen() also runs when navigating BACK to Run after a finished run;
        # resetting them would make the spinner animate and stories show "Running"
        # again. These flags are initialized only in _launch_run().

        # Idle state: when no run has started this session (e.g. reached via the
        # command palette) show a friendly empty state — NOT the live "Starting…"
        # scaffolding or a Stop button.
        if (not getattr(app, "_run_active", False)
                and not getattr(app, "_run_started", False)
                and not getattr(app, "_run_finished", False)
                and getattr(app, "last_report", None) is None):
            idle = ft.Container(
                ft.Column([
                    ft.Container(ft.Icon(ft.Icons.MONITOR_HEART, size=23, color=T.VIOLET_INK),
                                 width=50, height=50, bgcolor=T.VIOLET_SOFT,
                                 border_radius=14, alignment=ft.Alignment.CENTER),
                    ft.Container(height=12),
                    ft.Text(strings.t("run_no_run_yet"), size=15, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Container(height=4),
                    ft.Text(strings.t("run_idle_hint"),
                            size=12.5, color=T.INK_3, weight=ft.FontWeight.W_500,
                            text_align=ft.TextAlign.CENTER),
                    ft.Container(height=16),
                    primary_btn(strings.t("run_go_to_setup"), icon=ft.Icons.ARROW_FORWARD,
                                on_click=lambda e: app.goto("setup")),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                   alignment=ft.MainAxisAlignment.CENTER, spacing=0, tight=True),
                alignment=ft.Alignment.CENTER, expand=True,
                padding=ft.Padding.symmetric(vertical=40, horizontal=20))
            return app.shell(strings.t("run_title"), strings.t("run_no_run_in_progress"), idle)

        import platform_caps as _pc
        # Phone (mobile Phase 2): five fixed stat tiles overflow a ~390px
        # width — let the row wrap into two lines. Desktop keeps one line.
        _wrap = _pc.is_mobile()
        is_steps = (app.tool == "steps")
        if is_steps:
            app._stats_row = ft.Row(wrap=_wrap, controls=[
                stat_tile(strings.t("run_stat_test_cases"), s["total"]),
                stat_tile(strings.t("run_stat_created"), s.get("created", 0), tone="violet"),
                stat_tile(strings.t("run_stat_updated"), s["done"], tone="green"),
                stat_tile(strings.t("run_stat_skipped"), s["skipped"], tone="amber"),
                stat_tile(strings.t("run_stat_errors"), s["errors"], tone="red"),
            ], spacing=11)
        else:
            app._stats_row = ft.Row(wrap=_wrap, controls=[
                stat_tile(strings.t("run_stat_test_cases"), s["total"]),
                stat_tile(strings.t("run_stat_stories"), f"{s['stories_done']}", tone="violet", sub=f"/{s['total_stories']}"),
                stat_tile(strings.t("run_stat_created"), s["done"], tone="green"),
                stat_tile(strings.t("run_stat_skipped"), s["skipped"], tone="amber"),
                stat_tile(strings.t("run_stat_errors"), s["errors"], tone="red"),
            ], spacing=11)

        _stopping = getattr(app, "_stopping", False)
        _finished = getattr(app, "_run_finished", False)
        _done = p["pct"] >= 100 or _finished
        _idle = _done or _finished  # no spinner when finished/stopped-and-done
        app._bar = ft.ProgressBar(value=(p["pct"]/100 if (p["pct"] > 0 or _finished) else None),
                                   color=(T.AMBER if (_stopping and not _finished) else T.VIOLET),
                                   bgcolor="#EAE8F4", bar_height=7, border_radius=4)
        if _finished:
            app._bar.value = 1.0
        spinner = (ft.Container(width=14, height=14) if (_stopping or _idle)
                   else ft.ProgressRing(width=14, height=14, stroke_width=2, color=T.VIOLET))
        _started = getattr(app, "_run_started", False)
        _reason = (getattr(app, "last_report", None) or {}).get("reason")
        _was_stopped = _finished and (_reason == "credit"
                                      or getattr(app, "stop_flag", False))
        _label = (strings.t("run_stopped") if _was_stopped
                  else strings.t("run_completed") if _finished
                  else (strings.t("run_stopping_after") if _stopping
                        else (strings.t("run_completed") if _done
                              else (p["label"] if _started else strings.t("run_discovering")))))
        # Right label: once the run has finished it must NOT fall back to "Starting…"
        _pct_label = (strings.t("run_stopped") if _was_stopped
                      else strings.t("run_done") if _finished
                      else f"{p['pct']}%" if (p["pct"] > 0 or _started)
                      else strings.t("run_starting"))
        app._prow = ft.Row([
            spinner,
            ft.Text(_label, size=12, color=T.INK_2, weight=ft.FontWeight.BOLD),
            ft.Container(expand=True),
            ft.Text(_pct_label, size=12, color=T.VIOLET_INK, weight=ft.FontWeight.BOLD),
        ], spacing=8)

        # Per-story cards grid
        app._story_grid = ft.Column(app._build_story_cards(), spacing=12)

        # Recent activity log (compact). Rebuilding this Column from scratch on
        # every screen() call (e.g. navigating away to Settings mid-run to
        # switch the AI provider, then back to Run) used to leave
        # app._rendered_count stale — _refresh_run's _apply() (main.py) tracks
        # how many of app._log_lines are already reflected in app._log_col so
        # it only needs to append the DELTA on each new line, but this full
        # rebuild replaces app._log_col wholesale without telling that
        # bookkeeping anything changed. Depending on timing that produced
        # either duplicated lines (rendered_count too LOW for the fresh
        # column) or an empty-looking panel that wouldn't grow again until
        # app._log_lines caught back up past a stale, too-HIGH count — seen
        # live right after a mid-run provider switch. Locked the same as
        # _apply() itself so this rebuild can't race a concurrent background
        # append, and _rendered_count is set to match this exact snapshot so
        # the next _apply() call's delta is correct no matter what happened
        # to the count before this render.
        log_lines = app._render_log_lines()
        if not log_lines:
            log_lines = [ft.Row([
                ft.ProgressRing(width=14, height=14, stroke_width=2, color=T.VIOLET),
                ft.Text(strings.t("run_log_starting"),
                        size=12.5, color=T.INK_3, weight=ft.FontWeight.BOLD),
            ], spacing=10)]
        # ft.ListView, NOT ft.Column(scroll=…, expand=True).
        #
        # That Column shape — a scrolling Column with expand=True — is the one
        # documented in DEV_ROADMAP (flet-dev/flet#6087) as silently BLANKING
        # its whole containing card on Windows desktop rather than erroring.
        # The Automation log hit exactly this and was converted to a ListView;
        # the Run log kept the broken shape and was never converted, so the
        # same bug stayed live here. Symptom reported: switching the AI
        # provider while a run was paused on a credit limit re-rendered the Run
        # screen and left RECENT ACTIVITY completely empty — scroll track
        # visible, no content — while the run itself carried on fine.
        #
        # ListView is Flet's real scrolling-list control and is what
        # automation.py:343 already uses successfully. `.controls` works the
        # same, so _apply()'s append/rebuild bookkeeping below is unaffected.
        def _make_log_col():
            return ft.ListView(controls=log_lines, spacing=2,
                               expand=True, auto_scroll=True)

        _log_lock = getattr(app, "_run_log_ui_lock", None)
        if _log_lock:
            with _log_lock:
                app._log_col = _make_log_col()
                app._rendered_count = len(getattr(app, "_log_lines", []))
        else:
            app._log_col = _make_log_col()
            app._rendered_count = len(getattr(app, "_log_lines", []))

        def _log_tool_btn(icon, tip, cb, danger=False):
            # Same small rounded icon-button "chip" as the Automation screen's
            # Activity log toolbar (automation.py) — kept visually identical.
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

        log_card = card(ft.Column([
            ft.Row([ft.Text(strings.t("run_recent_activity"), size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
                    ft.Container(expand=True),
                    ft.Text(strings.t("run_select_to_copy"), size=10, color=T.INK_3,
                            weight=ft.FontWeight.W_500),
                    ft.Container(width=8),
                    _log_tool_btn(ft.Icons.COPY_ALL_OUTLINED, strings.t("run_copy_log_tip"),
                                 app._copy_run_log),
                    ft.Container(width=6),
                    _log_tool_btn(ft.Icons.DELETE_OUTLINE, strings.t("run_clear_log_tip"),
                                 app._clear_run_log, danger=True)],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=8),
            # NOT wrapped in its own ft.SelectionArea: shell() (main.py) already
            # wraps the ENTIRE screen body in one outer SelectionArea, so this
            # text is already selectable — a SECOND, nested SelectionArea here
            # is redundant and was the reported cause of Ctrl+C not copying
            # from this panel specifically (two overlapping SelectionArea
            # widgets fight over which one "owns" the current selection for
            # keyboard-shortcut purposes, even though drag-to-select still
            # visually worked). Same root cause already fixed once for
            # automation.py's log panel — see its comment for the full
            # Flutter-bug writeup (flutter/flutter#183079); this panel's own
            # fixed height=380 meant it never hit that bug's CRASH symptom,
            # which is why the redundant SelectionArea was left in place here
            # until now, but it still broke Ctrl+C on its own. The "Copy
            # entire log" toolbar button above is an unaffected fallback
            # either way.
            ft.Container(app._log_col, height=380, bgcolor=T.CARD_2,
                         border=ft.Border.all(1, T.BORDER), border_radius=T.R, padding=12),
        ], spacing=0))

        # ── "THIS RUN" live progress card (elapsed · ETA · story x/y) ──
        app._tr_meta = ft.Text(app._run_meta_line(), size=11.5, color=T.INK_3,
                                weight=ft.FontWeight.BOLD, font_family=T.F_MONO)
        prog_card = card(ft.Column([
            ft.Row([ft.Text(strings.t("run_this_run"), size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
                    ft.Container(expand=True),
                    app._tr_meta]),
            ft.Container(height=12),
            app._prow,
            ft.Container(height=2),
            app._bar,
        ], spacing=6))

        body = ft.Column([
            app._stats_row,
            prog_card,
            app._story_grid,
            log_card,
        ], spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)

        app._stop_btn_text = ft.Text(
            strings.t("run_stopping") if _stopping else strings.t("run_stop_btn"),
            size=13, color="#FFFFFF", weight=ft.FontWeight.BOLD)
        stop_btn = ft.FilledButton(
            content=ft.Row([ft.Icon(ft.Icons.STOP, size=14, color="#FFFFFF"), app._stop_btn_text],
                           spacing=8, tight=True),
            height=40, on_click=lambda e: app._stop_run(),
            disabled=_stopping,
            style=ft.ButtonStyle(bgcolor=T.RED, color="#FFFFFF", elevation=0,
                shape=ft.RoundedRectangleBorder(radius=T.R),
                padding=ft.Padding.symmetric(horizontal=16, vertical=0)))
        # design red shadow (skip when disabled/stopping)
        stop = (stop_btn if _stopping
                else ft.Container(stop_btn, border_radius=T.R,
                                  shadow=_btn_shadow(T.RED, 0.55)))
        # Pause/Resume — twin of Automation's. Pause lets in-flight cases
        # finish, then holds before the next item (switch the AI provider in
        # Setup meanwhile); a fatal provider error auto-pauses the run and
        # flips this button to Resume (see main.py's _run_on_ai_error).
        _paused = bool(getattr(app, "_run_paused", False))
        pause_btn = ft.FilledButton(
            content=ft.Row([ft.Icon(ft.Icons.PLAY_ARROW if _paused else ft.Icons.PAUSE,
                                    size=14, color="#FFFFFF"),
                            ft.Text(strings.t("run_resume") if _paused else strings.t("run_pause"), size=13,
                                    color="#FFFFFF", weight=ft.FontWeight.BOLD)],
                           spacing=8, tight=True),
            height=40, on_click=lambda e: app._toggle_run_pause(),
            disabled=_stopping,
            style=ft.ButtonStyle(bgcolor=(T.GREEN if _paused else T.AMBER),
                                 color="#FFFFFF", elevation=0,
                                 shape=ft.RoundedRectangleBorder(radius=T.R),
                                 padding=ft.Padding.symmetric(horizontal=16, vertical=0)))
        pause = (pause_btn if _stopping
                 else ft.Container(pause_btn, border_radius=T.R,
                                   shadow=_btn_shadow(T.GREEN if _paused else T.AMBER, 0.45)))
        actions = ft.Row([pause, stop], spacing=10, tight=True)
        sub = strings.t("run_sub_live_story", done=s['stories_done'], total=s['total_stories']) if s['total_stories'] else strings.t("run_sub_live")
        if _paused:
            sub += strings.t("run_paused_suffix")
        return app.shell(strings.t("run_title"), sub, body, actions)

