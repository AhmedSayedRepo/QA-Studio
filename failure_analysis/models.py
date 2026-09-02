"""Failure diagnostics, independent of runners, providers, UI, and storage.

Frozen dataclasses/string enums follow tracker/models.py and perf/models.py.
Missing facts remain None; evidence references point to existing artifacts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from enum import Enum
import math
from typing import Optional, Union


class FailureCategory(str, Enum):
    LOCATOR = "locator"
    TIMEOUT = "timeout"
    ASSERTION = "assertion"
    NAVIGATION = "navigation"
    NETWORK = "network"
    BACKEND = "backend"
    AUTHENTICATION = "authentication"
    ENVIRONMENT = "environment"
    DATA = "data"
    UNKNOWN = "unknown"


class EvidenceType(str, Enum):
    SCREENSHOT = "screenshot"
    CONSOLE_LOG = "console_log"
    EXECUTION_LOG = "execution_log"
    STACK_TRACE = "stack_trace"
    NETWORK_FAILURE = "network_failure"
    DOM_SNAPSHOT = "dom_snapshot"
    REQUEST_RESPONSE = "request_response"
    BROWSER_STATE = "browser_state"
    LOCATOR = "locator"
    TIMING = "timing"
    EXCEPTION = "exception"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DiagnosticAttribute:
    name: str
    value: Union[str, int, float, bool, None]


@dataclass(frozen=True)
class LocatorInfo:
    strategy: str = ""
    value: str = ""
    target: str = ""


@dataclass(frozen=True)
class FailureEvidence:
    evidence_id: str
    type: EvidenceType
    source: str
    timestamp: str
    value: Optional[str] = None
    reference: Optional[str] = None
    metadata: tuple[DiagnosticAttribute, ...] = ()


@dataclass(frozen=True)
class FailureAnalysisContext:
    execution_id: str
    run_id: str
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    test_name: str = ""
    action: str = ""
    locator: Optional[LocatorInfo] = None
    error_type: str = ""
    error_message: str = ""
    stack_trace: Optional[str] = None
    timestamp: str = ""
    url: Optional[str] = None
    runtime: tuple[DiagnosticAttribute, ...] = ()
    attempt: Optional[int] = None
    duration_ms: Optional[float] = None
    evidence: tuple[FailureEvidence, ...] = ()


@dataclass(frozen=True)
class AnalysisResult:
    category: FailureCategory = FailureCategory.UNKNOWN
    probable_cause: Optional[str] = None
    confidence: Optional[float] = None
    severity: Severity = Severity.UNKNOWN
    explanation: str = "Insufficient evidence to classify this failure."
    supporting_evidence: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    analyzer_id: str = "registry"
    analyzer_version: Optional[str] = None

    def __post_init__(self):
        if self.confidence is not None and (
                not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1):
            raise ValueError("confidence must be finite and between zero and one")


@dataclass(frozen=True)
class FailureAnalysis:
    context: FailureAnalysisContext
    results: tuple[AnalysisResult, ...]
    schema_version: int = 1

    def to_dict(self) -> dict:
        # JSON roundtrip produces lists and strings, never Enum/dataclass objects.
        # Reapply privacy at the persistence boundary, including plugin output.
        import json
        from .privacy import sanitize_tree
        return sanitize_tree(json.loads(json.dumps(asdict(self), allow_nan=False)))

    @classmethod
    def from_dict(cls, data: dict) -> FailureAnalysis:
        """Read v1 plus additive fields. Unknown taxonomy values fall back safely.

        A future incompatible schema raises; read_analyses isolates that record.
        Historical results without this optional field need no migration.
        """
        from .privacy import sanitize_tree
        data = sanitize_tree(data)
        if data.get("schema_version", 1) != 1:
            raise ValueError("Unsupported failure-analysis schema")

        def enum(kind, value):
            try:
                return kind(value)
            except (ValueError, TypeError):
                return kind.UNKNOWN

        def attrs(items):
            return tuple(DiagnosticAttribute(**known(DiagnosticAttribute, item)) for item in items or [])

        def known(kind, item):
            return {f.name: item[f.name] for f in fields(kind) if f.name in item}

        context = known(FailureAnalysisContext, data["context"])
        context["runtime"] = attrs(context.get("runtime"))
        if context.get("locator") is not None:
            context["locator"] = LocatorInfo(**known(LocatorInfo, context["locator"]))
        evidence = []
        for raw in context.get("evidence", []):
            item = known(FailureEvidence, raw)
            item["type"] = enum(EvidenceType, item.get("type"))
            item["metadata"] = attrs(item.get("metadata"))
            evidence.append(FailureEvidence(**item))
        context["evidence"] = tuple(evidence)
        results = []
        for raw in data.get("results", []):
            item = known(AnalysisResult, raw)
            item["category"] = enum(FailureCategory, item.get("category"))
            item["severity"] = enum(Severity, item.get("severity"))
            for key in ("supporting_evidence", "recommendations"):
                item[key] = tuple(item.get(key) or [])
            results.append(AnalysisResult(**item))
        return cls(FailureAnalysisContext(**context), tuple(results) or (AnalysisResult(),))
