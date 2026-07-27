"""tracker/azure.py — Azure DevOps adapter, implemented as a WRAPPER over the
existing engine.py functions.

THE KEY DECISION: WRAP, DON'T MOVE (yet)
`TRACKER_BACKENDS_PLAN.md` Phase 0 calls for relocating the Azure code into
this package. This file deliberately does NOT do that yet. It delegates to the
`engine.*` functions exactly as they are and converts their return values into
tracker DTOs.

Why that ordering is safer:
  * `engine.py` is ~10.4k lines and, with `main.py`, is documented in
    DEV_ROADMAP as the app's most fragile file. Moving 40 functions and
    normalizing their return types in one step is a large, unverifiable diff.
  * Wrapping is purely additive: no existing file changes, so the running app
    cannot regress. The current Azure code path stays live and untouched.
  * It makes the interface honest FIRST. If `Backend` can express the real
    Azure behaviour by delegation, the contract is right. If it can't, we find
    out now — while nothing depends on it — instead of after a 10k-line move.
  * Once callers speak DTOs, the engine internals can be relocated behind this
    boundary incrementally, one function at a time, each independently
    verifiable.

So this is a strangler-fig seam, not the final home.

IMPORT IS LAZY, DELIBERATELY. `import engine` is done inside the constructor
rather than at module scope for two reasons: importing `tracker` must stay
cheap and side-effect-free (engine performs module-level config work), and
engine.py uses PEP 701 f-strings that only parse on Python 3.12 — a module-level
import would make the whole `tracker` package unimportable on older
interpreters, taking the FakeBackend and its tests down with it.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .base import Backend, Capability
from .errors import (
    AuthFailed, NotConfigured, NotFound, PermissionDenied, TrackerError,
    Unavailable,
)
from .models import (
    Plan, Project, Ref, ReportData, Sprint, Step, Story, Suite, TaskStats,
    TestCase, User,
)


def _translate(exc, backend="azure"):
    """Map engine.py's RuntimeError-with-a-sentence onto our typed errors.

    engine._azure_get raises hand-written messages per status code. Rather than
    change engine (this file stays additive), we recover the semantics here by
    matching the stable, distinctive parts of those strings. Anything
    unrecognized passes through as a generic TrackerError with the original
    text intact, so no information is ever lost in translation.
    """
    if isinstance(exc, TrackerError):
        return exc
    msg = str(exc)
    low = msg.lower()
    if "organization" in low and "configured" in low:
        return NotConfigured(msg, remedy="Open Setup and fill in 'Azure Organization'.",
                             backend=backend)
    if "authentication failed" in low or "401" in low:
        return AuthFailed(msg, remedy="Check your PAT and its scopes.", backend=backend)
    if "access denied" in low or "403" in low:
        return PermissionDenied(msg, backend=backend)
    if "not found" in low or "404" in low:
        return NotFound(msg, backend=backend)
    if any(k in low for k in ("ssl", "cannot reach", "timed out", "network")):
        return Unavailable(msg, backend=backend)
    return TrackerError(msg, backend=backend)


class AzureBackend(Backend):
    """Azure DevOps, via the existing engine.py implementation."""

    name = "azure"
    label = "Azure DevOps"
    capabilities = frozenset({
        Capability.REQUIREMENT_SUITES, Capability.NESTED_SUITES,
        Capability.STORY_LINKS, Capability.CHILD_TASKS,
        Capability.TESTER_FIELD, Capability.TASK_STATS,
        Capability.EXECUTION_STATUS, Capability.SPRINT_REPORTS,
        Capability.ATTACHMENTS, Capability.DELETE_TEST_CASE,
        # NOT STEP_TEST_DATA: Azure's steps XML has no testData field, so
        # Step.data cannot survive a round-trip here. Declared rather than
        # silently dropped — the contract suite checks this claim.
    })

    def __init__(self, engine=None):
        if engine is None:
            import engine as engine_module   # lazy — see module docstring
            engine = engine_module
        self._e = engine

    # ── helpers ───────────────────────────────────────────────────────────
    def _org(self):
        return (getattr(self._e, "AZURE_ORG", "") or "").strip()

    @staticmethod
    def _project_name(project):
        """engine.* addresses projects by NAME; tracker addresses them by Ref."""
        if isinstance(project, Project):
            return project.name or project.ref.key
        return str(project)

    def _ensure_sdk(self):
        """Initialize the azure-devops SDK clients if they aren't already.

        Found by the contract suite against a live org ("fetch_stories returned
        nothing for a known ref"). A whole family of engine functions goes
        through `_wit_client` rather than `_azure_get` — fetch_stories,
        fetch_test_case_steps/detail/title, update_test_case_with_steps,
        create_child_tasks, assign_tester — and `_wit_client` starts as None.
        The app happens to initialize it in exactly ONE place (main.py's run
        path), so anything reaching engine another way finds it unset.

        What made this genuinely dangerous rather than merely broken: those
        call sites wrap the SDK call in `except Exception: pass`, so an unset
        client raises AttributeError, gets swallowed, and the function returns
        an EMPTY LIST. No error, no log — just silently no stories, which in
        the UI is indistinguishable from "this sprint has none".

        `connect_azure_sdk(project)` ignores its `project` argument (the
        connection is org-level), so there is nothing to thread through here.
        """
        if getattr(self._e, "_wit_client", None) is not None:
            return
        try:
            self._e.connect_azure_sdk(None)
        except Exception as exc:
            raise _translate(exc, self.name) from exc

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            raise _translate(exc, self.name) from exc

    # ── core ──────────────────────────────────────────────────────────────
    def validate_credentials(self):
        pat = getattr(self._e, "AZURE_PAT", "") or ""
        ok, msg = self._call(self._e.validate_pat, pat)
        if not ok:
            raise _translate(RuntimeError(msg), self.name)

    def fetch_projects(self):
        data = self._call(self._e.fetch_projects) or {}
        rows = data.get("value", data) if isinstance(data, dict) else data
        out = []
        for row in rows or []:
            if isinstance(row, dict):
                pid = str(row.get("id") or row.get("name") or "")
                nm = row.get("name") or pid
            else:
                pid = nm = str(row)
            out.append(Project(ref=Ref(id=pid, key=nm), name=nm))
        return out

    def fetch_sprints(self, project):
        rows = self._call(self._e.fetch_iterations, self._project_name(project)) or []
        out = []
        for row in rows:
            if isinstance(row, dict):
                path = row.get("path") or row.get("iterationPath") or ""
                name = row.get("name") or path.rsplit("\\", 1)[-1]
                sid = str(row.get("id") or path)
            else:
                path = name = str(row)
                sid = path
            out.append(Sprint(ref=Ref(id=sid, key=name), name=name, path=path))
        return out

    def fetch_stories_in_sprint(self, project, sprint_path):
        rows = self._call(self._e.fetch_stories_in_iteration,
                          self._project_name(project), sprint_path) or []
        return [self._to_story(r, sprint_path) for r in rows]

    def fetch_stories(self, refs):
        self._ensure_sdk()          # engine.fetch_stories is SDK-backed
        ids = [r.id if isinstance(r, Ref) else str(r) for r in refs]
        rows = self._call(self._e.fetch_stories, ids) or []
        return [self._to_story(r) for r in rows]

    def _to_story(self, row, sprint_path=""):
        # THREE shapes reach here, which is why this is defensive rather than
        # a straight dict read:
        #   1. SDK WorkItem objects (.id / .fields)      — engine.fetch_stories
        #   2. flat dicts {"id","title"}                 — fetch_stories_in_iteration
        #   3. raw REST dicts {"id","fields":{...}}      — other REST paths
        # Handling only (2)/(3) is what made the live contract run produce
        # degenerate Story objects for the SDK path.
        if not isinstance(row, dict):
            wid = getattr(row, "id", None)
            wfields = getattr(row, "fields", None)
            if wid is not None or isinstance(wfields, dict):
                row = {"id": wid, "fields": wfields or {}}
            else:
                return Story(ref=Ref(id=str(row)), title=str(row),
                             sprint_path=sprint_path)
        fields = row.get("fields") or row
        sid = str(row.get("id") or fields.get("System.Id") or "")
        assignee = None
        raw_assignee = fields.get("System.AssignedTo")
        if isinstance(raw_assignee, dict):
            assignee = User(id=raw_assignee.get("uniqueName") or raw_assignee.get("id") or "",
                            display_name=raw_assignee.get("displayName") or "",
                            email=raw_assignee.get("uniqueName") or "")
        elif isinstance(raw_assignee, str) and raw_assignee:
            assignee = User(id=raw_assignee, display_name=raw_assignee)
        return Story(
            ref=Ref(id=sid),
            title=fields.get("System.Title") or row.get("title") or "",
            description=fields.get("System.Description") or "",
            acceptance_criteria=(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria")
                                 or row.get("criteria") or ""),
            assignee=assignee,
            state=fields.get("System.State") or "",
            url=self.item_url(Ref(id=sid)),
            sprint_path=sprint_path or fields.get("System.IterationPath") or "")

    def fetch_test_plans(self, project):
        data = self._call(self._e.fetch_test_plans, self._project_name(project)) or {}
        rows = data.get("value", data) if isinstance(data, dict) else data
        out = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or "")
            out.append(Plan(ref=Ref(id=pid), name=row.get("name") or pid))
        return out

    def create_test_plan(self, project, name, sprint_path=""):
        row = self._call(self._e.create_test_plan, self._project_name(project),
                         name, sprint_path) or {}
        pid = str(row.get("id") or "") if isinstance(row, dict) else str(row)
        return Plan(ref=Ref(id=pid), name=name, sprint_path=sprint_path)

    def ensure_suite_for_story(self, project, plan, story):
        sid = self._call(self._e.create_requirement_suite,
                         self._project_name(project), plan.ref.id, story.ref.id)
        suite_id = str(sid.get("id") if isinstance(sid, dict) else sid)
        return Suite(ref=Ref(id=suite_id), name=story.title,
                     parent_ref=plan.ref, story_ref=story.ref)

    def fetch_test_cases_for_suite(self, project, plan, suite):
        rows = self._call(self._e.fetch_test_cases_for_suite,
                          self._project_name(project), plan.ref.id, suite.ref.id) or []
        out = []
        for row in rows:
            if isinstance(row, dict):
                cid = str(row.get("id") or row.get("testCaseId") or "")
                title = row.get("title") or row.get("name") or ""
            else:
                cid, title = str(row), ""
            out.append(TestCase(ref=Ref(id=cid), title=title, suite_ref=suite.ref,
                                story_ref=suite.story_ref,
                                url=self.item_url(Ref(id=cid), project)))
        return out

    def fetch_existing_titles_for_suite(self, project, plan, suite):
        return list(self._call(self._e.fetch_existing_titles_for_suite,
                               self._project_name(project), plan.ref.id,
                               suite.ref.id) or [])

    def create_test_case(self, project, plan, suite, title, story=None):
        cid = self._call(self._e.create_test_case, self._project_name(project),
                         plan.ref.id, suite.ref.id, title,
                         story.ref.id if story else None)
        case_id = str(cid.get("id") if isinstance(cid, dict) else cid)
        return TestCase(ref=Ref(id=case_id), title=title, suite_ref=suite.ref,
                        story_ref=(story.ref if story else suite.story_ref),
                        url=self.item_url(Ref(id=case_id), project))

    def fetch_test_case_steps(self, ref):
        self._ensure_sdk()          # SDK-backed engine call
        rows = self._call(self._e.fetch_test_case_steps, ref.id) or []
        out = []
        for i, row in enumerate(rows, start=1):
            if isinstance(row, dict):
                action = row.get("action") or row.get("step") or ""
                expected = row.get("expected") or row.get("expectedResult") or ""
            elif isinstance(row, (list, tuple)):
                action = row[0] if len(row) > 0 else ""
                expected = row[1] if len(row) > 1 else ""
            else:
                action, expected = str(row), ""
            out.append(Step(action=action, expected=expected, index=i))
        return out

    def update_test_case_steps(self, ref, steps):
        self._ensure_sdk()          # SDK-backed engine call
        # engine.build_steps_xml is already the right seam — it just gets called
        # too late today (callers hand XML around). Here the XML never escapes.
        payload = [{"action": s.action, "expected": s.expected} for s in steps]
        xml = self._call(self._e.build_steps_xml, payload)
        self._call(self._e.update_test_case_with_steps, ref.id, xml, None)

    def item_url(self, ref, project=None):
        org = self._org()
        proj = self._project_name(project) if project is not None else ""
        if not org:
            return ""
        if proj:
            return f"https://dev.azure.com/{org}/{proj}/_workitems/edit/{ref.id}"
        return f"https://dev.azure.com/{org}/_workitems/edit/{ref.id}"

    # ── extended ──────────────────────────────────────────────────────────
    def delete_test_case(self, project, plan, suite, ref):
        self._call(self._e.delete_test_case, self._project_name(project),
                   plan.ref.id, suite.ref.id, ref.id)

    def fetch_story_screenshots(self, story):
        self._ensure_sdk()          # SDK-backed engine call
        raw = self._call(self._e.fetch_story_screenshots, {"id": story.ref.id})
        return list(raw or [])

    def fetch_project_members(self, project):
        rows = self._call(self._e.fetch_project_members,
                          self._project_name(project)) or []
        out = []
        for row in rows:
            if isinstance(row, dict):
                # engine.fetch_project_members returns {"name","email"} — NOT
                # {"displayName","uniqueName"}. Reading the wrong keys gave every
                # member an empty email, and backend_setup's hybrid flatten drops
                # emailless members → "No members found" on Azure→TestRail (pure
                # Azure was unaffected: it consumes engine's dicts directly and
                # never goes through this adapter). Accept both shapes so this
                # can't silently break again if either side is refactored.
                email = row.get("email") or row.get("uniqueName") or ""
                name = row.get("name") or row.get("displayName") or ""
                out.append(User(id=email or row.get("id") or "",
                                display_name=name, email=email))
            else:
                out.append(User(id=str(row), display_name=str(row)))
        return out

    def tester_field_options(self, project):
        proj = self._project_name(project)
        ref = self._call(self._e.resolve_field_ref, proj, "Assigned To Tester")
        return list(self._call(self._e.tester_allowed_values, proj, ref) or [])

    def assign_testers(self, project, assignments):
        self._call(self._e.assign_testers, assignments)

    def create_child_tasks(self, project, items):
        self._ensure_sdk()          # SDK-backed engine call
        made = self._call(self._e.create_child_tasks,
                          self._project_name(project), items) or []
        return [Ref(id=str(m.get("id") if isinstance(m, dict) else m)) for m in made]

    def fetch_task_stats(self, project, sprint_path="", assignee=""):
        raw = self._call(self._e.fetch_user_task_stats, self._project_name(project),
                         sprint_path or None, assignee or None) or {}
        rows = raw.get("rows", raw) if isinstance(raw, dict) else raw
        out = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            out.append(TaskStats(
                user=User(id=row.get("assignee") or "",
                          display_name=row.get("assignee") or ""),
                total=int(row.get("total") or 0),
                completed=int(row.get("completed") or 0),
                in_progress=int(row.get("in_progress") or 0),
                remaining_hours=float(row.get("remaining") or 0.0),
                completed_hours=float(row.get("completed_hours") or 0.0)))
        return out

    def sprint_report_data(self, project, sprint_path):
        raw = self._call(self._e.sprint_report_data,
                         self._project_name(project), sprint_path) or {}
        if not isinstance(raw, dict):
            raw = {}
        return ReportData(project=self._project_name(project), sprint=sprint_path,
                          total_cases=int(raw.get("total") or 0),
                          passed=int(raw.get("passed") or 0),
                          failed=int(raw.get("failed") or 0),
                          blocked=int(raw.get("blocked") or 0),
                          not_run=int(raw.get("not_run") or 0),
                          per_story=list(raw.get("per_story") or []))


__all__ = ["AzureBackend"]
