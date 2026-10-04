"""Aggregate facts already produced by a runner. No browser, network or file I/O."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math
import traceback
from itertools import islice

from .models import (DiagnosticAttribute, EvidenceType, FailureAnalysisContext,
                     FailureEvidence, LocatorInfo)
from .observability import log_event
from .privacy import REDACTED, safe_url, sanitize_text, sensitive_name


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FailureCollector:
    def collect(self, *, execution_id: str, run_id: str, test_case_id=None,
                test_step_id=None, test_name="", action="", locator=None,
                exception=None, error_type="", error_message="", timestamp=None,
                url=None, runtime=(), attempt=None, duration_ms=None, evidence=(),
                secrets=()) -> FailureAnalysisContext | None:
        """None means diagnostics failed; the caller must retain its original result."""
        try:
            clean = lambda s: sanitize_text(s, tuple(secrets))
            stamp = clean(timestamp or utc_now())

            def attrs(items):
                return tuple(DiagnosticAttribute(clean(a.name),
                    REDACTED if sensitive_name(a.name) else
                    clean(a.value) if isinstance(a.value, str) else
                    a.value if a.value is None or isinstance(a.value, (bool, int)) else
                    a.value if isinstance(a.value, float) and math.isfinite(a.value) else None)
                    for a in islice(items, 32))

            stack = None
            if exception is not None:
                cls = type(exception)
                error_type = cls.__module__ + "." + cls.__qualname__
                error_message = str(exception)
                # No source lines/locals: they may contain password literals.
                frames = traceback.extract_tb(exception.__traceback__)
                if frames:
                    stack = clean("\n".join(
                        f"{f.filename}:{f.lineno} in {f.name}" for f in frames[-20:]))
            raw = list(islice(evidence, 64))
            if error_type or error_message:
                raw.append(FailureEvidence("exception", EvidenceType.EXCEPTION,
                    "runner", stamp, clean(error_type), metadata=(
                        DiagnosticAttribute("message", clean(error_message)),)))
            if stack:
                raw.append(FailureEvidence("stack", EvidenceType.STACK_TRACE, "runner", stamp, stack))
            if locator:
                locator = LocatorInfo(clean(locator.strategy), clean(locator.value), clean(locator.target))
                raw.append(FailureEvidence("locator", EvidenceType.LOCATOR, "runner", stamp,
                    locator.value, metadata=(DiagnosticAttribute("strategy", locator.strategy),)))
            if duration_ms is not None:
                if not math.isfinite(duration_ms) or duration_ms < 0:
                    duration_ms = None
                else:
                    raw.append(FailureEvidence("timing", EvidenceType.TIMING, "runner", stamp,
                        metadata=(DiagnosticAttribute("duration_ms", duration_ms),)))
            normalized = []
            seen = set()
            for item in raw:
                if not isinstance(item, FailureEvidence):
                    continue
                # Logs, DOM, screenshots and request payloads stay in their existing
                # artifacts. Never embed their contents (or binary data).
                reference_only = item.type in {
                    EvidenceType.SCREENSHOT, EvidenceType.DOM_SNAPSHOT,
                    EvidenceType.CONSOLE_LOG, EvidenceType.EXECUTION_LOG,
                    EvidenceType.REQUEST_RESPONSE}
                ref = clean(item.reference) if isinstance(item.reference, str) else None
                if ref and (item.reference.lstrip().lower().startswith("data:") or len(item.reference) > 4096):
                    ref = None
                val = None if reference_only else clean(item.value) if isinstance(item.value, str) else None
                meta = attrs(item.metadata)
                if not ref and val is None and not meta:
                    continue
                signature = (item.type, clean(item.source), val, ref, meta)
                if signature in seen:
                    continue
                seen.add(signature)
                normalized.append(replace(item, evidence_id=f"e{len(normalized) + 1}",
                    source=signature[1], timestamp=clean(item.timestamp or stamp),
                    value=val, reference=ref, metadata=meta))
            return FailureAnalysisContext(
                execution_id=clean(execution_id), run_id=clean(run_id),
                test_case_id=clean(str(test_case_id)) if test_case_id is not None else None,
                test_step_id=clean(str(test_step_id)) if test_step_id is not None else None,
                test_name=clean(test_name), action=clean(action), locator=locator,
                error_type=clean(error_type), error_message=clean(error_message),
                stack_trace=stack, timestamp=stamp, url=clean(safe_url(url)) if url else None,
                runtime=attrs(runtime), attempt=attempt if isinstance(attempt, int) and attempt > 0 else None,
                duration_ms=duration_ms, evidence=tuple(normalized))
        except Exception:
            # Deliberately omit unnormalized identifiers and raw errors here.
            log_event("collector_error")
            return None
