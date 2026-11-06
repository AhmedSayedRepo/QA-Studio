"""log_view.py — view model for the Automation activity rail (no Flet).

The rail keeps rendering one row per log line (main.py relies on that 1:1
mapping for its live append and in-place updates). This module only decides,
from the plain log lines, which rows belong to which test case, each case's
status, and which rows are visible — so grouping, auto-collapse and the
All / Issues filter are just `visible` flags on existing rows.

A case starts at the explorer's "walking '<title>'" line (that row becomes the
case header) and ends at the next case, a story line, or a run-summary line.
"""
from __future__ import annotations

import re

_CASE_RE = re.compile(r"^\s*walking '(.+?)'")
# Lines that end the open case: the run moved on to a summary or a new phase.
_CLOSERS = ("Exploration done", "Locator proof", "Live inspection captured",
            "Saved local inspection", "Replay", "Generating", "Generation blocked",
            "Verified inspection", "Stopped", "Automation failed", "Done —",
            "Nothing new")
ISSUE_TONES = ("warn", "err", "review")
_RANK = {"ok": 0, "warn": 1, "err": 2}


def case_title(msg):
    """The case title when `msg` is a case header line, else None."""
    m = _CASE_RE.match(msg or "")
    return m.group(1) if m else None


def _closes(line):
    if (line.get("tone") or "") == "story":
        return True
    text = (line.get("msg") or "").strip()
    return any(text.startswith(prefix) for prefix in _CLOSERS)


def build(lines):
    """{"group": [case id or None per line], "headers": {gid: line index},
    "titles": {gid: title}, "status": {gid: "ok"|"warn"|"err"}}"""
    group, headers, titles, status = [], {}, {}, {}
    current = None
    for i, line in enumerate(lines or []):
        title = case_title(line.get("msg"))
        if title is not None:
            current = len(headers)
            headers[current], titles[current], status[current] = i, title, "ok"
            group.append(current)
            continue
        if current is not None and _closes(line):
            current = None
        group.append(current)
        if current is not None:
            tone = line.get("tone") or "dim"
            level = "err" if tone == "err" else ("warn" if tone in ISSUE_TONES else "ok")
            if _RANK[level] > _RANK[status[current]]:
                status[current] = level
    return {"group": group, "headers": headers, "titles": titles, "status": status}


def visibility(lines, running, toggles=None, issues_only=False, model=None):
    """One visible flag per line.

    All: case headers and lines outside cases always show; a case's own lines
    show when it is open — the active (last) case while running, unless the
    user toggled it (`toggles` = {gid: open?}). Issues: only warn/err lines,
    plus the headers of cases that have one."""
    model = model or build(lines)
    toggles = toggles or {}
    last = max(model["headers"]) if model["headers"] else None
    out = []
    for i, line in enumerate(lines or []):
        gid = model["group"][i]
        is_header = gid is not None and model["headers"].get(gid) == i
        if issues_only:
            out.append(model["status"][gid] != "ok" if is_header
                       else (line.get("tone") or "") in ISSUE_TONES)
            continue
        if gid is None or is_header:
            out.append(True)
            continue
        out.append(toggles[gid] if gid in toggles else (running and gid == last))
    return out


def is_open(model, gid, running, toggles=None):
    """Whether case `gid` shows its lines in the All view (for the chevron)."""
    toggles = toggles or {}
    if gid in toggles:
        return toggles[gid]
    last = max(model["headers"]) if model["headers"] else None
    return running and gid == last


def banner_tone(kind):
    """Tone of the end-of-run result banner."""
    return {"generated": "ok", "nothing": "ok", "blocked": "err",
            "failed": "err", "stopped": "warn"}.get(kind, "dim")
