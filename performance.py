"""performance.py - Performance testing screen.

Extract load-test scenarios from existing test cases, emit them to JMeter, and
run locally. Follows the app's screen conventions exactly: app.shell(...) chrome,
card() panels, sec_head() sections, field_label(..., info=...) info icons, and
automation.py's ACTIVITY log-rail. ALL heavy lifting is in the unit-tested perf/
package (perf.service); this file is glue + widgets.

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
import json
import os
import re
import tempfile
import threading
import time
import traceback

import flet as ft
import theme as T
import regression
from ui import card, primary_btn, green_btn, ghost_btn, field_label, sec_head

from perf import service
from perf import har as har_import
from perf import curl as curl_import
from perf import report as perf_report
from perf import token_prefetch
from perf.models import DataSource, LoadProfile

# An empty, fillable skeleton (NOT a fake journey) - shown in the Paste-JSON box
# so the user sees the exact shape to fill in. Use {{variable}} placeholders that
# a Data CSV can fill; id/story_id are optional.
CASES_SKELETON = [{
    "title": "",
    "steps": [
        {"action": "", "expected": ""},
    ],
}]


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


def _logline(app, msg, tone="dim"):
    log = getattr(app, "_perf_log", None)
    if log is None:
        app._perf_log = log = []
    log.append({"msg": msg, "tone": tone})
    col = getattr(app, "_perf_log_col", None)
    if col is not None:
        def _do():
            col.controls.append(_log_widget(msg, tone))
            try:
                col.update()
            except Exception:
                pass
        try:
            app.ui_safe(_do)
        except Exception:
            _do()


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
                       duration_s=_i("_perf_duration", 60), thresholds=thr)


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
    domains = [d for d in re.split(r"[,\s]+",
               _get(app, "_perf_har_domains", "") or "") if d]
    _logline(app, f"Parsing HAR{' for ' + ', '.join(domains) if domains else ''}...")
    scenarios = har_import.scenarios_from_har(har_path, include_domains=domains)
    if not scenarios or not scenarios[0].requests:
        _logline(app, "No matching requests in the HAR. Clear the domain filter "
                      "or check you saved the right capture.", "warn")
        return None
    n = sum(len(s.requests) for s in scenarios)
    _logline(app, f"Imported {n} real request(s) from HAR.", "ok")
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
        _logline(app, "ERROR: " + str(ex), "err")
        _logline(app, traceback.format_exc(), "err")
    finally:
        app._perf_adding = False
        try:
            app.ui_safe(app.render)
        except Exception:
            pass


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
        _logline(app, f"Output folder: {out}", "info")

        # Prefer the accumulated basket; otherwise build from the current source
        # (keeps the simple single-source workflow working without Add-to-plan).
        basket = list(_get(app, "_perf_basket", []) or [])
        if basket:
            scenarios = basket
            _logline(app, f"Emitting {len(scenarios)} accumulated scenario(s) "
                          "from the plan basket...", "info")
        else:
            scenarios = _build_current_scenarios(app)
            if not scenarios:
                return

        target, paths = service.emit_scenarios(
            scenarios, _profile(app), out, target_name="jmeter", data=_data(app))
        for s in scenarios:
            _logline(app, f"[{s.id}] {s.title} - {s.request_count} req, vars={s.variables}", "ok")
        _logline(app, f"Emitted: {paths.entry}", "ok")
        if paths.data_csv:
            _logline(app, f"Data CSV copied to the project (includes any secrets it "
                          f"holds): {paths.data_csv}", "warn")
        app._perf_paths = paths
        app._perf_target = target
        app._perf_profile = _profile(app)
        ok, msg = target.preflight()
        app._perf_can_run = ok
        _logline(app, f"JMeter preflight: {'OK' if ok else 'NOT READY'} - {msg}",
                 "ok" if ok else "warn")
        if ok:
            _logline(app, "Ready — click the green “Run JMeter” button to start the "
                          "load test.", "info")
        else:
            _logline(app, "Install Apache JMeter and a Java runtime, then click "
                          "“Generate & Emit” again.", "warn")
    except Exception as ex:
        _logline(app, "ERROR: " + str(ex), "err")
        _logline(app, traceback.format_exc(), "err")
    finally:
        app._perf_running = False
        try:
            app.ui_safe(app.render)
        except Exception:
            pass


# JMeter console noise we drop, and the bits we keep + colour, so the Activity
# log reads like a run summary instead of raw stdout.
_JM_NOISE = ("scanning to locate", "to view the results", "createdb", "creating summariser",
             "waiting for possible shutdown", "starting standalone test", "tidying up",
             "will be removed in a future release", "created the tree successfully")


def _jm_tone(line):
    """Return a tone for a JMeter output line, or None to drop it as noise."""
    low = line.lower()
    if any(s in low for s in _JM_NOISE):
        return None
    if "error" in low or "exception" in low or "not found" in low:
        return "err"
    if "summary =" in low:                       # cumulative running total
        # An error count that isn't "0 (0.00%)" means failures are accumulating.
        return "warn" if ("err:" in low and "0 (0.00%)" not in low) else "ok"
    if "summary +" in low:                       # per-interval delta
        return "dim"
    return "dim"


def _run_worker(app):
    try:
        _logline(app, "Running JMeter (non-GUI); this takes about the test duration...")

        def _on(ev):
            msg = str(ev.get("msg", "")).strip()
            if not msg:
                return
            tone = _jm_tone(msg)
            if tone is not None:
                _logline(app, msg, tone)

        hosts = (_get(app, "_perf_remote_hosts", "") or "").strip()
        res = service.run(app._perf_target, app._perf_paths, app._perf_profile,
                          on_event=_on, remote_hosts=hosts)
        app._perf_result = res
        gate = {True: "PASS", False: "FAIL", None: "done"}[res.threshold_pass]
        _logline(app, f"Run {gate}: p95={res.p95_ms:.0f}ms  err={res.error_rate * 100:.2f}%  "
                      f"{res.throughput_rps:.1f} req/s", "ok" if res.threshold_pass else "warn")
        # Keep a short run history (this session) for trend/compare.
        hist = list(_get(app, "_perf_history", []) or [])
        hist.insert(0, {"when": time.strftime("%H:%M:%S"), "gate": gate,
                        "p95": res.p95_ms, "err": res.error_rate * 100,
                        "rps": res.throughput_rps, "report": getattr(res, "raw_report_dir", "")})
        app._perf_history = hist[:8]
    except Exception as ex:
        _logline(app, "RUN ERROR: " + str(ex), "err")
    finally:
        app._perf_running = False
        try:
            app.ui_safe(app.render)
        except Exception:
            pass


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
        _logline(app, "Preview error: " + str(ex), "err")
    finally:
        app._perf_previewing = False
        try:
            app.ui_safe(app.render)
        except Exception:
            pass


def _ask_open_path(title, patterns):
    """Native 'open file' dialog (tkinter) - mirrors main._ask_folder_path /
    regression's Save-As idiom byte for byte. MUST be called OFF the UI thread:
    it spins its own hidden Tk root, and a blocking Tk dialog on Flet's own event
    loop silently fails. Returns path / None (cancelled) / False (no native dialog)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return False
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        path = filedialog.askopenfilename(parent=root, title=title, filetypes=patterns)
        try:
            root.update()
            root.destroy()
        except Exception:
            pass
        return path or None
    except Exception:
        return False


def _ask_csv_path():
    return _ask_open_path("Select a data CSV",
                          [("CSV files", "*.csv"), ("All files", "*.*")])


def _ask_har_path():
    return _ask_open_path("Select a HAR capture",
                          [("HAR files", "*.har"), ("All files", "*.*")])


def _ask_folder_path():
    """Native folder chooser for the optional output directory. Off-thread."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return False
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        path = filedialog.askdirectory(parent=root, title="Choose an output folder")
        try:
            root.update()
            root.destroy()
        except Exception:
            pass
        return path or None
    except Exception:
        return False


def _ask_save_html(default="qastudio-performance-report.html"):
    """Native 'save as' dialog for the exported HTML report. Off-thread (see
    _ask_open_path). Returns path / None / False."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return False
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        path = filedialog.asksaveasfilename(
            parent=root, title="Save performance report", defaultextension=".html",
            initialfile=default, filetypes=[("HTML report", "*.html"), ("All files", "*.*")])
        try:
            root.update()
            root.destroy()
        except Exception:
            pass
        return path or None
    except Exception:
        return False


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
    src_label = {"plan": "test plan", "json": "pasted cases",
                 "har": "HAR capture"}.get(src, src)
    scope = (_get(app, "_perf_base_url", "") or "").strip()
    if not scope:
        scope = f"Load test — {src_label}"
    return {"title": "QA Studio — Performance Report", "scope": scope,
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
        _logline(app, "Token prep error: " + str(ex), "err")
        _logline(app, traceback.format_exc(), "err")
    finally:
        app._perf_tok_running = False
        try:
            app.ui_safe(app.render)
        except Exception:
            pass


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
        tooltip=("All latency/error budgets were met." if r.threshold_pass
                 else "A latency or error budget was exceeded." if r.threshold_pass is False
                 else "Run finished; no pass/fail budgets were set."))
    return card(ft.Column([
        ft.Row([sec_head("4", "Last result"), ft.Container(expand=True), badge],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=12),
        ft.Row([
            _stat("p50 (median)", f"{r.p50_ms:.0f} ms",
                  "Half of all requests were faster than this."),
            _stat("p90", f"{r.p90_ms:.0f} ms",
                  "9 in 10 requests were faster than this."),
            _stat("p95", f"{r.p95_ms:.0f} ms",
                  "19 in 20 requests were faster than this — the common SLA target.", T.VIOLET),
            _stat("p99", f"{r.p99_ms:.0f} ms",
                  "99 in 100 requests were faster than this — your slowest users."),
        ], spacing=10),
        ft.Container(height=10),
        ft.Row([
            _stat("Throughput", f"{r.throughput_rps:.1f}/s",
                  "Requests completed per second — how much load was served."),
            _stat("Error rate", f"{r.error_rate * 100:.1f}%",
                  "Share of requests that failed (non-2xx / assertion failures).", err_col),
            _stat("Samples", f"{r.samples}",
                  "Total requests sent during the run."),
            _stat("Errors", f"{r.errors}",
                  "Number of failed requests.", err_col),
        ], spacing=10),
    ], spacing=0))


def screen(app):
    # Connect gate — same centered "A few things first" state Automation and
    # Regression show, instead of locking the nav button. Keeps the nav clickable
    # so the user can open Performance and see exactly what to do next.
    if not getattr(app, "readonly", False) and not (getattr(app, "connected", False)
                                                    and getattr(app, "project", None)):
        return regression.locked_state(
            app, "Performance",
            "Load-test scenarios from your test cases, run with JMeter",
            "Connect your provider on the Setup screen and pick a project. Once "
            "connected you can load test cases from your plan, paste your own, or "
            "import a HAR — then run it with JMeter.",
            icon=ft.Icons.SPEED,
            steps=[(ft.Icons.TUNE, "Connect"), (ft.Icons.CHECKLIST, "Build"),
                   (ft.Icons.PLAY_ARROW, "Run")])

    # Restore the persisted output folder on first entry (mirrors Automation
    # restoring auto_local_path from creds on load).
    if not hasattr(app, "_perf_out_dir"):
        try:
            _c = getattr(app, "creds", None)
            app._perf_out_dir = (_c.get("perf_out_dir", "") if isinstance(_c, dict) else "") or ""
        except Exception:
            app._perf_out_dir = ""

    running = bool(_get(app, "_perf_running", False))
    can_run = bool(_get(app, "_perf_can_run", False))

    # Seed numeric defaults so the app's _auto_field (which reads getattr(attr))
    # shows sensible starting values instead of blanks on first open.
    for _k, _v in (("_perf_users", "20"), ("_perf_ramp", "15"), ("_perf_duration", "60"),
                   ("_perf_p95", "800"), ("_perf_err", "1")):
        if not str(_get(app, _k, "")).strip():
            setattr(app, _k, _v)

    curl_box = ft.TextField(
        value=_get(app, "_perf_curl", ""),
        multiline=True, min_lines=6, max_lines=14,
        hint_text="Paste one or more curl commands (DevTools → right-click a request "
                  "→ Copy → Copy as cURL)…",
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
        # Full re-render: HAR and cURL show different section bodies.
        app._perf_source = source.value
        app.render()

    source = ft.Dropdown(
        value=_get(app, "_perf_source", "har"), filled=True, bgcolor=T.CARD,
        border_color=T.BORDER, focused_border_color=T.VIOLET, expand=True,
        tooltip="Choose how requests come in: “Import HAR” replays a real browser "
                "capture; “Paste cURL” turns Copy-as-cURL commands into requests. Both "
                "give exact requests — no guessing.",
        options=[ft.DropdownOption(key="har", text="Import HAR (real traffic)"),
                 ft.DropdownOption(key="curl", text="Paste cURL")],
        on_select=lambda e: _src_changed())

    # A labeled dropdown that mirrors _auto_field's shape (label + ⓘ info, then field).
    def dd_field(label, info, control):
        return ft.Column([field_label(label, info=info),
                          ft.Container(control, padding=ft.Padding.only(top=4))], spacing=0)

    # log rail column
    app._perf_log = _get(app, "_perf_log", [])
    log_col = ft.ListView(spacing=3, auto_scroll=True, expand=True,
                          controls=[_log_widget(l["msg"], l["tone"]) for l in app._perf_log])
    app._perf_log_col = log_col

    # ---- handlers ----
    def do_emit(e=None):
        if _get(app, "_perf_running", False):
            return
        app._perf_running = True
        app._perf_result = None
        threading.Thread(target=_emit_worker, args=(app,), daemon=True).start()
        app.render()

    def do_add(e=None):
        if _get(app, "_perf_adding", False) or _get(app, "_perf_running", False):
            return
        app._perf_adding = True
        threading.Thread(target=_add_worker, args=(app,), daemon=True).start()
        app.render()

    def do_preview(e=None):
        if _get(app, "_perf_previewing", False) or _get(app, "_perf_running", False):
            return
        app._perf_previewing = True
        threading.Thread(target=_preview_worker, args=(app,), daemon=True).start()
        app.render()

    def do_clear_basket(e=None):
        app._perf_basket = []
        app._perf_paths = None
        app._perf_can_run = False
        app.render()

    def do_remove_scenario(idx):
        b = list(_get(app, "_perf_basket", []) or [])
        if 0 <= idx < len(b):
            b.pop(idx)
            app._perf_basket = b
            app._perf_paths = None
            app._perf_can_run = False
            app.render()

    def do_run(e=None):
        if _get(app, "_perf_running", False) or not _get(app, "_perf_paths", None):
            return
        app._perf_running = True
        threading.Thread(target=_run_worker, args=(app,), daemon=True).start()
        app.render()

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
            log_col.controls.clear()
            log_col.update()
        except Exception:
            app.render()

    def do_export_report(e=None):
        res = _get(app, "_perf_result", None)
        if not res:
            return

        def work():
            path = _ask_save_html()
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(_report_html(app, res))
                _logline(app, f"Report exported: {path}", "ok")
                try:
                    os.startfile(path)
                except Exception:
                    pass
            except Exception as ex:
                _logline(app, f"Export failed: {str(ex)[:160]}", "err")
        threading.Thread(target=work, daemon=True).start()

    def do_email_report(e=None):
        if _get(app, "_perf_emailing", False):
            return
        res = _get(app, "_perf_result", None)
        if not res:
            return
        to = [a for a in re.split(r"[,\s;]+", _get(app, "_perf_email_to", "") or "") if a]
        if not to:
            app._toast("Enter at least one recipient email.")
            return
        try:
            import engine as E
            if not getattr(E, "GMAIL_APP_PASS", ""):
                app._toast("Set the Gmail App Password on the Setup screen first.")
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
        app._toast("Sending the performance report…")

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

    def _pick_into(attr, picker):
        # Field is built by _auto_field (bound to attr), so set the attr and
        # re-render — same idiom automation's folder browse uses. Off the UI thread.
        def work():
            p = picker()
            if p:
                setattr(app, attr, p)
                try:
                    app.ui_safe(app.render)
                except Exception:
                    app.render()
        threading.Thread(target=work, daemon=True).start()

    def do_browse(e=None):
        _pick_into("_perf_data_path", _ask_csv_path)

    def do_browse_har(e=None):
        _pick_into("_perf_har_path", _ask_har_path)

    def do_browse_out(e=None):
        def work():
            p = _ask_folder_path()
            if p:
                app._perf_out_dir = p
                _persist_out_dir(app)          # remember it like Automation does
                try:
                    app.ui_safe(app.render)
                except Exception:
                    app.render()
        threading.Thread(target=work, daemon=True).start()

    def do_browse_users(e=None):
        _pick_into("_perf_tok_csv", _ask_csv_path)

    def do_fetch_tokens(e=None):
        if _get(app, "_perf_tok_running", False) or _get(app, "_perf_running", False):
            return
        app._perf_tok_running = True
        threading.Thread(target=_tokens_worker, args=(app,), daemon=True).start()
        app.render()

    def do_detect_login(e=None):
        # Pick a login HAR and auto-fill the login config from it.
        def work():
            p = _ask_har_path()
            if not p:
                return
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
            try:
                app.ui_safe(app.render)
            except Exception:
                app.render()
        threading.Thread(target=work, daemon=True).start()

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
            "Source",
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
                "Auth header (optional)", "_perf_auth",
                "Bearer {{token}} (per-user) or a static Bearer …",
                info="Sets Authorization on every request. Use 'Bearer {{token}}' together "
                     "with a Data CSV that has a token column, so each virtual user uses "
                     "its OWN token; or paste one static 'Bearer …'."),
            ft.Container(height=14),
            field_label("Variables — parameterize (optional)",
                        info="Turn captured literals into per-user {{variables}}. One rule "
                             "per line:  literalFromCapture => variableName. Then add a "
                             "matching column to your Data CSV. Example:  SKU-123 => product"),
            ft.Container(param_box, padding=ft.Padding.only(top=4)),
            ft.Container(height=14),
            field_label("Correlation — extract from a response (optional)",
                        info="Reuse a value a response returns (e.g. a cart id or CSRF). One "
                             "rule per line:  var = $.json.path @ /url-part   (or   "
                             "var ~ regex @ /url-part). The value is pulled from the request "
                             "whose URL contains the @ part, and usable as {{var}} later. "
                             "Example:  cartId = $.id @ /cart"),
            ft.Container(corr_box, padding=ft.Padding.only(top=4)),
            ft.Container(height=14),
            field_label("In-test login (optional)",
                        info="Each virtual user logs in at run time and uses its OWN fresh "
                             "token — no pre-fetching. Paste the login as a cURL command; "
                             "the token is extracted from its response and sent as "
                             "Authorization on every following request. Use {{email}} / "
                             "{{password}} in the body with a Data CSV to log in as different "
                             "users. Overrides the Auth header above when set."),
            ft.Container(login_box, padding=ft.Padding.only(top=4)),
            ft.Container(height=10),
            ft.Row([
                ft.Container(app._auto_field(
                    "Token JSON path", "_perf_login_tokenpath", "access_token",
                    info="Where the token sits in the login response — a dotted path, e.g. "
                         "'access_token' or 'data.token'."), expand=1),
                ft.Container(app._auto_field(
                    "Token variable", "_perf_login_var", "token",
                    info="Name to store the token under; referenced as {{token}} in the "
                         "Authorization header."), expand=1),
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
                "HAR file", "_perf_har_path", "path to a .har capture",
                info="In Chrome DevTools → Network, run the flow once, then right-click a "
                     "request → “Save all as HAR”. That file holds every real request."),
            ft.Container(height=8),
            ft.Row([ghost_btn("Browse HAR…", icon=ft.Icons.UPLOAD_FILE,
                              on_click=do_browse_har, ignore_ro=True)], spacing=8),
            ft.Container(height=12),
            app._auto_field(
                "Only these domains", "_perf_har_domains",
                "app.example.com, api.example.com",
                info="Comma-separated. Keeps only requests to these hosts (and their "
                     "subdomains); leave blank to keep all. Static assets "
                     "(js/css/images/fonts) are always skipped."),
        ] + _advanced_fields()

    cases_card = card(ft.Column([
        sec_head("1", "Scenario source"),
        ft.Container(height=10),
        ft.Row(source_row, spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        *source_body,
    ], spacing=0))

    load_card = card(ft.Column([
        sec_head("2", "Load profile"),
        ft.Container(height=10),
        ft.Row([
            ft.Container(app._auto_field(
                "Users", "_perf_users", "20",
                info="Concurrent virtual users driving the load."), expand=1),
            ft.Container(app._auto_field(
                "Ramp (s)", "_perf_ramp", "15",
                info="Seconds to ramp from 0 up to all users."), expand=1),
            ft.Container(app._auto_field(
                "Duration (s)", "_perf_duration", "60",
                info="Seconds to hold the full load once ramped up."), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        ft.Row([
            ft.Container(app._auto_field(
                "p95 budget (ms)", "_perf_p95", "800",
                info="Pass/fail budget: the 95th-percentile latency limit, in ms."), expand=1),
            ft.Container(app._auto_field(
                "Max error %", "_perf_err", "1",
                info="Pass/fail budget: the maximum allowed error rate, in percent."), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        ft.Row([
            ft.Container(app._auto_field(
                "p99 budget (ms, optional)", "_perf_p99", "0",
                info="Optional pass/fail budget on the 99th-percentile latency. 0 = off."), expand=1),
            ft.Container(app._auto_field(
                "Min throughput (req/s, optional)", "_perf_min_rps", "0",
                info="Optional pass/fail budget: fail if throughput drops below this. 0 = off."), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        app._auto_field(
            "Distributed engines (optional)", "_perf_remote_hosts",
            "10.0.0.5, 10.0.0.6  (running jmeter-server)",
            info="Comma-separated hosts running Apache jmeter-server. The load is split "
                 "across them (JMeter -R) so you can drive far more users than one machine. "
                 "Leave blank to run locally."),
        ft.Container(height=6),
        hint("The budgets decide the PASS / FAIL gate. For thousands of users, run "
             "distributed engines — a single machine caps out at a few hundred threads."),
    ], spacing=0))

    data_card = card(ft.Column([
        sec_head("3", "Data (optional)"),
        ft.Container(height=10),
        app._auto_field(
            "Data CSV", "_perf_data_path", "path to a .csv",
            info="A CSV whose columns fill {{variables}} in your requests (e.g. email, "
                 "password, token). The file — including any passwords/tokens it holds — "
                 "is copied into the generated JMeter project on your machine."),
        ft.Container(height=8),
        ft.Row([ghost_btn("Browse…", icon=ft.Icons.FOLDER_OPEN, on_click=do_browse,
                          ignore_ro=True)], spacing=8),
        ft.Container(height=6),
        hint("Leave blank to run without data-driving. With a CSV, each virtual user "
             "pulls the next row per iteration."),
        ft.Container(height=16),
        app._auto_field(
            "Output folder (optional)", "_perf_out_dir", "defaults to a temp folder",
            info="Where the generated JMeter project, results.jtl and HTML dashboard are "
                 "written. Leave blank to use a temp folder."),
        ft.Container(height=8),
        ft.Row([ghost_btn("Choose folder…", icon=ft.Icons.FOLDER_OPEN,
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
        app.render()

    tok_format = ft.Dropdown(
        value=_get(app, "_perf_tok_format", "json"), filled=True,
        bgcolor=(T.CARD if tok_editable else T.CARD_2), disabled=not tok_editable,
        border_color=T.BORDER, focused_border_color=T.VIOLET, expand=True,
        options=[ft.DropdownOption(key="json", text="JSON body"),
                 ft.DropdownOption(key="form", text="Form-encoded")],
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
    token_card = card(ft.Column([
        sec_head("3+", "Prepare auth tokens (optional)"),
        ft.Container(height=8),
        hint("Have a CSV of users + passwords? QA Studio can log each one in, grab its "
             "bearer token, and build a tokens CSV — then each virtual user load-tests "
             "with its own token. Fills the Data CSV and Auth header for you."),
        ft.Container(height=14),

        # Step 1 — auto-detect the login config from ONE recorded login, so the
        # user doesn't hand-enter URL / fields / token path.
        field_label("Login recording (HAR)", info="Log in ONCE in your browser with "
                    "DevTools → Network open, then Save all as HAR. QA Studio reads it to "
                    "fill in the login URL, request format, field names, and where the "
                    "token is — no manual setup."),
        ft.Container(
            ft.Text((_get(app, "_perf_tok_login_har", "") or "—"), size=12, color=T.INK_2,
                    no_wrap=True, max_lines=1),
            padding=ft.Padding.symmetric(vertical=10, horizontal=12),
            bgcolor=T.CARD_2, border=ft.Border.all(1, T.BORDER), border_radius=T.R),
        ft.Container(height=8),
        ft.Row([_tip(green_btn("Auto-detect from login HAR", icon=ft.Icons.AUTO_FIX_HIGH,
                               on_click=do_detect_login, ignore_ro=True),
                     "Pick a HAR of one successful login; QA Studio fills the settings "
                     "below automatically.")], spacing=8),

        ft.Container(height=16),
        app._auto_field("Users CSV", "_perf_tok_csv", "path to users.csv (email, password)",
                        info="A CSV with a username/email column and a password column."),
        ft.Container(height=8),
        ft.Row([ghost_btn("Browse…", icon=ft.Icons.FOLDER_OPEN, on_click=do_browse_users,
                          ignore_ro=True)], spacing=8),

        ft.Container(height=16),
        ft.Row([
            ft.Text(("DETECTED SETTINGS — editable" if tok_editable else
                     "DETECTED SETTINGS — read-only (auto-filled)"),
                    size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3, expand=True),
            (ft.Container() if tok_editable else
             _tip(ghost_btn("Edit manually", icon=ft.Icons.EDIT_OUTLINED,
                            on_click=do_edit_login_manually, ignore_ro=True),
                  "Unlock these fields to set them by hand.")),
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=10),
        _det_field("Login URL", "_perf_tok_url", "https://your-app/api/login",
                   "The endpoint that accepts a username + password and returns a token. "
                   "Auto-filled from your login HAR."),
        ft.Container(height=12),
        # Responsive: two fields per row so labels never truncate on a narrow panel.
        ft.Row([
            ft.Container(dd_field("Request body",
                "How credentials are sent: JSON body or form-encoded.", tok_format), expand=1),
            ft.Container(_det_field("Token JSON path", "_perf_tok_jsonpath", "access_token",
                "Where the token sits in the JSON response — a dotted path, e.g. "
                "'access_token' or 'data.access_token'. Blank if it's in a header."), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        ft.Row([
            ft.Container(_det_field("…or token header", "_perf_tok_header", "e.g. authorization",
                "If the token is returned in a response header instead of the body, the "
                "header name (e.g. 'authorization')."), expand=1),
            ft.Container(expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        ft.Row([
            ft.Container(_det_field("API user field", "_perf_tok_userfield", "email",
                "The field name your login API expects for the username."), expand=1),
            ft.Container(_det_field("API password field", "_perf_tok_passfield", "password",
                "The field name your login API expects for the password."), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=12),
        ft.Row([
            ft.Container(_det_field("CSV user column", "_perf_tok_usercol", "email",
                "Which column in your CSV holds the username/email."), expand=1),
            ft.Container(_det_field("CSV password column", "_perf_tok_passcol", "password",
                "Which column in your CSV holds the password."), expand=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=14),
        ft.Row([_tip(green_btn("Fetching tokens…" if tok_running else "Fetch tokens",
                               icon=ft.Icons.KEY, on_click=do_fetch_tokens,
                               disabled=tok_running or running, ignore_ro=True),
                     "Log every user in, collect a per-user token, and prepare the CSV.")],
               spacing=8),
        ft.Container(height=6),
        hint("Tip: tokens can expire — run this shortly before the load test. Test your "
             "config on a small CSV first."),
    ], spacing=0))

    basket = list(_get(app, "_perf_basket", []) or [])
    adding = bool(_get(app, "_perf_adding", False))
    _emit_label = ("Generate & Emit" + (f" ({len(basket)})" if basket else ""))
    btns = [
        _tip(primary_btn(_emit_label, on_click=do_emit, disabled=running or adding),
             "Build the JMeter plan from the basket (or, if empty, the current source). "
             "Does NOT run the test yet."),
        _tip(ghost_btn("Adding…" if adding else "Add to plan", icon=ft.Icons.ADD,
                       on_click=do_add, disabled=adding or running, ignore_ro=True),
             "Add the current source's scenarios to the plan, then switch source / import "
             "another and add again to combine them into one load test."),
        _tip(ghost_btn("Preview", icon=ft.Icons.VISIBILITY_OUTLINED, on_click=do_preview,
                       disabled=running or bool(_get(app, "_perf_previewing", False)),
                       ignore_ro=True),
             "List the parsed requests in the Activity log without emitting — a sanity "
             "check before Generate & Emit."),
    ]
    if _get(app, "_perf_paths", None):
        btns.append(_tip(green_btn("Run JMeter", on_click=do_run,
                                   disabled=running or not can_run, ignore_ro=True),
                         "Run the load test and stream live progress in the Activity log."))
        btns.append(_tip(ghost_btn("Open folder", on_click=do_open, ignore_ro=True),
                         "Open the folder with the generated plan.jmx, results and report."))
    _res = _get(app, "_perf_result", None)
    buttons_row = ft.Container(ft.Row(btns, spacing=10, wrap=True),
                              padding=ft.Padding.only(top=2, bottom=2))

    # "In this plan" basket — scenarios accumulated across sources/imports; all
    # get emitted into ONE JMeter plan (one thread group each).
    basket_card = None
    if basket:
        rows = []
        for i, s in enumerate(basket):
            rows.append(ft.Container(
                ft.Row([
                    ft.Icon(ft.Icons.LAYERS_OUTLINED, size=15, color=T.VIOLET),
                    ft.Text((s.title or s.id or "scenario"), size=12.5,
                            weight=ft.FontWeight.W_600, color=T.INK, expand=True,
                            no_wrap=True, max_lines=1),
                    ft.Text(f"{s.request_count} req", size=11, color=T.INK_3),
                    ft.IconButton(ft.Icons.CLOSE, icon_size=14, icon_color=T.INK_3,
                                  tooltip="Remove from plan",
                                  on_click=lambda e, idx=i: do_remove_scenario(idx),
                                  style=ft.ButtonStyle(padding=ft.Padding.all(2))),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=T.CARD_2, border=ft.Border.all(1, T.BORDER), border_radius=T.R,
                padding=ft.Padding.only(left=12, right=4, top=2, bottom=2)))
        total_req = sum(s.request_count for s in basket)
        basket_card = card(ft.Column([
            ft.Row([sec_head("≡", "In this plan"), ft.Container(expand=True),
                    _tip(ghost_btn("Clear all", icon=ft.Icons.DELETE_OUTLINE,
                                   on_click=do_clear_basket, ignore_ro=True),
                         "Empty the plan basket.")],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=10),
            ft.Column(rows, spacing=8),
            ft.Container(height=8),
            hint(f"{len(basket)} scenario(s) · {total_req} request(s) — combined into one "
                 "JMeter plan on Generate & Emit."),
        ], spacing=0))

    left_children = [cases_card, load_card, data_card, token_card]
    if basket_card is not None:
        left_children.append(basket_card)
    left_children.append(buttons_row)
    if _res is not None:
        left_children.append(_result_card(_res))
        emailing = bool(_get(app, "_perf_emailing", False))
        # Email UI mirrors regression / sprint plan: searchable recipient picker
        # (team members + custom emails) with the Send button as its trailing
        # control, greyed in place while sending.
        app._perf_send_btn = _tip(
            green_btn("Email report", icon=ft.Icons.SEND, on_click=do_email_report,
                      disabled=emailing, ignore_ro=True),
            "Email the report + JMeter dashboard .zip to the recipients.")
        email_picker = regression.email_recipient_picker(
            app, "_perf_email_to", is_open_key="_perf_email_open",
            sync_key="perf_emails", trailing=app._perf_send_btn)
        # Open buttons: QA Studio's own summary + (if a run produced one) JMeter's
        # interactive dashboard.
        open_btns = [
            _tip(ghost_btn("Open QA Studio report", icon=ft.Icons.DESCRIPTION_OUTLINED,
                           on_click=do_open_qa_report, ignore_ro=True),
                 "Open QA Studio's one-page summary (verdict, metrics, glossary)."),
        ]
        if getattr(_res, "raw_report_dir", ""):
            open_btns.append(_tip(
                ghost_btn("Open JMeter report", icon=ft.Icons.INSERT_CHART_OUTLINED,
                          on_click=do_open_report, ignore_ro=True),
                "Open JMeter's full interactive dashboard (charts over time)."))
        report_card = card(ft.Column([
            sec_head("5", "Report"),
            ft.Container(height=8),
            hint("Two reports: QA Studio's plain-language one-pager, and JMeter's "
                 "interactive dashboard. Open either, save the QA Studio one, or email "
                 "both (email needs a Gmail App Password on Setup → Connection)."),
            ft.Container(height=12),
            ft.Row(open_btns, spacing=10, wrap=True),
            ft.Container(height=16),
            ft.Text("EMAIL", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=8),
            email_picker,
            ft.Container(height=14),
            ft.Row([_tip(ghost_btn("Export QA Studio report", icon=ft.Icons.DOWNLOAD,
                                   on_click=do_export_report, ignore_ro=True),
                         "Save QA Studio's one-page HTML report to your computer.")],
                   spacing=10),
        ], spacing=0))
        left_children.append(report_card)

    # Run history (this session) — a compact trend list to compare runs.
    _hist = list(_get(app, "_perf_history", []) or [])
    if len(_hist) > 1:
        h_rows = []
        for h in _hist:
            gcol = {"PASS": T.GREEN, "FAIL": T.RED}.get(h.get("gate"), T.INK_2)
            h_rows.append(ft.Row([
                ft.Text(h.get("when", ""), size=11.5, color=T.INK_3, width=70, no_wrap=True),
                ft.Text(h.get("gate", ""), size=11.5, weight=ft.FontWeight.BOLD,
                        color=gcol, width=46, no_wrap=True),
                ft.Text(f"p95 {h.get('p95', 0):.0f} ms", size=11.5, color=T.INK_2,
                        expand=True, no_wrap=True),
                ft.Text(f"err {h.get('err', 0):.1f}%", size=11.5, color=T.INK_3,
                        width=76, no_wrap=True),
                ft.Text(f"{h.get('rps', 0):.1f}/s", size=11.5, color=T.INK_3,
                        width=64, no_wrap=True),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        left_children.append(card(ft.Column([
            sec_head("6", "Run history"),
            ft.Container(height=10),
            ft.Column(h_rows, spacing=7),
            ft.Container(height=4),
            hint("This session's runs, newest first — watch p95 and error rate across "
                 "changes."),
        ], spacing=0)))

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
                ft.Text("ACTIVITY", size=11, weight=ft.FontWeight.BOLD, color=T.INK_3,
                        expand=True),
                _log_btn(ft.Icons.COPY_ALL_OUTLINED, "Copy entire log", do_copy_log),
                _log_btn(ft.Icons.DELETE_OUTLINE, "Clear log", do_clear_log, danger=True)],
               spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        height=34)
    right = card(ft.Column([
        log_toolbar,
        ft.Container(log_col, expand=True, bgcolor=T.CARD_2,
                     border=ft.Border.all(1, T.BORDER), border_radius=T.R, padding=12),
    ], spacing=8, expand=True), expand=True)

    body = ft.Row([ft.Container(left, expand=True),
                   ft.Container(right, width=384)], spacing=22,
                  vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
    return app.shell("Performance", "Load-test scenarios from your test cases, run with JMeter", body)
