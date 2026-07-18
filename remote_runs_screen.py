"""remote_runs_screen.py — in-app viewer for GitHub-executed remote runs
(REMOTE_RUNS.md's "Next steps: the in-app live viewer is the next
iteration"). Two views in one screen() entry point:

  - LIST:   the signed-in user's own remote runs, newest first, tap to open.
  - DETAIL: live status + activity feed for one run, polling
            remote_run_events every ~2.5s while the run is queued/running/
            paused, with Pause/Resume/Stop wired to the `control` column
            (picked up by run_worker.py's _gate()/_control_poller() within
            ~2s — see that file). Reuses main.py's own _render_one_log() for
            each event so the log renders IDENTICALLY to a local run's
            activity feed (REMOTE_RUNS.md's own suggestion), rather than a
            second, drifting copy of that rendering logic.

State lives on the app instance (app._rr_*), same convention every other
screen module in this app already uses for its own screen-local state
(regression.py's app._reg_*, task_manager.py's app._tm_*, etc.) — this
module has no state of its own.

Polling, not Supabase Realtime: a websocket client is a new dependency this
app's mobile build has never carried (see build-apk.yml's minimal-
requirements posture and the azure-devops lesson from remote-run.yml this
same session — an unverified new dependency on the mobile build is a real
risk, not a style preference). Plain REST polling reuses the exact same
requests-based auth_supabase.py client every other remote-run call already
goes through, with no new moving parts.
"""
import time
import threading

import flet as ft
import theme as T
import platform_caps
import auth_supabase as auth
from ui import card, empty_state, ghost_btn, green_btn, danger_btn, badge, field_label


_STATUS_TONE = {
    "queued":  ("grey",   "Queued"),
    "running": ("violet", "Running"),
    "paused":  ("amber",  "Paused"),
    "done":    ("green",  "Done"),
    "stopped": ("amber",  "Stopped"),
    "error":   ("red",    "Error"),
}
_TERMINAL = ("done", "stopped", "error")


def _status_badge(status):
    kind, label = _STATUS_TONE.get(status, ("grey", status or "—"))
    return badge(label, kind=kind)


def _relative(ts_str):
    if not ts_str:
        return "—"
    try:
        import datetime as _dt
        t = _dt.datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        secs = (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds()
    except Exception:
        return str(ts_str)
    if secs < 45:
        return "just now"
    if secs < 90:
        return "1 min ago"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins} mins ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs} hr ago" if hrs == 1 else f"{hrs} hrs ago"
    days = hrs // 24
    return "1 day ago" if days == 1 else f"{days} days ago"


def screen(app):
    if not auth.configured():
        return app.shell("Remote Runs", "GitHub-executed runs",
                         empty_state(ft.Icons.CLOUD_OFF_OUTLINED, "Sign-in required",
                                     "Remote runs need Supabase sign-in — "
                                     "connect an account in Setup first."))
    if getattr(app, "_rr_view_id", None):
        return _detail_screen(app)
    return _list_screen(app)


# ── LIST ─────────────────────────────────────────────────────────────────────
def _load_list(app, show_busy=False):
    if show_busy:
        app._rr_list_loading = True
    app._rr_list = auth.list_remote_runs(limit=50) or []
    app._rr_list_loading = False
    app.ui_safe(app.render)


def _refresh_list(app):
    # Set synchronously, not inside the background closure — the caller's
    # very next render (which can happen before the bg thread even starts)
    # needs to already see loading=True so _list_screen's guard doesn't fire
    # a second redundant fetch in that narrow window.
    app._rr_list_loading = True
    app._bg(lambda: _load_list(app, show_busy=True))


def _kind_label(kind):
    return "Steps" if kind == "steps" else "Titles"


def _run_row(app, run):
    status = run.get("status") or "queued"
    story_ids = run.get("story_ids") or []
    subtitle = (f"{run.get('project') or '—'} · plan #{run.get('plan_id')} · "
               f"{len(story_ids)} stor{'y' if len(story_ids) == 1 else 'ies'}")
    row = ft.Row([
        ft.Column([
            ft.Row([
                ft.Text(_kind_label(run.get("kind")), size=13, weight=ft.FontWeight.BOLD,
                       color=T.INK),
                _status_badge(status),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text(subtitle, size=11.5, color=T.INK_3, weight=ft.FontWeight.W_500,
                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        ], spacing=3, expand=True, tight=True),
        ft.Column([
            ft.Text(_relative(run.get("created_at")), size=11, color=T.INK_3,
                   weight=ft.FontWeight.W_600),
            ft.Icon(ft.Icons.CHEVRON_RIGHT, size=17, color=T.INK_3),
        ], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.END),
    ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    return ft.Container(
        row, padding=ft.Padding.symmetric(vertical=12, horizontal=14),
        bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER_2), border_radius=T.R,
        margin=ft.Margin.only(bottom=8), ink=True,
        on_click=lambda e, rid=run.get("id"): _open_run(app, rid))


def _list_screen(app):
    # Only kick off a fetch if one isn't already in flight — an unrelated
    # re-render firing while the first load is still pending (e.g. another
    # background poller elsewhere in the app) would otherwise start a second,
    # redundant request every time this screen gets rebuilt before _rr_list
    # is set.
    if getattr(app, "_rr_list", None) is None and not getattr(app, "_rr_list_loading", False):
        _refresh_list(app)

    loading = getattr(app, "_rr_list_loading", True)
    runs = getattr(app, "_rr_list", None) or []

    refresh_btn = ghost_btn("Refresh", icon=ft.Icons.REFRESH,
                            on_click=lambda e: _refresh_list(app))

    if loading and getattr(app, "_rr_list", None) is None:
        body_content = card(ft.Column([
            ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2, color=T.VIOLET),
                   ft.Text("Loading remote runs…", size=12.5, color=T.INK_3,
                          weight=ft.FontWeight.W_500)],
                  spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=0))
    elif not runs:
        body_content = empty_state(
            ft.Icons.CLOUD_QUEUE_OUTLINED, "No remote runs yet",
            "Turn on “Run remotely” on the Setup screen to execute a run "
            "on GitHub Actions instead of locally — it'll show up here.")
    else:
        body_content = ft.Column([_run_row(app, r) for r in runs], spacing=0)

    body = ft.Column([
        ft.Row([ft.Container(expand=True), refresh_btn]),
        ft.Container(height=10),
        body_content,
    ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    return app.shell("Remote Runs", "GitHub-executed runs — live status & activity",
                     body)


# ── DETAIL ───────────────────────────────────────────────────────────────────
def _open_run(app, run_id):
    if not run_id:
        return
    app._rr_view_id = run_id
    app._rr_events = []
    app._rr_last_seq = 0
    app._rr_run = None
    app._rr_poll_stop = False
    app._rr_log_col = None        # stable log ListView, (re)built in screen()
    app._rr_last_status = None    # drives full-render-only-on-status-change
    app.render()
    _start_poll(app, run_id)


def _close_run(app):
    app._rr_poll_stop = True
    app._rr_view_id = None
    app.goto("remote_runs") if hasattr(app, "goto") else app.render()


def _start_poll(app, run_id):
    def work():
        # Bounded to ~45 min of polling so a forgotten-open tab/screen can't
        # poll forever — fails open (just stops refreshing; the row itself
        # is unaffected). Matches the same wall-clock-cap shape as
        # _poll_remote_run_done in main.py.
        for _ in range(1080):   # 1080 * 2.5s = 45 min
            if getattr(app, "_rr_poll_stop", True) or getattr(app, "_rr_view_id", None) != run_id:
                return
            row = auth.get_remote_run(run_id)
            if row is not None:
                app._rr_run = row
            # Fetch ALL events (after_seq=0), not just new seqs: the worker now
            # UPDATES an existing line's row in place (a test case's whole
            # "generating… → still generating Ns → done" lifecycle reuses one
            # seq — see run_worker.py's hb_id collapse), so an incremental
            # after_seq poll would never see those updates. Collapse keeps the
            # row count low, so re-fetching all each tick is cheap.
            evs = auth.get_remote_run_events(run_id, after_seq=0)
            if evs is not None:
                app._rr_events = evs
            _status = (row or {}).get("status")
            if getattr(app, "_rr_view_id", None) == run_id:
                if _status != getattr(app, "_rr_last_status", None):
                    # Status changed (queued→running→terminal / pause) — the
                    # meta card + control buttons change, so full render once.
                    app._rr_last_status = _status
                    app.ui_safe(app.render)
                else:
                    # Events-only tick: update the log list IN PLACE so the
                    # screen doesn't rebuild and jump the scroll to the top
                    # every 2.5s (reported live). The ListView auto-follows
                    # the tail.
                    app.ui_safe(lambda: (_refresh_meta(app), _refresh_log(app)))
            if row is not None and row.get("status") in _TERMINAL:
                # SETTLE PASS: don't stop polling the instant the status turns
                # terminal. The worker writes the final status LAST (after its
                # closing _flush_events + the "Report emailed…" line), but the
                # row and the events are two separate reads here — so the tick
                # that first SEES terminal can easily have fetched its events a
                # moment before that final flush landed. Stopping there froze
                # the feed one flush short of complete: the run showed
                # "5 updated" while the log still ended mid-generation.
                # One more fetch a few seconds later closes that window.
                time.sleep(4.0)
                if getattr(app, "_rr_poll_stop", True) or \
                        getattr(app, "_rr_view_id", None) != run_id:
                    return
                try:
                    _final = auth.get_remote_run_events(run_id, after_seq=0)
                    if _final is not None:
                        app._rr_events = _final
                    _frow = auth.get_remote_run(run_id)
                    if _frow is not None:
                        app._rr_run = _frow
                    if getattr(app, "_rr_view_id", None) == run_id:
                        app.ui_safe(app.render)
                except Exception:
                    pass
                return
            time.sleep(2.5)
    try:
        app._bg(work)
    except Exception:
        threading.Thread(target=work, daemon=True).start()


def _log_lines_for(app):
    lines = [ln for ln in (_ln_from_event(ev) for ev in getattr(app, "_rr_events", []) or [])
             if ln and ln.get("msg")]
    # REPLICATES THE DESKTOP RULE: "Only spin while the run is actually active —
    # once it stops/finishes, don't leave a perpetual spinner"
    # (main._build_story_cards). A line spins purely because its payload carries
    # wip=True (see main._log_icon), and the remote viewer had no equivalent
    # gate: once the run reached a terminal state, any line whose LAST persisted
    # payload was an in-progress one spun forever. Reported live — the run
    # finished "5 updated · 0 failed" while two cases sat spinning at
    # "generating…".
    #
    # A terminal run cannot have work in flight, so drop wip on every line. This
    # is display-only; the stored events are untouched.
    try:
        if (getattr(app, "_rr_run", None) or {}).get("status") in _TERMINAL:
            for ln in lines:
                ln.pop("wip", None)
    except Exception:
        pass
    return lines


def _refresh_log(app):
    """Update ONLY the activity log list in place — no full screen render, so
    the scroll position isn't reset on every 2.5s poll. Falls back to a full
    render if the stable log control isn't mounted."""
    col = getattr(app, "_rr_log_col", None)
    if col is None:
        try:
            app.render()
        except Exception:
            pass
        return
    try:
        col.controls = [app._render_one_log(ln) for ln in _log_lines_for(app)]
        col.update()
    except Exception:
        try:
            app.render()
        except Exception:
            pass


def _control(app, run_id, action, label):
    def go():
        ok, msg = auth.set_remote_run_control(run_id, action)
        if ok:
            app.ui_safe(lambda: app._toast(f"{label} sent."))
        else:
            app.ui_safe(lambda: app._err(f"Couldn't {label.lower()}: {msg}"))
    app._bg(go)


def _stop_confirm(app, run_id):
    app._confirm(
        "Stop this run?",
        "The worker finishes whatever test case is in flight, then ends the "
        "run as “stopped”. This can't be undone.",
        on_yes=lambda: _control(app, run_id, "stop", "Stop"),
        yes_label="Stop run", danger=True, icon=ft.Icons.STOP_CIRCLE_OUTLINED)


def _meta_row(label, value, _cell=None):
    """One label/value row. Pass _cell=(dict, key) to stash the VALUE Text so a
    caller can refresh it in place later without a full re-render (used for the
    relative timestamps — see _refresh_meta)."""
    val = ft.Text(str(value), size=12.5, color=T.INK, weight=ft.FontWeight.W_500,
                  expand=True, selectable=True)
    if _cell:
        try:
            _cell[0][_cell[1]] = val
        except Exception:
            pass
    return ft.Row([
        ft.Text(label, size=11.5, color=T.INK_3, weight=ft.FontWeight.W_600, width=90),
        val,
    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)


def _refresh_meta(app):
    """Re-compute the relative timestamps in place, every poll tick. Without
    this they froze at whatever they said during the last FULL render (which
    only happens on a status change), so a running job sat on 'just now' for
    minutes."""
    cells = getattr(app, "_rr_meta_cells", None)
    run = getattr(app, "_rr_run", None)
    if not cells or not run:
        return
    vals = {
        "created": _relative(run.get("created_at")),
        "started": (_relative(run.get("started_at")) if run.get("started_at") else "—"),
        "finished": (_relative(run.get("finished_at")) if run.get("finished_at") else "—"),
    }
    for key, txt in vals.items():
        c = cells.get(key)
        if c is None or c.value == txt:
            continue
        try:
            c.value = txt
            c.update()
        except Exception:
            pass   # not mounted (screen changed) — next full render fixes it


def _ln_from_event(ev):
    kind = ev.get("kind")
    payload = ev.get("payload") or {}
    if kind == "story":
        return {"tone": "story", "ico": "▸",
               "msg": f"Story {payload.get('id')} · {payload.get('title')}", "ar": True}
    if kind == "log":
        return dict(payload)
    return None   # stat/progress/story_progress/done are UI-state events,
                  # not log lines — the desktop's own cb() doesn't append
                  # them to _log_lines either (see main.py's cb(ev, payload)).


def _detail_screen(app):
    run_id = app._rr_view_id
    run = getattr(app, "_rr_run", None) or {}
    status = run.get("status") or "queued"
    can_control = app.can(auth.CAP_RUN) if hasattr(app, "can") else True

    back = ghost_btn("All runs", icon=ft.Icons.ARROW_BACK,
                     on_click=lambda e: _close_run(app))

    story_ids = run.get("story_ids") or []
    meta = card(ft.Column([
        ft.Row([
            ft.Text(_kind_label(run.get("kind")), size=15, weight=ft.FontWeight.BOLD,
                   color=T.INK),
            _status_badge(status),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=10),
        _meta_row("Project", run.get("project") or "—"),
        _meta_row("Plan", f"#{run.get('plan_id')}" if run.get("plan_id") else "—"),
        _meta_row("Stories", ", ".join(str(s) for s in story_ids) or "—"),
        # Keep handles on the three relative-time values. The poll loop only
        # does a FULL render when `status` CHANGES; every other tick updates the
        # log list in place (to avoid resetting scroll every 2.5s). These texts
        # were therefore baked at the last status change — queued→running
        # happens seconds after creation, so they rendered "just now" and then
        # never moved. At 3-4 minutes in they still read "just now" (reported
        # live). _refresh_meta() now re-computes them on every tick.
        _meta_row("Created", _relative(run.get("created_at")),
                  _cell=(app.__dict__.setdefault("_rr_meta_cells", {}), "created")),
        _meta_row("Started", _relative(run.get("started_at"))
                  if run.get("started_at") else "—",
                  _cell=(app._rr_meta_cells, "started")),
        _meta_row("Finished", _relative(run.get("finished_at"))
                  if run.get("finished_at") else "—",
                  _cell=(app._rr_meta_cells, "finished")),
        (ft.Container(
            ft.Column([ft.Container(height=8), _meta_row("Summary", run.get("summary"))]))
         if run.get("summary") else ft.Container(height=0)),
    ], spacing=6))

    # On a phone the buttons must SHARE the row evenly. Left to their natural
    # widths inside a wrap Row, the primary action (Resume/Pause) stretched to
    # the full width and pushed Stop onto its own line at a much smaller size —
    # reported live: the Resume button needed adjustment because the pair looked
    # mismatched and misaligned. expand=True on mobile splits the row into equal
    # halves (or thirds); desktop keeps the natural-width wrap layout.
    _m_ctl = platform_caps.is_mobile()
    controls = []
    if can_control and status not in _TERMINAL:
        if status in ("queued", "running"):
            controls.append(ghost_btn("Pause", icon=ft.Icons.PAUSE_CIRCLE_OUTLINE,
                                      expand=_m_ctl,
                                      on_click=lambda e: _control(app, run_id, "pause", "Pause")))
        if status == "paused":
            controls.append(green_btn("Resume", icon=ft.Icons.PLAY_CIRCLE_OUTLINE,
                                      expand=_m_ctl,
                                      on_click=lambda e: _control(app, run_id, "resume", "Resume")))
        controls.append(danger_btn("Stop", icon=ft.Icons.STOP_CIRCLE_OUTLINED,
                                   expand=_m_ctl,
                                   on_click=lambda e: _stop_confirm(app, run_id)))
    controls_row = (ft.Row(controls, spacing=10, wrap=not _m_ctl)
                    if controls else ft.Container(height=0))

    # Live badge: only meaningful while the run hasn't reached a terminal
    # state — a finished run's log is just history, no more polling happens.
    live_chip = (ft.Row([
        ft.Container(width=7, height=7, bgcolor=T.VIOLET, border_radius=4),
        ft.Text("Live — updates every few seconds", size=11, color=T.INK_3,
               weight=ft.FontWeight.W_600),
    ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                 if status not in _TERMINAL else ft.Container(height=0))

    log_lines = _log_lines_for(app)
    log_ctls = [app._render_one_log(ln) for ln in log_lines]
    # Stable ListView held on the app so the poll loop can update its
    # .controls in place (see _refresh_log) instead of a full render that
    # resets scroll. auto_scroll follows the tail as new lines land; height
    # is bounded so it's outside flet#6087's expand+scroll trap.
    if getattr(app, "_rr_log_col", None) is None:
        app._rr_log_col = ft.ListView(spacing=0, height=360, auto_scroll=True)
    app._rr_log_col.controls = log_ctls
    log_card = card(ft.Column([
        ft.Row([
            ft.Text("ACTIVITY", size=11, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK),
            ft.Container(expand=True), live_chip,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=8),
        (app._rr_log_col if log_ctls else
         ft.Text("No activity yet — the worker hasn't started, or the "
                "GitHub Actions dispatch is still queuing.", size=12,
                color=T.INK_3, weight=ft.FontWeight.W_500)),
    ], spacing=0))

    body = ft.Column([
        back, ft.Container(height=10), meta,
        (ft.Container(controls_row, padding=ft.Padding.only(top=10))
         if controls else ft.Container(height=0)),
        ft.Container(height=14), log_card,
    ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    return app.shell("Remote Runs", _kind_label(run.get("kind")) + " run", body)
