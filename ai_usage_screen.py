"""ai_usage_screen.py — "AI Usage" report screen for QA Studio.

Every signed-in user can open this screen and see AI usage — calls, EXACT
token counts (read straight from each provider's own response, never
estimated), and an approximate cost — grouped by date / provider / model
(and, for Admins, also by user). Sourced from the 'ai-usage' Supabase Edge
Function via engine.usage_report_all_users(); see ADMIN_USERS_SETUP.md §6 to
deploy it.

SCOPE: a Member/Viewer sees only their OWN usage; an Admin sees every signed-
in user's usage. Both cases call the exact same function/endpoint — the
scope is decided server-side, in the Edge Function, from the caller's own
verified role (never from anything this client sends). This screen just
adapts its labeling (and hides the redundant User column for a non-admin,
since every row is already theirs). See supabase/functions/ai-usage.
"""
import os
import re
import threading
from datetime import date, timedelta, datetime, timezone


def _utc_today() -> date:
    """'Today' in UTC, not the machine's local date.

    Usage rows are stamped in UTC end-to-end (engine.record_ai_usage uses
    datetime.now(timezone.utc); the ai-usage Edge Function's GET filters
    created_at with an explicit '...T00:00:00Z'/'...T23:59:59.999Z' range).
    This screen used to default to/cap the picker at Python's date.today(),
    which is the LOCAL date — for anyone not in UTC, local "today" covers a
    different UTC calendar day for part of the day (e.g. UTC+2/+3 users see
    their evening/night usage land under UTC "today" while their picker still
    shows local "today" as a later date, or vice-versa near local midnight).
    Filtering to a narrow/"today" range then queries a UTC window that simply
    doesn't contain those rows, so the report comes back empty even though
    usage was recorded — this is the "date filter doesn't work" bug. Always
    deriving "today" from UTC keeps the picker's dates and the server's query
    window talking about the same calendar day."""
    return datetime.now(timezone.utc).date()

import flet as ft
import theme as T
import engine as E
import auth_supabase as auth
from ui import (card, ghost_btn, green_btn, primary_btn, hover_field,
                stat_tile)
from regression import email_recipient_picker

_EXPORTERS = {"json": E.export_usage_json, "xlsx": E.export_usage_xlsx,
             "docx": E.export_usage_docx, "pdf": E.export_usage_pdf}


def _init(app):
    today = _utc_today()
    defaults = {
        "_usage_report": None, "_usage_loading": False, "_usage_msg": None,
        "_usage_start": (today - timedelta(days=30)).isoformat(),
        "_usage_end": today.isoformat(),
        "_usage_email_to": None, "_usage_email_open": False,
        "_usage_email_status": None,
        "_usage_provider_filter": "All",   # client-side filter over the
                                            # already-fetched report — the date
                                            # range is a real server-side query
                                            # param, provider isn't, and doesn't
                                            # need to be (usage volume is small).
    }
    for k, v in defaults.items():
        if not hasattr(app, k):
            setattr(app, k, v)


def _load(app):
    if app._usage_loading:
        return
    app._usage_loading = True
    app._usage_msg = None
    app.ui_safe(app.render)

    def _work():
        ok, res = E.usage_report_all_users(app._usage_start or None, app._usage_end or None)
        app._usage_loading = False
        if ok:
            app._usage_report = res
            app._usage_msg = None
        else:
            app._usage_report = None
            app._usage_msg = ("err", res)
        if getattr(app, "active", None) == "ai_usage":
            app.ui_safe(app.render)
    threading.Thread(target=_work, daemon=True).start()


def _export(app, fmt):
    def _do(e):
        report = app._usage_report
        if not report:
            app._toast("Generate the report first.")
            return

        def work():
            try:
                path = _EXPORTERS[fmt](report)
                app.ui_safe(lambda p=path: app._toast(f"Saved {fmt.upper()}: {p}"))
                try:
                    os.startfile(os.path.dirname(path))
                except Exception:
                    pass
            except Exception as ex:
                app.ui_safe(lambda ex=ex: app._err(f"Export failed: {ex}"))
        threading.Thread(target=work, daemon=True).start()
    return _do


def _email(app):
    def _do(e):
        report = app._usage_report
        if not report:
            app._toast("Generate the report first.")
            return
        if not E.GMAIL_APP_PASS:
            app._usage_email_status = "Set a Gmail App Password in Setup → Connection first."
            app.ui_safe(app.render)
            return
        to = [x.strip() for x in re.split(r"[,\s;]+", (app._usage_email_to or "")) if x.strip()]
        if not to:
            app._usage_email_status = "Enter at least one recipient."
            app.ui_safe(app.render)
            return
        app._usage_email_status = "Sending…"
        app.ui_safe(app.render)

        def work():
            html = E.build_ai_usage_email(report)
            ok, err = E.send_report(to, "QA Studio — AI Usage Report", html)
            def show():
                app._usage_email_status = (f"Report emailed to {', '.join(to)}" if ok
                                           else f"Email failed — {err}")
                app.render()
            app.ui_safe(show)
        threading.Thread(target=work, daemon=True).start()
    return _do


def _date_field(app, label, value_str, on_pick):
    """Click-to-open calendar field backed by ft.DatePicker (Flet's native
    date overlay) — replaces a raw 'YYYY-MM-DD' text box so the date can't be
    mistyped, and keeps the label as a plain caption ABOVE the box instead of
    a Material floating label INSIDE it. The floating-label style is what was
    getting visually clipped at the top of the old text fields (dense=True
    leaves too little clearance above the box for the label to float into);
    a plain caption above sidesteps that entirely rather than just papering
    over the clipping with more padding."""
    try:
        val = date.fromisoformat(value_str) if value_str else _utc_today()
    except Exception:
        val = _utc_today()

    value_text = ft.Text(value_str or "Pick a date", size=12.5, weight=ft.FontWeight.W_600,
                         color=T.INK, font_family=T.F_MONO)
    box = ft.Container(
        ft.Row([ft.Icon(ft.Icons.CALENDAR_MONTH_OUTLINED, size=15, color=T.INK_3),
                value_text], spacing=8),
        width=160, height=40, padding=ft.Padding.symmetric(horizontal=12),
        alignment=ft.Alignment.CENTER_LEFT,
        border=ft.Border.all(1, T.BORDER), border_radius=T.R, bgcolor=T.CARD_2)

    def _changed(e):
        d = e.control.value
        if d:
            # Known upstream Flet/Flutter DatePicker bug (flet-dev/flet#6145,
            # reproduced on web + macOS, still open as of Flet 0.85.3):
            # on_change delivers a date exactly one calendar day earlier than
            # what was actually tapped (e.g. tapping the 8th reports the
            # 7th) — not timezone-conditional in our testing, just a flat
            # off-by-one from the widget itself. Compensate here rather than
            # in on_pick/_utc_today so the correction lives right next to the
            # bug it's working around. Safe to remove once this pin is
            # bumped past a Flet release that fixes #6145 upstream.
            d = d + timedelta(days=1)
            on_pick(d.strftime("%Y-%m-%d"))
        app.ui_safe(app.render)

    dp = ft.DatePicker(value=val, first_date=date(2020, 1, 1),
                       last_date=_utc_today(), on_change=_changed)

    def _open(e):
        # Drop any stale DatePicker overlays from a previous render before
        # adding this one — controls are rebuilt fresh every render, so
        # without this the overlay list would grow one DatePicker per
        # click across the screen's lifetime (same cleanup _toast() already
        # does for SnackBars, for the same reason).
        try:
            app.page.overlay[:] = [c for c in app.page.overlay
                                   if not isinstance(c, ft.DatePicker)]
        except Exception:
            pass
        try:
            app.page.overlay.append(dp)
            dp.open = True
            app.page.update()
        except Exception:
            pass
        try:
            app.page.open(dp)
        except Exception:
            pass

    clickable = ft.GestureDetector(content=box, on_tap=_open)
    return ft.Column([
        ft.Text(label, size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
        clickable,
    ], spacing=6, tight=True)


def _filtered_rows_and_totals(app, report):
    """Apply the client-side provider filter over the already-fetched report
    and recompute totals/unpriced-count for just the filtered rows. The date
    range is a real server-side query param (narrows what's fetched at all);
    provider isn't — usage volume is small enough that filtering the rows
    already in memory is simpler than a second round trip, and it means
    switching the provider filter is instant."""
    rows = report.get("rows", [])
    pf = getattr(app, "_usage_provider_filter", "All") or "All"
    filtered = rows if pf == "All" else [r for r in rows if r.get("provider") == pf]
    totals = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    unpriced = 0
    for r in filtered:
        totals["calls"] += r["calls"]
        totals["input_tokens"] += r["input_tokens"]
        totals["output_tokens"] += r["output_tokens"]
        if r["cost_usd"] is None:
            unpriced += r["calls"]
        else:
            totals["cost_usd"] += r["cost_usd"]
    totals["cost_usd"] = round(totals["cost_usd"], 4)
    providers = sorted({r.get("provider", "") for r in rows if r.get("provider")})
    return filtered, totals, unpriced, providers


def _report_body(app, is_admin):
    report = app._usage_report
    rows, t, unpriced, providers = _filtered_rows_and_totals(app, report)

    def _set_provider(prov):
        def _do(e):
            app._usage_provider_filter = prov
            app.ui_safe(app.render)
        return _do

    provider_filter = ft.Row([
        ft.Text("Provider", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
        hover_field(ft.Dropdown(
            value=app._usage_provider_filter, width=170, text_size=12.5, dense=True,
            options=[ft.DropdownOption(key="All", text="All providers")]
                   + [ft.DropdownOption(key=p, text=p) for p in providers],
            on_select=lambda e: _set_provider(e.control.value)(e),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10))),
    ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    tiles = ft.Row([
        stat_tile("Calls", t.get("calls", 0), tone="violet"),
        stat_tile("Input Tokens", t.get("input_tokens", 0)),
        stat_tile("Output Tokens", t.get("output_tokens", 0)),
        stat_tile("Est. Cost", f'${t.get("cost_usd", 0):.2f}', tone="green"),
    ], spacing=10)

    unpriced_note = (ft.Container(
        ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=15, color=T.AMBER),
                ft.Text(f"{unpriced} call(s) use a model with no published price — "
                        "excluded from the cost total.", size=11.5, color=T.AMBER,
                        weight=ft.FontWeight.W_600, expand=True)], spacing=8),
        bgcolor=T.AMBER_SOFT, border_radius=T.R,
        padding=ft.Padding.symmetric(vertical=8, horizontal=12))
        if unpriced else ft.Container(height=0))

    # (name, width, right_aligned, mono, row-value-getter). The User column
    # only makes sense for an Admin's whole-org view — for a non-admin every
    # row is already their own, so it's just dropped rather than shown
    # repeating the same email down the whole table.
    _cols = [("Date", 178, False, True, lambda r: r.get("date_range", r["date"]))]
    if is_admin:
        _cols.append(("User", 190, False, False, lambda r: r["user"]))
    _cols += [
        ("Provider", 96, False, False, lambda r: r["provider"]),
        ("Model", 160, False, True, lambda r: r["model"]),
        ("Module", 150, False, False, lambda r: r.get("module") or "Other"),
        ("Calls", 52, True, True, lambda r: str(r["calls"])),
        ("Input", 76, True, True, lambda r: str(r["input_tokens"])),
        ("Output", 76, True, True, lambda r: str(r["output_tokens"])),
        ("Cost", 76, True, True,
         lambda r: (f'${r["cost_usd"]:.4f}' if r["cost_usd"] is not None else "—")),
    ]
    header_row = ft.Row([
        ft.Text(name, size=10, weight=ft.FontWeight.BOLD, color=T.INK_3, width=w,
               text_align=(ft.TextAlign.RIGHT if right else ft.TextAlign.LEFT))
        for (name, w, right, mono, _get) in _cols
    ], spacing=8)

    def _row(i, r):
        is_cost_row_ok = r["cost_usd"] is not None
        cells = []
        for name, w, right, mono, get in _cols:
            v = get(r)
            is_cost = (name == "Cost")
            cells.append(ft.Text(
                v, size=11.5,
                weight=(ft.FontWeight.BOLD if is_cost else ft.FontWeight.W_500),
                color=(T.GREEN if is_cost and is_cost_row_ok else T.INK),
                font_family=(T.F_MONO if mono else None), width=w,
                text_align=(ft.TextAlign.RIGHT if right else ft.TextAlign.LEFT),
                no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, tooltip=v))
        return ft.Container(
            ft.Row(cells, spacing=8),
            padding=ft.Padding.symmetric(vertical=8, horizontal=6),
            bgcolor=(T.CARD if i % 2 == 0 else T.CARD_2),
            border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER_2)))

    table_rows = [_row(i, r) for i, r in enumerate(rows)] if rows else [
        ft.Container(ft.Text("No AI usage recorded for this range/filter.", size=12.5,
                             color=T.INK_3, weight=ft.FontWeight.W_500),
                    padding=ft.Padding.symmetric(vertical=24),
                    alignment=ft.Alignment.CENTER)]

    # Fixed pixel column widths (~900px total) are wider than a small/narrow
    # window — wrapping the header+rows block in a horizontally-scrolling Row
    # (instead of letting Flet clip/ellipsis every cell) lets the user drag
    # sideways to see the trailing columns instead of losing them. Header and
    # rows scroll together since they're both inside this one Row's single
    # child, so columns stay aligned.
    table_inner = ft.Column([
        ft.Container(header_row, padding=ft.Padding.symmetric(horizontal=6, vertical=8),
                    bgcolor=T.CARD_2,
                    border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER))),
        ft.Column(table_rows, spacing=0, scroll=ft.ScrollMode.AUTO, height=320),
    ], spacing=0)

    table = ft.Container(
        ft.Row([table_inner], scroll=ft.ScrollMode.AUTO),
        bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER), border_radius=T.R,
        clip_behavior=ft.ClipBehavior.HARD_EDGE)

    export_row = ft.Row([
        ghost_btn("JSON", icon=ft.Icons.CODE, on_click=_export(app, "json")),
        ghost_btn("Excel", icon=ft.Icons.GRID_ON, on_click=_export(app, "xlsx")),
        ghost_btn("Word", icon=ft.Icons.DESCRIPTION, on_click=_export(app, "docx")),
        ghost_btn("PDF", icon=ft.Icons.PICTURE_AS_PDF, on_click=_export(app, "pdf")),
    ], spacing=8, wrap=True)

    email_btn = green_btn("Email report", icon=ft.Icons.MAIL_OUTLINED, on_click=_email(app))
    email_picker = email_recipient_picker(
        app, "_usage_email_to", is_open_key="_usage_email_open",
        sync_key="usage_emails", trailing=email_btn)
    status_ok = bool(app._usage_email_status) and app._usage_email_status.startswith("Report emailed")
    status = ft.Text(app._usage_email_status or "", size=11.5, weight=ft.FontWeight.BOLD,
                     color=(T.GREEN if status_ok else T.RED),
                     visible=bool(app._usage_email_status))

    truncated_note = (ft.Text(
        "The server returned the maximum row cap for one request — narrow the "
        "date range for a complete report.", size=11, color=T.AMBER,
        weight=ft.FontWeight.W_600)
        if report.get("truncated") else ft.Container(height=0))

    return ft.Column([
        provider_filter,
        tiles,
        unpriced_note,
        truncated_note,
        ft.Container(height=2),
        table,
        ft.Container(height=6),
        ft.Text("EXPORT", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
        export_row,
        ft.Container(height=4),
        ft.Text("EMAIL THIS REPORT", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
        email_picker,
        status,
    ], spacing=10)


def screen(app):
    _init(app)
    me = getattr(app, "user", None)
    is_admin = auth.configured() and auth.is_admin(me)
    sub = ("Whole-organization AI usage & cost (admin)" if is_admin
           else "Your AI usage & cost")

    # NOTE: body is wrapped in a bare ft.Column([...card...]) rather than
    # returning the card() Container directly — shell()'s _install_top_gap
    # only inserts its header→content spacer into a Column (or a Row of
    # Columns), never into a bare Container. Passing the card straight
    # through left it flush against the header with no gap, its top edge
    # visually cut off. See screen()'s main body below for the same fix.
    if not auth.configured():
        body = ft.Column([card(ft.Column([
            ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, color=T.INK_3, size=20),
                    ft.Text("Multi-user accounts aren't set up", size=16,
                            weight=ft.FontWeight.W_800, color=T.INK)], spacing=10),
            ft.Container(height=6),
            ft.Text("Per-user reporting (and an admin's whole-org view) needs Supabase "
                    "sign-in configured. Your own usage is still tracked locally on this "
                    "machine regardless.", size=12.5,
                    color=T.INK_3, no_wrap=False),
        ], spacing=2))], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        return app.shell("AI Usage", sub, body)

    if app._usage_report is None and not app._usage_loading and not app._usage_msg:
        _load(app)

    def _set_start(v):
        app._usage_start = v

    def _set_end(v):
        app._usage_end = v

    def _generate(e):
        _load(app)

    def _reset(e):
        # Back to the screen's own defaults — last 30 days (UTC) + every
        # provider — and re-fetch, so "reset" always lands on the same state
        # a fresh visit to the screen would show.
        today = _utc_today()
        app._usage_start = (today - timedelta(days=30)).isoformat()
        app._usage_end = today.isoformat()
        app._usage_provider_filter = "All"
        _load(app)

    start_field = _date_field(app, "Start date", app._usage_start, _set_start)
    end_field = _date_field(app, "End date", app._usage_end, _set_end)
    gen_btn = primary_btn("Generate", icon=ft.Icons.QUERY_STATS,
                          on_click=_generate, disabled=app._usage_loading)
    reset_btn = ghost_btn("Reset", icon=ft.Icons.RESTART_ALT,
                          on_click=_reset, disabled=app._usage_loading)

    controls = [
        ft.Row([start_field, end_field, gen_btn, reset_btn], spacing=10,
              vertical_alignment=ft.CrossAxisAlignment.END),
    ]

    if app._usage_loading and app._usage_report is None:
        controls.append(ft.Container(
            ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2, color=T.VIOLET),
                    ft.Text("Loading usage…", size=12.5, color=T.INK_2)], spacing=10),
            padding=ft.Padding.symmetric(vertical=20)))
    elif app._usage_msg and app._usage_msg[0] == "err":
        controls.append(ft.Container(
            ft.Row([ft.Icon(ft.Icons.ERROR_OUTLINE, color=T.RED, size=18),
                    ft.Text(str(app._usage_msg[1]), size=12.5, color=T.RED, expand=True)],
                   spacing=8),
            padding=ft.Padding.symmetric(vertical=14)))
    elif app._usage_report:
        controls.append(_report_body(app, is_admin))

    # Scroll lives on the OUTER column, with the card as a naturally-sized
    # child inside it — same shape as setup.py's `left` column (cards stacked
    # inside ONE scrolling Column, not each card scrolling internally while
    # pinned in place). This is what makes the card slide up and pass BEHIND
    # the translucent header as you scroll, like every other screen: shell()'s
    # _install_top_gap inserts its header→content spacer as this column's
    # first item, so the spacer — and then the card's own top edge — scroll
    # away together with the rest of the content, instead of a fixed gap that
    # never moves. Giving the card itself expand=True/scroll= (the earlier
    # version of this fix) put the scroll on an INNER column while the gap
    # spacer sat in an outer, non-scrolling one — that produced a static gap
    # with no "behind the header" effect at all.
    body = ft.Column([
        card(ft.Column(controls, spacing=16)),
    ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    return app.shell("AI Usage", sub, body)
