"""tracker/composite.py — the HYBRID backend: read from one tracker, write to another.

WHAT IT'S FOR
"Read the user stories from Azure/Jira, write the generated test cases into
TestRail." The app's whole model is one `Backend` that both reads requirements
and writes cases — but TestRail has no requirements to read, and Azure/Jira are
where the stories live. `CompositeBackend` bridges them: every READ delegates to
a source backend (Azure or Jira), every WRITE delegates to a target backend
(TestRail).

THE ONE HARD PROBLEM: TWO UNRELATED PROJECT LISTS
The story lives in an Azure/Jira project; the cases go into a TestRail project.
Those lists don't map. Per the chosen design (auto-create/match by source name),
the target project is resolved from the SOURCE project's name — matched if a
TestRail project with that name exists, created otherwise. So the app still
selects ONE project (the source), and writes are transparently retargeted to the
matching TestRail project.

ADDITIVE & ISOLATED: this composes existing, individually-verified backends via
their public `Backend` interface. It does not touch AzureBackend, the Jira read
path, or TestRailBackend. The single-backend Azure workflow is unaffected.

STATUS: mechanism stub-verified (read from a fake source, write to a fake
target, project auto-matched). The live flow needs its own probe.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Optional

from .base import Backend, Capability
from .errors import NotConfigured
from .models import (
    Plan, Project, Ref, ReportData, Sprint, Step, Story, Suite, TestCase, User,
)


class CompositeBackend(Backend):
    """Reads from `source`, writes to `target`. Target project matched/created
    by the source project's name."""

    def __init__(self, source, target, name="composite", label="Hybrid"):
        self._src = source
        self._tgt = target
        self.name = name
        self.label = label
        # Reads come from the source, writes from the target — advertise the
        # union so the UI enables what either side genuinely supports.
        self.capabilities = frozenset(
            set(getattr(source, "capabilities", frozenset()))
            | set(getattr(target, "capabilities", frozenset())))
        self._lock = threading.Lock()
        self._proj_map = {}       # source project id -> target Project
        self._plan_map = {}       # "tgtproj:srcplan" -> target Plan

    # ── project bridge (auto-create / match by name) ──────────────────────
    def _target_project(self, source_project):
        """The TestRail project for a given source project — matched by name,
        created if absent. Cached per source project."""
        if not isinstance(source_project, Project):
            # A bare id/name slipped through; wrap it so .name/.ref exist.
            source_project = Project(ref=Ref(id=str(source_project)),
                                     name=str(source_project))
        key = source_project.ref.id or source_project.name
        with self._lock:
            if key in self._proj_map:
                return self._proj_map[key]
        name = (source_project.name or source_project.ref.key or "").strip()
        match = None
        for p in self._tgt.fetch_projects():
            if (p.name or "").strip().lower() == name.lower():
                match = p
                break
        if match is None:
            match = self._tgt.create_project(name)
        with self._lock:
            self._proj_map[key] = match
        return match

    def _target_plan(self, source_project, source_plan):
        """The TARGET plan (TestRail suite) for a chosen SOURCE plan — matched by
        the source plan's name under the matched TestRail project, created if
        absent. On a single-suite TestRail project this resolves to the one
        auto-created suite ('Master'). Cached per source plan."""
        tgt_proj = self._target_project(source_project)
        sp_id = source_plan.ref.id if isinstance(source_plan, Plan) else str(source_plan)
        name = (source_plan.name if isinstance(source_plan, Plan) else str(source_plan)) or "Tests"
        key = f"{tgt_proj.ref.id}:{sp_id}"
        with self._lock:
            if key in self._plan_map:
                return self._plan_map[key]
        match = None
        for p in self._tgt.fetch_test_plans(tgt_proj):
            if (p.name or "").strip().lower() == name.strip().lower():
                match = p
                break
        if match is None:
            match = self._tgt.create_test_plan(tgt_proj, name)
        with self._lock:
            self._plan_map[key] = match
        return match

    # ── connection ────────────────────────────────────────────────────────
    def validate_credentials(self):
        # Validate what the hybrid actually USES: the source's READ access (not
        # its own test-management half — e.g. a Jira source must not require a
        # Zephyr token here) and the target's full access. fetch_projects is the
        # cheapest authenticated read on the source.
        self._src.fetch_projects()
        self._tgt.validate_credentials()

    # ── reads → source ────────────────────────────────────────────────────
    def fetch_projects(self):
        return self._src.fetch_projects()

    def fetch_sprints(self, project):
        return self._src.fetch_sprints(project)

    def fetch_stories_in_sprint(self, project, sprint_path):
        return self._src.fetch_stories_in_sprint(project, sprint_path)

    def fetch_bugs_in_sprint(self, project, sprint_path):
        # Bugs live with the STORIES (read source), same as sprint_report_data.
        return self._src.fetch_bugs_in_sprint(project, sprint_path)

    def fetch_stories(self, refs):
        return self._src.fetch_stories(refs)

    def fetch_story_screenshots(self, story):
        return self._src.fetch_story_screenshots(story)

    def fetch_test_plans(self, project):
        # Plans are READ from the SOURCE when the source HAS a test-plan concept:
        # AZURE_TESTRAIL picks an Azure plan that actually has requirement-suite
        # stories. But a JIRA_TESTRAIL source is Jira-CORE (no Zephyr/Xray), whose
        # fetch_test_plans reads Zephyr folders and raises NotConfigured — Jira
        # core has no plans at all. For that hybrid the plan container lives in the
        # TestRail TARGET (stories come from the Jira sprint, decoupled from the
        # plan — see backend_setup.fetch_stories_for_plan). Fall back to the target
        # so the picker loads instead of erroring "Zephyr Scale is not configured".
        try:
            return self._src.fetch_test_plans(project)
        except NotConfigured:
            return self._tgt.fetch_test_plans(self._target_project(project))

    # ── writes → target, mapping the chosen SOURCE plan to a TestRail plan ──
    def create_test_plan(self, project, name, sprint_path=""):
        # The user never creates a plan in the hybrid (they pick a source plan);
        # this exists for completeness and lands in the TestRail target.
        return self._tgt.create_test_plan(self._target_project(project), name, sprint_path)

    def ensure_suite_for_story(self, project, plan, story):
        return self._tgt.ensure_suite_for_story(
            self._target_project(project), self._target_plan(project, plan), story)

    def find_suite_for_story(self, project, plan, story):
        """Non-creating lookup — delegated to the WRITE target, same as
        ensure_suite_for_story (the suite lives where the cases are written).

        This class delegates every method EXPLICITLY, so a new interface method
        that isn't added here silently falls through to Backend's default. That
        default is `return None`, which made the existing-test-case count read 0
        on every hybrid while looking like it had worked — no error, no log."""
        return self._tgt.find_suite_for_story(
            self._target_project(project), self._target_plan(project, plan), story)

    def fetch_test_cases_for_suite(self, project, plan, suite):
        return self._tgt.fetch_test_cases_for_suite(
            self._target_project(project), self._target_plan(project, plan), suite)

    def fetch_existing_titles_for_suite(self, project, plan, suite):
        return self._tgt.fetch_existing_titles_for_suite(
            self._target_project(project), self._target_plan(project, plan), suite)

    def create_test_case(self, project, plan, suite, title, story=None):
        return self._tgt.create_test_case(
            self._target_project(project), self._target_plan(project, plan), suite, title, story)

    def fetch_test_case_steps(self, ref):
        # ref is a TARGET (TestRail) case id — no project needed.
        return self._tgt.fetch_test_case_steps(ref)

    def update_test_case_steps(self, ref, steps):
        return self._tgt.update_test_case_steps(ref, steps)

    def delete_test_case(self, project, plan, suite, ref):
        return self._tgt.delete_test_case(
            self._target_project(project), self._target_plan(project, plan), suite, ref)

    def item_url(self, ref, project=None):
        # A test-case link points at the TARGET (where the case lives).
        return self._tgt.item_url(ref)

    # ── Task Manager & tester assignment → SOURCE ────────────────────────────
    # These are all STORY-side: team members, task rollups, child tasks and the
    # tester field belong to the work-item tracker (Azure/Jira), not to the
    # test-case store. This class delegates every method explicitly, so before
    # this block they fell through to Backend's defaults — which raise
    # "not supported" — and Task Manager simply refused to work on a hybrid.
    def fetch_project_members(self, project):
        return self._src.fetch_project_members(project)

    def tester_field_options(self, project):
        return self._src.tester_field_options(project)

    def assign_testers(self, project, assignments):
        return self._src.assign_testers(project, assignments)

    def create_child_tasks(self, project, items):
        return self._src.create_child_tasks(project, items)

    def fetch_subtasks(self, project, story):
        return self._src.fetch_subtasks(project, story)

    def subtask_field_schema(self, ref):
        return self._src.subtask_field_schema(ref)

    def update_subtask(self, ref, fields):
        return self._src.update_subtask(ref, fields)

    def subtask_work_types(self, project):
        return self._src.subtask_work_types(project)

    def subtask_create_field_schema(self, project, type_id):
        return self._src.subtask_create_field_schema(project, type_id)

    def create_subtask(self, project, parent, type_id, summary, fields=None):
        return self._src.create_subtask(project, parent, type_id, summary, fields)

    def fetch_task_stats(self, project, sprint_path="", assignee=""):
        return self._src.fetch_task_stats(project, sprint_path=sprint_path,
                                          assignee=assignee)

    def sprint_report_data(self, project, sprint_path):
        # Sprint closure inputs are stories/bugs — SOURCE side, same reasoning.
        return self._src.sprint_report_data(project, sprint_path)

    def create_project(self, name):
        # The one exception in this block: creating a project is a WRITE, and
        # its own docstring in base.py says it exists for "the hybrid's
        # auto-create-by-name" on TestRail.
        return self._tgt.create_project(name)

    def close(self):
        for b in (self._src, self._tgt):
            try:
                b.close()
            except Exception:
                pass


__all__ = ["CompositeBackend"]
