"""Use the app's nonthrowing local diagnostic sink; never log exception bodies."""
import json


def log_event(event, *, run_id="", execution_id="", analyzer_id="", category=""):
    try:
        import diag_log
        from .privacy import sanitize_text
        payload = {k: sanitize_text(str(v)) for k, v in {
            "event": event, "run_id": run_id, "execution_id": execution_id,
            "analyzer_id": analyzer_id, "category": category}.items()}
        diag_log.log("failure_analysis " + json.dumps(payload, ensure_ascii=True),
                     level="warning" if event.endswith("error") else "info")
    except Exception:
        pass
