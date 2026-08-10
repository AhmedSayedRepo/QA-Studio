"""perf/models.py - normalized performance-testing domain model.

Pure data. No I/O, no tool formats, no Flet, no tracker imports. Mirrors the
"one rule" of tracker/models.py: the core speaks ONLY these types; every load
tool's format lives behind an adapter (perf/targets/*) and never leaks here.

All types are frozen dataclasses - they cross thread boundaries (local + remote
run workers), and immutability keeps that safe (same rationale as tracker/models).

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class PerfCapability(str, Enum):
    """What a PerfTarget can do; the UI capability-gates on these (same idea as
    the tracker Capability flags)."""
    THRESHOLDS = "thresholds"        # honors LoadProfile.thresholds as pass/fail
    DISTRIBUTED = "distributed"      # multi-node / multi-worker load
    CLOUD = "cloud"                  # runs on a managed cloud service
    CSV_DATA = "csv_data"            # data-driven from an external CSV
    BROWSER = "browser"              # browser-level virtual users


class AssertionKind(str, Enum):
    STATUS = "status"                # response status == value (e.g. "200")
    MAX_LATENCY = "max_latency"      # response time <= value (ms)
    BODY_CONTAINS = "body_contains"  # response body contains value


@dataclass(frozen=True)
class Assertion:
    kind: AssertionKind
    value: str


@dataclass(frozen=True)
class Extraction:
    """Correlate a value out of a response into a variable for later requests,
    e.g. json_path='$.token' -> var 'authToken'."""
    var: str
    json_path: str = ""              # JSON extractor path
    regex: str = ""                  # OR a regex (capture group 1)


@dataclass(frozen=True)
class PerfRequest:
    """One protocol interaction (or a pure wait when url is empty)."""
    method: str = "GET"
    url: str = ""                    # may be templated: https://{{host}}/api/login
    headers: Dict[str, str] = field(default_factory=dict)
    body: str = ""                   # payload template (${email})
    assertions: List[Assertion] = field(default_factory=list)
    extractions: List[Extraction] = field(default_factory=list)
    think_ms: int = 0                # pacing AFTER this request
    source_step: str = ""            # traceability back to the functional Step

    @property
    def is_wait(self) -> bool:
        return not (self.url or "").strip()


@dataclass(frozen=True)
class PerfScenario:
    """One user journey == one functional TestCase. `id` is the SAME identity as
    the source TestCase ref, so a perf run stays traceable to the requirement."""
    id: str
    title: str
    requests: List[PerfRequest] = field(default_factory=list)
    variables: List[str] = field(default_factory=list)   # {{names}} a CSV must supply
    story_id: str = ""

    @property
    def request_count(self) -> int:
        return sum(1 for r in self.requests if not r.is_wait)


@dataclass(frozen=True)
class DataSource:
    """A user-uploaded CSV that feeds `variables` with real data (accounts,
    search terms, ids). `sensitive_columns` are NOT written into the emitted
    project; they are resolved from store.py / the worker credential path."""
    csv_path: str
    columns: List[str] = field(default_factory=list)
    recycle: bool = True             # loop back to the top at EOF
    share_mode: str = "all"          # "all" (shared) | "thread"
    sensitive_columns: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class LoadProfile:
    """HOW to drive scenarios - set by the user, never extracted."""
    users: int = 10
    ramp_up_s: int = 30
    duration_s: int = 300
    pacing_ms: int = 0
    thresholds: Dict[str, float] = field(default_factory=dict)  # {"p95_ms":800,"error_rate":0.01}

    def passed(self, result: "PerfResult") -> Optional[bool]:
        """Evaluate thresholds against a result. None when no thresholds set."""
        if not self.thresholds:
            return None
        ok = True
        if "p95_ms" in self.thresholds:
            ok = ok and result.p95_ms <= self.thresholds["p95_ms"]
        if "p99_ms" in self.thresholds:
            ok = ok and result.p99_ms <= self.thresholds["p99_ms"]
        if "error_rate" in self.thresholds:
            ok = ok and result.error_rate <= self.thresholds["error_rate"]
        if "min_throughput_rps" in self.thresholds:
            ok = ok and result.throughput_rps >= self.thresholds["min_throughput_rps"]
        return ok


@dataclass(frozen=True)
class RequestStat:
    label: str
    samples: int = 0
    errors: int = 0
    avg_ms: float = 0.0
    p95_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0

    @property
    def error_rate(self) -> float:
        return (self.errors / self.samples) if self.samples else 0.0


@dataclass(frozen=True)
class FailureGroup:
    """Why some requests failed, aggregated: a response code + message (or an
    assertion's failure message) and how many samples hit it, for one request."""
    label: str
    code: str = ""
    message: str = ""
    count: int = 0


@dataclass(frozen=True)
class PerfResult:
    """Normalized run outcome - every tool's output is mapped onto this so
    reporting/export (report.py) is tool-agnostic."""
    scenario_id: str
    target: str
    samples: int = 0
    errors: int = 0
    duration_s: float = 0.0
    p50_ms: float = 0.0
    p90_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    avg_ms: float = 0.0
    throughput_rps: float = 0.0
    per_request: List[RequestStat] = field(default_factory=list)
    failures: List[FailureGroup] = field(default_factory=list)
    threshold_pass: Optional[bool] = None
    raw_report_dir: str = ""

    @property
    def error_rate(self) -> float:
        return (self.errors / self.samples) if self.samples else 0.0


__all__ = [
    "PerfCapability", "AssertionKind", "Assertion", "Extraction", "PerfRequest",
    "PerfScenario", "DataSource", "LoadProfile", "RequestStat", "FailureGroup",
    "PerfResult",
]
