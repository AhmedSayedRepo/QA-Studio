"""Live "case N / M" counter for an Automation run, read from the run's own log
lines (no extra plumbing in the worker). Pure functions - no Flet - so the
parsing is unit-tested (tests/test_run_progress.py).

Phases, in the order a run goes through them:
  inspect   "walking '<case>'" lines            -> Inspecting 3 / 31
  sequence  "Sequencing case 5/31"              -> Sequencing 5 / 31
  replay    "✓ replay: …" / "✗ replay: …"       -> Replaying 10 / 30
"""
import re

_LOADED = re.compile(r"\bLoaded\b.*?(\d+)\s+test case\(s\)")
_REUSING = re.compile(r"\binspecting\s+(\d+)\s+new, changed or failed case")
_WALKING = re.compile(r"^\s*walking\s+'")
_SEQ = re.compile(r"\bSequencing case\s+(\d+)\s*/\s*(\d+)")
_REPLAY_N = re.compile(r"\bReplaying the\s+(\d+)\s+fully verified case")
_REPLAY_ALL = re.compile(r"\bReplaying every case\b")
_REPLAY_LINE = re.compile(r"^\s*[✓✗x✕]\s+replay:")


def new_state():
    return {"total": 0, "inspect_total": 0, "inspected": 0, "phase": "",
            "seq": 0, "seq_total": 0, "replay_total": 0, "replayed": 0}


def update(state, msg):
    """Feed one log line. Returns True when the counter text may have changed."""
    m = str(msg or "")
    if _LOADED.search(m):
        state["total"] = int(_LOADED.search(m).group(1))
        return True
    if _REUSING.search(m):
        state["inspect_total"] = int(_REUSING.search(m).group(1))
        return True
    if _WALKING.search(m):
        state["phase"] = "inspect"
        state["inspected"] += 1
        return True
    if _SEQ.search(m):
        s = _SEQ.search(m)
        state["phase"] = "sequence"
        state["seq"], state["seq_total"] = int(s.group(1)), int(s.group(2))
        return True
    if _REPLAY_N.search(m):
        state["phase"] = "replay"
        state["replay_total"] = int(_REPLAY_N.search(m).group(1))
        return True
    if _REPLAY_ALL.search(m):
        state["phase"] = "replay"
        state["replay_total"] = state["inspected"] or state["inspect_total"] or state["total"]
        return True
    if _REPLAY_LINE.search(m):
        state["phase"] = "replay"
        state["replayed"] += 1
        return True
    return False


def text(state):
    """'Inspecting 3 / 31' etc., or '' before the first case."""
    ph = state.get("phase")
    if ph == "inspect":
        tot = state["inspect_total"] or state["total"]
        n = state["inspected"]
        return "Inspecting %d / %d" % (min(n, tot), tot) if tot else "Inspecting %d" % n
    if ph == "sequence":
        return "Sequencing %d / %d" % (state["seq"], state["seq_total"])
    if ph == "replay":
        tot = state["replay_total"]
        n = state["replayed"]
        return "Replaying %d / %d" % (min(n, tot), tot) if tot else "Replaying %d" % n
    return ""
