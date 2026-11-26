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


def lines_to_copy(lines, issues_only=False, model=None):
    """What Copy takes from the rail: every line on the All tab; on the Issues
    tab only what that tab shows (warn/err lines and their case headers)."""
    lines = list(lines or [])
    if not issues_only:
        return lines
    vis = visibility(lines, False, None, True, model)
    return [line for line, shown in zip(lines, vis) if shown]


# ---- Shared by every activity log (Generate, Automation, …) ----

_GLYPH = {"ok": "✓", "err": "✗", "warn": "⚠", "review": "⚠", "story": "▸"}


def is_issue(line):
    return (line.get("tone") or "") in ISSUE_TONES


def issue_count(lines):
    """Warn / error lines, for the 'Issues 3' chip."""
    return sum(1 for line in (lines or []) if is_issue(line))


def flat_visibility(lines, issues_only=False):
    """All / Issues filter for a log without case groups (Generate): on Issues,
    warn/err lines show, plus the indented detail lines right under them."""
    out, show_detail = [], False
    for line in lines or []:
        if not issues_only:
            out.append(True)
            continue
        if line.get("indent"):
            out.append(show_detail)
            continue
        show_detail = is_issue(line)
        out.append(show_detail)
    return out


# ---- Generate (Run) log: grouped by story, like the Automation log's cases ----

def run_build(lines):
    """Group the Generate log by story: each "story" line heads a group that
    runs to the next story line. {"group", "headers", "status", "counts"} -
    counts[gid] = {"ok": n, "warn": n, "err": n} over the group's test-case
    lines (lines with an id)."""
    group, headers, status, counts = [], {}, {}, {}
    current = None
    for i, line in enumerate(lines or []):
        if (line.get("tone") or "") == "story":
            current = len(headers)
            headers[current], status[current] = i, "ok"
            counts[current] = {"ok": 0, "warn": 0, "err": 0}
            group.append(current)
            continue
        group.append(current)
        if current is None:
            continue
        tone = line.get("tone") or "dim"
        level = "err" if tone == "err" else ("warn" if tone in ISSUE_TONES else "ok")
        if _RANK[level] > _RANK[status[current]]:
            status[current] = level
        if line.get("id") and tone in ("ok", "err", "warn", "review"):
            counts[current][level] += 1
    return {"group": group, "headers": headers, "status": status, "counts": counts}


def run_is_open(model, gid, toggles=None):
    """A story group shows its lines when the user opened it, else only the
    latest story stays open (earlier ones fold away as the run moves on)."""
    toggles = toggles or {}
    if gid in toggles:
        return toggles[gid]
    return bool(model["headers"]) and gid == max(model["headers"])


def run_visibility(lines, toggles=None, issues_only=False, model=None):
    """One visible flag per Generate log line. All: headers and lines outside
    a story always show; a story's lines when it is open. Issues: warn/err
    lines (plus their indented detail) and the headers of stories with one."""
    model = model or run_build(lines)
    out, show_detail = [], False
    for i, line in enumerate(lines or []):
        gid = model["group"][i]
        is_header = gid is not None and model["headers"].get(gid) == i
        if issues_only:
            if is_header:
                out.append(model["status"][gid] != "ok")
            elif line.get("indent"):
                out.append(show_detail)
            else:
                show_detail = is_issue(line)
                out.append(show_detail)
            continue
        out.append(gid is None or is_header or run_is_open(model, gid, toggles))
    return out


def run_lines_to_copy(lines, issues_only=False):
    """Copy / Save on the Generate log: every line on All (collapsed stories
    included); on Issues what that tab shows."""
    lines = list(lines or [])
    if not issues_only:
        return lines
    return [line for line, shown in zip(lines, run_visibility(lines, None, True)) if shown]


def flat_lines_to_copy(lines, issues_only=False):
    lines = list(lines or [])
    if not issues_only:
        return lines
    return [line for line, shown in zip(lines, flat_visibility(lines, True)) if shown]


def stamp(line, now=None):
    """Give a log line its time once (kept when the line is edited in place)."""
    if isinstance(line, dict) and line.get("ts") is None:
        import time
        line["ts"] = now if now is not None else time.time()
    return line


def format_lines(lines):
    """Copy / save text: 'HH:MM:SS  ✓ message', one line per log line, so a
    pasted log reads the same from every screen. Spinner-only lines are kept;
    empty ones are dropped."""
    import time
    out = []
    for line in lines or []:
        msg = str(line.get("msg") or "").replace("\x1f", " ")
        if not msg.strip():
            continue
        ts = line.get("ts")
        clock = time.strftime("%H:%M:%S", time.localtime(ts)) if ts is not None else "        "
        glyph = line.get("ico") or _GLYPH.get(line.get("tone") or "", " ")
        indent = "    " if line.get("indent") else ""
        tag = ("[%s] " % line["id"] if line.get("id") else "") + ("#%s " % line["seq"] if line.get("seq") else "")
        body = msg.strip() if indent else msg.rstrip()
        if line.get("detail"):          # the case's time, shown inline in the row
            body += "  ·  " + str(line["detail"]).strip()
        out.append("%s  %s %s%s%s" % (clock, glyph, indent, tag, body))
    return out


def save_path(screen, folder=None, now=None):
    """Where 'Save log' writes: Downloads (or home) / qa-studio-<screen>-<time>.log."""
    import os
    import time
    if folder is None:
        home = os.path.expanduser("~")
        down = os.path.join(home, "Downloads")
        folder = down if os.path.isdir(down) else home
    stamp_txt = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    safe = re.sub(r"[^a-z0-9]+", "-", str(screen or "log").lower()).strip("-") or "log"
    return os.path.join(folder, "qa-studio-%s-%s.log" % (safe, stamp_txt))


def save_lines(lines, screen, folder=None):
    """Write the formatted log; returns the path, or '' when there is nothing."""
    text = format_lines(lines)
    if not text:
        return ""
    path = save_path(screen, folder)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(text) + "\n")
    return path
