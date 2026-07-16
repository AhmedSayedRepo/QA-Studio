"""run_worker.py — headless remote-run executor (GitHub Actions).

Executes ONE queued row of Supabase's `remote_runs` table with the exact same
engine the desktop app uses (`E.run_titles` / `E.run_steps`), streaming every
cb(kind, payload) event into `remote_run_events` so the desktop/mobile apps can
watch the identical activity feed live (Supabase Realtime), and honoring the
same Pause/Resume/Stop semantics via the run row's `control` column:

    control='pause'  → the between-items gate holds after in-flight items finish
    control='resume' → clears the pause (worker resets control to NULL)
    control='stop'   → should_stop() turns true; run ends as 'stopped'

A fatal provider error takes the SAME path as the desktop's pause-on-error
(engine's on_ai_error callback): status flips to 'paused', and the run waits
for control='resume' (retry the failed item) or 'stop'.

Environment (all provided by the GitHub Actions workflow from repo secrets):
    RUN_ID                       remote_runs.id to execute
    SUPABASE_URL                 https://<ref>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY    service key (worker only — bypasses RLS)
    AZURE_ORG / AZURE_PAT        Azure DevOps
    QA_AI_PROVIDER / QA_AI_API_KEY / QA_AI_MODEL   AI provider (same names the
                                 generated test projects already use)

Run locally for debugging with the same env vars:  python run_worker.py
"""
import json
import os
import sys
import threading
import time

import requests

SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
RUN_ID = os.environ["RUN_ID"].strip()
_HDRS = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
         "Content-Type": "application/json"}


def _sb(method, path, payload=None, params=None):
    r = requests.request(method, f"{SB_URL}/rest/v1/{path}", headers=_HDRS,
                         json=payload, params=params, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"Supabase {method} {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.json() if (r.text or "").strip() else None


def _get_run():
    rows = _sb("GET", "remote_runs", params={"id": f"eq.{RUN_ID}", "select": "*"})
    if not rows:
        raise SystemExit(f"remote_runs row {RUN_ID} not found")
    return rows[0]


def _patch_run(fields):
    _sb("PATCH", f"remote_runs?id=eq.{RUN_ID}", payload=fields)


# ── event feed: buffered, batched, never allowed to kill the run ────────────
_ev_lock = threading.Lock()
_ev_buf = []
_ev_seq = 0
_ev_stop = threading.Event()


def _cb(kind, payload):
    """The engine's cb — same (kind, payload) protocol as the desktop UI.
    Also mirrored to stdout so the Actions job log doubles as a raw trace."""
    global _ev_seq
    try:
        line = json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False)
        print(line[:2000], flush=True)
    except Exception:
        pass
    with _ev_lock:
        _ev_seq += 1
        _ev_buf.append({"run_id": RUN_ID, "seq": _ev_seq, "kind": str(kind),
                        "payload": payload if isinstance(payload, dict) else {"value": payload}})


def _flush_events():
    with _ev_lock:
        batch, _ev_buf[:] = _ev_buf[:], []
    if not batch:
        return
    try:
        _sb("POST", "remote_run_events?on_conflict=run_id,seq", payload=batch)
    except Exception as ex:
        # Telemetry must never take the run down — note it and move on.
        print(f"[worker] event flush failed ({len(batch)} events): {ex}", file=sys.stderr)


def _event_flusher():
    while not _ev_stop.is_set():
        _flush_events()
        _ev_stop.wait(1.0)
    _flush_events()


# ── control channel: poll the run row's `control` column ────────────────────
_ctrl = {"value": None}
_ctrl_stop = threading.Event()


def _control_poller():
    while not _ctrl_stop.is_set():
        try:
            rows = _sb("GET", "remote_runs", params={"id": f"eq.{RUN_ID}", "select": "control"})
            _ctrl["value"] = (rows[0].get("control") if rows else None)
        except Exception:
            pass   # transient — keep the last-known value
        _ctrl_stop.wait(2.0)


def _should_stop():
    return _ctrl["value"] == "stop"


def _clear_control():
    _ctrl["value"] = None
    try:
        _patch_run({"control": None})
    except Exception:
        pass


def _gate():
    """Between-items gate (engine calls it before dispatching each item):
    hold while paused; False = stop. Mirrors main.py's _run_gate."""
    if _ctrl["value"] == "pause":
        _patch_run({"status": "paused"})
        _cb("log", {"msg": "Paused (remote control) — waiting for Resume…",
                    "tone": "warn", "ico": "⏸"})
        while _ctrl["value"] == "pause":
            time.sleep(1.0)
        if _ctrl["value"] == "resume":
            _clear_control()
        if not _should_stop():
            _patch_run({"status": "running"})
            _cb("log", {"msg": "Resumed.", "tone": "ok"})
    return not _should_stop()


def _on_ai_error(msg):
    """Fatal-provider-error pause — the engine already logged the error and
    the 'Paused on provider error…' line before calling this. Waits for
    control='resume' (→ 'retry') or 'stop'. Provider/key changes for a remote
    run mean updating the repo secrets, so 'resume' here mostly makes sense
    after a rate-limit/credit top-up on the SAME provider."""
    _patch_run({"status": "paused"})
    while _ctrl["value"] not in ("resume", "stop"):
        time.sleep(1.0)
    if _ctrl["value"] == "stop":
        return "stop"
    _clear_control()
    _patch_run({"status": "running"})
    return "retry"


def main():
    run = _get_run()
    if run.get("status") not in ("queued", "running"):
        raise SystemExit(f"run {RUN_ID} is '{run.get('status')}' — nothing to do")
    _patch_run({"status": "running", "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    threading.Thread(target=_event_flusher, daemon=True).start()
    threading.Thread(target=_control_poller, daemon=True).start()

    import engine as E
    E.set_output_lang(run.get("output_lang") or "ar")
    E.reset_session_credentials(os.environ["AZURE_ORG"], os.environ["AZURE_PAT"])
    E.set_credentials(provider=os.environ.get("QA_AI_PROVIDER", "anthropic"),
                      api_key=os.environ.get("QA_AI_API_KEY", ""),
                      model=os.environ.get("QA_AI_MODEL", "") or None)
    E.clear_stop()

    done_box = {}

    def cb(kind, payload):
        if kind == "done" and isinstance(payload, dict):
            done_box.update(payload)
        _cb(kind, payload)

    story_ids = [int(s) for s in (run.get("story_ids") or [])]
    status, summary = "done", None
    try:
        if run["kind"] == "titles":
            E.run_titles(run["project"], run["plan_id"], story_ids, cb,
                         should_stop=_should_stop, on_ai_error=_on_ai_error,
                         gate=_gate)
        else:
            E.run_steps(run["project"], run["plan_id"], story_ids, cb,
                        should_stop=_should_stop,
                        existing_mode=run.get("existing_mode") or "skip",
                        on_ai_error=_on_ai_error, gate=_gate)
        summary = done_box.get("summary")
        if _should_stop() or (done_box.get("reason") in ("stopped", "stop")):
            status = "stopped"
        elif done_box.get("reason") in ("credit", "auth", "bad_model", "not_found", "network"):
            status = "error"
    except SystemExit:
        raise
    except BaseException as ex:  # noqa: BLE001 — final status must always be written
        status, summary = "error", f"{type(ex).__name__}: {str(ex)[:300]}"
        _cb("log", {"msg": f"Worker crashed: {summary}", "tone": "err"})
    finally:
        _ev_stop.set()
        time.sleep(1.5)          # let the flusher drain
        _ctrl_stop.set()
        _flush_events()
        try:
            _patch_run({"status": status, "summary": summary,
                        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "control": None})
        except Exception as ex:
            print(f"[worker] final status write failed: {ex}", file=sys.stderr)
    print(f"[worker] finished: {status} — {summary or 'ok'}")


if __name__ == "__main__":
    main()
