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

Credentials are PER USER: when the run row carries `created_by` (the app
user's auth uid), the worker resolves that user's Azure PAT + AI provider/key
via the `worker_get_credentials` RPC — a SECURITY DEFINER function backed by
Supabase Vault that ONLY the service role may execute (verified: no anon/
authenticated grants). They're re-fetched on every Resume after a provider
error, so switching provider/key in the app rescues a paused remote run.

Environment (from repo secrets):
    RUN_ID                       remote_runs.id to execute
    SUPABASE_URL                 https://<ref>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY    service key (worker only — bypasses RLS)
    AZURE_ORG / AZURE_PAT / QA_AI_*   OPTIONAL fallback used only when the run
                                 has no created_by (local debugging).

Run locally for debugging with the same env vars:  python run_worker.py
"""
import json
import os
import sys
import threading
import time

import requests

SB_URL = os.environ["SUPABASE_URL"].strip().rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
RUN_ID = os.environ["RUN_ID"].strip()
# Supabase has TWO server-key formats: the legacy service_role JWT ("eyJ…",
# sent as apikey + Authorization: Bearer) and the new secret keys
# ("sb_secret_…", sent as apikey ONLY — a Bearer header carrying a non-JWT
# gets rejected as "Invalid API key"; confirmed live on the first dispatch).
_HDRS = {"apikey": SB_KEY, "Content-Type": "application/json"}
if SB_KEY.startswith("eyJ"):
    _HDRS["Authorization"] = f"Bearer {SB_KEY}"


class _SbError(RuntimeError):
    """Carries the HTTP status so callers can react per-status (see
    _flush_events' 409 handling)."""

    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def _sb(method, path, payload=None, params=None, headers=None):
    _h = dict(_HDRS)
    if headers:
        _h.update(headers)
    r = requests.request(method, f"{SB_URL}/rest/v1/{path}", headers=_h,
                         json=payload, params=params, timeout=30)
    if r.status_code >= 300:
        raise _SbError(r.status_code,
                       f"Supabase {method} {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.json() if (r.text or "").strip() else None


def _get_run():
    rows = _sb("GET", "remote_runs", params={"id": f"eq.{RUN_ID}", "select": "*"})
    if not rows:
        raise SystemExit(f"remote_runs row {RUN_ID} not found")
    return rows[0]


def _patch_run(fields):
    _sb("PATCH", f"remote_runs?id=eq.{RUN_ID}", payload=fields)


# Set by main() so _on_ai_error can re-apply credentials on Resume.
_ENGINE = None
_RUN = None

# In-memory activity log for the completion email — mirrors main.py's
# self._log_lines exactly (same "story"/"log" cb handling, including the
# heartbeat-line collapse and replace_wip removal) so build_report_email()'s
# log_lines= section renders identically to a local run's report. Kept
# SEPARATE from _ev_buf/remote_run_events (that feed is batched+cleared for
# Supabase Realtime; this one accumulates for the whole run so the final
# email has the complete log, not just whatever hadn't been flushed yet).
_log_lines = []


def _append_log_line(kind, payload):
    if kind == "story" and isinstance(payload, dict):
        _log_lines.append({"tone": "story", "ico": "▸",
                           "msg": f"Story {payload.get('id')} · {payload.get('title')}",
                           "ar": True})
    elif kind == "log" and isinstance(payload, dict):
        rw = payload.get("replace_wip")
        if rw is not None:
            _log_lines[:] = [l for l in _log_lines
                             if l.get("wip_id") != rw and l.get("hb_id") != rw]
        hb = payload.get("hb_id")
        if hb is not None:
            for l in _log_lines:
                if l.get("hb_id") == hb:
                    l.clear(); l.update(payload)
                    return
            _log_lines.append(dict(payload))
            return
        _log_lines.append(dict(payload))
        if payload.get("detail"):
            _log_lines.append({"tone": "dim", "indent": True, "msg": payload["detail"]})


def _apply_credentials(E, run):
    """Per-user credentials (Supabase Vault, service-role-only RPC) when the
    run carries created_by; env-var fallback otherwise (local debugging).
    Also applies Gmail sender/app-password (worker_get_credentials now
    returns those too — see the remote_run_email_and_gmail_vault migration)
    so _email_report() can send the completion report the same way a local
    run's Setup → Connection Gmail App Password does. Gmail is OPTIONAL:
    a user who never synced it just gets no email, same as a local run with
    no App Password configured — it never blocks the actual test-case run.
    Returns a short description for the activity feed."""
    uid = str(run.get("created_by") or "").strip()
    if uid:
        rows = _sb("POST", "rpc/worker_get_credentials", payload={"p_user_id": uid}) or []
        c = rows[0] if rows else None
        if (not c or not (c.get("azure_pat") or "").strip()
                or not (c.get("ai_api_key") or "").strip()):
            raise SystemExit(f"user {uid} has no complete credentials on file — "
                             "set the Azure PAT and AI key in the app first")
        E.reset_session_credentials(c.get("azure_org") or "", c["azure_pat"])
        E.set_credentials(provider=c.get("ai_provider") or "anthropic",
                          api_key=c["ai_api_key"],
                          model=(c.get("ai_model") or "") or None,
                          gmail=(c.get("gmail_app_pass") or "") or None,
                          gmail_sender=(c.get("gmail_sender") or "") or None,
                          gmail_sender_name=c.get("gmail_sender_name"))
        return f"user {uid[:8]}… · {c.get('ai_provider') or 'anthropic'}"
    E.reset_session_credentials(os.environ["AZURE_ORG"], os.environ["AZURE_PAT"])
    E.set_credentials(provider=os.environ.get("QA_AI_PROVIDER", "anthropic"),
                      api_key=os.environ.get("QA_AI_API_KEY", ""),
                      model=os.environ.get("QA_AI_MODEL", "") or None)
    return "environment credentials (no created_by on the run)"


# ── event feed: buffered, batched, never allowed to kill the run ────────────
_ev_lock = threading.Lock()
# Buffer is keyed by SEQ (not a plain list) so an in-place update to a line
# that hasn't flushed yet collapses to just its latest payload.
_ev_buf = {}
_ev_seq = 0
_ev_dirty = set()      # seqs changed since the last flush (re-post these)
_ev_stop = threading.Event()
# hb_id / wip_id -> seq, so a test case's whole lifecycle (generating… →
# "still generating Ns" heartbeats → done) updates ONE line in place instead
# of appending a fresh remote_run_events row every ~15s. Mirrors the desktop
# Run log's hb_id collapse (main.py _refresh_run) — reported live: the remote
# viewer stacked a new "Still generating… Ns so far" line per heartbeat.
_hb_seq = {}


def _cb(kind, payload):
    """The engine's cb — same (kind, payload) protocol as the desktop UI.
    Also mirrored to stdout so the Actions job log doubles as a raw trace."""
    global _ev_seq
    try:
        line = json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False)
        print(line[:2000], flush=True)
    except Exception:
        pass
    pd = payload if isinstance(payload, dict) else {"value": payload}
    with _ev_lock:
        key = pd.get("hb_id") or pd.get("wip_id")
        rep = pd.get("replace_wip")
        seq = None
        # A completion line that "replaces" a wip/heartbeat takes over that
        # line's seq → updates it in place to the final content.
        if rep is not None and rep in _hb_seq:
            seq = _hb_seq.pop(rep)
        # A heartbeat / wip update for an already-open line reuses its seq.
        if seq is None and key is not None and key in _hb_seq:
            seq = _hb_seq[key]
        if seq is None:
            _ev_seq += 1
            seq = _ev_seq
        # (Re)map this line's key to its seq so future updates find it.
        if key is not None:
            _hb_seq[key] = seq
        _ev_buf[seq] = {"run_id": RUN_ID, "seq": seq, "kind": str(kind), "payload": pd}
        _ev_dirty.add(seq)


def _flush_events():
    with _ev_lock:
        if not _ev_dirty:
            return
        batch = [_ev_buf[s] for s in sorted(_ev_dirty) if s in _ev_buf]
        _ev_dirty.clear()
    if not batch:
        return
    try:
        # UPSERT on (run_id, seq): a reused seq UPDATES its row in place rather
        # than inserting a duplicate line.
        #
        # `Prefer: resolution=merge-duplicates` is MANDATORY. In PostgREST the
        # `on_conflict` query param ALONE does nothing — without this header the
        # request is a plain INSERT. So the first time the hb_id collapse reused
        # a seq that had already been flushed, the batch violated
        # remote_run_events_run_id_seq_key UNIQUE(run_id, seq) and returned 409.
        # Combined with the blanket re-dirty below, that one poisoned seq rode
        # along in EVERY later flush, so every flush 409'd for the rest of the
        # run and NO further events were ever stored. Confirmed against the live
        # project: a continuous POST->409 storm while the client GETs all
        # returned 200, and affected runs stored only ~5 events before the feed
        # went silent — the "activity frozen mid-run" report.
        _sb("POST", "remote_run_events?on_conflict=run_id,seq", payload=batch,
            headers={"Prefer": "resolution=merge-duplicates"})
    except Exception as ex:
        # Telemetry must never take the run down — note it and move on.
        status = getattr(ex, "status", None)
        if status == 409:
            # A conflict means these rows already exist server-side. Retrying
            # the SAME batch can only 409 again, and re-dirtying it wedges the
            # queue permanently (the bug above). Drop it and keep going —
            # newer events still get through.
            print(f"[worker] event flush 409 — dropping {len(batch)} already-stored "
                  f"event(s) instead of wedging the queue: {ex}", file=sys.stderr)
        else:
            # Transient (network/5xx) — retry these on the next flush.
            with _ev_lock:
                _ev_dirty.update(b["seq"] for b in batch)
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
    control='resume' (→ 'retry', with the user's credentials RE-FETCHED so a
    provider/key change made in the app takes effect) or 'stop'."""
    _patch_run({"status": "paused"})
    while _ctrl["value"] not in ("resume", "stop"):
        time.sleep(1.0)
    if _ctrl["value"] == "stop":
        return "stop"
    _clear_control()
    # Re-fetch the user's credentials before retrying — the whole point of a
    # remote Resume is usually that they just switched provider / topped up
    # credits / rotated a key in the app. Failure here is non-fatal: the
    # engine re-reads the active provider on every attempt anyway.
    try:
        if _ENGINE is not None and _RUN is not None:
            src = _apply_credentials(_ENGINE, _RUN)
            _cb("log", {"msg": f"Credentials refreshed for retry — {src}", "tone": "dim"})
    except BaseException as ex:  # noqa: BLE001 — never kill the pause gate
        _cb("log", {"msg": f"Credential refresh failed ({str(ex)[:120]}) — "
                           "retrying with the previous credentials", "tone": "warn"})
    _patch_run({"status": "running"})
    return "retry"


def _email_report(E, run, status, summary, rpt):
    """Mirrors main.py's post-run email block (~4882-4949) as closely as
    possible — same build_report_email()/send_report() call, same stats
    shape, same structured log — so a remote run's report looks identical to
    a local run's ('every recipients should get mail based on the mail
    reports design same like in desktop app'). One send with every recipient
    on the To: line (send_report's existing to_addrs=list semantics), same
    as the desktop. Best-effort end to end: any failure here is logged and
    swallowed — it must never touch the run's already-decided status,
    summary, or exit code."""
    to = [str(e).strip() for e in (run.get("email_recipients") or []) if str(e).strip()]
    if not to:
        _cb("log", {"tone": "dim",
                    "msg": "No report email sent — no recipients on this run."})
        return
    if E is None or not (getattr(E, "GMAIL_APP_PASS", "") or "").strip():
        _cb("log", {"tone": "warn",
                    "msg": "No email sent — Gmail App Password not synced "
                           "(Settings → Remote runs → Sync now)."})
        return
    try:
        tool_name = "Test Case Steps" if run.get("kind") == "steps" else "Test Case Titles"
        rpt = rpt or {}
        _secs = rpt.get("total_secs")
        if run.get("kind") == "steps":
            stats = {"Created": rpt.get("created", 0), "Updated": rpt.get("updated", 0),
                     "Skipped": rpt.get("skipped", 0), "Failed": rpt.get("errors", 0),
                     "Stories": f"{rpt.get('stories_done', 0)}/{rpt.get('total_stories', 0)}"}
        else:
            stats = {"Created": rpt.get("created", 0), "Skipped": rpt.get("skipped", 0),
                     "Failed": rpt.get("errors", 0),
                     "Stories": f"{rpt.get('stories_done', 0)}/{rpt.get('total_stories', 0)}"}
        if _secs not in (None, "", 0):
            stats["Time"] = E._fmt_secs(_secs)
        plan_url = None
        if run.get("project") and run.get("plan_id"):
            # Remote runs are Azure-only today (run_worker has no `app` object
            # for backend_setup to read creds from), so this stays a direct URL
            # build. Revisit when remote runs learn about non-Azure backends.
            plan_url = (f"https://dev.azure.com/{E.AZURE_ORG}/{run['project']}"
                        f"/_testPlans/define?planId={run['plan_id']}")
        # Same structured-log shape as the desktop's email_log build (icon ·
        # id · title · detail, not raw text) so the report reads the same.
        email_log = []
        for ln in _log_lines:
            msg = ln.get("msg", "")
            if not msg:
                continue
            email_log.append({
                "msg": msg, "id": ln.get("id", ""), "ico": ln.get("ico", ""),
                "detail": ln.get("detail", ""), "tone": ln.get("tone", "dim"),
                "indent": bool(ln.get("indent")), "ar": bool(ln.get("ar"))})
        html = E.build_report_email(
            tool_name, rpt.get("summary") or summary or status, stats,
            rpt.get("action_items", []), rpt.get("skipped_items", []),
            per_story=rpt.get("per_story", []), plan_url=plan_url,
            total_secs=_secs, log_lines=email_log,
            org=E.AZURE_ORG, project=run.get("project"))
        ok, err = E.send_report(to, f"QA Studio — {tool_name} report", html)
        if ok:
            _cb("log", {"tone": "ok", "msg": f"Report emailed to {', '.join(to)}"})
        else:
            _cb("log", {"tone": "warn", "ico": "✉", "msg": f"Report not emailed — {err}"})
    except Exception as ex:
        _cb("log", {"tone": "warn", "msg": f"Report email failed: {str(ex)[:160]}"})


def main():
    run = _get_run()
    if run.get("status") not in ("queued", "running"):
        raise SystemExit(f"run {RUN_ID} is '{run.get('status')}' — nothing to do")
    _patch_run({"status": "running", "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    threading.Thread(target=_event_flusher, daemon=True).start()
    threading.Thread(target=_control_poller, daemon=True).start()

    done_box = {}

    def cb(kind, payload):
        if kind == "done" and isinstance(payload, dict):
            done_box.update(payload)
        _append_log_line(kind, payload)
        _cb(kind, payload)

    status, summary = "done", None
    try:
        # Setup lives INSIDE the guarded block: a credentials/config failure
        # here must still reach the `finally` status write — otherwise the
        # row would sit as a zombie 'running' forever (it was patched to
        # 'running' above, before anything could fail).
        import engine as E
        global _ENGINE, _RUN
        _ENGINE, _RUN = E, run
        E.set_output_lang(run.get("output_lang") or "ar")
        cred_src = _apply_credentials(E, run)
        _cb("log", {"msg": f"Remote run starting — credentials: {cred_src}", "tone": "dim"})
        E.clear_stop()
        story_ids = [int(s) for s in (run.get("story_ids") or [])]
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
    except SystemExit as ex:
        # Deliberate abort (e.g. "user has no complete credentials on file"):
        # mark the row 'error' with the message, then let the job exit nonzero.
        status, summary = "error", str(ex)[:300]
        _cb("log", {"msg": summary, "tone": "err"})
        raise
    except BaseException as ex:  # noqa: BLE001 — final status must always be written
        status, summary = "error", f"{type(ex).__name__}: {str(ex)[:300]}"
        _cb("log", {"msg": f"Worker crashed: {summary}", "tone": "err"})
    finally:
        _ev_stop.set()
        time.sleep(1.5)          # let the flusher drain
        _ctrl_stop.set()
        _flush_events()
        # Email BEFORE the status write, not after: status/summary are
        # already final at this point (every branch above sets them before
        # falling into finally), and _ENGINE (module global, set right after
        # `import engine as E` above) survives even if the try block failed
        # before that import somehow completed. Runs while _cb still logs to
        # stdout AND remote_run_events (the second _flush_events() below
        # catches the "Report emailed to…" line this adds), so the live
        # viewer's activity feed shows the email attempt too, not just the
        # Actions job log.
        try:
            _email_report(_ENGINE, run, status, summary, done_box)
        except Exception as ex:
            print(f"[worker] email report failed: {ex}", file=sys.stderr)
        _flush_events()
        try:
            _patch_run({"status": status, "summary": summary,
                        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "control": None})
        except Exception as ex:
            print(f"[worker] final status write failed: {ex}", file=sys.stderr)
    print(f"[worker] finished: {status} — {summary or 'ok'}")
    # Only 'done'/'stopped' are real, expected outcomes — 'error' means the
    # run never actually produced test cases (a crashed import, bad creds,
    # provider failure the pause/resume loop couldn't recover, etc.). Every
    # non-SystemExit failure above is caught by `except BaseException` so the
    # row always gets a final status/summary — but that same catch meant
    # main() always returned normally and the process exited 0, so the
    # Actions job showed a green "succeeded" checkmark no matter what
    # actually happened inside. Confirmed live: the missing-azure-module
    # crash below looked identical, from the Actions UI, to a real success.
    # Exiting nonzero here makes a genuine failure show up as a failed job —
    # the same "loud, not silent" pattern build-apk.yml already uses for a
    # missing signing secret.
    if status == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
