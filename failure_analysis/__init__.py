"""Public, fail-soft failure-analysis facade. No AI, retries or artifact capture."""
from .collector import FailureCollector
from .models import FailureAnalysis
from .observability import log_event
from .registry import AnalyzerRegistry
from .timeout import TimeoutAnalyzer


registry = AnalyzerRegistry((TimeoutAnalyzer(),))


def analyze_failure(*, collector=None, analyzers=None, **facts):
    try:
        context = (collector or FailureCollector()).collect(**facts)
        if context is None:
            return None
        result = FailureAnalysis(context, (analyzers or registry).analyze(context))
        # Sanitize plugin-generated strings as well, using this run's secrets.
        from .privacy import sanitize_tree
        return FailureAnalysis.from_dict(sanitize_tree(result.to_dict(), tuple(facts.get("secrets", ()))))
    except Exception:
        log_event("analysis_error")
        return None


def serialize_analyses(records):
    """One corrupt record must not stop the existing report/history write."""
    out = []
    try:
        for record in records or ():
            try:
                out.append(record.to_dict())
            except Exception:
                log_event("serialization_error")
    except Exception:
        log_event("serialization_error")
    return out


def read_analyses(data):
    out = []
    for item in data if isinstance(data, list) else []:
        try:
            out.append(FailureAnalysis.from_dict(item))
        except Exception:
            log_event("deserialization_error")
    return tuple(out)
