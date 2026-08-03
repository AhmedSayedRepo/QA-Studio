"""perf/extract.py - build a PerfScenario from a functional test case.

Two extractors behind one port (perf.ports.ScenarioExtractor):

  * HeuristicExtractor - deterministic, offline, NO AI. Best-effort: turns each
    step into a request (real URL if the prose contains one, else a templated
    placeholder), infers method/assertions, collects {{variables}}. Always
    produces something; doubles as the guaranteed baseline and the AI fallback.

  * AIExtractor - asks an injected `ai_complete(prompt) -> str` for a STRUCTURED
    JSON draft, then validates every field programmatically. This is the
    roadmap's hard-won lesson made concrete: never trust a weak/free model's
    output - force structure, backstop it in code, and degrade GRACEFULLY to the
    heuristic on any parse/validation failure rather than emit a confident-but-
    wrong load script.

Input is primitives (title + step dicts {action, expected, data, pre}), so this
module never imports tracker/ or Flet. The app adapts a tracker TestCase to
these at the call boundary.

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

from .models import Assertion, AssertionKind, PerfRequest, PerfScenario
from .ports import AiComplete, ScenarioExtractor

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_STATUS_RE = re.compile(r"\b([1-5]\d\d)\b")
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_WRITE_WORDS = ("submit", "create", "login", "log in", "sign in", "save", "send",
                "add", "post", "register", "update", "upload", "checkout", "pay")
_DELETE_WORDS = ("delete", "remove", "cancel")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or "step"


def _guess_method(text: str) -> str:
    low = (text or "").lower()
    if any(w in low for w in _DELETE_WORDS):
        return "DELETE"
    if any(w in low for w in _WRITE_WORDS):
        return "POST"
    return "GET"


def _find_vars(*texts: str) -> List[str]:
    seen: List[str] = []
    for t in texts:
        for m in _VAR_RE.findall(t or ""):
            if m not in seen:
                seen.append(m)
    return seen


def _assertions_from_expected(expected: str) -> List[Assertion]:
    exp = (expected or "").strip()
    if not exp:
        return []
    m = _STATUS_RE.search(exp)
    if m:
        return [Assertion(AssertionKind.STATUS, m.group(1))]
    # A positive functional outcome -> assert the body mentions it (first ~4 words).
    words = re.findall(r"[A-Za-z][A-Za-z']+", exp)
    if words:
        return [Assertion(AssertionKind.BODY_CONTAINS, " ".join(words[:4]))]
    return []


class HeuristicExtractor(ScenarioExtractor):
    """Deterministic, AI-free extraction. Never raises; always returns a scenario."""

    def extract(self, case_id: str, title: str, steps: List[dict],
                story_id: str = "") -> PerfScenario:
        requests: List[PerfRequest] = []
        variables: List[str] = []
        for st in steps or []:
            action = str(st.get("action", "") or "")
            expected = str(st.get("expected", "") or "")
            data = str(st.get("data", "") or "")
            if not (action or expected):
                continue
            found = _URL_RE.search(action) or _URL_RE.search(data) or _URL_RE.search(expected)
            url = found.group(0) if found else f"https://{{{{host}}}}/{_slug(action)}"
            req = PerfRequest(
                method=_guess_method(action),
                url=url,
                body=data,
                assertions=_assertions_from_expected(expected),
                think_ms=0,
                source_step=action[:120],
            )
            requests.append(req)
            for v in _find_vars(action, expected, data):
                if v not in variables:
                    variables.append(v)
        return PerfScenario(id=str(case_id), title=title or str(case_id),
                            requests=requests, variables=variables, story_id=story_id)


_AI_PROMPT = """You convert a functional UI test case into HTTP requests for a load test.
Return ONLY a JSON array. Each element:
{{"method":"GET|POST|PUT|PATCH|DELETE","url":"https://.../path or /path","body":"optional",
  "assert_status":"200 (optional)","think_ms":0}}
Rules:
- One element per meaningful backend interaction; omit pure UI-only steps.
- Use {{{{variable}}}} placeholders for data-driven values (email, password, id).
- Do NOT invent hostnames you cannot infer; use a relative /path when unsure.
- Output the JSON array and nothing else.

Test case title: {title}
Story context: {story}
Steps:
{steps}
"""


class AIExtractor(ScenarioExtractor):
    """AI draft + programmatic validation backstop, graceful heuristic fallback."""

    def __init__(self, ai_complete: AiComplete,
                 fallback: Optional[ScenarioExtractor] = None):
        self._ai = ai_complete
        self._fallback = fallback or HeuristicExtractor()

    def extract(self, case_id: str, title: str, steps: List[dict],
                story_id: str = "") -> PerfScenario:
        base = self._fallback.extract(case_id, title, steps, story_id)
        try:
            raw = self._ai(self._build_prompt(title, steps, story_id))
            requests = self._parse_and_validate(raw, steps)
        except Exception:
            requests = []
        if not requests:
            return base  # graceful degradation - never emit nothing/garbage
        variables = sorted({v for r in requests for v in _find_vars(r.url, r.body)})
        return PerfScenario(id=str(case_id), title=title or str(case_id),
                            requests=requests, variables=list(variables), story_id=story_id)

    # -- internals -------------------------------------------------------------
    def _build_prompt(self, title: str, steps: List[dict], story_id: str) -> str:
        lines = []
        for i, st in enumerate(steps or [], 1):
            a = str(st.get("action", "") or "")
            e = str(st.get("expected", "") or "")
            lines.append(f"{i}. {a}" + (f"  =>  {e}" if e else ""))
        return _AI_PROMPT.format(title=title or "", story=story_id or "",
                                 steps="\n".join(lines) or "(none)")

    def _parse_and_validate(self, raw: str, steps: List[dict]) -> List[PerfRequest]:
        """Trust NOTHING: parse, then validate every field; drop bad rows; on a
        malformed payload return [] so the caller falls back to the heuristic."""
        text = (raw or "").strip()
        # Tolerate models that wrap JSON in prose/code fences.
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            rows = json.loads(text[start:end + 1])
        except Exception:
            return []
        if not isinstance(rows, list):
            return []
        out: List[PerfRequest] = []
        step_texts = [str(s.get("action", "") or "") for s in (steps or [])]
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            method = str(row.get("method", "GET")).upper().strip()
            if method not in _ALLOWED_METHODS:
                continue
            url = str(row.get("url", "") or "").strip()
            # reject empty / obviously-hallucinated urls; allow relative paths
            if not url or (not url.startswith(("http://", "https://", "/"))):
                continue
            asserts: List[Assertion] = []
            code = str(row.get("assert_status", "") or "").strip()
            if _STATUS_RE.fullmatch(code):
                asserts.append(Assertion(AssertionKind.STATUS, code))
            think = row.get("think_ms", 0)
            think = int(think) if isinstance(think, (int, float)) and think >= 0 else 0
            out.append(PerfRequest(
                method=method, url=url, body=str(row.get("body", "") or ""),
                assertions=asserts, think_ms=think,
                source_step=(step_texts[idx][:120] if idx < len(step_texts) else ""),
            ))
        return out


__all__ = ["HeuristicExtractor", "AIExtractor"]
