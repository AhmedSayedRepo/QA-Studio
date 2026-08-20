"""perf/tests/test_service.py - orchestration-layer tests.

Run:  python -m unittest perf.tests.test_service -v

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import performance
import strings
from perf import service
from perf.models import LoadProfile, PerfResult
from perf.ports import PerfTarget

CASES = [{
    "id": "TC-1", "title": "Login", "story_id": "US-9",
    "steps": [
        {"action": "Log in with {{email}}", "expected": "200"},
        {"action": "Open dashboard", "expected": "Welcome"},
    ],
}]


class _StubAI:
    def __call__(self, prompt):
        return '[{"method":"POST","url":"/api/login","assert_status":"200"}]'


class TestService(unittest.TestCase):
    def test_jmeter_progress_is_translated_for_non_technical_readers(self):
        previous = strings.UI_LANG
        strings.UI_LANG = "en"
        try:
            message, tone = performance._friendly_jmeter_line(
                "summary +     11 in 00:00:13 =    0.9/s Avg:   774 "
                "Min:   675 Max:  1483 Err:     0 (0.00%) Active: 11 "
                "Started: 11 Finished: 0")
            self.assertEqual(tone, "dim")
            self.assertEqual(
                message,
                "Recent progress: 11 requests in 13 sec · 0.9 requests/sec · "
                "average 774 ms · 0 failed · 11 users active.")

            message, tone = performance._friendly_jmeter_line(
                "summary =   1369 in 00:02:13 =   10.3/s Avg:   333 "
                "Min:   259 Max:  1483 Err:     2 (0.15%)")
            self.assertEqual(tone, "warn")
            self.assertEqual(
                message,
                "Overall so far: 1,369 requests in 2 min 13 sec · "
                "10.3 requests/sec · average 333 ms · 2 failed (0.15%).")
        finally:
            strings.UI_LANG = previous

    def test_jmeter_console_boilerplate_is_hidden(self):
        self.assertIsNone(performance._friendly_jmeter_line(
            "Running JMeter (non-GUI)..."))

    def test_performance_refresh_uses_mounted_panels_without_full_render(self):
        calls = []
        app = SimpleNamespace(
            active="performance",
            _perf_refresh_mounted=lambda: calls.append("mounted"),
            render=lambda **_kwargs: calls.append("full"))
        performance._refresh_performance(app)
        self.assertEqual(calls, ["mounted"])

    def test_mounted_refresh_keeps_shell_header_gap(self):
        gap = object()
        mounted = SimpleNamespace(controls=[gap, "old card"], _qa_gap=True)
        fresh = SimpleNamespace(controls=["new card"])

        performance._replace_mounted_children(mounted, fresh)

        self.assertEqual(mounted.controls, [gap, "new card"])

    def test_mounted_refresh_without_shell_gap_uses_fresh_rows(self):
        mounted = SimpleNamespace(controls=["old card"], _qa_gap=False)
        fresh = SimpleNamespace(controls=["new card"])

        performance._replace_mounted_children(mounted, fresh)

        self.assertEqual(mounted.controls, ["new card"])

    def test_static_mounted_refresh_keeps_shell_header_gap(self):
        gap = object()
        wrapper = SimpleNamespace(controls=[gap, "old rail"])
        mounted = SimpleNamespace(content=wrapper, _qa_gap=True)
        fresh = object()

        performance._replace_mounted_content(mounted, fresh)

        self.assertIs(mounted.content, wrapper)
        self.assertEqual(wrapper.controls, [gap, fresh])

    def test_static_mounted_refresh_without_shell_gap_replaces_content(self):
        mounted = SimpleNamespace(content="old rail", _qa_gap=False)
        fresh = object()

        performance._replace_mounted_content(mounted, fresh)

        self.assertIs(mounted.content, fresh)

    def test_workload_preset_updates_profile_and_invalidates_plan(self):
        app = SimpleNamespace(_perf_paths=object(), _perf_can_run=True)
        performance._apply_workload_preset(app, "spike")
        self.assertEqual(app._perf_users, "200")
        self.assertEqual(app._perf_ramp, "10")
        self.assertEqual(app._perf_duration, "300")
        self.assertEqual(app._perf_pacing, "0")
        self.assertIsNone(app._perf_paths)
        self.assertFalse(app._perf_can_run)

    def test_profile_includes_global_pacing(self):
        app = SimpleNamespace(_perf_users="5", _perf_ramp="2",
                              _perf_duration="30", _perf_pacing="750",
                              _perf_p95="0", _perf_err="-1",
                              _perf_p99="0", _perf_min_rps="0")
        self.assertEqual(performance._profile(app).pacing_ms, 750)

    def test_scenarios_heuristic(self):
        sc = service.scenarios_from_cases(CASES)          # no AI -> heuristic
        self.assertEqual(len(sc), 1)
        self.assertEqual(sc[0].id, "TC-1")
        self.assertEqual(sc[0].request_count, 2)
        self.assertIn("email", sc[0].variables)

    def test_scenarios_ai(self):
        sc = service.scenarios_from_cases(CASES, ai_complete=_StubAI())
        self.assertEqual(sc[0].request_count, 1)          # AI returned one request
        self.assertEqual(sc[0].requests[0].url, "/api/login")

    def test_build_and_emit(self):
        with tempfile.TemporaryDirectory() as d:
            profile = LoadProfile(users=15, duration_s=30)
            scenarios, target, paths = service.build_and_emit(CASES, profile, d)
            self.assertEqual(target.name, "jmeter")
            self.assertTrue(os.path.exists(paths.entry))
            self.assertEqual(ET.parse(paths.entry).getroot().tag, "jmeterTestPlan")

    def test_apply_thresholds(self):
        r = PerfResult(scenario_id="TC-1", target="jmeter", samples=100, errors=0, p95_ms=500)
        self.assertTrue(service.apply_thresholds(r, LoadProfile(thresholds={"p95_ms": 600})).threshold_pass)
        self.assertFalse(service.apply_thresholds(r, LoadProfile(thresholds={"p95_ms": 400})).threshold_pass)
        self.assertIsNone(service.apply_thresholds(r, LoadProfile()).threshold_pass)

    def test_run_passes_cancel_check_to_supporting_target(self):
        class _Target(PerfTarget):
            name = "stub"

            def emit(self, scenarios, profile, out_dir, data=None):
                raise NotImplementedError

            def preflight(self):
                return True, "ok"

            def run(self, project, on_event=lambda _ev: None, remote_hosts="",
                    cancel_check=lambda: False):
                self.cancel_check = cancel_check
                return PerfResult(scenario_id="s", target="stub")

        target = _Target()
        marker = lambda: True
        service.run(target, object(), LoadProfile(), cancel_check=marker)
        self.assertIs(target.cancel_check, marker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
