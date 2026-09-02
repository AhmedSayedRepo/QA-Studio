"""Explicit registration with deterministic order and isolated plugin failures."""
from dataclasses import replace
import re
from threading import RLock

from .models import AnalysisResult, FailureCategory, Severity
from .observability import log_event
from .ports import FailureAnalyzer


class AnalyzerRegistry:
    def __init__(self, analyzers=()):
        self._analyzers = {}
        self._lock = RLock()
        for analyzer in analyzers:
            self.register(analyzer)

    def register(self, analyzer: FailureAnalyzer):
        if not isinstance(analyzer, FailureAnalyzer):
            raise TypeError("An analyzer must implement FailureAnalyzer")
        key = analyzer.analyzer_id
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", key):
            raise ValueError("Invalid analyzer identifier")
        with self._lock:
            if key in self._analyzers:
                raise ValueError("Analyzer identifier is already registered")
            self._analyzers[key] = analyzer

    def analyze(self, context):
        with self._lock:
            analyzers = tuple(self._analyzers.items())
        findings = []
        for key, analyzer in analyzers:
            try:
                if not analyzer.supports(context):
                    continue
                result = analyzer.analyze(context)
                if not isinstance(result, AnalysisResult):
                    raise TypeError("Invalid analyzer result")
                if not isinstance(result.category, FailureCategory) or not isinstance(result.severity, Severity):
                    raise TypeError("Invalid analyzer taxonomy")
                if (not isinstance(result.explanation, str) or
                        result.probable_cause is not None and not isinstance(result.probable_cause, str) or
                        not isinstance(analyzer.version, str) or
                        not isinstance(result.recommendations, (tuple, list)) or
                        not all(isinstance(item, str) for item in result.recommendations) or
                        not isinstance(result.supporting_evidence, (tuple, list)) or
                        not all(isinstance(item, str) for item in result.supporting_evidence)):
                    raise TypeError("Invalid analyzer result fields")
                if not set(result.supporting_evidence).issubset({e.evidence_id for e in context.evidence}):
                    raise ValueError("Analyzer referenced unavailable evidence")
                result = replace(result, analyzer_id=key, analyzer_version=analyzer.version)
                if result.category != FailureCategory.UNKNOWN:
                    findings.append(result)
                log_event("analyzed", run_id=context.run_id, execution_id=context.execution_id,
                          analyzer_id=key, category=result.category.value)
            except Exception:
                log_event("analyzer_error", run_id=context.run_id,
                          execution_id=context.execution_id, analyzer_id=key)
        if not findings:
            log_event("analyzed", run_id=context.run_id, execution_id=context.execution_id,
                      analyzer_id="registry", category="unknown")
        return tuple(findings) or (AnalysisResult(),)
