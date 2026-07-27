"""tracker/testrail.py — TestRail backend (standalone, not Jira-based).

WHY THIS ONE IS DIFFERENT
TestRail is not a Jira app — it's an independent SaaS (`{org}.testrail.io`) with
a mature, stable REST API and dead-simple auth (email + API key, HTTP Basic).
That makes it the FIRST backend that can be live-verified without waiting on any
Atlassian marketplace provisioning — the thing that has blocked Zephyr and Xray.

STRUCTURAL CONSEQUENCE: it inherits NOTHING from the Jira read path. TestRail has
no stories, sprints, or requirements — it's a pure test-case library
(project → suite → section → case). So `TestRailBackend` extends `Backend`
directly (not `JiraZephyrBackend`), and the story/sprint read methods return
empty rather than pretending. In the app it's naturally a WRITE TARGET: requirements
would be read from Jira/Azure and cases written here. That pairing is a separate
design decision (see TRACKER_BACKENDS_PLAN.md); this file implements the write
side and is verifiable on its own via run_testrail_probe.py.

API SHAPE (confirmed against TestRail API v2 docs):
  * URL form is unusual: {base}/index.php?/api/v2/{method}[/{id}][&param=…].
    Params ride AFTER the method with `&`, not a normal query string — so URLs
    are built by hand, not via requests `params=`.
  * Auth: HTTP Basic (email : api_key). Enable in Administration → Site Settings
    → API (+ "Enable API for individual users"); key from My Settings.
  * `add_case` / `update_case` take `custom_steps_separated` (a list of
    {content, expected}); submitting it REPLACES all steps — native OVERWRITE,
    exactly what `update_test_case_steps` wants.
  * Newer responses are paginated ({offset, limit, size, _links, <key>:[…]});
    older ones are a bare list. Handled both ways.

STATUS: written from the documented contract; run_testrail_probe.py is the live
gate.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from .base import Backend, Capability
from .errors import NotConfigured, TrackerError
from .http import TrackerSession
from .models import (
    Plan, Project, Ref, ReportData, Sprint, Step, Story, Suite, TestCase, User,
)

_TESTRAIL_SUFFIXES = (".testrail.io", ".testrail.com")


def validate_testrail_url(url, allow_any_host=False):
    """Validate a user-supplied TestRail base URL before any key is sent to it.

    Same SSRF/key-exfiltration guard as the Jira site field (that field is free
    text too): reject non-HTTPS, loopback/private/reserved IPs, and embedded
    credentials. Cloud hosts are `*.testrail.io`; self-hosted needs the explicit
    opt-in. Returns the normalized origin (scheme+host, no path).
    """
    raw = (url or "").strip()
    if not raw:
        raise NotConfigured("No TestRail URL configured.",
                            remedy="Enter your TestRail address, e.g. https://your-org.testrail.io")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        raise NotConfigured("TestRail URL must use HTTPS.",
                            remedy="Use an https:// URL — the API key is sent with every request.")
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        raise NotConfigured("TestRail URL must not contain credentials.",
                            remedy="Enter the host only, e.g. https://your-org.testrail.io")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise NotConfigured("TestRail URL has no host.",
                            remedy="e.g. https://your-org.testrail.io")
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        addr = None
    if addr is not None:
        raise NotConfigured("Enter a TestRail hostname, not an IP address.",
                            remedy="e.g. https://your-org.testrail.io")
    if host in ("localhost",) or host.endswith(".local"):
        raise NotConfigured(f"Refusing to connect to {host}.",
                            remedy="Enter your TestRail address, e.g. https://your-org.testrail.io")
    if not allow_any_host and not host.endswith(_TESTRAIL_SUFFIXES):
        raise NotConfigured(
            f"{host} is not a recognized TestRail Cloud host.",
            remedy="Use your *.testrail.io URL, or enable self-hosted mode in Setup.")
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return f"https://{host}{port}"


class TestRailBackend(Backend):
    """TestRail Cloud/Server. Standalone test-case library — write target."""

    name = "testrail"
    label = "TestRail"
    capabilities = frozenset({
        Capability.REQUIREMENT_SUITES, Capability.NESTED_SUITES,
        Capability.DELETE_TEST_CASE,
        # NOT STEP_TEST_DATA: `custom_steps_separated` rows are {content,
        # expected} only — no per-step data column by default, so Step.data
        # can't round-trip. Declared, not silently dropped.
        # NOT STORY_LINKS / sprints / task-manager: TestRail has no Jira issues.
    })

    def __init__(self, base_url="", email="", api_key="",
                 allow_any_host=False, session=None):
        self.base = validate_testrail_url(base_url, allow_any_host) if base_url else ""
        self._email = email or ""
        self._sess = session if session is not None else (
            TrackerSession(base_url=self.base, auth=(email, api_key),
                           backend="testrail",
                           headers={"Content-Type": "application/json"})
            if base_url else None)
        self._tmpl_cache = {}          # project id -> steps-template id

    def _need(self):
        if self._sess is None:
            raise NotConfigured("TestRail is not configured.",
                                remedy="Enter your TestRail URL, email and API key in Setup.")
        return self._sess

    def _url(self, method):
        return f"{self.base}/index.php?/api/v2/{method}"

    def _get(self, method):
        return self._need().get(self._url(method))

    def _post(self, method, body):
        return self._need().post(self._url(method), json=body)

    def _list(self, method, key, max_pages=100):
        """Return all rows for a list method, handling both the new paginated
        envelope and the legacy bare-array response."""
        out, offset, limit = [], 0, 250
        for _ in range(max_pages):
            payload = self._get(f"{method}&offset={offset}&limit={limit}")
            if isinstance(payload, list):        # legacy: bare array, no paging
                return payload
            rows = (payload or {}).get(key) or []
            out.extend(rows)
            links = (payload or {}).get("_links") or {}
            if not links.get("next") or not rows:
                break
            offset += limit
        return out

    def close(self):
        if self._sess is not None:
            self._sess.close()

    # ── core ──────────────────────────────────────────────────────────────
    def validate_credentials(self):
        # get_projects is the cheapest authenticated call; a bad key → 401.
        self._get("get_projects")

    def fetch_projects(self):
        rows = self._list("get_projects", "projects")
        return [Project(ref=Ref(id=str(p.get("id") or ""), key=str(p.get("id") or "")),
                        name=p.get("name") or "")
                for p in rows if isinstance(p, dict)]

    def create_project(self, name):
        """Create a TestRail project (used by the hybrid's auto-create-by-name).
        suite_mode 3 = MULTIPLE suites — required for QA Studio's "plan" concept
        to work: a plan maps to a TestRail SUITE, and only mode 3 lets
        create_test_plan actually create a NAMED suite. Mode 1 (single) silently
        reuses the one "Master" suite, so every created plan collapsed onto Master
        and nothing new appeared in TestRail (reported live). A fresh mode-3
        project still gets a default suite, and stories/cases map to sections
        underneath the chosen suite exactly as before."""
        row = self._post("add_project",
                         {"name": (name or "Untitled")[:250], "suite_mode": 3}) or {}
        return Project(ref=Ref(id=str(row.get("id") or ""),
                               key=str(row.get("id") or "")),
                       name=row.get("name") or name)

    # TestRail has no requirements/sprints. Empty, honestly — not an error.
    def fetch_sprints(self, project):
        return []

    def fetch_stories_in_sprint(self, project, sprint_path):
        return []

    def fetch_stories(self, refs):
        return []

    def fetch_test_plans(self, project):
        """Plan → TestRail suite. Returns the project's suites."""
        rows = self._list(f"get_suites/{project.ref.id}", "suites")
        return [Plan(ref=Ref(id=str(s.get("id") or "")), name=s.get("name") or "")
                for s in rows if isinstance(s, dict)]

    def create_test_plan(self, project, name, sprint_path=""):
        pid = project.ref.id
        proj = self._get(f"get_project/{pid}") or {}
        # suite_mode: 1=single, 2=single+baselines, 3=multiple. Only mode 3 lets
        # you add suites; otherwise reuse the project's one auto-created suite.
        mode = proj.get("suite_mode", 1) if isinstance(proj, dict) else 1
        suites = self._list(f"get_suites/{pid}", "suites")
        if mode != 3:
            s = suites[0] if suites else self._post(f"add_suite/{pid}", {"name": name})
        else:
            s = next((x for x in suites if (x.get("name") or "") == name), None) \
                or self._post(f"add_suite/{pid}", {"name": name})
        return Plan(ref=Ref(id=str((s or {}).get("id") or "")),
                    name=(s or {}).get("name") or name, sprint_path=sprint_path)

    def ensure_suite_for_story(self, project, plan, story):
        """Suite → a TestRail section under the plan's suite. Idempotent by name
        so regeneration lands in the same section (the dedupe requirement)."""
        pid, suite_id = project.ref.id, plan.ref.id
        name = (f"{story.ref.key} {story.title}".strip()
                if story is not None else "QA Studio")
        sections = self._list(f"get_sections/{pid}&suite_id={suite_id}", "sections")
        sec = next((x for x in sections if (x.get("name") or "") == name), None)
        if sec is None:
            sec = self._post(f"add_section/{pid}",
                             {"name": name[:250], "suite_id": int(suite_id)})
        return Suite(ref=Ref(id=str((sec or {}).get("id") or "")), name=name,
                     parent_ref=plan.ref,
                     story_ref=(story.ref if story is not None else None))

    def find_suite_for_story(self, project, plan, story):
        """Non-creating lookup of the story's SECTION (see base.py). Same name
        match as ensure_suite_for_story, minus the add_section call — so
        counting existing cases can't create empty sections as a side effect."""
        pid, suite_id = project.ref.id, plan.ref.id
        if not (pid and suite_id):
            return None
        name = (f"{story.ref.key} {story.title}".strip()
                if story is not None else "QA Studio")
        try:
            sections = self._list(f"get_sections/{pid}&suite_id={suite_id}", "sections")
        except TrackerError:
            return None
        sec = next((x for x in sections
                    if (x.get("name") or "") == name), None)
        if not sec:
            return None
        return Suite(ref=Ref(id=str(sec.get("id") or "")), name=name,
                     parent_ref=plan.ref,
                     story_ref=(story.ref if story is not None else None))

    def fetch_test_cases_for_suite(self, project, plan, suite):
        rows = self._list(
            f"get_cases/{project.ref.id}&suite_id={plan.ref.id}&section_id={suite.ref.id}",
            "cases")
        return [TestCase(ref=Ref(id=str(c.get("id") or "")), title=c.get("title") or "",
                         suite_ref=suite.ref, story_ref=suite.story_ref,
                         url=self.item_url(Ref(id=str(c.get("id") or ""))))
                for c in rows if isinstance(c, dict)]

    def _steps_template_id(self, project):
        """The id of the project's separated-steps template ("Test Case (Steps)").

        Found live (probe: "wrote 2, read 0"): `custom_steps_separated` only
        exists on cases whose TEMPLATE supports separated steps. The DEFAULT
        template is "Test Case (Text)" — a single text field, no separated-steps
        field — so setting the field on such a case is silently dropped and reads
        back empty. Template ids vary per project, so discover the steps one by
        name (matches "steps"). Cached; None if the project has no such template,
        in which case we create with the default and step-writes won't stick
        (declared honestly rather than pretending)."""
        pid = project.ref.id
        if pid in self._tmpl_cache:
            return self._tmpl_cache[pid]
        tid = None
        try:
            for t in (self._get(f"get_templates/{pid}") or []):
                if isinstance(t, dict) and "step" in (t.get("name") or "").lower():
                    tid = t.get("id")
                    break
        except Exception:
            tid = None
        self._tmpl_cache[pid] = tid
        return tid

    def create_test_case(self, project, plan, suite, title, story=None):
        body = {"title": (title or "")[:250]}
        tid = self._steps_template_id(project)
        if tid is not None:
            # Create with the separated-steps template so update_test_case_steps
            # has a custom_steps_separated field to write into.
            body["template_id"] = tid
        # References: link the case back to its source requirement (the user
        # story), so TestRail's "References" column shows e.g. 101046 and — with
        # a reference-URL integration configured — deep-links to Azure/Jira.
        if story is not None:
            ref = (story.ref.key or story.ref.id) if getattr(story, "ref", None) else ""
            if ref:
                body["refs"] = str(ref)[:250]
        row = self._post(f"add_case/{suite.ref.id}", body) or {}
        cid = str(row.get("id") or "")
        return TestCase(ref=Ref(id=cid), title=title, suite_ref=suite.ref,
                        story_ref=(story.ref if story else suite.story_ref),
                        url=self.item_url(Ref(id=cid)))

    def fetch_test_case_steps(self, ref):
        c = self._get(f"get_case/{ref.id}") or {}
        rows = (c.get("custom_steps_separated") if isinstance(c, dict) else None) or []
        return [Step(action=r.get("content") or "", expected=r.get("expected") or "",
                     index=i)
                for i, r in enumerate(rows, start=1)]

    def update_test_case_steps(self, ref, steps):
        # custom_steps_separated REPLACES all steps — native OVERWRITE.
        payload = [{"content": s.action or "", "expected": s.expected or ""}
                   for s in steps]
        body = {"custom_steps_separated": payload}
        # PRECONDITIONS: TestRail has a dedicated field, so use it instead of
        # inheriting Azure's "Precondition: … / Action: …" text folded into step
        # 1 (Azure has no such field; see backend_setup._unfold_precondition).
        # The field is CASE-level while the generator produces PER-STEP
        # preconditions, so several are joined and numbered by step — dropping
        # all but the first would silently lose setup context.
        pres = [(s.index or i, (getattr(s, "pre", "") or "").strip())
                for i, s in enumerate(steps, start=1)]
        pres = [(idx, txt) for idx, txt in pres if txt]
        if pres:
            seen, lines = set(), []
            for idx, txt in pres:
                if txt in seen:      # identical precondition repeated per step
                    continue
                seen.add(txt)
                lines.append(txt if len(pres) == 1 else f"{idx}. {txt}")
            body["custom_preconds"] = "\n".join(lines)
        try:
            self._post(f"update_case/{ref.id}", body)
        except TrackerError:
            # Not every TestRail template exposes custom_preconds; a rejected
            # field must not cost the user their STEPS. Retry without it —
            # same fail-soft shape as the steps-template discovery above.
            if "custom_preconds" not in body:
                raise
            body.pop("custom_preconds", None)
            self._post(f"update_case/{ref.id}", body)

    def delete_test_case(self, project, plan, suite, ref):
        self._post(f"delete_case/{ref.id}", {})

    def item_url(self, ref, project=None):
        if not self.base or not ref.id:
            return ""
        return f"{self.base}/index.php?/cases/view/{ref.id}"


__all__ = ["TestRailBackend", "validate_testrail_url"]
