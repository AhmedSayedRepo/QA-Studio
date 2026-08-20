"""perf/tests/test_perf.py - unit + contract tests for the perf core.

Run from the repo root:  python -m unittest perf.tests.test_perf -v

Covers: model math, both extractors (incl. the AI validation backstop + graceful
fallback), JMeter .jmx emission (well-formedness + structure + placeholder
translation + CSV), and .jtl parsing with threshold evaluation. No JMeter/JRE
needed - emit and parse are pure.

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from perf.models import (Assertion, AssertionKind, DataSource, LoadProfile,
                         WORKLOAD_PRESETS,
                         PerfRequest, PerfResult, PerfScenario)
from perf.extract import AIExtractor, HeuristicExtractor
from perf.targets.jmeter import JMeterTarget, apply_thresholds, parse_jtl
from perf.ports import PerfRunCancelled
from perf.registry import get_target, target_names

STEPS = [
    {"action": "Log in with {{email}} and {{password}}", "expected": "200 OK"},
    {"action": "Open the dashboard at https://app.example.com/dashboard", "expected": "Welcome"},
    {"action": "Delete the item", "expected": "204"},
]


class TestModels(unittest.TestCase):
    def test_workload_presets_cover_standard_profiles(self):
        self.assertEqual(
            set(WORKLOAD_PRESETS), {"smoke", "load", "stress", "spike", "soak"})
        self.assertEqual(WORKLOAD_PRESETS["smoke"]["users"], 1)
        self.assertGreater(WORKLOAD_PRESETS["soak"]["duration_s"],
                           WORKLOAD_PRESETS["load"]["duration_s"])

    def test_error_rate(self):
        r = PerfResult(scenario_id="1", target="jmeter", samples=200, errors=4)
        self.assertAlmostEqual(r.error_rate, 0.02)

    def test_threshold_pass_and_fail(self):
        r = PerfResult(scenario_id="1", target="jmeter", samples=100, errors=1,
                       p95_ms=300, throughput_rps=50)
        self.assertTrue(LoadProfile(thresholds={"p95_ms": 400, "error_rate": 0.05}).passed(r))
        self.assertFalse(LoadProfile(thresholds={"p95_ms": 250}).passed(r))
        self.assertIsNone(LoadProfile().passed(r))  # no thresholds -> None


class TestHeuristicExtractor(unittest.TestCase):
    def test_extracts_requests_vars_and_methods(self):
        sc = HeuristicExtractor().extract("TC-1", "Login journey", STEPS, story_id="US-9")
        self.assertEqual(sc.id, "TC-1")
        self.assertEqual(sc.story_id, "US-9")
        self.assertEqual(len(sc.requests), 3)
        self.assertIn("email", sc.variables)
        self.assertIn("password", sc.variables)
        self.assertEqual(sc.requests[0].method, "POST")   # "log in" -> write
        self.assertEqual(sc.requests[1].method, "GET")
        self.assertEqual(sc.requests[2].method, "DELETE")
        # real URL kept, missing URL -> templated placeholder
        self.assertEqual(sc.requests[1].url, "https://app.example.com/dashboard")
        self.assertIn("{{host}}", sc.requests[0].url)


class _StubAI:
    def __init__(self, reply): self.reply = reply
    def __call__(self, prompt): return self.reply


class TestAIExtractor(unittest.TestCase):
    def test_uses_valid_ai_json(self):
        reply = ('[{"method":"POST","url":"/api/login","body":"u={{email}}",'
                 '"assert_status":"200","think_ms":500}]')
        sc = AIExtractor(_StubAI(reply)).extract("TC-2", "Login", STEPS)
        self.assertEqual(len(sc.requests), 1)
        self.assertEqual(sc.requests[0].method, "POST")
        self.assertEqual(sc.requests[0].url, "/api/login")
        self.assertEqual(sc.requests[0].think_ms, 500)
        self.assertIn("email", sc.variables)

    def test_drops_invalid_rows(self):
        reply = ('[{"method":"WAT","url":"/x"},{"method":"GET","url":""},'
                 '{"method":"GET","url":"/ok"}]')
        sc = AIExtractor(_StubAI(reply)).extract("TC-3", "Mixed", STEPS)
        self.assertEqual(len(sc.requests), 1)             # only the valid GET /ok
        self.assertEqual(sc.requests[0].url, "/ok")

    def test_garbage_falls_back_to_heuristic(self):
        sc = AIExtractor(_StubAI("the model apologises and returns prose")).extract(
            "TC-4", "Login", STEPS)
        # graceful degradation: never empty, matches heuristic count
        self.assertEqual(len(sc.requests), 3)

    def test_ai_exception_falls_back(self):
        def boom(_): raise RuntimeError("no credits")
        sc = AIExtractor(boom).extract("TC-5", "Login", STEPS)
        self.assertEqual(len(sc.requests), 3)


class TestJMeterEmit(unittest.TestCase):
    def _scenario(self):
        return PerfScenario(id="TC-1", title="Login", requests=[
            PerfRequest(method="POST", url="https://{{host}}/api/login",
                        body="user=${email}",
                        assertions=[Assertion(AssertionKind.STATUS, "200")],
                        think_ms=250, source_step="Log in"),
            PerfRequest(method="GET", url="/dashboard",
                        assertions=[Assertion(AssertionKind.BODY_CONTAINS, "Welcome")]),
        ], variables=["host", "email"])

    def test_emits_wellformed_jmx(self):
        with tempfile.TemporaryDirectory() as d:
            paths = JMeterTarget().emit([self._scenario()], LoadProfile(users=25, ramp_up_s=10,
                                        duration_s=120), d)
            self.assertTrue(os.path.exists(paths.entry))
            tree = ET.parse(paths.entry)                  # must be well-formed XML
            root = tree.getroot()
            self.assertEqual(root.tag, "jmeterTestPlan")
            with open(paths.entry, encoding="utf-8") as f:
                xml = f.read()
            self.assertEqual(xml.count("<HTTPSamplerProxy"), 2)
            self.assertIn("<ThreadGroup", xml)
            self.assertIn(">25<", xml)                    # num_threads
            self.assertIn("${host}", xml)                 # {{host}} translated
            self.assertNotIn("{{host}}", xml)
            self.assertIn("/api/login", xml)
            self.assertIn("Welcome", xml)                 # body-contains assertion
            self.assertTrue(os.path.exists(os.path.join(d, "run.bat")))

    def test_emits_global_iteration_pacing(self):
        with tempfile.TemporaryDirectory() as d:
            paths = JMeterTarget().emit(
                [self._scenario()], LoadProfile(pacing_ms=1250), d)
            with open(paths.entry, encoding="utf-8") as f:
                xml = f.read()
            self.assertIn('testname="Iteration pacing"', xml)
            self.assertIn(
                '<stringProp name="ActionProcessor.duration">1250</stringProp>',
                xml)

    def test_run_honours_cancellation_before_waiting_for_output(self):
        class _Proc:
            pid = 321
            stdout = iter(())
            returncode = None

            def poll(self):
                return None

            def wait(self, timeout=None):
                self.returncode = 1
                return 1

        with tempfile.TemporaryDirectory() as d:
            entry = os.path.join(d, "plan.jmx")
            with open(entry, "w", encoding="utf-8") as f:
                f.write("<jmeterTestPlan/>")
            paths = type("Paths", (), {"root": d, "entry": entry})()
            proc = _Proc()
            target = JMeterTarget()
            with mock.patch.object(target, "preflight", return_value=(True, "ok")), \
                    mock.patch("perf.targets.jmeter.subprocess.Popen", return_value=proc), \
                    mock.patch("perf.targets.jmeter._terminate_process_tree") as stop:
                with self.assertRaises(PerfRunCancelled):
                    target.run(paths, cancel_check=lambda: True)
            stop.assert_called_once_with(proc)

    def test_csv_dataset_excludes_sensitive(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src.csv")
            with open(src, "w", encoding="utf-8") as f:
                f.write("email,password\na@b.com,secret\n")
            data = DataSource(csv_path=src, columns=["email", "password"],
                              sensitive_columns=["password"])
            paths = JMeterTarget().emit([self._scenario()], LoadProfile(), d, data=data)
            with open(paths.entry, encoding="utf-8") as f:
                xml = f.read()
            self.assertIn("<CSVDataSet", xml)
            self.assertIn("<stringProp name=\"variableNames\">email</stringProp>", xml)
            self.assertNotIn("password</stringProp>", xml)   # sensitive col not a variable
            # the copied data.csv must NOT contain the sensitive column or its value
            with open(paths.data_csv, encoding="utf-8") as f:
                body = f.read()
            self.assertIn("email", body)
            self.assertNotIn("password", body)
            self.assertNotIn("secret", body)


_JTL = (
    "timeStamp,elapsed,label,responseCode,responseMessage,threadName,dataType,"
    "success,failureMessage,bytes,sentBytes,grpThreads,allThreads,URL,Latency,IdleTime,Connect\n"
    "1000,100,POST /login,200,OK,t1,text,true,,10,5,1,1,http://x,50,0,5\n"
    "1100,200,POST /login,500,ERR,t1,text,false,boom,10,5,1,1,http://x,50,0,5\n"
    "1200,300,GET /home,200,OK,t1,text,true,,10,5,1,1,http://x,50,0,5\n"
)


class TestJtlParse(unittest.TestCase):
    def test_parse_and_thresholds(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "results.jtl")
            with open(p, "w", encoding="utf-8") as f:
                f.write(_JTL)
            res = parse_jtl(p, scenario_id="TC-1")
            self.assertEqual(res.samples, 3)
            self.assertEqual(res.errors, 1)
            self.assertAlmostEqual(res.error_rate, 1 / 3, places=3)
            self.assertEqual(res.p50_ms, 200.0)          # nearest-rank
            self.assertEqual(res.p95_ms, 300.0)
            self.assertAlmostEqual(res.duration_s, 0.2, places=3)
            self.assertEqual(len(res.per_request), 2)     # two labels
            # threshold gate
            self.assertFalse(apply_thresholds(res, LoadProfile(thresholds={"p95_ms": 250})).threshold_pass)
            self.assertTrue(apply_thresholds(res, LoadProfile(thresholds={"p95_ms": 350})).threshold_pass)


class TestRegistry(unittest.TestCase):
    def test_get_target(self):
        self.assertIn("jmeter", target_names())
        self.assertIsInstance(get_target("jmeter"), JMeterTarget)
        with self.assertRaises(ValueError):
            get_target("nope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
