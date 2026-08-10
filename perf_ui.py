"""perf_ui.py - standalone local tester UI for the performance pipeline.

A small, self-contained Flet window that drives perf.service, so you can test the
whole flow from a UI WITHOUT touching main.py (zero risk to the shipping app).
Once the UX is validated here, it folds into the app as a real Performance screen.

Run (same Python/env as the app - the 3.12 that has Flet):
    py -3 perf_ui.py

Uses only Flet idioms already used in the app (Dropdown with ft.DropdownOption +
on_select, filled dropdowns, FilledButton with a ButtonStyle bgcolor dict).

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import traceback

import flet as ft

from perf import service
from perf.models import DataSource, LoadProfile

VIOLET = "#3A57D6"
INK = "#181A24"
INK2 = "#6E7180"
CARD = "#FFFFFF"
BG = "#F7F8FE"
BORDER = "#E6E8F1"
GREEN = "#1F8A52"
RED = "#D6414A"
AMBER = "#AB780C"

SAMPLE_CASES = [{
    "id": "TC-101", "title": "Search journey", "story_id": "US-42",
    "steps": [
        {"action": "Log in with {{email}} and {{password}}", "expected": "200 OK"},
        {"action": "Open the dashboard", "expected": "Welcome"},
        {"action": "Search for {{term}}", "expected": "results are shown"},
        {"action": "Log out", "expected": "204"},
    ],
}]


def main(page: ft.Page):
    page.title = "QA Studio - Performance (local tester)"
    page.bgcolor = BG
    page.padding = 18
    page.window_width = 1080
    page.window_height = 760

    state = {"running": False, "paths": None, "target": None, "profile": None,
             "can_run": False, "out": ""}

    def numf(label, value, w=108):
        return ft.TextField(label=label, value=str(value), width=w, dense=True,
                            keyboard_type=ft.KeyboardType.NUMBER,
                            border_color=BORDER, focused_border_color=VIOLET)

    users = numf("Users", 20)
    ramp = numf("Ramp (s)", 15)
    duration = numf("Duration (s)", 60)
    p95 = numf("p95 ms", 800)
    err = numf("Max err %", 1)

    extractor = ft.Dropdown(
        value="heuristic", width=230, filled=True, bgcolor=CARD,
        border_color=BORDER, focused_border_color=VIOLET,
        options=[ft.DropdownOption(key="heuristic", text="Heuristic (offline, no key)"),
                 ft.DropdownOption(key="ai", text="AI (engine.ai_complete)")],
        on_select=lambda e: None)

    source = ft.Dropdown(
        value="sample", width=230, filled=True, bgcolor=CARD,
        border_color=BORDER, focused_border_color=VIOLET,
        options=[ft.DropdownOption(key="sample", text="Built-in sample case"),
                 ft.DropdownOption(key="json", text="Paste cases JSON below")],
        on_select=lambda e: _sync_source())

    cases_json = ft.TextField(
        label="cases JSON (used when source = Paste JSON)", multiline=True,
        min_lines=5, max_lines=12, value=json.dumps(SAMPLE_CASES, indent=2),
        border_color=BORDER, focused_border_color=VIOLET, visible=False)

    data_path = ft.TextField(label="Data CSV path (optional)", width=430, dense=True,
                             hint_text="paste the full path to a .csv",
                             border_color=BORDER, focused_border_color=VIOLET)

    log = ft.ListView(spacing=3, auto_scroll=True, expand=True)
    results = ft.Column(spacing=6)

    # ---- helpers ----
    def _sync_source():
        cases_json.visible = (source.value == "json")
        cases_json.update()

    def logline(msg, tone="dim"):
        color = {"ok": GREEN, "err": RED, "warn": AMBER}.get(tone, INK2)
        log.controls.append(ft.Text(str(msg), size=12, color=color, selectable=True))
        try:
            log.update()
        except Exception:
            page.update()

    def _cases():
        return SAMPLE_CASES if source.value == "sample" else json.loads(cases_json.value)

    def _profile():
        thr = {}
        try:
            if float(p95.value) > 0:
                thr["p95_ms"] = float(p95.value)
        except ValueError:
            pass
        try:
            if float(err.value) >= 0:
                thr["error_rate"] = float(err.value) / 100.0
        except ValueError:
            pass
        def _i(v, d):
            try:
                return int(float(v))
            except (ValueError, TypeError):
                return d
        return LoadProfile(users=_i(users.value, 10), ramp_up_s=_i(ramp.value, 15),
                           duration_s=_i(duration.value, 60), thresholds=thr)

    def _ai():
        if extractor.value != "ai":
            return None
        try:
            import engine
            logline("Using engine.ai_complete for extraction.", "ok")
            return lambda prompt: engine.ai_complete(prompt, tag="perf")
        except Exception as ex:
            logline(f"engine AI unavailable ({ex}); using heuristic.", "warn")
            return None

    def _data():
        p = (data_path.value or "").strip()
        if not p or not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            header = [h.strip() for h in f.readline().strip().split(",") if h.strip()]
        return DataSource(csv_path=p, columns=header)

    def _set_running(v):
        state["running"] = v
        gen_btn.disabled = v
        run_btn.disabled = v or not state["can_run"]
        page.update()

    def _show_result(r):
        gate = {True: ("PASS", GREEN), False: ("FAIL", RED), None: ("n/a", INK2)}[r.threshold_pass]
        rows = [
            ft.Text(f"Result: {gate[0]}", size=18, weight=ft.FontWeight.BOLD, color=gate[1]),
            ft.Text(f"samples={r.samples}  errors={r.errors} ({r.error_rate * 100:.2f}%)  "
                    f"throughput={r.throughput_rps:.1f} req/s", color=INK2, size=12),
            ft.Text(f"p50={r.p50_ms:.0f}  p90={r.p90_ms:.0f}  p95={r.p95_ms:.0f}  "
                    f"p99={r.p99_ms:.0f} ms", color=INK, size=12),
        ]
        for rs in r.per_request:
            rows.append(ft.Text(f"  {rs.label}  n={rs.samples} err={rs.errors} p95={rs.p95_ms:.0f}ms",
                                color=INK2, size=11))
        results.controls = rows
        results.update()

    def _emit_worker():
        try:
            cases = _cases()
            profile = _profile()
            data = _data()
            ai = _ai()
            out = os.path.join(tempfile.gettempdir(), "qastudio_perf_ui")
            os.makedirs(out, exist_ok=True)
            state["out"] = out
            logline(f"Extracting {len(cases)} case(s)...")
            scenarios, target, paths = service.build_and_emit(
                cases, profile, out, target_name="jmeter", ai_complete=ai, data=data)
            for s in scenarios:
                logline(f"[{s.id}] {s.title} - {s.request_count} req, vars={s.variables}", "ok")
            logline(f"Emitted: {paths.entry}", "ok")
            if paths.data_csv:
                logline(f"Data CSV (sensitive stripped): {paths.data_csv}", "ok")
            state.update(paths=paths, target=target, profile=profile)
            ok, msg = target.preflight()
            state["can_run"] = ok
            logline(f"JMeter preflight: {'OK' if ok else 'NOT READY'} - {msg}",
                    "ok" if ok else "warn")
            open_btn.visible = True
            open_btn.update()
        except Exception as ex:
            logline("ERROR: " + str(ex), "err")
            logline(traceback.format_exc(), "err")
        finally:
            _set_running(False)

    def _run_worker():
        try:
            logline("Running JMeter (non-GUI); this takes ~the test duration...")
            res = service.run(state["target"], state["paths"], state["profile"],
                              on_event=lambda ev: logline("[run] " + str(ev.get("msg", ""))))
            _show_result(res)
            logline("Run complete.", "ok")
        except Exception as ex:
            logline("RUN ERROR: " + str(ex), "err")
        finally:
            _set_running(False)

    def do_emit(e):
        results.controls = []
        results.update()
        _set_running(True)
        threading.Thread(target=_emit_worker, daemon=True).start()

    def do_run(e):
        if not state.get("paths"):
            logline("Emit first.", "warn")
            return
        _set_running(True)
        threading.Thread(target=_run_worker, daemon=True).start()

    def do_open(e):
        try:
            os.startfile(state["out"])   # Windows
        except Exception as ex:
            logline(f"Could not open folder: {ex}", "warn")

    gen_btn = ft.FilledButton(
        content=ft.Text("Generate & Emit", weight=ft.FontWeight.BOLD),
        on_click=do_emit,
        style=ft.ButtonStyle(bgcolor={"": VIOLET}, color={"": "#FFFFFF"}))
    run_btn = ft.FilledButton(
        content=ft.Text("Run JMeter", weight=ft.FontWeight.BOLD),
        on_click=do_run, disabled=True,
        style=ft.ButtonStyle(bgcolor={"": GREEN}, color={"": "#FFFFFF"}))
    open_btn = ft.TextButton(content=ft.Text("Open output folder", color=VIOLET),
                             on_click=do_open, visible=False)
    def sect(title):
        return ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=INK)

    left = ft.Container(
        width=470, bgcolor=CARD, border_radius=14, padding=18,
        border=ft.Border.all(1, BORDER),
        content=ft.Column([
            ft.Text("Performance - local tester", size=20, weight=ft.FontWeight.BOLD, color=INK),
            ft.Text("Drives perf.service; makes no changes to the app.", size=12, color=INK2),
            ft.Divider(height=16, color=BORDER),
            sect("Load profile"),
            ft.Row([users, ramp, duration], spacing=10),
            ft.Row([p95, err], spacing=10),
            ft.Divider(height=12, color=BORDER),
            sect("Extraction & source"),
            ft.Row([extractor, source], spacing=10),
            cases_json,
            data_path,
            ft.Divider(height=14, color=BORDER),
            ft.Row([gen_btn, run_btn], spacing=10),
            open_btn,
            ft.Divider(height=12, color=BORDER),
            sect("Results"),
            results,
        ], spacing=10, scroll=ft.ScrollMode.AUTO, expand=True))

    right = ft.Container(
        expand=True, bgcolor="#15151F", border_radius=14, padding=14,
        content=ft.Column([
            ft.Text("Activity log", size=12, color="#9FA2B2"),
            ft.Container(log, expand=True),
        ], expand=True))

    page.add(ft.Row([left, right], expand=True, spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.START))
    _sync_source()


if __name__ == "__main__":
    ft.run(main)
