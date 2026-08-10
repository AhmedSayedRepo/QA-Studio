"""perf/tests/test_service.py - orchestration-layer tests.

Run:  python -m unittest perf.tests.test_service -v

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from perf import service
from perf.models import LoadProfile, PerfResult

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
