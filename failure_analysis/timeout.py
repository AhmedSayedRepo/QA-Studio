"""One deterministic analyzer. Exception identity, never message keyword guessing."""
from .models import AnalysisResult, EvidenceType, FailureCategory, Severity
from .ports import FailureAnalyzer


class TimeoutAnalyzer(FailureAnalyzer):
    analyzer_id = "timeout"
    version = "1"
    ERROR_TYPES = frozenset({
        "builtins.TimeoutError", "selenium.common.exceptions.TimeoutException",
        "playwright._impl._errors.TimeoutError", "java.net.SocketTimeoutException",
        "java.util.concurrent.TimeoutException", "org.apache.http.conn.ConnectTimeoutException",
    })

    def supports(self, context):
        return context.error_type in self.ERROR_TYPES

    def analyze(self, context):
        if not self.supports(context):
            return AnalysisResult(analyzer_id=self.analyzer_id, analyzer_version=self.version)
        return AnalysisResult(
            category=FailureCategory.TIMEOUT, confidence=1.0, severity=Severity.ERROR,
            explanation="The runtime explicitly reported a timeout exception. "
                        "This identifies the failure category, not its root cause.",
            supporting_evidence=tuple(e.evidence_id for e in context.evidence
                                      if e.type == EvidenceType.EXCEPTION),
            recommendations=("Review the existing timing and page/request evidence before changing timeouts.",),
            analyzer_id=self.analyzer_id, analyzer_version=self.version)
