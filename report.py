"""report.py — Report screen (post-run summary).

Extracted from main.py (Step-4 modular refactor). screen(app) renders the
summary cards from app state.
"""
import flet as ft
import theme as T
import engine as E
from ui import card, _btn_shadow, primary_btn, ghost_btn, progress_ring, stat_tile, badge


def screen(app):
        r = app.last_report or {"summary": "No run data", "updated": 0, "skipped": 0, "errors": 0}
        is_steps = (app.tool == "steps")
        updated = r.get("updated", r.get("created", 0))
        created = r.get("created", 0)
        skipped = r.get("skipped", 0)
        errors = r.get("errors", 0)
        stories_done = r.get("stories_done", 0)
        total_stories = r.get("total_stories", 0)
        action_items = r.get("action_items", [])

        if is_steps:
            _sub = (f"Test Case Steps · {created} created · {updated} updated with steps · "
                    f"{skipped} skipped · {errors} failed across {total_stories} "
                    f"{'story' if total_stories == 1 else 'stories'}.")
        else:
            _sub = (f"Test Case Titles · {updated} created · {skipped} skipped · "
                    f"{errors} failed across {total_stories} "
                    f"{'story' if total_stories == 1 else 'stories'}.")

        head_card = ft.Container(
            ft.Row([
                ft.Container(ft.Icon(ft.Icons.CHECK, size=26, color="#FFFFFF"),
                             width=52, height=52, bgcolor=T.GREEN, border_radius=14,
                             alignment=ft.Alignment.CENTER,
                             shadow=_btn_shadow(T.GREEN, 0.45)),
                ft.Column([
                    ft.Text("Run complete", size=18, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Text(_sub, size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500),
                ], spacing=3, expand=True),
            ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=18, bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER), border_radius=T.R_LG)

        if is_steps:
            stats = ft.Row([
                stat_tile("Created", created, tone="violet"),
                stat_tile("Updated", updated, tone="green"),
                stat_tile("Skipped", skipped, tone="amber"),
                stat_tile("Failed", errors, tone="red"),
                stat_tile("Stories", stories_done, tone="violet", sub=f"/{total_stories}"),
            ], spacing=11)
        else:
            stats = ft.Row([
                stat_tile("Created", updated, tone="green"),
                stat_tile("Skipped", skipped, tone="amber"),
                stat_tile("Failed", errors, tone="red"),
                stat_tile("Stories", stories_done, tone="violet", sub=f"/{total_stories}"),
            ], spacing=11)

        # Per-story breakdown (matches design)
        per_story = r.get("per_story", [])
        story_rows = []
        for sp in per_story:
            total = sp.get("total", 0); ok = sp.get("ok", 0)
            skipped = sp.get("skipped", 0); err = sp.get("err", 0)
            processed = ok + skipped + err
            pct = int(processed / total * 100) if total else 0
            ring_c = T.AMBER if err else (T.GREEN if processed >= total and total else T.VIOLET)
            chips = []
            if ok: chips.append(badge(f"✓ {ok}", "green"))
            if skipped: chips.append(badge(f"⏭ {skipped}", "amber"))
            if err: chips.append(badge(f"✕ {err}", "red"))
            _sid = sp.get('id', '')
            _su = (f"https://dev.azure.com/{E.AZURE_ORG}/{app.project}"
                   f"/_workitems/edit/{_sid}") if _sid else None
            story_rows.append(ft.Container(
                ft.Row([
                    progress_ring(pct, ring_c, size=46, label=pct),
                    ft.Column([
                        ft.Text(sp.get("title", ""), size=13, weight=ft.FontWeight.BOLD,
                                color=T.INK, font_family=T.F_AR, text_align=ft.TextAlign.RIGHT,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(f"#{sp.get('id','')}", size=11, color=T.INK_3,
                                weight=ft.FontWeight.BOLD, font_family=T.F_MONO),
                    ], spacing=2, expand=True),
                    ft.Row(chips, spacing=5, tight=True),
                ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=12, horizontal=14),
                border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER_2)),
                tooltip=(f"{sp.get('title','')}  ·  open #{_sid}" if sp.get('title') else None),
                on_click=(lambda e, u=_su: app._open_url(u)) if _su else None,
                ink=bool(_su)))
        if not story_rows:
            story_rows = [ft.Text("No per-story data.", size=12, color=T.INK_3,
                                  weight=ft.FontWeight.W_500)]
        breakdown_card = card(ft.Column([
            ft.Row([ft.Text("Per-story breakdown", size=13, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Container(expand=True),
                    ft.Text(f"{len(per_story)} stories", size=11, color=T.INK_3,
                            weight=ft.FontWeight.BOLD)]),
            ft.Container(height=6),
            ft.Column(story_rows, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True),
        ], spacing=0), expand=True)

        # Collapsible run activity log below the breakdown
        log_lines = app._render_log_lines() if getattr(app, "_log_lines", None) else [
            ft.Text("No activity recorded.", size=12, color=T.INK_3, weight=ft.FontWeight.W_500)]

        def _log_tool_btn(icon, tip, cb, danger=False):
            # Same small rounded icon-button "chip" as the Run/Automation
            # screens' Activity log toolbar — kept visually identical. Reuses
            # app._copy_run_log / app._clear_run_log since the Report screen
            # shows the same app._log_lines the Run screen just produced.
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
            ft.Row([ft.Text("Run activity log", size=13, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Container(expand=True),
                    ft.Text(f"{len(getattr(app,'_log_lines',[]))} lines", size=11,
                            color=T.INK_3, weight=ft.FontWeight.BOLD),
                    ft.Container(width=10),
                    _log_tool_btn(ft.Icons.COPY_ALL_OUTLINED, "Copy entire log",
                                 app._copy_run_log),
                    ft.Container(width=6),
                    _log_tool_btn(ft.Icons.DELETE_OUTLINE, "Clear log",
                                 app._clear_run_log, danger=True)],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=8),
            # NOT wrapped in its own ft.SelectionArea — shell() (main.py) already
            # wraps the whole screen body in one outer SelectionArea, so a nested
            # one here is redundant and breaks Ctrl+C on this panel specifically
            # (two overlapping SelectionArea widgets fight over which one owns
            # the current selection for keyboard-shortcut purposes). Same fix as
            # run.py's identical log panel and automation.py's Activity log —
            # see either for the full writeup.
            ft.Container(
                ft.Column(log_lines, spacing=2, scroll=ft.ScrollMode.AUTO, expand=True),
                height=240, bgcolor=T.CARD_2, border=ft.Border.all(1, T.BORDER),
                border_radius=T.R, padding=12),
        ], spacing=0))
        left = ft.Column([head_card, stats, breakdown_card, log_card], spacing=14,
                         expand=True, scroll=ft.ScrollMode.AUTO)

        # right: needs review + buttons
        review_items = []
        for a in action_items:
            _tc_id = a.get("id")
            _tc_url = (f"https://dev.azure.com/{E.AZURE_ORG}/{app.project}"
                       f"/_workitems/edit/{_tc_id}") if _tc_id else None
            review_items.append(ft.Container(
                ft.Column([
                    ft.Row([badge("Review", "amber", ft.Icons.WARNING_AMBER_ROUNDED),
                            ft.Text(f"#{a['id']}", size=11, color=T.INK_3, weight=ft.FontWeight.BOLD,
                                    font_family=T.F_MONO),
                            ft.Container(expand=True),
                            ft.Icon(ft.Icons.OPEN_IN_NEW, size=13, color=T.INK_3)], spacing=7),
                    ft.Text(a.get("title", ""), size=12.5, weight=ft.FontWeight.BOLD, color=T.INK,
                            font_family=T.F_AR, text_align=ft.TextAlign.RIGHT),
                    ft.Text(a.get("reason", ""), size=11, color=T.INK_2, weight=ft.FontWeight.W_500),
                ], spacing=4),
                padding=ft.Padding.symmetric(vertical=12, horizontal=11), border=ft.Border.all(1, T.BORDER),
                border_radius=T.R, bgcolor=T.CARD_2, margin=ft.Margin.only(bottom=9),
                tooltip=(f"Open test case #{_tc_id} in Azure DevOps" if _tc_url else None),
                on_click=(lambda e, u=_tc_url: app._open_url(u)) if _tc_url else None,
                ink=bool(_tc_url)))
        if not review_items:
            review_items = [ft.Text("Nothing flagged — all good.", size=12, color=T.INK_3,
                                    weight=ft.FontWeight.W_500)]

        # email confirmation chip (if a report was emailed)
        email_chip = None
        emailed_to = getattr(app, "_emailed_to", None)
        if emailed_to:
            email_chip = ft.Container(
                ft.Row([ft.Icon(ft.Icons.MAIL_OUTLINED, size=15, color=T.GREEN),
                        ft.Text(f"Report emailed to {emailed_to}", size=12,
                                color=T.GREEN, weight=ft.FontWeight.BOLD, expand=True)],
                       spacing=8),
                padding=ft.Padding.symmetric(vertical=11, horizontal=13),
                bgcolor=T.GREEN_SOFT, border_radius=T.R,
                border=ft.Border.all(1, "#CFEAD9"), margin=ft.Margin.only(top=10))

        # Header (+ optional subtitle), then scrollable list that expands,
        # then the email chip pinned at the bottom of the card.
        review_header = [
            ft.Row([ft.Text("Needs your review", size=13, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Container(expand=True),
                    ft.Text(str(len(action_items)), size=12, color=T.INK_3, weight=ft.FontWeight.BOLD)]),
        ]
        if action_items:
            review_header.append(
                ft.Text("Existing steps were judged inadequate and regenerated.",
                        size=11.5, color=T.INK_2, weight=ft.FontWeight.W_500))
        review_body = ft.Column([
            *review_header,
            ft.Container(height=10),
            ft.Container(
                ft.Column(review_items, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True),
                expand=True),
            *([email_chip] if email_chip else []),
        ], spacing=0, expand=True)

        right = ft.Column([
            ft.Container(card(review_body, expand=True), expand=True),
            primary_btn("New run", icon=ft.Icons.ARROW_FORWARD, expand=True,
                        on_click=lambda e: app._new_run()),
            ghost_btn("Open plan in Azure", icon=ft.Icons.FOLDER_OUTLINED, expand=True,
                      on_click=lambda e: app._open_azure(),
                      disabled=not app.can("act.open_plan"), ignore_ro=True),
        ], spacing=14, expand=True)
        body = ft.Row([ft.Container(left, expand=True),
                       ft.Container(right, width=340)], spacing=22,
                      vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
        tag = ft.Container(
            ft.Row([ft.Icon(ft.Icons.CHECK, size=13, color=T.GREEN),
                    ft.Text("Completed", size=11, color=T.GREEN, weight=ft.FontWeight.BOLD)], spacing=5, tight=True),
            padding=ft.Padding.symmetric(vertical=10, horizontal=5), bgcolor=T.GREEN_SOFT, border_radius=20,
            border=ft.Border.all(1, "#CFEAD9"))
        return app.shell("Report", app._relative_time(), body, tag)

