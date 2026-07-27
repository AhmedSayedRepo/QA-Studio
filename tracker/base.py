"""tracker/base.py — the Backend contract every tracker adapter implements.

DESIGN: TWO TIERS, NOT ONE FLAT ABC
Methods split into:

  * CORE (@abstractmethod) — the generation loop. Connect, list projects and
    sprints, read stories + acceptance criteria, create suites and test cases,
    read/write steps. A backend that cannot do these is not a backend; making
    them abstract means Python refuses to instantiate a half-built adapter.

  * EXTENDED (default raises BackendUnsupported) — Task Manager, tester
    assignment, sprint reports. Real products differ here: Zephyr Squad has no
    folder tree, not every Jira site exposes a tester field, and a read-only
    connection can't create sub-tasks. Forcing these abstract would push every
    adapter to write `raise NotImplementedError` stubs, which turns an honest
    capability gap into a crash at the call site.

Capability flags are how the UI finds out WITHOUT calling: grey the control out
rather than letting the user click into an exception. BackendUnsupported is the
backstop for a missed guard, not the intended path.

WHY NOT typing.Protocol: an ABC gives instantiation-time enforcement, which is
what actually catches a missing method during Phase 0's port. Structural typing
would let a partial adapter through until first call.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional

from .errors import BackendUnsupported
from .models import (
    Plan, Project, Ref, ReportData, Sprint, Step, Story, Suite, TaskStats,
    TestCase, User,
)


class Capability:
    """Declarative feature flags. Plain strings so they survive being stored in
    creds/JSON and compared across a process boundary (run_worker.py)."""

    REQUIREMENT_SUITES = "requirement_suites"   # a suite bound to a story
    NESTED_SUITES = "nested_suites"             # suite trees, not a flat list
    STORY_LINKS = "story_links"                 # explicit case↔story traceability
    CHILD_TASKS = "child_tasks"                 # sub-task creation
    TESTER_FIELD = "tester_field"               # assignable tester field
    TASK_STATS = "task_stats"                   # Task Manager rollups
    EXECUTION_STATUS = "execution_status"       # pass/fail/blocked for reports
    SPRINT_REPORTS = "sprint_reports"
    ATTACHMENTS = "attachments"                 # story screenshots for vision
    DELETE_TEST_CASE = "delete_test_case"       # dedupe can actually delete
    STEP_TEST_DATA = "step_test_data"           # Step.data survives a round-trip
    EDIT_SUBTASKS = "edit_subtasks"             # read a story's sub-tasks + edit their fields

    ALL = frozenset({
        REQUIREMENT_SUITES, NESTED_SUITES, STORY_LINKS, CHILD_TASKS,
        TESTER_FIELD, TASK_STATS, EXECUTION_STATUS, SPRINT_REPORTS,
        ATTACHMENTS, DELETE_TEST_CASE, STEP_TEST_DATA, EDIT_SUBTASKS,
    })


class Backend(ABC):
    """One tracker (Azure DevOps, or Jira+Zephyr, or a test fake).

    Adapters are constructed with an already-resolved credential/config object
    and are expected to be cheap to build and safe to hold. They are NOT
    required to be thread-safe for writes, but every read must be, because the
    run/automation worker pools call them concurrently.
    """

    #: Stable identifier persisted in creds["backend"]. Never localize.
    name: str = ""

    #: Human-facing label for the Setup picker.
    label: str = ""

    #: Subset of Capability.ALL this adapter actually supports.
    capabilities: frozenset = frozenset()

    # ── capability helpers ────────────────────────────────────────────────
    def supports(self, capability):
        return capability in self.capabilities

    def require(self, capability, what=""):
        """Guard for the top of an EXTENDED method in a concrete adapter."""
        if capability not in self.capabilities:
            raise BackendUnsupported(
                f"{self.label or self.name} does not support {what or capability}.",
                remedy="Switch backends in Setup, or use the equivalent feature in that tool.",
                backend=self.name)

    def _unsupported(self, what):
        raise BackendUnsupported(
            f"{self.label or self.name} does not support {what}.",
            remedy="This feature is unavailable for the connected tracker.",
            backend=self.name)

    # ══════════════════════════════════════════════════════════════════════
    #  CORE — every backend must implement
    # ══════════════════════════════════════════════════════════════════════

    @abstractmethod
    def validate_credentials(self) -> None:
        """Cheapest call that proves the connection works.

        Returns None on success; raises a tracker.errors type on failure. Note
        it RAISES rather than returning (ok, msg) like engine.validate_pat does
        — the typed error carries strictly more information, and the registry
        adapts it back to a tuple for existing callers."""

    @abstractmethod
    def fetch_projects(self) -> List[Project]: ...

    @abstractmethod
    def fetch_sprints(self, project: Project) -> List[Sprint]: ...

    @abstractmethod
    def fetch_stories_in_sprint(self, project: Project, sprint_path: str) -> List[Story]: ...

    def fetch_bugs_in_sprint(self, project: Project, sprint_path: str) -> List[Dict[str, Any]]:
        """Bugs in a sprint as `[{id,state,tags}]` for the Sprint Report. Optional
        — default empty, so backends with no separate bug concept (TestRail, or
        Azure which reports via the engine path) don't have to implement it."""
        return []

    @abstractmethod
    def fetch_stories(self, refs: Iterable[Ref]) -> List[Story]:
        """Batch fetch by ref. Batched deliberately: the per-story loop is the
        hottest read path in the app and both backends support bulk reads
        (Azure `workitems?ids=`, Jira JQL `key IN (…)`)."""

    @abstractmethod
    def fetch_test_plans(self, project: Project) -> List[Plan]: ...

    @abstractmethod
    def create_test_plan(self, project: Project, name: str, sprint_path: str = "") -> Plan: ...

    @abstractmethod
    def ensure_suite_for_story(self, project: Project, plan: Plan, story: Story) -> Suite:
        """Idempotent get-or-create. Must be safe to call repeatedly for the
        same story — regenerating into an existing suite is the normal case,
        and the dedupe logic depends on landing in the SAME suite each time."""

    def find_suite_for_story(self, project: Project, plan: Plan,
                             story: Story) -> Optional[Suite]:
        """NON-CREATING counterpart to ensure_suite_for_story: return the
        existing Suite for `story`, or None if there isn't one.

        Deliberately NOT abstract — it defaults to None so every existing
        adapter keeps working, and callers must treat None as "unknown" rather
        than "empty". It exists because several read-only features cannot use
        ensure_suite_for_story without corrupting the very thing they measure:
        counting a story's existing test cases, and the Automation screen's
        read-only suite discovery, would both CREATE an empty suite for every
        story that doesn't have one yet (get-OR-CREATE), so merely opening a
        screen would litter the tracker."""
        return None

    @abstractmethod
    def fetch_test_cases_for_suite(self, project: Project, plan: Plan, suite: Suite) -> List[TestCase]: ...

    @abstractmethod
    def create_test_case(self, project: Project, plan: Plan, suite: Suite,
                         title: str, story: Optional[Story] = None) -> TestCase: ...

    @abstractmethod
    def fetch_test_case_steps(self, ref: Ref) -> List[Step]: ...

    @abstractmethod
    def update_test_case_steps(self, ref: Ref, steps: List[Step]) -> None:
        """Replace a case's steps wholesale (Zephyr's OVERWRITE semantics).
        Append-style behaviour is intentionally NOT part of the contract: the
        generation loop always produces a complete step list, and supporting
        both modes would make round-trip fidelity untestable."""

    @abstractmethod
    def item_url(self, ref: Ref, project: Optional[Project] = None) -> str:
        """Browser URL for a story/case. Replaces the hardcoded
        `dev.azure.com/{org}/…/_workitems/edit/{id}` builds currently spread
        across engine.py, main.py, regression.py, report.py and run_worker.py."""

    # ══════════════════════════════════════════════════════════════════════
    #  EXTENDED — capability-gated, default to an honest refusal
    # ══════════════════════════════════════════════════════════════════════

    def fetch_existing_titles_for_suite(self, project: Project, plan: Plan, suite: Suite) -> List[str]:
        """Titles only — the dedupe fast path. Default derives them from the
        full fetch; adapters override when the API offers a cheaper call."""
        return [tc.title for tc in self.fetch_test_cases_for_suite(project, plan, suite)]

    def delete_test_case(self, project: Project, plan: Plan, suite: Suite, ref: Ref) -> None:
        self._unsupported("deleting test cases")

    def fetch_story_screenshots(self, story: Story) -> List[bytes]:
        """Images for the vision/UI-description pass. Empty list (not an error)
        when the backend has none — callers already treat that as 'no visuals'."""
        return []

    def fetch_project_members(self, project: Project) -> List[User]:
        self._unsupported("listing project members")

    def tester_field_options(self, project: Project) -> List[str]:
        self._unsupported("the tester field")

    def assign_testers(self, project: Project, assignments: Dict[str, str]) -> None:
        self._unsupported("assigning testers")

    def create_child_tasks(self, project: Project, items: List[Dict[str, Any]]) -> List[Ref]:
        self._unsupported("creating child tasks")

    # ── sub-task editing (EDIT_SUBTASKS) ──────────────────────────────────
    # Three-method seam: LIST a story's existing sub-tasks, READ one sub-task's
    # editable field set (so the UI can offer whatever fields that sub-task
    # actually has, not a hardcoded four), then WRITE the chosen values back.
    # All default to inert so non-Jira backends are unaffected and Azure stays
    # byte-identical — the feature is enabled only where EDIT_SUBTASKS is set.
    def fetch_subtasks(self, project: Project, story: Any) -> List[Dict[str, Any]]:
        """A story's existing child sub-tasks as
        `[{id,key,summary,assignee,status,labels,url}]`. Optional — default
        empty for backends with no sub-task concept."""
        return []

    def subtask_field_schema(self, ref: Any) -> List[Dict[str, Any]]:
        """Editable fields for ONE sub-task as
        `[{id,name,type,items,required,allowed:[{id,label}]}]` — drives the
        dynamic field picker. `status` appears as a pseudo-field whose `allowed`
        ids are workflow TRANSITION ids (status is a transition, not a write).
        Default empty."""
        return []

    def update_subtask(self, ref: Any, fields: Dict[str, Any]) -> None:
        """Apply `{field_id: value}` to a sub-task; values are the ids/text the
        schema offered, and the backend shapes them to the tracker's payload.
        A `status` entry carries a transition id. Default refuses."""
        self._unsupported("editing sub-tasks")

    def subtask_work_types(self, project: Project) -> List[Dict[str, Any]]:
        """Sub-task work types as `[{id,name}]` for the create picker. Default
        empty."""
        return []

    def subtask_create_field_schema(self, project: Project,
                                    type_id: str) -> List[Dict[str, Any]]:
        """Fields available when CREATING a sub-task of `type_id` — same shape as
        `subtask_field_schema` without current values. Default empty."""
        return []

    def create_subtask(self, project: Project, parent: Any, type_id: str,
                       summary: str, fields: Optional[Dict[str, Any]] = None) -> Ref:
        """Create one sub-task under `parent` as work type `type_id` with the
        chosen `fields`. Default refuses."""
        self._unsupported("creating sub-tasks")

    def fetch_task_stats(self, project: Project, sprint_path: str = "",
                         assignee: str = "") -> List[TaskStats]:
        self._unsupported("task statistics")

    def sprint_report_data(self, project: Project, sprint_path: str) -> ReportData:
        self._unsupported("sprint reports")

    def create_project(self, name: str) -> Project:
        """Create a project. Only backends that support it (e.g. TestRail, for
        the hybrid's auto-create-by-name) implement this; default refuses."""
        self._unsupported("creating projects")

    def close(self) -> None:
        """Release sockets/sessions. Safe to call more than once."""


__all__ = ["Backend", "Capability"]
