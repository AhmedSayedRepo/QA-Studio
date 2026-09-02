"""Runner adapters. Aggregate existing facts only; never change execution policy."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import uuid

from . import analyze_failure, serialize_analyses
from .collector import utc_now
from .models import DiagnosticAttribute as Attr, EvidenceType, FailureEvidence, LocatorInfo
from .observability import log_event


def optional_analysis_fields(records):
    """Safe additive JSON field for existing history/inspection report writers."""
    try:
        data = serialize_analyses(records)
        return {"failure_analysis": data} if data else {}
    except Exception:
        log_event("serialization_error")
        return {}


def record_inspection_failure(records, *, run_id, execution_id, story, case,
                              intent, step_ids, kind, driver, captures,
                              exception=None, duration_ms=None, secrets=(), attempted_locator=None):
    """Called after the inspector's existing unresolved-step bookkeeping.

    DOM evidence references the latest already-created capture, labeled as prior
    state. Browser disconnection is normal in failures; metadata is best effort.
    """
    try:
        url = None
        runtime = [Attr("runtime", "selenium-inspection"), Attr("story_id", str(story.get("id", ""))),
                   Attr("scope", "inspection_step"), Attr("unresolved_kind", kind)]
        try:
            url = driver.current_url
        except Exception:
            pass
        try:
            caps = driver.capabilities or {}
            runtime.extend(Attr(k, caps[k]) for k in ("browserName", "browserVersion", "platformName") if k in caps)
        except Exception:
            pass
        stamp = utc_now()
        evidence = []
        if captures:
            # Scope the reference to this case; a preceding case is not evidence.
            last = captures[-1]
            if last.get("story") == story.get("id", "") and last.get("test_case") == case.get("title", ""):
                evidence.append(FailureEvidence("dom", EvidenceType.DOM_SNAPSHOT,
                    "inspection", stamp, reference=f"inspection-screens.json#/screen_captures/{len(captures)-1}",
                    metadata=(Attr("relation", "last available capture; not a new failure snapshot"),)))
        locator = attempted_locator or intent.get("live_locator") or {}
        target = intent.get("target") or ""
        # Typed values can appear inside browser exception text; never retain them.
        private_values = tuple(secrets)
        if intent.get("verb") == "type" and intent.get("value"):
            private_values += (intent["value"],)
        record = analyze_failure(
            execution_id=execution_id, run_id=run_id,
            test_case_id=case.get("id"), test_step_id=",".join(map(str, step_ids)) or None,
            test_name=case.get("title", ""), action=intent.get("verb") or intent.get("role", ""),
            locator=LocatorInfo(locator.get("by", ""), locator.get("value", ""), target),
            exception=exception, error_message=f"Unresolved inspection step: {kind}.",
            timestamp=stamp, url=url, runtime=tuple(runtime), attempt=1,
            duration_ms=duration_ms, evidence=tuple(evidence), secrets=private_values)
        if record is not None:
            records.append(record)
    except Exception:
        log_event("inspection_adapter_error")


def diagnose_jmeter(result, jtl_path, details):
    """Associate diagnostics with the existing top-30 failure groups.

    A group is not an individual test case: preserve the scenario id only if the
    parser caller supplied it. Reference the first failed CSV row for each group.
    """
    if not result.errors:
        return result
    try:
        records = []
        run_id = uuid.uuid4().hex
        for index, group in enumerate(result.failures):
            detail = details.get((group.label, group.code, group.message), {})
            stamp = utc_now()
            try:
                if detail.get("timestamp"):
                    stamp = datetime.fromtimestamp(float(detail["timestamp"]) / 1000, timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError, OSError):
                pass
            code = group.code
            prefix = "Non HTTP response code: "
            error_type = code[len(prefix):].strip() if code.startswith(prefix) else ""
            evidence = (FailureEvidence("jtl", EvidenceType.EXECUTION_LOG,
                "jmeter", stamp, reference=jtl_path + "#row=" + str(detail.get("row", "")),
                metadata=(Attr("failure_count", group.count), Attr("response_code", code),
                          Attr("message_source", detail.get("message_source", "unknown")))),)
            record = analyze_failure(
                execution_id=f"{run_id}:group:{index + 1}", run_id=run_id,
                test_case_id=result.scenario_id or None, test_name=group.label,
                action="jmeter.sample", error_type=error_type, error_message=group.message,
                timestamp=stamp, url=detail.get("url"), duration_ms=detail.get("elapsed"),
                runtime=(Attr("runtime", "jmeter"), Attr("scope", "failure_group")), evidence=evidence)
            if record is not None:
                records.append(record)
        return replace(result, failure_analysis=tuple(records))
    except Exception:
        log_event("jmeter_adapter_error")
        return result


def diagnose_performance(result, profile):
    """Cover threshold-only failures and emit-only/third-party target results.

    Latency threshold breaches are NOT inferred timeouts. With no typed runtime
    error, the default analyzer must return UNKNOWN.
    """
    if not result.errors and result.threshold_pass is not False:
        return result
    try:
        records = list(result.failure_analysis)
        if records and result.threshold_pass is not False:
            return result
        run_id = records[0].context.run_id if records else uuid.uuid4().hex
        threshold = result.threshold_pass is False
        record = analyze_failure(
            execution_id=run_id + (":thresholds" if threshold else ":aggregate"), run_id=run_id,
            test_case_id=result.scenario_id or None, test_name=result.scenario_id,
            action="evaluate_thresholds" if threshold else "run",
            error_message="Performance thresholds failed." if threshold else "The target reported failed samples.",
            duration_ms=result.duration_s * 1000,
            runtime=(Attr("runtime", result.target), Attr("scope", "run"),
                     Attr("errors", result.errors), Attr("p95_ms", result.p95_ms),
                     Attr("p99_ms", result.p99_ms), Attr("error_rate", result.error_rate),
                     Attr("throughput_rps", result.throughput_rps)) + tuple(
                         Attr("threshold_" + str(k), v) for k, v in profile.thresholds.items()))
        if record is not None:
            records.append(record)
        return replace(result, failure_analysis=tuple(records))
    except Exception:
        log_event("performance_adapter_error")
        return result
