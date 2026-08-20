"""perf/service.py - the application/orchestration layer.

Ties the pieces together WITHOUT importing Flet, engine, or tracker: the AI is an
injected `ai_complete`, test cases arrive as plain dicts, and the target comes
from the registry. Both the CLI and the future Flet screen call THIS, so the
pipeline is identical and fully testable headless.

case dict shape (what the app adapts a tracker TestCase into):
    {"id": "TC-1", "title": "...", "story_id": "US-9",
     "steps": [{"action": "...", "expected": "...", "data": "", "pre": ""}, ...]}

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

import dataclasses
import inspect
import re
from typing import List, Optional, Tuple

from .extract import AIExtractor, HeuristicExtractor
from .models import (DataSource, Extraction, LoadProfile, PerfResult, PerfScenario)
from .ports import (AiComplete, CancelCheck, OnEvent, PerfTarget, ProjectPaths,
                    never_cancel, noop_event)
from .registry import get_target

# Matches an optional scheme followed by the {{host}} placeholder the heuristic
# emits, e.g. "https://{{host}}" or "{{host}}" - anchored at the start.
_HOST_PLACEHOLDER = re.compile(r"^(?:https?://)?\{\{\s*host\s*\}\}", re.I)


def rebase(scenarios: List[PerfScenario], base_url: str) -> List[PerfScenario]:
    """Prefix RELATIVE request URLs (/path) and {{host}} placeholders with
    base_url, so heuristic/AI scenarios actually hit a real host. Genuinely
    absolute http(s) URLs (e.g. from a HAR) are left untouched. No-op when
    base_url is empty."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return scenarios
    out: List[PerfScenario] = []
    for sc in scenarios:
        reqs = []
        for r in sc.requests:
            u = (r.url or "").strip()
            m = _HOST_PLACEHOLDER.match(u)
            if m:                                   # scheme+{{host}} or bare {{host}}
                path = u[m.end():]
                new_url = base + (path if path.startswith("/") else "/" + path)
            elif u.startswith(("http://", "https://")):
                new_url = u                         # real absolute URL - leave it
            else:                                   # relative /path
                new_url = base + (u if u.startswith("/") else "/" + u)
            reqs.append(dataclasses.replace(r, url=new_url))
        out.append(dataclasses.replace(sc, requests=reqs))
    return out


def with_auth(scenarios: List[PerfScenario], auth_value: str,
              header: str = "Authorization") -> List[PerfScenario]:
    """Inject an auth header (e.g. 'Bearer eyJ…') onto every request - the fix for
    a SANITIZED HAR whose credentials Chrome stripped. No-op when auth_value empty;
    an existing same-named header is overwritten."""
    v = (auth_value or "").strip()
    if not v:
        return scenarios
    out: List[PerfScenario] = []
    for sc in scenarios:
        reqs = []
        for r in sc.requests:
            h = dict(r.headers or {})
            h[header] = v
            reqs.append(dataclasses.replace(r, headers=h))
        out.append(dataclasses.replace(sc, requests=reqs))
    return out


def scenarios_from_cases(cases: List[dict],
                         ai_complete: Optional[AiComplete] = None) -> List[PerfScenario]:
    """Adapt plain test-case dicts -> PerfScenarios. Uses the AI extractor when a
    callable is supplied (validated + graceful fallback), else the offline heuristic."""
    extractor = AIExtractor(ai_complete) if ai_complete else HeuristicExtractor()
    out: List[PerfScenario] = []
    for c in cases or []:
        out.append(extractor.extract(
            case_id=str(c.get("id") or c.get("title") or "case"),
            title=str(c.get("title") or ""),
            steps=list(c.get("steps") or []),
            story_id=str(c.get("story_id") or "")))
    return out


def build_and_emit(cases: List[dict], profile: LoadProfile, out_dir: str,
                   target_name: str = "jmeter", ai_complete: Optional[AiComplete] = None,
                   data: Optional[DataSource] = None
                   ) -> Tuple[List[PerfScenario], PerfTarget, ProjectPaths]:
    """cases -> scenarios -> emitted tool project. No run (safe/offline)."""
    scenarios = scenarios_from_cases(cases, ai_complete)
    target = get_target(target_name)
    paths = target.emit(scenarios, profile, out_dir, data=data)
    return scenarios, target, paths


def parameterize(scenarios: List[PerfScenario],
                 rules: List[Tuple[str, str]]) -> List[PerfScenario]:
    """Turn captured literals into {{variables}}: each (find, replace) rule replaces
    the literal `find` with `replace` (e.g. '{{product}}') everywhere in each
    request's URL, body and header values. So a HAR/cURL value can be data-driven
    from the CSV per user. No-op when there are no rules."""
    rules = [(f, r) for f, r in (rules or []) if f]
    if not rules:
        return scenarios

    def sub(s: str) -> str:
        for find, repl in rules:
            s = (s or "").replace(find, repl)
        return s

    out: List[PerfScenario] = []
    for sc in scenarios:
        reqs = [dataclasses.replace(
            rq, url=sub(rq.url), body=sub(rq.body),
            headers={k: sub(v) for k, v in (rq.headers or {}).items()})
            for rq in sc.requests]
        out.append(dataclasses.replace(sc, requests=reqs))
    return out


def correlate(scenarios: List[PerfScenario],
              rules: List[dict]) -> List[PerfScenario]:
    """Attach response extractions (correlation). Each rule = {var, json_path|regex,
    match}: the value is pulled out of the FIRST request whose URL contains `match`
    (or the first request when `match` is empty) and saved into `var`, so LATER
    requests referencing {{var}} get a value derived at run time (e.g. a cart id or
    CSRF token). No-op when there are no rules."""
    rules = [r for r in (rules or []) if r.get("var") and (r.get("json_path") or r.get("regex"))]
    if not rules:
        return scenarios
    out: List[PerfScenario] = []
    for sc in scenarios:
        reqs = list(sc.requests)
        for rule in rules:
            match = (rule.get("match") or "").strip()
            idx = next((i for i, rq in enumerate(reqs)
                        if not match or match in (rq.url or "")), None)
            if idx is None:
                continue
            ex = Extraction(var=rule["var"], json_path=rule.get("json_path", "") or "",
                            regex=rule.get("regex", "") or "")
            rq = reqs[idx]
            reqs[idx] = dataclasses.replace(rq, extractions=list(rq.extractions) + [ex])
        out.append(dataclasses.replace(sc, requests=reqs))
    return out


def with_login(scenarios: List[PerfScenario], login_request, token_var: str = "token",
               token_json_path: str = "", regex: str = "") -> List[PerfScenario]:
    """In-test login: PREPEND a login request to every scenario, extract the token
    from its response into `token_var`, and send `Authorization: Bearer {{var}}` on
    the scenario's own requests. So each virtual user logs in at run time and uses
    its own fresh token — no pre-fetching. No-op when login_request is None."""
    if login_request is None:
        return scenarios
    import dataclasses as _dc
    ex = Extraction(var=token_var, json_path=(token_json_path or ""), regex=(regex or ""))
    login = _dc.replace(login_request, extractions=list(login_request.extractions) + [ex])
    auth_hdr = {"Authorization": "Bearer {{" + token_var + "}}"}
    out: List[PerfScenario] = []
    for sc in scenarios:
        reqs = [login]
        for r in sc.requests:
            h = dict(r.headers or {})
            h.update(auth_hdr)
            reqs.append(_dc.replace(r, headers=h))
        out.append(_dc.replace(sc, requests=reqs))
    return out


def emit_scenarios(scenarios: List[PerfScenario], profile: LoadProfile, out_dir: str,
                   target_name: str = "jmeter", data: Optional[DataSource] = None
                   ) -> Tuple[PerfTarget, ProjectPaths]:
    """Emit a tool project from ALREADY-BUILT scenarios (e.g. from a HAR import),
    skipping the prose->request extraction step. Same emit path as build_and_emit,
    so a HAR-sourced plan and a test-case-sourced plan run identically."""
    target = get_target(target_name)
    paths = target.emit(scenarios, profile, out_dir, data=data)
    return target, paths


def apply_thresholds(result: PerfResult, profile: LoadProfile) -> PerfResult:
    """Return a copy of `result` with threshold_pass evaluated (the CI gate)."""
    return dataclasses.replace(result, threshold_pass=profile.passed(result))


def run(target: PerfTarget, paths: ProjectPaths, profile: LoadProfile,
        on_event: OnEvent = noop_event, remote_hosts: str = "",
        cancel_check: CancelCheck = never_cancel) -> PerfResult:
    """Execute an emitted project and return a threshold-evaluated result.
    remote_hosts (comma/space list) drives distributed engines where supported."""
    # Capability-by-signature keeps older third-party targets compatible without
    # catching a TypeError raised *inside* their implementation.
    params = inspect.signature(target.run).parameters
    kwargs = {"on_event": on_event}
    if "remote_hosts" in params:
        kwargs["remote_hosts"] = remote_hosts
    if "cancel_check" in params:
        kwargs["cancel_check"] = cancel_check
    result = target.run(paths, **kwargs)
    return apply_thresholds(result, profile)


__all__ = ["scenarios_from_cases", "build_and_emit", "emit_scenarios",
           "rebase", "with_auth", "parameterize", "correlate", "with_login",
           "apply_thresholds", "run"]
