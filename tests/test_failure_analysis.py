"""Deterministic failure-domain tests; no browsers, providers, or network calls."""
from __future__ import annotations

import json
import math
import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

from failure_analysis import analyze_failure, read_analyses, serialize_analyses
from failure_analysis.collector import FailureCollector
from failure_analysis.models import (
    AnalysisResult, DiagnosticAttribute, EvidenceType, FailureAnalysis,
    FailureAnalysisContext, FailureCategory, FailureEvidence, LocatorInfo, Severity,
)
from failure_analysis.observability import log_event
from failure_analysis.ports import FailureAnalyzer
from failure_analysis.privacy import REDACTED, sanitize_text, sanitize_tree
from failure_analysis.registry import AnalyzerRegistry
from failure_analysis.timeout import TimeoutAnalyzer


STAMP = "2026-08-31T12:00:00+00:00"


def context(**changes):
    return replace(FailureAnalysisContext("execution-1", "run-1", timestamp=STAMP), **changes)


def evidence(kind=EvidenceType.EXCEPTION, **changes):
    return replace(FailureEvidence("original-id", kind, "runner", STAMP,
                                   value="builtins.TimeoutError"), **changes)


class StubAnalyzer(FailureAnalyzer):
    analyzer_id = "stub"
    version = "3"

    def __init__(self, *, finding=None, support=True, fail_in=None):
        self.finding = finding if finding is not None else AnalysisResult(
            category=FailureCategory.ASSERTION, severity=Severity.ERROR,
            explanation="An explicit assertion failed.")
        self.support = support
        self.fail_in = fail_in
        self.calls = []

    def supports(self, supplied):
        self.calls.append(("supports", supplied))
        if self.fail_in == "supports":
            raise RuntimeError("password=plugin-secret")
        return self.support

    def analyze(self, supplied):
        self.calls.append(("analyze", supplied))
        if self.fail_in == "analyze":
            raise RuntimeError("password=plugin-secret")
        return self.finding


class FailureTestCase(unittest.TestCase):
    def setUp(self):
        # Do not create diagnostics.log in either the checkout or staged source.
        sink = patch("diag_log.log")
        self.log = sink.start()
        self.addCleanup(sink.stop)


class DomainTests(FailureTestCase):
    def test_context_is_typed_immutable_and_missing_facts_stay_absent(self):
        supplied = context()
        self.assertIsNone(supplied.test_step_id)
        self.assertIsNone(supplied.attempt)
        self.assertIsNone(supplied.duration_ms)
        self.assertEqual(supplied.evidence, ())
        with self.assertRaises(FrozenInstanceError):
            supplied.run_id = "changed"

    def test_unknown_is_available_in_every_fallback_taxonomy(self):
        for taxonomy in (FailureCategory, EvidenceType, Severity):
            with self.subTest(taxonomy=taxonomy):
                self.assertEqual(taxonomy.UNKNOWN.value, "unknown")
        self.assertEqual(AnalysisResult().category, FailureCategory.UNKNOWN)
        self.assertIsNone(AnalysisResult().probable_cause)

    def test_analysis_confidence_rejects_nonfinite_and_out_of_range_values(self):
        for confidence in (-0.01, 1.01, math.inf, -math.inf, math.nan):
            with self.subTest(confidence=confidence):
                with self.assertRaises(ValueError):
                    AnalysisResult(confidence=confidence)
        for confidence in (None, 0, 0.25, 1):
            self.assertEqual(AnalysisResult(confidence=confidence).confidence, confidence)

    def test_analyzer_contract_requires_both_operations(self):
        with self.assertRaises(TypeError):
            FailureAnalyzer()

        class Incomplete(FailureAnalyzer):
            def supports(self, supplied):
                return True

        with self.assertRaises(TypeError):
            Incomplete()


class CollectorTests(FailureTestCase):
    def setUp(self):
        super().setUp()
        self.collector = FailureCollector()

    def collect(self, **facts):
        return self.collector.collect(execution_id="execution-1", run_id="run-1",
                                      timestamp=STAMP, **facts)

    def test_builds_context_using_supplied_ids_runtime_locator_and_timing(self):
        actual = self.collect(
            test_case_id=123, test_step_id=4, test_name="Open checkout",
            action="click", locator=LocatorInfo("css", "#pay", "Pay button"),
            error_type="builtins.TimeoutError", error_message="Deadline exceeded",
            url="https://example.test/checkout", attempt=2, duration_ms=123.5,
            runtime=(DiagnosticAttribute("browser", "chromium"),))
        self.assertIsInstance(actual, FailureAnalysisContext)
        self.assertEqual((actual.execution_id, actual.run_id), ("execution-1", "run-1"))
        self.assertEqual((actual.test_case_id, actual.test_step_id), ("123", "4"))
        self.assertEqual(actual.timestamp, STAMP)
        self.assertEqual(actual.action, "click")
        self.assertEqual(actual.locator.value, "#pay")
        self.assertEqual(actual.runtime, (DiagnosticAttribute("browser", "chromium"),))
        self.assertEqual((actual.attempt, actual.duration_ms), (2, 123.5))
        self.assertEqual({item.type for item in actual.evidence},
                         {EvidenceType.EXCEPTION, EvidenceType.LOCATOR, EvidenceType.TIMING})

    def test_generates_utc_timestamp_when_runner_does_not_supply_one(self):
        from datetime import datetime
        actual = self.collector.collect(execution_id="e", run_id="r")
        self.assertEqual(datetime.fromisoformat(actual.timestamp).utcoffset().total_seconds(), 0)

    def test_normalizes_deduplicates_and_reassigns_evidence_ids(self):
        original = evidence(value="timeout", timestamp="")
        duplicate = replace(original, evidence_id="duplicate-id", timestamp=STAMP)
        different = replace(original, source="network")
        actual = self.collect(evidence=(original, duplicate, different, object()))
        self.assertEqual(len(actual.evidence), 2)
        self.assertEqual([item.evidence_id for item in actual.evidence], ["e1", "e2"])
        self.assertEqual([item.timestamp for item in actual.evidence], [STAMP, STAMP])
        self.assertEqual(original.evidence_id, "original-id")

    def test_binary_and_large_artifact_sources_only_keep_existing_references(self):
        reference_types = (EvidenceType.SCREENSHOT, EvidenceType.DOM_SNAPSHOT,
                           EvidenceType.CONSOLE_LOG, EvidenceType.EXECUTION_LOG,
                           EvidenceType.REQUEST_RESPONSE)
        inputs = tuple(evidence(kind, value="private raw content", reference=f"artifacts/{kind.value}")
                       for kind in reference_types)
        inputs += (evidence(EvidenceType.SCREENSHOT, value=b"raw image bytes"),)
        with patch("builtins.open", side_effect=AssertionError("collector must not access files")):
            actual = self.collect(evidence=inputs)
        self.assertEqual(len(actual.evidence), len(reference_types))
        for item in actual.evidence:
            self.assertIsNone(item.value)
            self.assertTrue(item.reference.startswith("artifacts/"))

    def test_inline_data_urls_and_oversized_artifact_references_are_not_retained(self):
        actual = self.collect(evidence=(
            evidence(EvidenceType.SCREENSHOT, reference="data:image/png;base64,SECRETBINARY"),
            evidence(EvidenceType.SCREENSHOT, reference="x" * 4097),
            evidence(EvidenceType.EXCEPTION, value=b"binary exception payload"),
        ))
        self.assertEqual(actual.evidence, ())

    def test_data_uri_scheme_case_and_whitespace_cannot_embed_binary_as_reference(self):
        for reference in ("DATA:image/png;base64,PRIVATEBINARY",
                          "  data:image/png;base64,PRIVATEBINARY"):
            with self.subTest(reference=reference):
                actual = self.collect(evidence=(evidence(EvidenceType.SCREENSHOT,
                                                         reference=reference),))
                self.assertEqual(actual.evidence, ())

    def test_metadata_is_bounded_and_unsupported_values_are_omitted(self):
        items = tuple(DiagnosticAttribute(f"field_{i}", i) for i in range(40))
        actual = self.collect(runtime=items, evidence=(evidence(metadata=(
            DiagnosticAttribute("opaque", object()), DiagnosticAttribute("nonfinite", math.inf),
            DiagnosticAttribute("valid", True))),))
        self.assertEqual(len(actual.runtime), 32)
        values = {item.name: item.value for item in actual.evidence[0].metadata}
        self.assertEqual(values, {"opaque": None, "nonfinite": None, "valid": True})

    def test_invalid_timing_and_attempt_do_not_invent_measurements(self):
        for duration in (-1, math.inf, math.nan):
            with self.subTest(duration=duration):
                actual = self.collect(duration_ms=duration, attempt=0)
                self.assertIsNone(actual.duration_ms)
                self.assertIsNone(actual.attempt)
                self.assertFalse(any(item.type == EvidenceType.TIMING for item in actual.evidence))

    def test_exception_identity_and_stack_exclude_source_and_local_values(self):
        local_password = "local-only-secret-sentinel"
        try:
            raise TimeoutError("deadline exceeded")  # source-only-secret-sentinel
        except TimeoutError as caught:
            actual = self.collect(exception=caught)
        self.assertEqual(actual.error_type, "builtins.TimeoutError")
        self.assertEqual(actual.error_message, "deadline exceeded")
        self.assertIn("test_exception_identity_and_stack_exclude_source_and_local_values", actual.stack_trace)
        serialized = json.dumps(FailureAnalysis(actual, (AnalysisResult(),)).to_dict())
        self.assertNotIn(local_password, serialized)
        self.assertNotIn("source-only-secret-sentinel", serialized)
        self.assertNotIn('raise TimeoutError', actual.stack_trace)

    def test_redacts_supplied_secrets_in_every_collected_text_surface(self):
        secret = "known-private-value"
        actual = self.collect(
            test_case_id=secret, test_step_id=secret, test_name=secret, action=secret,
            locator=LocatorInfo("css", secret, secret), error_message=secret,
            url=f"https://example.test/page?unfamiliar={secret}#fragment",
            runtime=(DiagnosticAttribute("password", "other-private-value"),
                     DiagnosticAttribute("browser", secret)),
            evidence=(evidence(value=secret, reference=f"artifacts/{secret}",
                               metadata=(DiagnosticAttribute("Authorization", "Bearer third-private"),)),),
            secrets=(secret,))
        serialized = json.dumps(FailureAnalysis(actual, (AnalysisResult(),)).to_dict())
        for private in (secret, "other-private-value", "third-private", "fragment"):
            self.assertNotIn(private, serialized)
        self.assertEqual(actual.url, "https://example.test/page")

    def test_collection_errors_are_isolated_without_logging_exception_contents(self):
        class Unprintable(Exception):
            def __str__(self):
                raise RuntimeError("sensitive failure detail")

        self.assertIsNone(self.collect(exception=Unprintable()))
        self.assertIn("collector_error", str(self.log.call_args_list))
        self.assertNotIn("sensitive failure detail", str(self.log.call_args_list))


class PrivacyTests(FailureTestCase):
    def test_repeated_serialization_does_not_accumulate_redaction_markers(self):
        for raw in ('password: unquoted multiple words', '{"password": "space secret"}',
                    'X-Amz-Signature: signing value', 'api_key=private'):
            with self.subTest(raw=raw):
                once = sanitize_text(raw)
                self.assertEqual(sanitize_text(once), once)
                record = analyze_failure(execution_id="e", run_id="r", error_message=raw)
                self.assertEqual(FailureAnalysis.from_dict(record.to_dict()), record)

    def test_non_http_url_and_inline_payload_are_removed_at_all_model_boundaries(self):
        for url in ("ftp://user:PRIVATEPASSWORD@example.test/folder",
                    "data:text/plain;base64,cGFzc3dvcmQ9eA=="):
            with self.subTest(url=url):
                record = analyze_failure(execution_id="e", run_id="r", url=url)
                self.assertIsNotNone(record)
                self.assertEqual(record.context.url, REDACTED)
                manual = FailureAnalysis(context(url=url), (AnalysisResult(),))
                self.assertEqual(manual.to_dict()["context"]["url"], REDACTED)
                raw = manual.to_dict()
                raw["context"]["url"] = url
                self.assertEqual(FailureAnalysis.from_dict(raw).context.url, REDACTED)

    def test_known_typed_values_cannot_rename_required_schema_fields(self):
        for secret in ("run", "id", "context"):
            with self.subTest(secret=secret):
                record = analyze_failure(execution_id="execution-1", run_id="run-1",
                    exception=TimeoutError("expired"), secrets=(secret,))
                self.assertIsNotNone(record)
                self.assertEqual(record.results[0].category, FailureCategory.TIMEOUT)
                self.assertIn("execution_id", record.to_dict()["context"])

    def test_unquoted_multiword_credentials_are_redacted_to_the_delimiter(self):
        actual = sanitize_text("password=private pass phrase; message=failed")
        self.assertNotIn("private", actual)
        self.assertNotIn("pass phrase", actual)
        self.assertIn("message=failed", actual)

    def test_headers_cookies_assignments_bearer_and_jwt_are_redacted(self):
        examples = (
            ("Authorization: Bearer HEADERSECRET", "HEADERSECRET"),
            ("Cookie: sessionid=COOKIESECRET; other=private", "COOKIESECRET"),
            ('{"password": "space separated secret"}', "space separated secret"),
            ("api_key=ASSIGNSECRET", "ASSIGNSECRET"),
            ("Bearer BEARERSECRET", "BEARERSECRET"),
            ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.signature", "eyJhbGci"),
        )
        for raw, private in examples:
            with self.subTest(raw=raw):
                self.assertNotIn(private, sanitize_text(raw))

    def test_urls_drop_credentials_all_query_parameters_and_fragment(self):
        raw = "failed https://alice:pass@example.test:8443/path?ticket=UNKNOWNSECRET#fragment"
        actual = sanitize_text(raw)
        self.assertEqual(actual, "failed https://example.test:8443/path")

    def test_known_secret_literal_and_url_encoded_variants_are_redacted(self):
        raw = "actual=a b/c ; encoded=a%20b%2Fc"
        actual = sanitize_text(raw, ("a b/c",))
        self.assertNotIn("a b/c", actual)
        self.assertNotIn("a%20b%2Fc", actual)

    def test_recursive_sensitive_keys_and_attribute_values_are_redacted(self):
        actual = sanitize_tree({"nested": {"access_token": "TREESECRET"},
                                "runtime": [{"name": "cookie", "value": "ATTRSECRET"}],
                                "safe": "browser"})
        self.assertEqual(actual["nested"]["access_token"], REDACTED)
        self.assertEqual(actual["runtime"][0]["value"], REDACTED)
        self.assertEqual(actual["safe"], "browser")

    def test_authentication_signatures_are_redacted_in_metadata_and_json(self):
        for name in ("signature", "X-Amz-Signature"):
            with self.subTest(name=name):
                actual = sanitize_tree({name: "SIGNINGSECRET",
                                        "runtime": [{"name": name, "value": "SIGNINGSECRET"}]})
                self.assertNotIn("SIGNINGSECRET", json.dumps(actual))

    def test_untrusted_blobs_are_bounded_and_binary_is_not_serialized(self):
        self.assertLessEqual(len(sanitize_text("x" * 6000)), 4096)
        self.assertIn("omitted", sanitize_text("x" * 70000))
        actual = sanitize_tree({"binary": b"SECRETBYTES", "nan": math.nan,
                                "many": list(range(200))})
        self.assertNotIn("SECRETBYTES", json.dumps(actual))
        self.assertIsNone(actual["nan"])
        self.assertEqual(len(actual["many"]), 128)
        deep = "private"
        for _ in range(20):
            deep = {"nested": deep}
        self.assertIn("depth limit", json.dumps(sanitize_tree(deep)))

    def test_structured_observability_includes_identifiers_without_raw_secrets(self):
        log_event("analyzed", run_id="run-1", execution_id="exec-1",
                  analyzer_id="timeout", category="timeout")
        payload = json.loads(self.log.call_args.args[0].removeprefix("failure_analysis "))
        self.assertEqual(payload, {"event": "analyzed", "run_id": "run-1",
                                   "execution_id": "exec-1", "analyzer_id": "timeout",
                                   "category": "timeout"})
        log_event("collector_error", run_id="password=LOGSECRET")
        self.assertNotIn("LOGSECRET", str(self.log.call_args))
        self.assertEqual(self.log.call_args.kwargs["level"], "warning")

    def test_broken_log_sink_cannot_escape_into_failure_processing(self):
        self.log.side_effect = RuntimeError("logger unavailable")
        log_event("collector_error")
        self.assertIsNotNone(analyze_failure(execution_id="e", run_id="r",
                                            error_type="builtins.TimeoutError"))


class RegistryTests(FailureTestCase):
    def test_registration_rejects_duplicate_ids_and_incompatible_contracts(self):
        registry = AnalyzerRegistry((StubAnalyzer(),))
        with self.assertRaises(ValueError):
            registry.register(StubAnalyzer())
        with self.assertRaises(TypeError):
            registry.register(object())
        invalid = StubAnalyzer()
        invalid.analyzer_id = "invalid id with spaces"
        with self.assertRaises(ValueError):
            registry.register(invalid)

    def test_registry_instances_do_not_share_registered_plugins(self):
        first = AnalyzerRegistry((StubAnalyzer(),))
        second = AnalyzerRegistry()
        self.assertEqual(first.analyze(context())[0].category, FailureCategory.ASSERTION)
        self.assertEqual(second.analyze(context())[0].category, FailureCategory.UNKNOWN)

    def test_unsupported_analyzer_is_not_invoked_and_returns_unknown(self):
        plugin = StubAnalyzer(support=False)
        actual = AnalyzerRegistry((plugin,)).analyze(context())
        self.assertEqual([name for name, _ in plugin.calls], ["supports"])
        self.assertEqual(actual[0].category, FailureCategory.UNKNOWN)

    def test_supports_and_analyze_errors_do_not_block_later_analyzers(self):
        for stage in ("supports", "analyze"):
            with self.subTest(stage=stage):
                broken = StubAnalyzer(fail_in=stage)
                actual = AnalyzerRegistry((broken, TimeoutAnalyzer())).analyze(
                    context(error_type="builtins.TimeoutError"))
                self.assertEqual([item.category for item in actual], [FailureCategory.TIMEOUT])
        self.assertIn("analyzer_error", str(self.log.call_args_list))
        self.assertNotIn("plugin-secret", str(self.log.call_args_list))

    def test_bad_plugin_results_taxonomy_and_missing_evidence_fall_back_unknown(self):
        invalid = (
            {"category": "timeout"},
            AnalysisResult(category="timeout"),
            AnalysisResult(severity="error"),
            AnalysisResult(category=FailureCategory.TIMEOUT, supporting_evidence=("missing",)),
        )
        for finding in invalid:
            with self.subTest(finding=finding):
                actual = AnalyzerRegistry((StubAnalyzer(finding=finding),)).analyze(context())
                self.assertEqual(actual[0].category, FailureCategory.UNKNOWN)

    def test_registry_overrides_plugin_identity_and_preserves_valid_evidence_references(self):
        supplied = context(evidence=(evidence(),))
        result = AnalysisResult(category=FailureCategory.ASSERTION,
                                supporting_evidence=("original-id",), analyzer_id="spoofed",
                                analyzer_version="spoofed")
        actual = AnalyzerRegistry((StubAnalyzer(finding=result),)).analyze(supplied)[0]
        self.assertEqual((actual.analyzer_id, actual.analyzer_version), ("stub", "3"))
        self.assertEqual(actual.supporting_evidence, ("original-id",))

    def test_matching_analyzers_are_returned_in_registration_order(self):
        plugin = StubAnalyzer()
        actual = AnalyzerRegistry((plugin, TimeoutAnalyzer())).analyze(
            context(error_type="builtins.TimeoutError"))
        self.assertEqual([item.analyzer_id for item in actual], ["stub", "timeout"])

    def test_explicit_unknown_plugin_result_does_not_create_a_false_positive(self):
        actual = AnalyzerRegistry((StubAnalyzer(finding=AnalysisResult()),)).analyze(context())
        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0].category, FailureCategory.UNKNOWN)
        self.assertIsNone(actual[0].confidence)


class TimeoutTests(FailureTestCase):
    def test_supported_exception_identities_are_classified_without_root_cause_claim(self):
        analyzer = TimeoutAnalyzer()
        types = ("builtins.TimeoutError", "selenium.common.exceptions.TimeoutException",
                 "playwright._impl._errors.TimeoutError", "java.net.SocketTimeoutException",
                 "java.util.concurrent.TimeoutException", "org.apache.http.conn.ConnectTimeoutException")
        for error_type in types:
            with self.subTest(error_type=error_type):
                supplied = context(error_type=error_type, evidence=(evidence(),))
                self.assertTrue(analyzer.supports(supplied))
                actual = analyzer.analyze(supplied)
                self.assertEqual(actual.category, FailureCategory.TIMEOUT)
                self.assertEqual(actual.confidence, 1.0)
                self.assertIsNone(actual.probable_cause)
                self.assertEqual(actual.supporting_evidence, ("original-id",))

    def test_keywords_or_ambiguous_exception_names_remain_unknown(self):
        for error_type in ("", "TimeoutError", "TimeoutException", "custom.TimeoutError",
                           "builtins.AssertionError", "builtins.timeouterror"):
            with self.subTest(error_type=error_type):
                supplied = context(error_type=error_type, error_message="request timed out: timeout exceeded")
                self.assertFalse(TimeoutAnalyzer().supports(supplied))
                self.assertEqual(TimeoutAnalyzer().analyze(supplied).category, FailureCategory.UNKNOWN)


class FacadeAndSerializationTests(FailureTestCase):
    def test_facade_collects_and_analyzes_a_real_failed_execution(self):
        actual = analyze_failure(execution_id="e", run_id="r", timestamp=STAMP,
                                 exception=TimeoutError("deadline"))
        self.assertIsInstance(actual, FailureAnalysis)
        self.assertEqual(actual.context.error_type, "builtins.TimeoutError")
        self.assertEqual(actual.results[0].category, FailureCategory.TIMEOUT)
        self.assertTrue(actual.results[0].supporting_evidence)

    def test_facade_returns_none_for_collector_failure_or_missing_context(self):
        class Broken:
            def collect(self, **facts):
                raise RuntimeError("password=FACADESECRET")

        class Empty:
            def collect(self, **facts):
                return None

        self.assertIsNone(analyze_failure(collector=Broken()))
        self.assertIsNone(analyze_failure(collector=Empty()))
        self.assertNotIn("FACADESECRET", str(self.log.call_args_list))

    def test_facade_sanitizes_plugin_generated_text_with_run_secrets(self):
        private = "plugin-probable-cause-secret"
        result = AnalysisResult(category=FailureCategory.ASSERTION,
                                probable_cause=private, explanation=private,
                                recommendations=(private,))
        actual = analyze_failure(execution_id="e", run_id="r", secrets=(private,),
                                 analyzers=AnalyzerRegistry((StubAnalyzer(finding=result),)))
        self.assertEqual(actual.results[0].category, FailureCategory.ASSERTION)
        self.assertNotIn(private, json.dumps(actual.to_dict()))

    def test_plugin_with_nonserializable_result_cannot_discard_other_findings(self):
        invalid = AnalysisResult(category=FailureCategory.ASSERTION, explanation=object())
        actual = analyze_failure(execution_id="e", run_id="r", error_type="builtins.TimeoutError",
                                 analyzers=AnalyzerRegistry((StubAnalyzer(finding=invalid),
                                                            TimeoutAnalyzer())))
        self.assertIsNotNone(actual)
        self.assertEqual([result.category for result in actual.results], [FailureCategory.TIMEOUT])

    def test_serialization_roundtrip_preserves_typed_domain_and_safe_evidence(self):
        actual = analyze_failure(execution_id="e", run_id="r", timestamp=STAMP,
                                 locator=LocatorInfo("css", "#checkout"), attempt=2,
                                 duration_ms=40, error_type="builtins.TimeoutError",
                                 runtime=(DiagnosticAttribute("browser", "chromium"),),
                                 evidence=(evidence(EvidenceType.SCREENSHOT,
                                                    reference="artifacts/failure.png"),))
        payload = actual.to_dict()
        decoded = FailureAnalysis.from_dict(json.loads(json.dumps(payload, allow_nan=False)))
        self.assertEqual(actual, decoded)
        self.assertIsInstance(decoded.context.evidence[0].type, EvidenceType)
        self.assertIsInstance(decoded.results[0].category, FailureCategory)
        self.assertIsInstance(decoded.context.runtime[0], DiagnosticAttribute)

    def test_historical_missing_optional_analysis_and_fields_remain_readable(self):
        for historical in (None, {}, "", 0):
            self.assertEqual(read_analyses(historical), ())
        old = {"context": {"execution_id": "old-exec", "run_id": "old-run"}}
        actual = FailureAnalysis.from_dict(old)
        self.assertEqual(actual.context.evidence, ())
        self.assertIsNone(actual.context.attempt)
        self.assertEqual(actual.results[0].category, FailureCategory.UNKNOWN)

    def test_unknown_future_enums_and_additive_fields_have_safe_fallbacks(self):
        data = {"schema_version": 1, "future_top": "ignored",
                "context": {"execution_id": "e", "run_id": "r", "future_context": 1,
                            "locator": {"strategy": "css", "future_locator": True},
                            "evidence": [{"evidence_id": "e1", "type": "future_evidence",
                                          "source": "runner", "timestamp": STAMP,
                                          "value": "fact", "future_evidence_field": 1}]},
                "results": [{"category": "future_category", "severity": "future_severity",
                             "future_result": "ignored"}]}
        actual = FailureAnalysis.from_dict(data)
        self.assertEqual(actual.context.evidence[0].type, EvidenceType.UNKNOWN)
        self.assertEqual(actual.results[0].category, FailureCategory.UNKNOWN)
        self.assertEqual(actual.results[0].severity, Severity.UNKNOWN)

    def test_additive_attribute_fields_do_not_discard_historical_analysis(self):
        data = {"context": {"execution_id": "e", "run_id": "r",
                            "runtime": [{"name": "browser", "value": "chromium", "future": True}],
                            "evidence": [{"evidence_id": "e1", "type": "timing",
                                          "source": "runner", "timestamp": STAMP,
                                          "metadata": [{"name": "duration_ms", "value": 12,
                                                        "future": True}]}]}}
        records = read_analyses([data])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].context.runtime, (DiagnosticAttribute("browser", "chromium"),))
        self.assertEqual(records[0].context.evidence[0].metadata,
                         (DiagnosticAttribute("duration_ms", 12),))

    def test_incompatible_schema_and_malformed_record_do_not_hide_valid_history(self):
        valid = FailureAnalysis(context(), (AnalysisResult(),)).to_dict()
        actual = read_analyses([None, {"schema_version": 99}, valid, {"context": {}}, valid])
        self.assertEqual(len(actual), 2)
        self.assertEqual(actual[0], actual[1])
        with self.assertRaises(ValueError):
            FailureAnalysis.from_dict({"schema_version": 99})

    def test_serialization_isolates_corrupt_records_and_broken_iteration(self):
        valid = FailureAnalysis(context(), (AnalysisResult(),))
        self.assertEqual(serialize_analyses([object(), valid]), [valid.to_dict()])

        def interrupted():
            yield valid
            raise RuntimeError("password=ITERATORSECRET")

        self.assertEqual(serialize_analyses(interrupted()), [valid.to_dict()])
        self.assertNotIn("ITERATORSECRET", str(self.log.call_args_list))

    def test_persistence_boundary_redacts_manually_constructed_sensitive_fields(self):
        record = FailureAnalysis(context(error_message="password=MANUALSECRET",
                                         runtime=(DiagnosticAttribute("Authorization", "Bearer METASECRET"),)),
                                 (AnalysisResult(explanation="api_key=OUTPUTSECRET"),))
        payload = json.dumps(record.to_dict())
        for private in ("MANUALSECRET", "METASECRET", "OUTPUTSECRET"):
            self.assertNotIn(private, payload)


if __name__ == "__main__":
    unittest.main()
