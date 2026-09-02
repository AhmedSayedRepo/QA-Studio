"""Independently testable analyzer contract; no execution-engine dependency."""
from abc import ABC, abstractmethod

from .models import AnalysisResult, FailureAnalysisContext


class FailureAnalyzer(ABC):
    analyzer_id: str
    version: str = "1"

    @abstractmethod
    def supports(self, context: FailureAnalysisContext) -> bool:
        """True only when this analyzer has relevant structured evidence."""

    @abstractmethod
    def analyze(self, context: FailureAnalysisContext) -> AnalysisResult:
        """Pure analysis. Return UNKNOWN when evidence cannot support a finding."""
