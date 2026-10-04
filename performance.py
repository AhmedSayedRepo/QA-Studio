"""performance.py - Performance testing screen.

Extract load-test scenarios from existing test cases, emit them to JMeter, and
run locally. Follows the app's screen conventions exactly: app.shell(...) chrome,
card() panels, sec_head() sections, field_label(..., info=...) info icons, and
automation.py's ACTIVITY log-rail. ALL heavy lifting is in the unit-tested perf/
package (perf.service); this file is glue + widgets.

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
import json
import strings
import os
import re
import tempfile
import threading
import time
import traceback

import flet as ft
import theme as T
import regression
import image_assets
from ui import (card, danger_btn, primary_btn, green_btn, ghost_btn, field_label,
                sec_head)

from perf import service
from perf import har as har_import
from perf import curl as curl_import
from perf import report as perf_report
from perf import token_prefetch
from perf.models import DataSource, LoadProfile, WORKLOAD_PRESETS
from perf.ports import PerfRunCancelled

# An empty, fillable skeleton (NOT a fake journey) - shown in the Paste-JSON box
# so the user sees the exact shape to fill in. Use {{variable}} placeholders that
# a Data CSV can fill; id/story_id are optional.
CASES_SKELETON = [{
    "title": "",
    "steps": [
        {"action": "", "expected": ""},
    ],
}]

_PERF_HISTORY_LIMIT = 20


def _get(app, key, default):
    return getattr(app, key, default)


def _log_widget(msg, tone):
    """One activity-log line: a tone icon + coloured text, matching the Run and
    Automation logs (see main._auto_log_line) so a non-technical reader can scan
    outcomes at a glance — green check = good, red x = failed, ⚠ = heads-up."""
    color = {"ok": T.GREEN, "err": T.RED, "warn": T.AMBER,
             "info": T.VIOLET_INK}.get(tone, T.INK_2)
    if tone == "ok":
        sym = ft.Icon(ft.Icons.CHECK, size=13, color=T.GREEN)
    elif tone == "err":
        sym = ft.Icon(ft.Icons.CLOSE, size=13, color=T.RED)
    elif tone == "warn":
        sym = ft.Text("⚠", size=12, color=T.AMBER, weight=ft.FontWeight.BOLD)
    elif tone == "info":
        sym = ft.Icon(ft.Icons.PLAY_ARROW, size=13, color=T.VIOLET_INK)
    else:  # dim / unknown → a small chevron, like the Run/Automation logs
        sym = ft.Text("›", size=14, color=T.INK_3, weight=ft.FontWeight.BOLD)
    weight = ft.FontWeight.BOLD if tone == "ok" else ft.FontWeight.W_500
    # Top-align the glyph so it sits right beside the FIRST line of a wrapped
    # message (not floating at the vertical middle of a two-line entry).
    return ft.Container(
        ft.Row([
            ft.Container(sym, width=15, alignment=ft.Alignment.TOP_LEFT,
                         padding=ft.Padding.only(top=1)),
            ft.Text(str(msg), size=12, color=color, weight=weight, selectable=True,
                    expand=True),
        ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
        padding=ft.Padding.only(top=1, bottom=1))


def _empty_activity():
    """Helpful empty state for the activity rail before the first action."""
    return ft.Container(
        ft.Column([
            ft.Container(
                ft.Icon(ft.Icons.TERMINAL, size=20, color=T.VIOLET_INK),
                width=42, height=42, bgcolor=T.VIOLET_SOFT,
                border_radius=12, alignment=ft.Alignment.CENTER),
            ft.Text(strings.t("perf_empty_activity_title"), size=12.5,
                    weight=ft.FontWeight.BOLD, color=T.INK),
            ft.Text(strings.t("perf_empty_activity_body"), size=11.5,
                    color=T.INK_3, text_align=ft.TextAlign.CENTER),
        ], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
           tight=True),
        alignment=ft.Alignment.CENTER, expand=True,
        padding=ft.Padding.symmetric(horizontal=18, vertical=24))


def _logline(app, msg, tone="dim"):
    log = getattr(app, "_perf_log", None)
    if log is None:
        app._perf_log = log = []
    log.append({"msg": msg, "tone": tone})
    col = getattr(app, "_perf_log_col", None)
    if col is not None:
        def _do():
            if getattr(app, "_perf_log_empty", False):
                col.controls.clear()
                app._perf_log_empty = False
            col.controls.append(_log_widget(msg, tone))
            try:
                col.update()
            except Exception:
                pass
        try:
            app.ui_safe(_do)
        except Exception:
            _do()


def _refresh_performance(app):
    """Refresh only the mounted Performance panels.

    Flet 0.85 resets a newly-created scroll control to the top and its
    ``scroll_to`` coroutine cannot reliably restore it.  The Performance screen
    therefore installs an in-place refresher after its first mount.  The
    preserve-rail render is only a defensive fallback for tests or an unusually
    early callback before the screen has mounted.
    """
    # A worker may finish after the user has navigated elsewhere. Store its
    # state/logs, but do not repaint an unmounted screen or disturb the new one.
    if getattr(app, "active", "performance") != "performance":
        return
    refresh = getattr(app, "_perf_refresh_mounted", None)
    if callable(refresh):
        try:
            refresh()
            return
        except Exception:
            pass
    try:
        app.render(preserve_rail=True)
    except TypeError:
        app.render()


def _queue_performance_refresh(app):
    """Run the mounted refresh on Flet's UI thread from a worker callback."""
    try:
        app.ui_safe(lambda: _refresh_performance(app))
    except Exception:
        _refresh_performance(app)


def _replace_mounted_children(mounted, fresh):
    """Replace dynamic panel rows without removing the shell's header gap.

    ``App.shell()`` inserts a first child into the primary scrolling column so
    content starts below its fixed glass header.  Performance refreshes its
    panels in place while a test runs; replacing ``mounted.controls`` blindly
    used to discard that shell-owned child.  The next activity entry then made
    the top of the left panel appear underneath the header, even at scroll 0.
    """
    rows = list(getattr(fresh, "controls", []) or [])
    if getattr(mounted, "_qa_gap", False):
        existing = list(getattr(mounted, "controls", []) or [])
        if existing:
            rows.insert(0, existing[0])
    mounted.controls = rows


def _replace_mounted_content(mounted, fresh):
    """Replace a static panel without discarding the shell-owned top gap.

    The right-hand activity rail is mounted in a ``Container`` rather than a
    scrolling ``Column``.  ``App.shell()`` therefore wraps it in a column that
    begins with the fixed-header spacer.  Refreshing ``mounted.content``
    directly used to replace that wrapper, putting the activity toolbar behind
    the fixed header after the first live log update.
    """
    wrapper = getattr(mounted, "content", None)
    if getattr(mounted, "_qa_gap", False):
        rows = list(getattr(wrapper, "controls", []) or [])
        if rows:
            # Keep the shell-installed spacer and replace only the rail card.
            wrapper.controls = [rows[0], fresh]
            return
    mounted.content = fresh


def _log_action_error(app, action, ex):
    """Show one readable error while retaining the traceback in diagnostics."""
    try:
        regression._perf_log(
            f"Performance {action} failed: {ex}\n{traceback.format_exc()}")
    except Exception:
        pass
    _logline(app, strings.t("perf_log_action_failed", reason=str(ex)[:180]), "err")


def _ai(app):
    if _get(app, "_perf_extractor", "heuristic") != "ai":
        return None
    try:
        import engine
        _logline(app, "Using engine.ai_complete for extraction.", "ok")
        return lambda prompt: engine.ai_complete(prompt, tag="perf")
    except Exception as ex:
        _logline(app, f"AI unavailable ({ex}); using heuristic.", "warn")
        return None


def _profile(app):
    def _f(key, d):
        try:
            return float(_get(app, key, d))
        except (ValueError, TypeError):
            return d

    def _i(key, d):
        try:
            return int(float(_get(app, key, d)))
        except (ValueError, TypeError):
            return d

    thr = {}
    if _f("_perf_p95", 800) > 0:
        thr["p95_ms"] = _f("_perf_p95", 800)
    if _f("_perf_err", 1) >= 0:
        thr["error_rate"] = _f("_perf_err", 1) / 100.0
    if _f("_perf_p99", 0) > 0:                       # optional
        thr["p99_ms"] = _f("_perf_p99", 0)
    if _f("_perf_min_rps", 0) > 0:                   # optional min throughput
        thr["min_throughput_rps"] = _f("_perf_min_rps", 0)
    return LoadProfile(users=_i("_perf_users", 20), ramp_up_s=_i("_perf_ramp", 15),
                       duration_s=_i("_perf_duration", 60),
                       pacing_ms=max(0, _i("_perf_pacing", 0)), thresholds=thr)


def _apply_workload_preset(app, name):
    """Apply one standard profile and invalidate a plan emitted with old values."""
    name = str(name or "custom").lower()
    app._perf_preset = name if name in WORKLOAD_PRESETS else "custom"
    values = WORKLOAD_PRESETS.get(name)
    if values:
        app._perf_users = str(values["users"])
        app._perf_ramp = str(values["ramp_up_s"])
        app._perf_duration = str(values["duration_s"])
        app._perf_pacing = str(values["pacing_ms"])
        app._perf_paths = None
        app._perf_can_run = False


def _ensure_perf_history(app):
    """Load per-user history/baseline once from the encrypted local store."""
    if not hasattr(app, "_perf_history"):
        creds = getattr(app, "creds", None)
        raw = creds.get("perf_history", []) if isinstance(creds, dict) else []
        app._perf_history = [dict(item) for item in raw
                             if isinstance(item, dict)][:_PERF_HISTORY_LIMIT]
    if not hasattr(app, "_perf_baseline"):
        creds = getattr(app, "creds", None)
        raw = creds.get("perf_baseline") if isinstance(creds, dict) else None
        app._perf_baseline = dict(raw) if isinstance(raw, dict) else None


def _persist_perf_history(app):
    """Persist non-secret metric summaries inside the current user's vault."""
    try:
        import store
        creds = getattr(app, "creds", None)
        if not isinstance(creds, dict):
            return
        creds["perf_history"] = list(_get(app, "_perf_history", []) or [])[:_PERF_HISTORY_LIMIT]
        baseline = _get(app, "_perf_baseline", None)
        if baseline:
            creds["perf_baseline"] = dict(baseline)
        else:
            creds.pop("perf_baseline", None)
        store.save(creds)
    except Exception:
        pass


def _data(app):
    p = (_get(app, "_perf_data_path", "") or "").strip()
    if not p or not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        header = [h.strip() for h in f.readline().strip().split(",") if h.strip()]
    return DataSource(csv_path=p, columns=header)


def _parse_param_rules(text):
    """One rule per line: 'literalFromCapture => variableName'. Returns
    [(literal, '{{variableName}}'), …]."""
    rules = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "=>" not in line:
            continue
        find, name = line.split("=>", 1)
        find = find.strip()
        name = name.strip().strip("{} ").strip()
        if find and name:
            rules.append((find, "{{" + name + "}}"))
    return rules


def _parse_corr_rules(text):
    """One rule per line:
        var = $.json.path @ /url-part      (JSON extractor)
        var ~ regex(with one group) @ /url-part   (regex extractor)
    The '@ /url-part' picks which request's response to read from (optional)."""
    rules = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = ""
        if "@" in line:
            line, match = line.rsplit("@", 1)
            match, line = match.strip(), line.strip()
        if "~" in line:
            var, rx = line.split("~", 1)
            if var.strip() and rx.strip():
                rules.append({"var": var.strip(), "regex": rx.strip(), "match": match})
        elif "=" in line:
            var, jp = line.split("=", 1)
            if var.strip() and jp.strip():
                rules.append({"var": var.strip(), "json_path": jp.strip(), "match": match})
    return rules


def _apply_advanced(app, scenarios):
    """Apply the optional in-test login / Auth header, parameterize (literals ->
    {{vars}}) and correlation (extract-from-response) transforms — shared by HAR + cURL."""
    login_curl = (_get(app, "_perf_login_curl", "") or "").strip()
    if login_curl:
        # In-test login: each user logs in at run time and uses its own fresh token.
        lscs = curl_import.scenarios_from_curl(login_curl)
        login_req = (lscs[0].requests[0] if lscs and lscs[0].requests else None)
        if login_req is not None:
            tvar = (_get(app, "_perf_login_var", "") or "token").strip() or "token"
            tpath = (_get(app, "_perf_login_tokenpath", "") or "access_token").strip()
            scenarios = service.with_login(scenarios, login_req,
                                           token_var=tvar, token_json_path=tpath)
            _logline(app, f"In-test login prepended — token extracted into {{{{{tvar}}}}} "
                          "and sent as Authorization on every request.", "ok")
        else:
            _logline(app, "Login cURL couldn't be parsed; skipping the login step.", "warn")
    else:
        auth = (_get(app, "_perf_auth", "") or "").strip()
        if auth:
            scenarios = service.with_auth(scenarios, auth)
            # Log only the scheme (e.g. 'Bearer'), never the token bytes.
            _scheme = auth.split(" ", 1)[0] if " " in auth else "custom"
            _logline(app, f"Applied Authorization header ({_scheme} …) to every request.", "ok")
    prules = _parse_param_rules(_get(app, "_perf_param", ""))
    if prules:
        scenarios = service.parameterize(scenarios, prules)
        _logline(app, f"Parameterized {len(prules)} value(s) into {{variables}}.", "ok")
    crules = _parse_corr_rules(_get(app, "_perf_corr", ""))
    if crules:
        scenarios = service.correlate(scenarios, crules)
        _logline(app, f"Added {len(crules)} correlation rule(s) "
                      "(extract from a response, reuse later).", "ok")
    return scenarios


_MAX_HAR_BYTES = 64 * 1024 * 1024    # keep in sync with perf/har.py
_MAX_CSV_BYTES = 25 * 1024 * 1024    # users CSV for load testing is normally tiny


def _oversize(path, cap):
    """True if `path` exceeds `cap` bytes (used to reject pathologically large
    inputs before parsing them into memory)."""
    try:
        return os.path.getsize(path) > cap
    except OSError:
        return False


def _build_current_scenarios(app):
    """Build PerfScenarios from the current source (HAR or cURL — both give exact
    requests), then apply the auth / parameterize / correlation transforms. Returns
    a list, or None on a guard failure. Shared by 'Add to plan' and 'Generate & Emit'."""
    src = _get(app, "_perf_source", "har")
    if src == "curl":
        text = _get(app, "_perf_curl", "") or ""
        _logline(app, "Parsing cURL command(s)...")
        scenarios = curl_import.scenarios_from_curl(text)
        if not scenarios or not scenarios[0].requests:
            _logline(app, "No cURL command found. Paste one or more 'curl …' lines "
                          "(e.g. DevTools → Copy as cURL).", "warn")
            return None
        n = sum(len(s.requests) for s in scenarios)
        _logline(app, f"Parsed {n} request(s) from cURL.", "ok")
        return _apply_advanced(app, scenarios)

    # default: HAR
    har_path = (_get(app, "_perf_har_path", "") or "").strip()
    if not har_path or not os.path.exists(har_path):
        _logline(app, "Pick a .har file first (Browse HAR…).", "warn")
        return None
    if _oversize(har_path, _MAX_HAR_BYTES):
        _logline(app, "That HAR is too large (max %d MB) — capture a shorter "
                      "session or filter it down." % (_MAX_HAR_BYTES // (1024 * 1024)), "err")
        return None
    domains = [d for d in re.split(r"[,\s]+",
               _get(app, "_perf_har_domains", "") or "") if d]
    _logline(app, (strings.t("perf_log_reading_har_filtered",
                             domains=", ".join(domains)) if domains else
                   strings.t("perf_log_reading_har")), "info")
    scenarios = har_import.scenarios_from_har(har_path, include_domains=domains)
    if not scenarios or not scenarios[0].requests:
        _logline(app, "No matching requests in the HAR. Clear the domain filter "
                      "or check you saved the right capture.", "warn")
        return None
    n = sum(len(s.requests) for s in scenarios)
    _logline(app, strings.t("perf_log_imported_requests", count=n), "ok")
    return _apply_advanced(app, scenarios)


def _add_worker(app):
    """Build scenarios from the current source and APPEND them to the plan basket
    (accumulate across HARs / test cases / JSON) instead of replacing."""
    try:
        scenarios = _build_current_scenarios(app)
        if not scenarios:
            return
        basket = list(_get(app, "_perf_basket", []) or [])
        basket.extend(scenarios)
        app._perf_basket = basket
        # A freshly-changed basket invalidates any previously-emitted plan.
        app._perf_paths = None
        app._perf_can_run = False
        total = sum(s.request_count for s in basket)
        _logline(app, f"Added {len(scenarios)} scenario(s). Plan now holds "
                      f"{len(basket)} scenario(s), {total} request(s). Click "
                      "“Generate & Emit” when ready.", "ok")
    except Exception as ex:
        _log_action_error(app, "add scenario", ex)
    finally:
        app._perf_adding = False
        _queue_performance_refresh(app)


def _emit_worker(app):
    try:
        custom = (_get(app, "_perf_out_dir", "") or "").strip()
        out = custom or os.path.join(tempfile.gettempdir(), "qastudio_perf")
        try:
            os.makedirs(out, exist_ok=True)
        except Exception as ex:
            _logline(app, f"Can't use that output folder ({ex}); using a temp folder.", "warn")
            out = os.path.join(tempfile.gettempdir(), "qastudio_perf")
            os.makedirs(out, exist_ok=True)
        app._perf_out = out
        _persist_out_dir(app)          # remember a typed folder too
        _logline(app, strings.t("perf_log_workspace_ready"), "info")

        # Prefer the accumulated basket; otherwise build from the current source
        # (keeps the simple single-source workflow working without Add-to-plan).
        basket = list(_get(app, "_perf_basket", []) or [])
        if basket:
            scenarios = basket
            _logline(app, strings.t("perf_log_combining_scenarios",
                                    count=len(scenarios)), "info")
        else:
            scenarios = _build_current_scenarios(app)
            if not scenarios:
                return

        target, paths = service.emit_scenarios(
            scenarios, _profile(app), out, target_name="jmeter", data=_data(app))
        for s in scenarios:
            _logline(app, strings.t("perf_log_scenario_ready",
                                    name=(s.title or s.id),
                                    requests=s.request_count), "ok")
        _logline(app, strings.t("perf_log_plan_created"), "ok")
        if paths.data_csv:
            _logline(app, f"Data CSV copied to the project (includes any secrets it "
                          f"holds): {paths.data_csv}", "warn")
        app._perf_paths = paths
        app._perf_target = target
        app._perf_profile = _profile(app)
        ok, msg = target.preflight()
        app._perf_can_run = ok
        _logline(app, strings.t("perf_log_tools_ready") if ok else
                 strings.t("perf_log_tools_not_ready", reason=msg),
                 "ok" if ok else "warn")
        if ok:
            _logline(app, strings.t("perf_log_ready_to_run"), "info")
        else:
            _logline(app, strings.t("perf_log_install_tools"), "warn")
    except Exception as ex:
        _log_action_error(app, "generate plan", ex)
    finally:
        app._perf_running = False
        _queue_performance_refresh(app)


# JMeter console noise we drop, and the bits we keep + colour, so the Activity
# log reads like a run summary instead of raw stdout.
_JM_NOISE = ("scanning to locate", "to view the results", "createdb", "creating summariser",
             "waiting for possible shutdown", "starting standalone test", "tidying up",
             "will be removed in a future release", "created the tree successfully",
             "running jmeter (non-gui)")

_JM_SUMMARY_RE = re.compile(
    r"^summary\s+([+=])\s+(\d+)\s+in\s+([0-9:]+)\s+=\s+([0-9.]+)/s\s+"
    r"Avg:\s*(\d+)\s+Min:\s*(\d+)\s+Max:\s*(\d+)\s+"
    r"Err:\s*(\d+)\s+\(([0-9.]+)%\)"
    r"(?:\s+Active:\s*(\d+)\s+Started:\s*(\d+)\s+Finished:\s*(\d+))?",
    re.IGNORECASE)


def _friendly_duration(value):
    """Turn JMeter's HH:MM:SS duration into a short human-readable value."""
    try:
        hours, minutes, seconds = [int(part) for part in str(value).split(":")]
    except (TypeError, ValueError):
        return str(value)
    parts = []
    if hours:
        parts.append(strings.t("perf_log_hours", count=hours))
    if minutes:
        parts.append(strings.t("perf_log_minutes", count=minutes))
    if seconds or not parts:
        parts.append(strings.t("perf_log_seconds", count=seconds))
    return " ".join(parts)


def _friendly_jmeter_line(line):
    """Translate one JMeter console line into user-facing progress.

    Returns ``(message, tone)`` or ``None`` for implementation noise. Unknown
    error lines remain visible so troubleshooting evidence is never hidden.
    """
    text = str(line or "").strip()
    low = text.lower()
    if not text or any(s in low for s in _JM_NOISE):
        return None
    match = _JM_SUMMARY_RE.match(text)
    if match:
        (kind, requests, duration, rps, avg_ms, _min_ms, _max_ms, errors,
         error_pct, active, _started, _finished) = match.groups()
        values = dict(
            requests=f"{int(requests):,}",
            duration=_friendly_duration(duration),
            rps=rps,
            avg_ms=avg_ms,
            errors=f"{int(errors):,}",
            error_pct=error_pct,
            active=(f"{int(active):,}" if active is not None else "0"),
        )
        if kind == "+":
            return strings.t("perf_log_recent_progress", **values), (
                "warn" if int(errors) else "dim")
        return strings.t("perf_log_overall_progress", **values), (
            "warn" if int(errors) else "ok")
    if "error" in low or "exception" in low or "not found" in low:
        return strings.t("perf_log_technical_error", detail=text), "err"
    return strings.t("perf_log_status_detail", detail=text), "dim"


def _jm_tone(line):
    """Compatibility helper retained for callers that only need the tone."""
    event = _friendly_jmeter_line(line)
    return event[1] if event else None


def _run_worker(app):
    try:
        _logline(app, strings.t("perf_log_run_started"), "info")

        def _on(ev):
            msg = str(ev.get("msg", "")).strip()
            if not msg:
                return
            friendly = _friendly_jmeter_line(msg)
            if friendly is not None:
                _logline(app, friendly[0], friendly[1])

        hosts = (_get(app, "_perf_remote_hosts", "") or "").strip()
        cancel_event = _get(app, "_perf_cancel_event", None)
        res = service.run(app._perf_target, app._perf_paths, app._perf_profile,
                          on_event=_on, remote_hosts=hosts,
                          cancel_check=(cancel_event.is_set if cancel_event else lambda: False))
        app._perf_result = res
        gate = {True: "PASS", False: "FAIL", None: "done"}[res.threshold_pass]
        _logline(app, strings.t("perf_log_run_finished", result=gate,
                                p95=f"{res.p95_ms:.0f}",
                                errors=f"{res.error_rate * 100:.2f}",
                                rps=f"{res.throughput_rps:.1f}"),
                 "ok" if res.threshold_pass else "warn")
        # Keep a per-user local history for trend and baseline comparison.
        hist = list(_get(app, "_perf_history", []) or [])
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        profile = _get(app, "_perf_profile", None) or _profile(app)
        hist.insert(0, {"id": time.strftime("%Y%m%d-%H%M%S"), "when": stamp, "gate": gate,
                        "p95": res.p95_ms, "err": res.error_rate * 100,
                        "rps": res.throughput_rps,
                        "users": profile.users, "duration": profile.duration_s,
                        "report": getattr(res, "raw_report_dir", "")})
        if getattr(res, "failure_analysis", ()):
            try:
                from failure_analysis.integration import optional_analysis_fields
                hist[0].update(optional_analysis_fields(res.failure_analysis))
            except Exception:
                # Diagnostic serialization must never prevent history persistence.
                pass
        app._perf_history = hist[:_PERF_HISTORY_LIMIT]
        _persist_perf_history(app)
    except PerfRunCancelled:
        _logline(app, strings.t("perf_cancelled"), "warn")
    except Exception as ex:
        _log_action_error(app, "run", ex)
    finally:
        app._perf_running = False
        app._perf_cancelling = False
        app._perf_cancel_event = None
        _queue_performance_refresh(app)


def _preview_worker(app):
    """Build scenarios from the current source and list the parsed requests, WITHOUT
    emitting — a sanity check before Generate & Emit."""
    try:
        scenarios = _build_current_scenarios(app)
        if not scenarios:
            return
        total = sum(s.request_count for s in scenarios)
        _logline(app, f"Preview — {len(scenarios)} scenario(s), {total} request(s):", "info")
        for s in scenarios:
            for r in s.requests:
                if getattr(r, "is_wait", False):
                    continue
                _logline(app, f"  {r.method} {r.url}", "dim")
        _logline(app, "Preview only — nothing emitted. Click Generate & Emit to build the "
                      "plan.", "ok")
    except Exception as ex:
        _log_action_error(app, "preview", ex)
    finally:
        app._perf_previewing = False
        _queue_performance_refresh(app)


def _report_meta(app):
    # Inline the logo as a base64 data URI so it renders in the STANDALONE
    # exported file (E._logo_tag is a cid: reference that only resolves inside an
    # email). Degrades to no logo if the asset isn't found.
    logo = ""
    try:
        import ui as _ui
        b64 = _ui._logo_b64()
        if b64:
            logo = (f"<img src='data:image/png;base64,{b64}' width='34' height='34' "
                    f"alt='QA Studio' style='display:block;border:0;border-radius:8px' />")
    except Exception:
        logo = ""
    src = _get(app, "_perf_source", "plan")
    src_label = {"plan": strings.t("perf_src_plan"), "json": strings.t("perf_src_json"),
                 "har": strings.t("perf_src_har")}.get(src, src)
    scope = (_get(app, "_perf_base_url", "") or "").strip()
    if not scope:
        scope = strings.t("perf_scope_load_test", src=src_label)
    return {"title": strings.t("perf_report_title"), "scope": scope,
            "source": src_label, "base_url": _get(app, "_perf_base_url", ""),
            "target": "JMeter", "logo_html": logo}


def _report_html(app, res):
    return perf_report.render_html(
        res, _get(app, "_perf_profile", None) or _profile(app), _report_meta(app))


def _tokens_worker(app):
    """Log every user in the chosen CSV in, collect a per-user bearer token, write
    a new CSV with a `token` column, and point the load test at it."""
    import csv
    try:
        src = (_get(app, "_perf_tok_csv", "") or "").strip()
        if not src or not os.path.exists(src):
            _logline(app, "Pick a users CSV first (Browse…).", "warn")
            return
        url = (_get(app, "_perf_tok_url", "") or "").strip()
        if not url:
            _logline(app, "Enter your login URL (e.g. https://app/api/login).", "warn")
            return
        if not url.lower().startswith("https://"):
            _logline(app, "⚠ Login URL is not HTTPS — passwords would be sent in "
                          "plaintext. Use an https:// endpoint.", "warn")
        if _oversize(src, _MAX_CSV_BYTES):
            _logline(app, "That CSV is too large (max %d MB)."
                          % (_MAX_CSV_BYTES // (1024 * 1024)), "err")
            return
        with open(src, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            _logline(app, "That CSV is empty.", "warn")
            return
        user_col = (_get(app, "_perf_tok_usercol", "") or "email").strip()
        pass_col = (_get(app, "_perf_tok_passcol", "") or "password").strip()
        if user_col not in rows[0] or pass_col not in rows[0]:
            _logline(app, f"CSV needs '{user_col}' and '{pass_col}' columns. Found: "
                          f"{list(rows[0].keys())}", "err")
            return

        cfg = token_prefetch.LoginConfig(
            url=url, body_format=(_get(app, "_perf_tok_format", "json") or "json"),
            api_user_field=(_get(app, "_perf_tok_userfield", "") or "email").strip(),
            api_pass_field=(_get(app, "_perf_tok_passfield", "") or "password").strip(),
            token_json_path=(_get(app, "_perf_tok_jsonpath", "") or "").strip(),
            token_header=(_get(app, "_perf_tok_header", "") or "").strip())
        if not cfg.token_json_path and not cfg.token_header:
            cfg.token_json_path = "access_token"
        login_fn = token_prefetch.make_http_login(cfg)

        _logline(app, f"Logging in {len(rows)} user(s) at {url}...", "info")

        def _prog(done, total):
            if done % 50 == 0 or done == total:
                _logline(app, f"  …{done}/{total} logged in", "dim")

        out_rows, ok, failures = token_prefetch.prefetch_tokens(
            rows, login_fn, user_col, pass_col, token_col="token",
            concurrency=20, retries=2, on_progress=_prog)

        base, ext = os.path.splitext(src)
        out_path = base + "_with_tokens.csv"
        fieldnames = list(rows[0].keys())
        if "token" not in fieldnames:
            fieldnames.append("token")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(out_rows)

        _logline(app, f"Got {ok}/{len(rows)} tokens → {out_path}",
                 "ok" if ok else "err")
        for user, err in failures[:8]:
            _logline(app, f"  ✗ {user}: {err}", "warn")
        if ok == 0:
            # Every login failed → the config is probably wrong; unlock it to fix.
            app._perf_tok_editable = True
            _logline(app, "All logins failed — the detected settings are now editable. "
                          "Check the login URL, field names and token path, then retry.",
                     "warn")
        if ok:
            # Wire the load test to use it: per-user token via the Data CSV.
            app._perf_data_path = out_path
            app._perf_auth = "Bearer {{token}}"
            _logline(app, "Set Data CSV to the tokens file and Auth header to "
                          "“Bearer {{token}}”. Each virtual user now uses its own token.",
                     "ok")
    except Exception as ex:
        _log_action_error(app, "prepare tokens", ex)
    finally:
        app._perf_tok_running = False
        _queue_performance_refresh(app)


def _persist_out_dir(app):
    """Remember the chosen output folder across sessions — same mechanism the
    Automation screen uses for auto_local_path (app.creds + store.save)."""
    try:
        import store
        creds = getattr(app, "creds", None)
        if isinstance(creds, dict):
            creds["perf_out_dir"] = (_get(app, "_perf_out_dir", "") or "")
            store.save(creds)
    except Exception:
        pass


def _stat(label, value, tip, color=None):
    """A metric tile for the Last-result card: big number + short label, with a
    plain-English tooltip so a non-technical reader knows what it means. Text is
    kept to one line (no_wrap) so a narrow panel never mangles it."""
    return ft.Container(
        ft.Column([
            ft.Text(str(value), size=16, weight=ft.FontWeight.BOLD,
                    color=(color or T.INK), no_wrap=True, max_lines=1),
            ft.Text(label, size=10.5, color=T.INK_3, weight=ft.FontWeight.W_600,
                    no_wrap=True, max_lines=1),
        ], spacing=3, tight=True),
        tooltip=tip,
        bgcolor=T.CARD_2, border=ft.Border.all(1, T.BORDER), border_radius=T.R,
        padding=ft.Padding.symmetric(vertical=10, horizontal=12), expand=1)


def _result_card(r):
    """Last-result panel: a PASS/FAIL badge over two rows of metric tiles."""
    gate_txt, gate_col, gate_bg = {
        True: ("PASS", T.GREEN, T.GREEN_SOFT if hasattr(T, "GREEN_SOFT") else T.CARD_2),
        False: ("FAIL", T.RED, T.CARD_2),
        None: ("COMPLETED", T.INK_2, T.CARD_2),
    }[r.threshold_pass]
    err_col = T.RED if r.error_rate > 0 else T.GREEN
    badge = ft.Container(
        ft.Text(gate_txt, size=12, weight=ft.FontWeight.BOLD, color=gate_col),
        bgcolor=gate_bg, border=ft.Border.all(1, gate_col), border_radius=T.R_SM,
        padding=ft.Padding.symmetric(vertical=4, horizontal=12),
        tooltip=(strings.t("perf_badge_pass_tip") if r.threshold_pass
                 else strings.t("perf_badge_fail_tip") if r.threshold_pass is False
                 else strings.t("perf_badge_none_tip")))
    return card(ft.Column([
        ft.Row([sec_head("5", strings.t("perf_sec_last_result")), ft.Container(expand=True), badge],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=12),
        ft.Row([
            _stat(strings.t("perf_stat_p50"), f"{r.p50_ms:.0f} ms",
                  strings.t("perf_stat_p50_tip")),
            _stat("p90", f"{r.p90_ms:.0f} ms",
                  strings.t("perf_stat_p90_tip")),
            _stat("p95", f"{r.p95_ms:.0f} ms",
                  strings.t("perf_stat_p95_tip"), T.VIOLET),
            _stat("p99", f"{r.p99_ms:.0f} ms",
                  strings.t("perf_stat_p99_tip")),
        ], spacing=10),
        ft.Container(height=10),
        ft.Row([
            _stat(strings.t("perf_stat_throughput"), f"{r.throughput_rps:.1f}/s",
                  strings.t("perf_stat_throughput_tip")),
            _stat(strings.t("perf_stat_error_rate"), f"{r.error_rate * 100:.1f}%",
                  strings.t("perf_stat_error_rate_tip"), err_col),
            _stat(strings.t("perf_stat_samples"), f"{r.samples}",
                  strings.t("perf_stat_samples_tip")),
            _stat(strings.t("perf_stat_errors"), f"{r.errors}",
                  strings.t("perf_stat_errors_tip"), err_col),
        ], spacing=10),
    ], spacing=0))


def screen(app, _fragment=False):
    # Connect gate — same centered "A few things first" state Automation and
    # Regression show, instead of locking the nav button. Keeps the nav clickable
    # so the user can open Performance and see exactly what to do next.
    if not getattr(app, "readonly", False) and not (getattr(app, "connected", False)
                                                    and getattr(app, "project", None)):
        return regression.locked_state(
            app, strings.t("perf_title"),
            strings.t("perf_subtitle"),
            "Connect your provider on the Setup screen and pick a project. Once "
            "connected you can load test cases from your plan, paste your own, or "
            "import a HAR — then run it with JMeter.",
            icon=ft.Icons.SPEED,
            steps=[(ft.Icons.TUNE, strings.t("perf_step_connect")), (ft.Icons.CHECKLIST, strings.t("perf_step_build")),
                   (ft.Icons.PLAY_ARROW, strings.t("perf_step_run"))])

    # Restore the persisted output folder on first entry (mirrors Automation
    # restoring auto_local_path from creds on load).
    if not hasattr(app, "_perf_out_dir"):
        try:
            _c = getattr(app, "creds", None)
            app._perf_out_dir = (_c.get("perf_out_dir", "") if isinstance(_c, dict) else "") or ""
        except Exception:
            app._perf_out_dir = ""

    _ensure_perf_history(app)

    running = bool(_get(app, "_perf_running", False))
    can_run = bool(_get(app, "_perf_can_run", False))

    # Seed numeric defaults so the app's _auto_field (which reads getattr(attr))
    # shows sensible starting values instead of blanks on first open.
    for _k, _v in (("_perf_users", "20"), ("_perf_ramp", "15"), ("_perf_duration", "60"),
                   ("_perf_pacing", "0"), ("_perf_p95", "800"), ("_perf_err", "1")):
        if not str(_get(app, _k, "")).strip():
            setattr(app, _k, _v)

    curl_box = ft.TextField(
        value=_get(app, "_perf_curl", ""),
        multiline=True, min_lines=6, max_lines=14,
        hint_text=strings.t("au_pf_paste_curl"),
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=11, horizontal=12), text_size=12)
    curl_box.on_change = lambda e: setattr(app, "_perf_curl", curl_box.value)

    def _mk_multiline(attr, hint_text, lines=3):
        tf = ft.TextField(
            value=_get(app, attr, ""), multiline=True, min_lines=2, max_lines=lines,
            hint_text=hint_text, border_color=T.BORDER, focused_border_color=T.VIOLET,
            border_radius=T.R, text_size=12,
            content_padding=ft.Padding.symmetric(vertical=10, horizontal=12))
        tf.on_change = lambda e, a=attr, ff=tf: setattr(app, a, ff.value)
        return tf

    param_box = _mk_multiline("_perf_param", "SKU-123 => product")
    corr_box = _mk_multiline("_perf_corr", "cartId = $.id @ /cart")
    login_box = _mk_multiline("_perf_login_curl",
                              "curl 'https://app/api/login' -H 'Content-Type: application/json' "
                              "--data-raw '{\"email\":\"{{email}}\",\"password\":\"{{password}}\"}'")

    def _src_changed():
        # HAR and cURL have different section bodies. Rebuild only the mounted
        # Performance panels so the page and navigation keep their scroll state.
        app._perf_source = source.value
        _refresh_performance(app)

    source = ft.Dropdown(
        value=_get(app, "_perf_source", "har"), filled=True, bgcolor=T.CARD,
        border_color=T.BORDER, focused_border_color=T.VIOLET, expand=True,
        tooltip=strings.t("au_pf_source_info"),
        options=[ft.DropdownOption(key="har", text=strings.t("perf_opt_har")),
                 ft.DropdownOption(key="curl", text=strings.t("perf_opt_curl"))],
        on_select=lambda e: _src_changed())

    # A labeled dropdown that mirrors _auto_field's shape (label + ⓘ info, then field).
    def dd_field(label, info, control):
        return ft.Column([field_label(label, info=info),
                          ft.Container(control, padding=ft.Padding.only(top=4))], spacing=0)

    # log rail column
    app._perf_log = _get(app, "_perf_log", [])
    app._perf_log_empty = not bool(app._perf_log)
    log_col = ft.ListView(
        spacing=3, auto_scroll=True, expand=True,
        controls=([_log_widget(l["msg"], l["tone"]) for l in app._perf_log]
                  if app._perf_log else [_empty_activity()]))
    app._perf_log_col = log_col

    # ---- handlers ----
    def do_emit(e=None):
        if _get(app, "_perf_running", False):
            return
        app._perf_running = True
        app._perf_result = None
        threading.Thread(target=_emit_worker, args=(app,), daemon=True).start()
        _refresh_performance(app)

    def do_add(e=None):
        if _get(app, "_perf_adding", False) or _get(app, "_perf_running", False):
            return
        app._perf_adding = True
        threading.Thread(target=_add_worker, args=(app,), daemon=True).start()
        _refresh_performance(app)

    def do_preview(e=None):
        if _get(app, "_perf_previewing", False) or _get(app, "_perf_running", False):
            return
        app._perf_previewing = True
        threading.Thread(target=_preview_worker, args=(app,), daemon=True).start()
        _refresh_performance(app)

    def do_clear_basket(e=None):
        app._perf_basket = []
        app._perf_paths = None
        app._perf_can_run = False
        _refresh_performance(app)

    def do_remove_scenario(idx):
        b = list(_get(app, "_perf_basket", []) or [])
        if 0 <= idx < len(b):
            b.pop(idx)
            app._perf_basket = b
            app._perf_paths = None
            app._perf_can_run = False
            _refresh_performance(app)

    def do_run(e=None):
        if _get(app, "_perf_running", False) or not _get(app, "_perf_paths", None):
            return
        app._perf_running = True
        app._perf_cancelling = False
        app._perf_cancel_event = threading.Event()
        threading.Thread(target=_run_worker, args=(app,), daemon=True).start()
        _refresh_performance(app)

    def do_cancel(e=None):
        event = _get(app, "_perf_cancel_event", None)
        if not _get(app, "_perf_running", False) or event is None or event.is_set():
            return
        app._perf_cancelling = True
        event.set()
        _logline(app, strings.t("perf_cancel_requested"), "warn")
        _refresh_performance(app)

    def do_preset_changed(e=None):
        _apply_workload_preset(app, preset.value)
        _refresh_performance(app)

    def do_set_baseline(item):
        app._perf_baseline = dict(item)
        _persist_perf_history(app)
        _refresh_performance(app)

    def do_clear_history(e=None):
        app._perf_history = []
        app._perf_baseline = None
        _persist_perf_history(app)
        _refresh_performance(app)

    def do_open(e=None):
        try:
            os.startfile(_get(app, "_perf_out", tempfile.gettempdir()))
        except Exception:
            pass

    def do_open_report(e=None):
        # JMeter's own interactive dashboard.
        res = _get(app, "_perf_result", None)
        rdir = getattr(res, "raw_report_dir", "") if res else ""
        index = os.path.join(rdir, "index.html") if rdir else ""
        try:
            os.startfile(index if os.path.exists(index) else rdir)
        except Exception:
            pass

    def do_open_qa_report(e=None):
        # QA Studio's own one-page summary — render to a temp file and open it.
        res = _get(app, "_perf_result", None)
        if not res:
            return

        def work():
            try:
                path = os.path.join(tempfile.gettempdir(), "qastudio_perf_report.html")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(_report_html(app, res))
                os.startfile(path)
            except Exception as ex:
                _logline(app, f"Couldn't open the QA Studio report: {str(ex)[:120]}", "err")
        threading.Thread(target=work, daemon=True).start()

    def do_copy_log(e=None):
        try:
            app._copy_log_text([l.get("msg", "") for l in _get(app, "_perf_log", [])])
        except Exception:
            pass

    def do_clear_log(e=None):
        app._perf_log = []
        try:
            log_col.controls = [_empty_activity()]
            app._perf_log_empty = True
            log_col.update()
        except Exception:
            _refresh_performance(app)

    def do_export_report(e=None):
        res = _get(app, "_perf_result", None)
        if not res:
            return

        def _selected(path):
            def work():
                out_path = path if path.lower().endswith(".html") else path + ".html"
                try:
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(_report_html(app, res))
                    _logline(app, f"Report exported: {out_path}", "ok")
                    try:
                        os.startfile(out_path)
                    except Exception:
                        pass
                except Exception as ex:
                    _logline(app, f"Export failed: {str(ex)[:160]}", "err")
            threading.Thread(target=work, daemon=True).start()

        image_assets.choose_save_path(
            app, strings.t("perf_dlg_save_report"), "qastudio-performance-report.html",
            ["html"], _selected,
            lambda: _logline(app, strings.t("file_picker_unavailable"), "err"))

    def do_email_report(e=None):
        if _get(app, "_perf_emailing", False):
            return
        res = _get(app, "_perf_result", None)
        if not res:
            return
        to = [a for a in re.split(r"[,\s;]+", _get(app, "_perf_email_to", "") or "") if a]
        if not to:
            app._toast(strings.t("perf_toast_need_recipient"))
            return
        try:
            import engine as E
            if not getattr(E, "GMAIL_APP_PASS", ""):
                app._toast(strings.t("perf_toast_need_gmail_pass"))
                return
        except Exception:
            pass
        app._perf_emailing = True
        _b = getattr(app, "_perf_send_btn", None)   # grey the button IN PLACE (no full render)
        if _b is not None:
            try:
                _b.disabled = True
                _b.opacity = 0.55
                _b.update()
            except Exception:
                pass
        app._toast(strings.t("perf_toast_sending"))

        def work():
            try:
                import shutil
                import engine as E
                html = _report_html(app, res)
                attach = []
                tmp_html = os.path.join(tempfile.gettempdir(), "qastudio_perf_report.html")
                with open(tmp_html, "w", encoding="utf-8") as f:
                    f.write(html)
                attach.append(tmp_html)
                rdir = getattr(res, "raw_report_dir", "")
                if rdir and os.path.isdir(rdir):
                    try:
                        zp = shutil.make_archive(
                            os.path.join(tempfile.gettempdir(), "qastudio_perf_dashboard"),
                            "zip", rdir)
                        attach.append(zp)
                    except Exception:
                        pass
                gate = {True: "PASS", False: "FAIL", None: "done"}[res.threshold_pass]
                subj = f"Performance report — {gate} — p95 {res.p95_ms:.0f}ms, err {res.error_rate * 100:.1f}%"
                ok, err = E.send_report(to, subj, html, attachments=attach)
                _logline(app, f"Emailed report to {', '.join(to)}." if ok
                         else (err or "Email failed."), "ok" if ok else "err")
            except Exception as ex:
                _logline(app, f"Email failed: {str(ex)[:160]}", "err")
            finally:
                app._perf_emailing = False

                def _fin():
                    _b2 = getattr(app, "_perf_send_btn", None)   # re-enable IN PLACE
                    if _b2 is not None:
                        try:
                            _b2.disabled = False
                            _b2.opacity = 1.0
                            _b2.update()
                        except Exception:
                            pass
                try:
                    app.ui_safe(_fin)
                except Exception:
                    _fin()
        threading.Thread(target=work, daemon=True).start()

    def _pick_into(attr, title, extensions):
        # Field is built by _auto_field (bound to attr), so set the attr and
        # refresh only the mounted Performance panels from the UI thread.
        def _selected(path):
            setattr(app, attr, path)
            _queue_performance_refresh(app)
        image_assets.choose_file(app, title, extensions, _selected,
                                 lambda: _logline(app, strings.t("file_picker_unavailable"), "err"))

    def do_browse(e=None):
        _pick_into("_perf_data_path", strings.t("perf_dlg_select_csv"), ["csv"])

    def do_browse_har(e=None):
        _pick_into("_perf_har_path", strings.t("perf_dlg_select_har"), ["har"])

    def do_browse_out(e=None):
        def _selected(path):
            app._perf_out_dir = path
            _persist_out_dir(app)              # remember it like Automation does
            _queue_performance_refresh(app)
        image_assets.choose_directory(app, strings.t("perf_dlg_choose_out"), _selected,
                                      lambda: _logline(app, strings.t("folder_picker_unavailable"), "err"))

    def do_browse_users(e=None):
        _pick_into("_perf_tok_csv", strings.t("perf_dlg_select_csv"), ["csv"])

    def do_fetch_tokens(e=None):
        if _get(app, "_perf_tok_running", False) or _get(app, "_perf_running", False):
            return
        app._perf_tok_running = True
        threading.Thread(target=_tokens_worker, args=(app,), daemon=True).start()
        _refresh_performance(app)

    def do_detect_login(e=None):
        # Pick a login HAR and auto-fill the login config from it.
        def _selected(p):
            def work():
                app._perf_tok_login_har = p
                try:
                    cfg = token_prefetch.detect_login_config_from_har(p)
                except Exception as ex:
                    _logline(app, f"Couldn't read that HAR: {str(ex)[:120]}", "err")
                    cfg = {"ok": False}
                if cfg.get("ok"):
                    app._perf_tok_url = cfg.get("url", "")
                    app._perf_tok_format = cfg.get("body_format", "json")
                    app._perf_tok_userfield = cfg.get("user_field", "email")
                    app._perf_tok_passfield = cfg.get("pass_field", "password")
                    app._perf_tok_jsonpath = cfg.get("token_json_path", "")
                    app._perf_tok_header = cfg.get("token_header", "")
                    _logline(app, f"Detected login: {cfg.get('method')} {cfg.get('url')} "
                                  f"({cfg.get('body_format')}), fields "
                                  f"{cfg.get('user_field')}/{cfg.get('pass_field')}.", "ok")
                    if cfg.get("token_json_path"):
                        _logline(app, f"Token found at JSON path: {cfg['token_json_path']}", "ok")
                    elif cfg.get("token_header"):
                        _logline(app, f"Token found in header: {cfg['token_header']}", "ok")
                    # Clean detection (url + a token location) → keep fields locked;
                    # otherwise unlock so the user can fix what's missing.
                    complete = bool(cfg.get("url")) and bool(
                        cfg.get("token_json_path") or cfg.get("token_header"))
                    app._perf_tok_editable = not complete
                    if cfg.get("note"):
                        _logline(app, cfg["note"], "warn")
                    if complete:
                        _logline(app, "Settings locked (read-only). They'll unlock if a login "
                                      "test fails.", "dim")
                    else:
                        _logline(app, "Some settings couldn't be detected — they're editable "
                                      "below; fill them in.", "warn")
                else:
                    app._perf_tok_editable = True
                    _logline(app, cfg.get("note", "Couldn't detect a login in that HAR. Fill the "
                                  "settings below manually."), "warn")
                _queue_performance_refresh(app)
            threading.Thread(target=work, daemon=True).start()
        image_assets.choose_file(app, strings.t("perf_dlg_select_har"), ["har"], _selected,
                                 lambda: _logline(app, strings.t("file_picker_unavailable"), "err"))

    # ---- left column: one card() per section, exactly like automation.py ----
    hint = lambda t: ft.Text(t, size=11, color=T.INK_3, weight=ft.FontWeight.W_500)

    def _tip(btn, text):
        try:
            btn.tooltip = text
        except Exception:
            pass
        return btn

    src = _get(app, "_perf_source", "har")

    # Full-width Source field so the option label isn't truncated.
    source_row = [
        ft.Container(dd_field(
            strings.t("perf_lbl_source"),
            "“Import HAR” replays a real browser capture; “Paste cURL” turns Copy-as-cURL "
            "commands into requests. Both give exact requests — no guessing.",
            source), expand=1),
    ]

    # Shared per-user / dynamic-data controls for HAR + cURL: auth header,
    # parameterize (literals -> {{vars}}), and correlation (extract from responses).
    def _advanced_fields():
        return [
            ft.Container(height=12),
            app._auto_field(
                strings.t("perf_lbl_auth"), "_perf_auth",
                strings.t("perf_ph_auth"),
                info=strings.t("au_pf_auth_info")),
            ft.Container(height=14),
            field_label(strings.t("perf_lbl_variables"),
                        info=strings.t("au_pf_param_info")),
            ft.Container(param_box, padding=ft.Padding.only(top=4)),
            ft.Container(height=14),
            field_label(strings.t("perf_lbl_correlation"),
                        info=strings.t("au_pf_corr_info")),
            ft.Container(corr_box, padding=ft.Padding.only(top=4)),
            ft.Container(height=14),
            field_label(strings.t("perf_lbl_intest_login"),
                        info=strings.t("au_pf_login_info")),
            ft.Container(login_box, padding=ft.Padding.only(top=4)),
            ft.Container(height=10),
            ft.Row([
                ft.Container(app._auto_field(
                    strings.t("perf_lbl_token_jsonpath"), "_perf_login_tokenpath", "access_token",
                    info=strings.t("au_pf_tokpath_info")), expand=1),
                ft.Container(app._auto_field(
                    strings.t("perf_lbl_token_var"), "_perf_login_var", "token",
                    info=strings.t("au_pf_tokvar_info")), expand=1),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ]

    if src == "curl":
        source_body = [
            ft.Container(curl_box, padding=ft.Padding.only(top=12)),
            ft.Container(height=6),
            hint("Each curl command becomes one exact request (method, URL, headers, "
                 "body). Paste several to chain a flow. Tip: DevTools → Network → "
                 "right-click a request → Copy → Copy as cURL (bash)."),
        ] + _advanced_fields()
    else:  # har (default)
        source_body = [
            ft.Container(height=12),
            app._auto_field(
                strings.t("perf_lbl_har_file"), "_perf_har_path", strings.t("perf_ph_har_file"),
                info=strings.t("au_pf_har_info")),
            ft.Container(height=8),
            ft.Row([ghost_btn(strings.t("au_pf_browse_har"), icon=ft.Icons.UPLOAD_FILE,
                              on_click=do_browse_har, ignore_ro=True)], spacing=8),
            ft.Container(height=12),
            app._auto_field(
                strings.t("perf_lbl_only_domains"), "_perf_har_domains",
                "app.example.com, api.example.com",
                info=strings.t("au_pf_domains_info")),
        ] + _advanced_fields()

    cases_card = card(ft.Column([
        sec_head("1", strings.t("perf_sec_scenario_source")),
        ft.Container(height=10),
        ft.Row(source_row, spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        *source_body,
    ], spacing=0))

    preset = ft.Dropdown(
        value=_get(app, "_perf_preset", "custom"), filled=True, bgcolor=T.CARD,
        border_color=T.BORDER, focused_border_color=T.VIOLET, expand=True,
        options=[
            ft.DropdownOption(key="custom", text=strings.t("perf_preset_custom")),
            *[ft.DropdownOption(key=name, text=strings.t("perf_preset_" + name))
              for name in ("smoke", "load", "stress", "spike", "soak")],
        ],
        on_select=do_preset_changed)

    load_card = card(ft.Column([
        sec_head("2", strings.t("perf_sec_load_profile")),
        ft.Container(height=10),
        ft.Row([
            ft.Container(dd_field(strings.t("perf_lbl_preset"),
                                  strings.t("perf_preset_hint"), preset), expand=2),
            ft.Container(app._auto_field(
                strings.t("perf_lbl_pacing"), "_perf_pacing", "0",
                info=strings.t("perf_info_pacing")), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        ft.Row([
            ft.Container(app._auto_field(
                strings.t("perf_lbl_users"), "_perf_users", "20",
                info=strings.t("perf_info_users")), expand=1),
            ft.Container(app._auto_field(
                strings.t("perf_lbl_ramp"), "_perf_ramp", "15",
                info=strings.t("perf_info_ramp")), expand=1),
            ft.Container(app._auto_field(
                strings.t("perf_lbl_duration"), "_perf_duration", "60",
                info=strings.t("perf_info_duration")), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        ft.Row([
            ft.Container(app._auto_field(
                strings.t("perf_lbl_p95_budget"), "_perf_p95", "800",
                info=strings.t("perf_info_p95_budget")), expand=1),
            ft.Container(app._auto_field(
                strings.t("perf_lbl_max_error"), "_perf_err", "1",
                info=strings.t("perf_info_max_error")), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        ft.Row([
            ft.Container(app._auto_field(
                strings.t("perf_lbl_p99_budget"), "_perf_p99", "0",
                info=strings.t("perf_info_p99_budget")), expand=1),
            ft.Container(app._auto_field(
                strings.t("perf_lbl_min_throughput"), "_perf_min_rps", "0",
                info=strings.t("perf_info_min_throughput")), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        app._auto_field(
            strings.t("perf_lbl_distributed"), "_perf_remote_hosts",
            "10.0.0.5, 10.0.0.6  (running jmeter-server)",
            info=strings.t("au_pf_engines_info")),
        ft.Container(height=6),
        hint("The budgets decide the PASS / FAIL gate. For thousands of users, run "
             "distributed engines — a single machine caps out at a few hundred threads."),
    ], spacing=0))

    data_card = card(ft.Column([
        sec_head("3", strings.t("perf_sec_data")),
        ft.Container(height=10),
        app._auto_field(
            strings.t("perf_lbl_data_csv"), "_perf_data_path", strings.t("perf_ph_data_csv"),
            info=strings.t("au_pf_datacsv_info")),
        ft.Container(height=8),
        ft.Row([ghost_btn(strings.t("perf_btn_browse"), icon=ft.Icons.FOLDER_OPEN, on_click=do_browse,
                          ignore_ro=True)], spacing=8),
        ft.Container(height=6),
        hint("Leave blank to run without data-driving. With a CSV, each virtual user "
             "pulls the next row per iteration."),
        ft.Container(height=16),
        app._auto_field(
            strings.t("perf_lbl_output_folder"), "_perf_out_dir", strings.t("perf_ph_output_folder"),
            info=strings.t("au_pf_output_info")),
        ft.Container(height=8),
        ft.Row([ghost_btn(strings.t("perf_btn_choose_folder"), icon=ft.Icons.FOLDER_OPEN,
                          on_click=do_browse_out, ignore_ro=True)], spacing=8),
    ], spacing=0))

    # ---- Prepare auth tokens (optional): log a users CSV in, collect a per-user
    # token, write a tokens CSV, and wire it up as the Data CSV + Auth header. ----
    tok_running = bool(_get(app, "_perf_tok_running", False))
    # Detected settings are read-only until detection is incomplete or a login
    # fails (or the user clicks Edit manually).
    tok_editable = bool(_get(app, "_perf_tok_editable", False))

    def do_edit_login_manually(e=None):
        app._perf_tok_editable = True
        _refresh_performance(app)

    tok_format = ft.Dropdown(
        value=_get(app, "_perf_tok_format", "json"), filled=True,
        bgcolor=(T.CARD if tok_editable else T.CARD_2), disabled=not tok_editable,
        border_color=T.BORDER, focused_border_color=T.VIOLET, expand=True,
        options=[ft.DropdownOption(key="json", text=strings.t("perf_opt_json_body")),
                 ft.DropdownOption(key="form", text=strings.t("perf_opt_form_encoded"))],
        on_select=lambda e: setattr(app, "_perf_tok_format", tok_format.value))

    def _det_field(label, attr, hint_text, info):
        """A 'detected setting' field: read-only until tok_editable, then editable.
        Uses expand so it fills its column (responsive within the row)."""
        ro = not tok_editable
        tf = ft.TextField(
            value=str(_get(app, attr, "") or ""), hint_text=hint_text, read_only=ro,
            bgcolor=(T.CARD_2 if ro else None), color=(T.INK_2 if ro else T.INK),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=11, horizontal=12),
            text_size=13, expand=True)
        if not ro:
            tf.on_change = lambda e, a=attr, ff=tf: setattr(app, a, ff.value)
        return ft.Column([field_label(label, info=info),
                          ft.Container(tf, padding=ft.Padding.only(top=4))], spacing=0)
    token_card = card(ft.ExpansionTile(
        title=sec_head("A", strings.t("perf_sec_prepare_tokens")),
        subtitle=ft.Text(strings.t("perf_tokens_summary"), size=11,
                         color=T.INK_3, weight=ft.FontWeight.W_500),
        controls=[ft.Column([
        ft.Container(height=10),

        # Step 1 — auto-detect the login config from ONE recorded login, so the
        # user doesn't hand-enter URL / fields / token path.
        field_label(strings.t("perf_lbl_login_recording"), info=strings.t("au_pf_prep_info")),
        ft.Container(
            ft.Text((_get(app, "_perf_tok_login_har", "") or "—"), size=12, color=T.INK_2,
                    no_wrap=True, max_lines=1),
            padding=ft.Padding.symmetric(vertical=10, horizontal=12),
            bgcolor=T.CARD_2, border=ft.Border.all(1, T.BORDER), border_radius=T.R),
        ft.Container(height=8),
        ft.Row([_tip(green_btn(strings.t("perf_btn_autodetect"), icon=ft.Icons.AUTO_FIX_HIGH,
                               on_click=do_detect_login, ignore_ro=True),
                     "Pick a HAR of one successful login; QA Studio fills the settings "
                     "below automatically.")], spacing=8),

        ft.Container(height=16),
        app._auto_field(strings.t("perf_lbl_users_csv"), "_perf_tok_csv", strings.t("perf_ph_users_csv"),
                        info=strings.t("perf_info_users_csv")),
        ft.Container(height=8),
        ft.Row([ghost_btn(strings.t("perf_btn_browse"), icon=ft.Icons.FOLDER_OPEN, on_click=do_browse_users,
                          ignore_ro=True)], spacing=8),

        ft.Container(height=16),
        ft.Row([
            ft.Text((strings.t("perf_detected_editable") if tok_editable else
                     strings.t("perf_detected_readonly")),
                    size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3, expand=True),
            (ft.Container() if tok_editable else
             _tip(ghost_btn(strings.t("perf_btn_edit_manually"), icon=ft.Icons.EDIT_OUTLINED,
                            on_click=do_edit_login_manually, ignore_ro=True),
                  strings.t("perf_tip_edit_manually"))),
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=10),
        _det_field(strings.t("perf_lbl_login_url"), "_perf_tok_url", "https://your-app/api/login",
                   strings.t("perf_info_login_ep")),
        ft.Container(height=12),
        # Responsive: two fields per row so labels never truncate on a narrow panel.
        ft.Row([
            ft.Container(dd_field(strings.t("perf_lbl_request_body"),
                strings.t("perf_info_request_body"), tok_format), expand=1),
            ft.Container(_det_field(strings.t("perf_lbl_token_jsonpath"), "_perf_tok_jsonpath", "access_token",
                strings.t("perf_info_token_jsonpath")), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        ft.Row([
            ft.Container(_det_field(strings.t("perf_lbl_token_header"), "_perf_tok_header", strings.t("perf_ph_token_header"),
                strings.t("perf_info_token_header")), expand=1),
            ft.Container(expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        ft.Row([
            ft.Container(_det_field(strings.t("perf_lbl_api_user_field"), "_perf_tok_userfield", strings.t("perf_ph_email"),
                strings.t("perf_info_api_user_field")), expand=1),
            ft.Container(_det_field(strings.t("perf_lbl_api_pass_field"), "_perf_tok_passfield", strings.t("perf_ph_password"),
                strings.t("perf_info_api_pass_field")), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        ft.Row([
            ft.Container(_det_field(strings.t("perf_lbl_csv_user_col"), "_perf_tok_usercol", strings.t("perf_ph_email"),
                strings.t("perf_info_csv_user_col")), expand=1),
            ft.Container(_det_field(strings.t("perf_lbl_csv_pass_col"), "_perf_tok_passcol", strings.t("perf_ph_password"),
                strings.t("perf_info_csv_pass_col")), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=14),
        ft.Row([_tip(green_btn(strings.t("perf_btn_fetching") if tok_running else strings.t("perf_btn_fetch"),
                               icon=ft.Icons.KEY, on_click=do_fetch_tokens,
                               disabled=tok_running or running, ignore_ro=True),
                     strings.t("perf_tip_fetch"))],
               spacing=8),
        ft.Container(height=6),
        hint(strings.t("perf_tokens_expiry_hint")),
    ], spacing=0)],
        expanded=bool(tok_running or _get(app, "_perf_tok_csv", "")
                      or _get(app, "_perf_tok_login_har", "")),
        maintain_state=True, dense=True,
        tile_padding=ft.Padding.all(0),
        controls_padding=ft.Padding.only(top=4),
        text_color=T.INK, collapsed_text_color=T.INK,
        icon_color=T.VIOLET_INK, collapsed_icon_color=T.INK_3))

    basket = list(_get(app, "_perf_basket", []) or [])
    adding = bool(_get(app, "_perf_adding", False))
    _emit_label = (strings.t("perf_emit") + (f" ({len(basket)})" if basket else ""))
    btns = [
        _tip(primary_btn(_emit_label, on_click=do_emit, disabled=running or adding),
             "Build the JMeter plan from the basket (or, if empty, the current source). "
             "Does NOT run the test yet."),
        _tip(ghost_btn(strings.t("perf_btn_adding") if adding else strings.t("perf_btn_add_plan"), icon=ft.Icons.ADD,
                       on_click=do_add, disabled=adding or running, ignore_ro=True),
             "Add the current source's scenarios to the plan, then switch source / import "
             "another and add again to combine them into one load test."),
        _tip(ghost_btn(strings.t("perf_btn_preview"), icon=ft.Icons.VISIBILITY_OUTLINED, on_click=do_preview,
                       disabled=running or bool(_get(app, "_perf_previewing", False)),
                       ignore_ro=True),
             "List the parsed requests in the Activity log without emitting — a sanity "
             "check before Generate & Emit."),
    ]
    if _get(app, "_perf_paths", None):
        btns.append(_tip(green_btn(strings.t("perf_btn_run_jmeter"), on_click=do_run,
                                   disabled=running or not can_run, ignore_ro=True),
                         strings.t("perf_tip_run_jmeter")))
        if running:
            cancelling = bool(_get(app, "_perf_cancelling", False))
            btns.append(_tip(
                danger_btn(strings.t("perf_btn_stopping") if cancelling
                           else strings.t("perf_btn_stop"),
                           icon=ft.Icons.STOP_CIRCLE_OUTLINED, on_click=do_cancel,
                           disabled=cancelling),
                strings.t("perf_btn_stop")))
        btns.append(_tip(ghost_btn(strings.t("perf_btn_open_folder"), on_click=do_open, ignore_ro=True),
                         strings.t("perf_tip_open_folder")))
    _res = _get(app, "_perf_result", None)
    if running:
        _exec_text, _exec_color, _exec_bg = (
            strings.t("perf_execute_running"), T.VIOLET_INK, T.VIOLET_SOFT)
    elif _get(app, "_perf_paths", None) and can_run:
        _exec_text, _exec_color, _exec_bg = (
            strings.t("perf_execute_ready"), T.GREEN, T.GREEN_SOFT)
    else:
        _exec_text, _exec_color, _exec_bg = (
            strings.t("perf_execute_draft"), T.INK_3, T.CARD_2)
    execution_badge = ft.Container(
        ft.Text(_exec_text, size=10.5, weight=ft.FontWeight.BOLD,
                color=_exec_color),
        bgcolor=_exec_bg, border=ft.Border.all(1, _exec_color),
        border_radius=T.R_SM,
        padding=ft.Padding.symmetric(vertical=4, horizontal=10))
    action_card = card(ft.Column([
        ft.Row([sec_head("4", strings.t("perf_sec_execute")),
                ft.Container(expand=True), execution_badge],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=8),
        hint(strings.t("perf_execute_hint")),
        ft.Container(height=12),
        ft.Row(btns, spacing=10, wrap=True),
    ], spacing=0))

    # "In this plan" basket — scenarios accumulated across sources/imports; all
    # get emitted into ONE JMeter plan (one thread group each).
    basket_card = None
    if basket:
        rows = []
        for i, s in enumerate(basket):
            rows.append(ft.Container(
                ft.Row([
                    ft.Icon(ft.Icons.LAYERS_OUTLINED, size=15, color=T.VIOLET),
                    ft.Text((s.title or s.id or strings.t("perf_scenario_fallback")), size=12.5,
                            weight=ft.FontWeight.W_600, color=T.INK, expand=True,
                            no_wrap=True, max_lines=1),
                    ft.Text(strings.t("perf_req_count", n=s.request_count), size=11, color=T.INK_3),
                    ft.IconButton(ft.Icons.CLOSE, icon_size=14, icon_color=T.INK_3,
                                  tooltip=strings.t("perf_tip_remove_plan"),
                                  on_click=lambda e, idx=i: do_remove_scenario(idx),
                                  style=ft.ButtonStyle(padding=ft.Padding.all(2))),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=T.CARD_2, border=ft.Border.all(1, T.BORDER), border_radius=T.R,
                padding=ft.Padding.only(left=12, right=4, top=2, bottom=2)))
        total_req = sum(s.request_count for s in basket)
        basket_card = card(ft.Column([
            ft.Row([sec_head("≡", strings.t("perf_sec_in_plan")), ft.Container(expand=True),
                    _tip(ghost_btn(strings.t("perf_btn_clear_all"), icon=ft.Icons.DELETE_OUTLINE,
                                   on_click=do_clear_basket, ignore_ro=True),
                         strings.t("perf_tip_clear_all"))],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=10),
            ft.Column(rows, spacing=8),
            ft.Container(height=8),
            hint(strings.t("perf_plan_summary", scenarios=len(basket), requests=total_req)),
        ], spacing=0))

    left_children = [cases_card, load_card, data_card, token_card]
    if basket_card is not None:
        left_children.append(basket_card)
    left_children.append(action_card)
    if _res is not None:
        left_children.append(_result_card(_res))
        emailing = bool(_get(app, "_perf_emailing", False))
        # Email UI mirrors regression / sprint plan: searchable recipient picker
        # (team members + custom emails) with the Send button as its trailing
        # control, greyed in place while sending.
        app._perf_send_btn = _tip(
            green_btn(strings.t("perf_email_report_btn"), icon=ft.Icons.SEND, on_click=do_email_report,
                      disabled=emailing, ignore_ro=True),
            "Email the report + JMeter dashboard .zip to the recipients.")
        email_picker = regression.email_recipient_picker(
            app, "_perf_email_to", is_open_key="_perf_email_open",
            sync_key="perf_emails", trailing=app._perf_send_btn)
        # Open buttons: QA Studio's own summary + (if a run produced one) JMeter's
        # interactive dashboard.
        open_btns = [
            _tip(ghost_btn(strings.t("au_pf_open_qa_report"), icon=ft.Icons.DESCRIPTION_OUTLINED,
                           on_click=do_open_qa_report, ignore_ro=True),
                 "Open QA Studio's one-page summary (verdict, metrics, glossary)."),
        ]
        if getattr(_res, "raw_report_dir", ""):
            open_btns.append(_tip(
                ghost_btn(strings.t("au_pf_open_jmeter_report"), icon=ft.Icons.INSERT_CHART_OUTLINED,
                          on_click=do_open_report, ignore_ro=True),
                "Open JMeter's full interactive dashboard (charts over time)."))
        report_card = card(ft.Column([
            sec_head("6", strings.t("au_pf_report")),
            ft.Container(height=8),
            hint("Two reports: QA Studio's plain-language one-pager, and JMeter's "
                 "interactive dashboard. Open either, save the QA Studio one, or email "
                 "both (email needs a Gmail App Password on Setup → Connection)."),
            ft.Container(height=12),
            ft.Row(open_btns, spacing=10, wrap=True),
            ft.Container(height=16),
            ft.Text(strings.t("au_pf_email"), size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=8),
            email_picker,
            ft.Container(height=14),
            ft.Row([_tip(ghost_btn(strings.t("au_pf_export_qa_report"), icon=ft.Icons.DOWNLOAD,
                                   on_click=do_export_report, ignore_ro=True),
                         "Save QA Studio's one-page HTML report to your computer.")],
                   spacing=10),
        ], spacing=0))
        left_children.append(report_card)

    # Persistent per-user run history with an explicit comparison baseline.
    _hist = list(_get(app, "_perf_history", []) or [])
    if _hist:
        baseline = _get(app, "_perf_baseline", None)
        h_rows = []
        for h in _hist[:10]:
            gcol = {"PASS": T.GREEN, "FAIL": T.RED}.get(h.get("gate"), T.INK_2)
            is_baseline = bool(baseline and (
                (h.get("id") and h.get("id") == baseline.get("id")) or h == baseline))
            h_rows.append(ft.Container(ft.Row([
                ft.Text(h.get("when", ""), size=11.5, color=T.INK_3,
                        width=142, no_wrap=True),
                ft.Text(h.get("gate", ""), size=11.5, weight=ft.FontWeight.BOLD,
                        color=gcol, width=46, no_wrap=True),
                ft.Text(f"p95 {h.get('p95', 0):.0f} ms", size=11.5, color=T.INK_2,
                        expand=True, no_wrap=True),
                ft.Text(f"err {h.get('err', 0):.1f}%", size=11.5, color=T.INK_3,
                        width=76, no_wrap=True),
                ft.Text(f"{h.get('rps', 0):.1f}/s", size=11.5, color=T.INK_3,
                        width=64, no_wrap=True),
                ft.IconButton(
                    icon=(ft.Icons.FLAG if is_baseline else ft.Icons.FLAG_OUTLINED),
                    icon_size=16, icon_color=(T.VIOLET if is_baseline else T.INK_3),
                    tooltip=(strings.t("perf_baseline_badge") if is_baseline
                             else strings.t("perf_tip_set_baseline")),
                    on_click=lambda e, item=dict(h): do_set_baseline(item),
                    style=ft.ButtonStyle(padding=ft.Padding.all(2))),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=(T.VIOLET_SOFT if is_baseline else T.CARD_2),
                border=ft.Border.all(1, T.VIOLET if is_baseline else T.BORDER),
                border_radius=T.R, padding=ft.Padding.only(left=10, right=2, top=3, bottom=3)))
        history_children = [
            ft.Row([sec_head("7", strings.t("au_pf_run_history")),
                    ft.Container(expand=True),
                    ghost_btn(strings.t("perf_btn_clear_history"),
                              icon=ft.Icons.DELETE_OUTLINE,
                              on_click=do_clear_history, ignore_ro=True)],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=10),
        ]
        if baseline:
            current = _hist[0]
            history_children.extend([
                ft.Container(
                    ft.Text(strings.t(
                        "perf_baseline_compare",
                        p95=float(current.get("p95", 0)) - float(baseline.get("p95", 0)),
                        err=float(current.get("err", 0)) - float(baseline.get("err", 0)),
                        rps=float(current.get("rps", 0)) - float(baseline.get("rps", 0))),
                        size=11.5, color=T.INK_2, weight=ft.FontWeight.W_600),
                    bgcolor=T.VIOLET_SOFT, border_radius=T.R,
                    padding=ft.Padding.symmetric(vertical=9, horizontal=12)),
                ft.Container(height=8),
            ])
        history_children.extend([
            ft.Column(h_rows, spacing=7),
            ft.Container(height=4),
            hint(strings.t("perf_history_hint")),
        ])
        left_children.append(card(ft.Column(history_children, spacing=0)))

    left = ft.Column(left_children, spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)

    # ---- activity log rail, styled exactly like automation.py (spinner + Copy/Clear) ----
    spinner = (ft.ProgressRing(width=15, height=15, stroke_width=2, color=T.VIOLET)
               if running else ft.Icon(ft.Icons.TERMINAL, size=15, color=T.INK_3))

    def _log_btn(icon, tip, handler, danger=False):
        return ft.IconButton(icon=icon, icon_size=16, tooltip=tip, on_click=handler,
                             icon_color=(T.RED if danger else T.INK_3),
                             style=ft.ButtonStyle(padding=ft.Padding.all(4)))

    log_toolbar = ft.Container(
        ft.Row([ft.Container(spinner),
                ft.Text(strings.t("au_pf_activity"), size=11, weight=ft.FontWeight.BOLD, color=T.INK_3,
                        expand=True),
                _log_btn(ft.Icons.COPY_ALL_OUTLINED, strings.t("perf_tip_copy_log"), do_copy_log),
                _log_btn(ft.Icons.DELETE_OUTLINE, strings.t("perf_tip_clear_log"), do_clear_log, danger=True)],
               spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        height=34)
    right = card(ft.Column([
        log_toolbar,
        ft.Container(log_col, expand=True, bgcolor=T.CARD_2,
                     border=ft.Border.all(1, T.BORDER), border_radius=T.R, padding=12),
    ], spacing=8, expand=True), expand=True)

    if _fragment:
        return left, right

    # Keep these two controls mounted for the lifetime of this Performance
    # screen. Subsequent actions transplant fresh child controls into them,
    # avoiding app.render() and preserving native body + rail scroll offsets.
    left_host = ft.Container(left, expand=True)
    right_host = ft.Container(right, width=384)

    def _refresh_mounted():
        if getattr(app, "active", "performance") != "performance":
            return
        fresh_left, fresh_right = screen(app, _fragment=True)
        # Keep the shell-owned header spacer at index 0.  It is installed only
        # once, when this page first mounts, and must survive every log-driven
        # partial refresh so scroll position 0 remains below the fixed header.
        _replace_mounted_children(left, fresh_left)
        for name in ("spacing", "run_spacing", "horizontal_alignment",
                     "alignment", "tight", "expand"):
            try:
                setattr(left, name, getattr(fresh_left, name))
            except Exception:
                pass
        _replace_mounted_content(right_host, fresh_right)
        left.update()
        right_host.update()

    app._perf_refresh_mounted = _refresh_mounted
    body = ft.Row([left_host, right_host], spacing=22,
                  vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
    return app.shell(strings.t("perf_title"), strings.t("perf_subtitle"), body)
