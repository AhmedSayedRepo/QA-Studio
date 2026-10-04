"""Exercise real parser, service, inspection walk and history/report persistence.

Only external browser/process/UI/store boundaries are faked; no network or live
tracker writes. The original runner must remain usable when diagnostics break.
"""
import copy
import csv
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import engine
import performance
from failure_analysis import FailureCollector, read_analyses
from failure_analysis.integration import optional_analysis_fields, record_inspection_failure
from failure_analysis.models import EvidenceType, FailureCategory
from perf import service
from perf.models import LoadProfile, PerfResult
from perf.targets.jmeter import parse_jtl


class _Browser:
    """Minimal deterministic WebDriver boundary for navigation-only scenarios."""
    capabilities = {"browserName": "chrome", "browserVersion": "test",
                    "platformName": "test", "password": "do-not-copy-capabilities"}
    title = "Local fixture"

    def __init__(self, fail=True):
        self.current_url = "about:blank"
        self.visits = []
        self.closed = False
        self.fail = fail
        self.switch_to = SimpleNamespace(default_content=lambda: None)

    def set_page_load_timeout(self, timeout):
        pass

    def get(self, url):
        self.visits.append(url)
        self.current_url = url
        if self.fail and url.endswith("/fail"):
            from selenium.common.exceptions import TimeoutException
            raise TimeoutException("Navigation expired; Authorization: Bearer secret-browser-token")

    def execute_script(self, script, *args):
        if script == "return document.readyState":
            return "complete"
        return False

    def quit(self):
        self.closed = True


class FailureIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.logger = patch("diag_log.log")
        self.log = self.logger.start()
        self.addCleanup(self.logger.stop)

    def _jtl(self, directory, failed=True, code=None):
        path = os.path.join(directory, "results-fixture.jtl")
        names = ["timeStamp", "elapsed", "label", "success", "responseCode",
                 "responseMessage", "failureMessage", "URL"]
        rows = [
            ["1000", "20", "Open dashboard", "true", "200", "OK", "", "https://example.test/"],
            ["2000", "250", "Load account", "false" if failed else "true",
             code or "Non HTTP response code: java.net.SocketTimeoutException",
             "read expired; password=sample-password", "",
             "https://user:sample-password@example.test/account?novelToken=private#session"],
        ]
        with open(path, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(names)
            writer.writerows(rows)
        return path

    def _walk(self, fail=True):
        browser = _Browser(fail)
        payload = [{"story": {"id": "US-1", "title": "Navigation"}, "test_cases": [{
            "id": "TC-1", "title": "Open destination", "steps": [
                {"action": "Open destination", "expected": ""},
                {"action": "Continue", "expected": ""}]}]}]
        intents = [{"role": "action", "verb": "navigate", "target": "destination", "kind": "link",
                    "from_steps": [1], "value": "https://example.test/fail"},
                   {"role": "action", "verb": "navigate", "target": "next", "kind": "link",
                    "from_steps": [2], "value": "https://example.test/next"}]
        with patch("selenium.webdriver.Chrome", return_value=browser), \
             patch.object(engine, "compile_test_case", return_value=copy.deepcopy(intents)), \
             patch("time.sleep"):
            result = engine.explore_and_map(payload, None, "https://example.test/", wait_secs=0)
        return browser, result

    def test_real_inspection_failure_is_analyzed_and_persisted_without_stopping_next_step(self):
        browser, result = self._walk()
        self.assertTrue(browser.closed)
        self.assertEqual(browser.visits[-1], "https://example.test/next")
        record, = result["failure_analysis"]
        self.assertEqual(record.results[0].category, FailureCategory.TIMEOUT)
        self.assertEqual(record.context.test_case_id, "TC-1")
        self.assertEqual(record.context.test_step_id, "1")
        self.assertEqual(record.context.url, "https://example.test/fail")
        self.assertEqual(record.context.action, "navigate")
        self.assertIsNotNone(record.context.stack_trace)
        self.assertEqual(result["stats"]["guess"], 1)
        with tempfile.TemporaryDirectory() as directory:
            path = engine.write_inspection_screens(directory, result)
            report = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(read_analyses(report["failure_analysis"]), (record,))
            self.assertEqual(report["schema_version"], 4)
            self.assertIn("execution_plans", report)
            self.assertNotIn("secret-browser-token", json.dumps(report))
            # Reader still rehydrates original plans without interpreting analysis.
            new_payload = copy.deepcopy(result["stories_payload"])
            new_payload[0]["test_cases"][0].pop("inspection_plan")
            self.assertGreater(engine.apply_inspection_screens(new_payload, path), 0)

    def test_successful_inspection_never_invokes_analysis_or_adds_report_fields(self):
        with patch("failure_analysis.integration.record_inspection_failure") as hook:
            browser, result = self._walk(fail=False)
        hook.assert_not_called()
        self.assertNotIn("failure_analysis", result)
        self.assertEqual(result["stats"]["guess"], 0)
        with tempfile.TemporaryDirectory() as directory:
            report = json.loads(Path(engine.write_inspection_screens(directory, result)).read_text(encoding="utf-8"))
            self.assertNotIn("failure_analysis", report)

    def test_broken_inspection_adapter_preserves_failure_bookkeeping_and_report(self):
        with patch("failure_analysis.integration.record_inspection_failure", side_effect=RuntimeError("internal")):
            browser, result = self._walk()
        self.assertTrue(browser.closed)
        self.assertEqual(result["stats"]["guess"], 1)
        self.assertNotIn("failure_analysis", result)
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(engine.write_inspection_screens(directory, result))

    def test_inspection_reference_reuses_existing_dom_without_binary_capture(self):
        class Disconnected:
            @property
            def current_url(self):
                raise OSError("closed")
            @property
            def capabilities(self):
                raise OSError("closed")
        records = []
        record_inspection_failure(records, run_id="r", execution_id="e", story={"id": "S"},
            case={"id": "C", "title": "Case"}, intent={"role": "assertion", "target": "Heading"},
            step_ids=[2], kind="assertion", driver=Disconnected(),
            captures=[{"story": "S", "test_case": "Case", "elements": ["do-not-copy"]}])
        record, = records
        self.assertEqual(record.results[0].category, FailureCategory.UNKNOWN)
        evidence = next(e for e in record.context.evidence if e.type == EvidenceType.DOM_SNAPSHOT)
        self.assertEqual(evidence.reference, "inspection-screens.json#/screen_captures/0")
        self.assertIsNone(evidence.value)
        self.assertNotIn("do-not-copy", json.dumps(record.to_dict()))

    def test_jtl_timeout_retains_metrics_and_references_original_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._jtl(directory)
            original = Path(path).read_bytes()
            result = parse_jtl(path, scenario_id="TC-22", report_dir="existing-report")
            record, = result.failure_analysis
            self.assertEqual((result.samples, result.errors, result.duration_s), (2, 1, 1))
            self.assertEqual(result.avg_ms, 135)
            self.assertEqual(result.raw_report_dir, "existing-report")
            self.assertEqual(record.results[0].category, FailureCategory.TIMEOUT)
            self.assertEqual(record.context.timestamp, "1970-01-01T00:00:02+00:00")
            self.assertEqual(record.context.test_case_id, "TC-22")
            self.assertEqual(record.context.url, "https://example.test/account")
            self.assertEqual(record.context.duration_ms, 250)
            self.assertIsNone(record.context.attempt)  # aggregate, not invented retry data
            self.assertEqual(record.context.evidence[0].reference, path + "#row=3")
            self.assertNotIn("sample-password", json.dumps(record.to_dict()))
            self.assertEqual(Path(path).read_bytes(), original)
            self.assertEqual(os.listdir(directory), ["results-fixture.jtl"])

    def test_unverified_attempted_locator_is_kept_only_as_diagnostic_evidence(self):
        intent = {"role": "action", "verb": "type", "target": "Account", "value": "private typed value"}
        records = []
        record_inspection_failure(records, run_id="r", execution_id="e", story={"id": "S"},
            case={"id": "C", "title": "Case"}, intent=intent, step_ids=[1], kind="action",
            driver=_Browser(), captures=[], attempted_locator={"by": "css", "value": "#account"})
        self.assertEqual(records[0].context.locator.value, "#account")
        self.assertEqual(records[0].context.locator.strategy, "css")
        self.assertNotIn("live_locator", intent)
        self.assertNotIn("private typed value", json.dumps(records[0].to_dict()))

    def test_real_failed_input_keeps_exception_and_attempted_locator_without_verifying_it(self):
        from selenium.common.exceptions import TimeoutException
        class Input:
            tag_name = "input"
            def get_attribute(self, name):
                return ""
            def clear(self):
                pass
            def send_keys(self, value):
                raise TimeoutException("Could not type private typed value")
        browser = _Browser(fail=False)
        browser.find_element = lambda *args: Input()
        element = {"id": "profile-name", "tag": "input"}
        payload = [{"story": {"id": "US-1", "title": "Profile"}, "test_cases": [{
            "id": "TC-1", "title": "Edit profile", "steps": [{"action": "Edit name", "expected": ""}]}]}]
        intent = {"role": "action", "verb": "type", "target": "profile name", "kind": "input",
                  "from_steps": [1], "value": "private typed value"}
        with patch("selenium.webdriver.Chrome", return_value=browser), \
             patch.object(engine, "compile_test_case", return_value=[intent]), \
             patch.object(engine, "_rank_candidates", return_value=[(10, element)]), \
             patch.object(engine, "_harvest_dom", return_value=[element]), patch("time.sleep"):
            result = engine.explore_and_map(payload, None, "https://example.test/", wait_secs=0)
        record, = result["failure_analysis"]
        self.assertEqual(record.context.locator.value, "profile-name")
        self.assertEqual(record.context.locator.strategy, "id")
        self.assertEqual(record.results[0].category, FailureCategory.TIMEOUT)
        self.assertNotIn("private typed value", json.dumps(record.to_dict()))
        self.assertNotIn("live_locator", intent)
        self.assertEqual(intent["inspection_unresolved"], "action_failed")
        self.assertEqual(payload[0]["test_cases"][0]["steps"][0]["locator_src"], "guess")

    def test_http_failure_and_timeout_wording_do_not_guess_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            result = parse_jtl(self._jtl(directory, code="504"))
        self.assertEqual(result.failure_analysis[0].results[0].category, FailureCategory.UNKNOWN)
        self.assertIsNone(result.failure_analysis[0].context.test_case_id)

    def test_jtl_success_does_not_collect_or_change_metrics(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("failure_analysis.integration.diagnose_jmeter") as hook:
            result = parse_jtl(self._jtl(directory, failed=False))
        hook.assert_not_called()
        self.assertEqual(result.failure_analysis, ())
        self.assertEqual((result.samples, result.errors, result.avg_ms), (2, 0, 135))

    def test_parser_diagnostics_and_logging_failure_cannot_lose_original_result(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("failure_analysis.integration.diagnose_jmeter", side_effect=RuntimeError("diagnostic")), \
             patch("diag_log.log", side_effect=OSError("disk")):
            result = parse_jtl(self._jtl(directory))
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.failure_analysis, ())
        self.assertEqual(len(result.failures), 1)

    def test_threshold_failure_is_unknown_and_does_not_change_threshold_policy(self):
        class Target:
            def run(self, paths, on_event):
                return PerfResult("TC", "custom", samples=4, p95_ms=500)
        result = service.run(Target(), object(), LoadProfile(thresholds={"p95_ms": 100}))
        self.assertFalse(result.threshold_pass)
        self.assertEqual(result.failure_analysis[0].results[0].category, FailureCategory.UNKNOWN)
        self.assertEqual(result.failure_analysis[0].context.action, "evaluate_thresholds")

    def test_passing_service_result_never_calls_diagnostics(self):
        original = PerfResult("TC", "custom", samples=4, p95_ms=50)
        class Target:
            def run(self, paths, on_event):
                return original
        with patch("failure_analysis.integration.diagnose_performance") as hook:
            result = service.run(Target(), object(), LoadProfile(thresholds={"p95_ms": 100}))
        hook.assert_not_called()
        self.assertEqual(result, replace(original, threshold_pass=True))

    def test_service_target_failure_is_still_the_original_exception(self):
        error = RuntimeError("external runner failed")
        class Target:
            def run(self, paths, on_event):
                raise error
        with self.assertRaises(RuntimeError) as caught:
            service.run(Target(), object(), LoadProfile())
        self.assertIs(caught.exception, error)

    def test_broken_collector_keeps_failed_service_result_and_metrics(self):
        class Target:
            def run(self, paths, on_event):
                return PerfResult("TC", "custom", samples=4, errors=2)
        with patch.object(FailureCollector, "collect", side_effect=RuntimeError("collector")):
            result = service.run(Target(), object(), LoadProfile())
        self.assertEqual((result.errors, result.samples), (2, 4))
        self.assertEqual(result.failure_analysis, ())

    def test_history_roundtrip_through_existing_store_boundary_and_old_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            result = parse_jtl(self._jtl(directory))
        legacy = {"id": "old", "p95": 12, "when": "yesterday"}
        app = SimpleNamespace(creds={"perf_history": [legacy]}, _perf_target=object(),
            _perf_paths=object(), _perf_profile=LoadProfile())
        performance._ensure_perf_history(app)
        saved = []
        with patch.object(service, "run", return_value=result), \
             patch.object(performance, "_logline"), patch.object(performance, "_queue_performance_refresh"), \
             patch("store.save", side_effect=lambda value: saved.append(json.loads(json.dumps(value)))):
            performance._run_worker(app)
        self.assertEqual(saved[-1]["perf_history"][1], legacy)
        records = saved[-1]["perf_history"][0]["failure_analysis"]
        self.assertEqual(read_analyses(records), result.failure_analysis)
        reloaded = SimpleNamespace(creds=saved[-1])
        performance._ensure_perf_history(reloaded)
        self.assertEqual(reloaded._perf_history, saved[-1]["perf_history"])
        self.assertEqual(read_analyses(legacy.get("failure_analysis")), ())

    def test_broken_serialization_does_not_stop_history_or_inspection_write(self):
        result = PerfResult("TC", "custom", errors=1, failure_analysis=(object(),))
        app = SimpleNamespace(creds={}, _perf_history=[], _perf_target=object(),
            _perf_paths=object(), _perf_profile=LoadProfile())
        with patch.object(service, "run", return_value=result), \
             patch.object(performance, "_logline"), patch.object(performance, "_queue_performance_refresh"), \
             patch("store.save") as save:
            performance._run_worker(app)
        save.assert_called_once()
        self.assertNotIn("failure_analysis", app.creds["perf_history"][0])
        self.assertEqual(app.creds["perf_history"][0]["err"], 0)
        with tempfile.TemporaryDirectory() as directory:
            path = engine.write_inspection_screens(directory, {"failure_analysis": (object(),)})
            self.assertTrue(path)
            self.assertNotIn("failure_analysis", json.loads(Path(path).read_text(encoding="utf-8")))

    def test_existing_perf_model_constructor_and_asdict_remain_json_compatible(self):
        legacy = {"scenario_id": "old", "target": "jmeter", "samples": 1, "errors": 0}
        result = PerfResult(**legacy)
        self.assertEqual(json.loads(json.dumps(asdict(result)))["failure_analysis"], [])
        self.assertEqual(optional_analysis_fields(result.failure_analysis), {})


if __name__ == "__main__":
    unittest.main()
