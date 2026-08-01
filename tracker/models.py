"""tracker/models.py — the normalized shapes every backend speaks.

THE ONE RULE THIS FILE ENFORCES
`engine.py`'s generation logic (run_titles, run_steps, generate_titles,
dedupe_*, compile_test_case, evaluate_existing_steps) must operate on THESE
types and never on a vendor payload. Today that rule is broken in a very
visible way: `evaluate_existing_steps(tc_title, criteria, existing_steps_xml)`
— an AI function — takes Azure's `Microsoft.VSTS.TCM.Steps` XML. Anything that
takes XML is unportable by construction.

`Step` below is the fix. Azure serializes it through the existing
`engine.build_steps_xml` / `engine.parse_steps_xml` pair (which is already the
right seam, just used too late); Zephyr maps it directly onto its native JSON
step objects. Neither format escapes its adapter.

Everything is a frozen dataclass: these cross thread boundaries constantly
(the run/automation worker pools), and immutable values are the cheapest way
to not have to think about that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Ref:
    """An object identified by BOTH an opaque id and a human-facing key.

    This exists because of a specific, predictable bug class. In Jira an issue
    has a numeric id (`10100`) AND a key (`PROJ-123`); Zephyr's
    `createTestCaseIssueLink` requires the NUMERIC issueId, while every screen,
    log line, and AI prompt the user ever sees uses the key. In Azure the two
    are the same value. If a DTO carried only one of them, every Jira call site
    would need to re-derive the other — which means an extra round-trip, or a
    guess, and eventually a wrong link silently attached to the wrong issue.

    So both travel together, always, from the moment a backend constructs the
    object. Never reconstruct one from the other.
    """

    id: str
    key: str = ""

    def __post_init__(self):
        # A backend with a single identifier (Azure) passes id only; key
        # mirrors it so callers can use either field unconditionally.
        if not self.key:
            object.__setattr__(self, "key", self.id)
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "key", str(self.key))

    def __str__(self):
        return self.key


@dataclass(frozen=True)
class User:
    """A person. `id` is whatever the backend needs to ASSIGN work to them
    (Azure: unique name / descriptor; Jira: Atlassian accountId)."""

    id: str
    display_name: str = ""
    email: str = ""

    def __str__(self):
        return self.display_name or self.email or self.id


@dataclass(frozen=True)
class Project:
    ref: Ref
    name: str

    def __str__(self):
        return self.name or self.ref.key


@dataclass(frozen=True)
class Sprint:
    """A time-boxed iteration.

    `path` is the backend's own addressing form, kept opaque and passed back
    verbatim rather than parsed: Azure uses a hierarchical iteration path
    (`\\Project\\Release 1\\Sprint 3`) from a tree that can be 10 levels deep,
    while a Jira sprint is a flat numeric id owned by a BOARD, not a project.
    Those two cannot be normalized into a common structure without losing
    information, so callers treat `path` as a token: get it from
    fetch_sprints(), hand it back to fetch_stories_in_sprint().
    """

    ref: Ref
    name: str
    path: str = ""
    state: str = ""            # "future" | "active" | "closed" (best-effort)
    start: Optional[date] = None
    end: Optional[date] = None

    def __str__(self):
        return self.name


@dataclass(frozen=True)
class Story:
    """A requirement/user story — the unit test cases are generated FROM.

    `description` and `acceptance_criteria` are normalized to **HTML** (or
    plain text), never to a vendor document model. Azure already stores HTML.
    Jira Cloud stores ADF (Atlassian Document Format) JSON, so its adapter owes
    an ADF renderer here — the single most under-estimated task in the whole
    Jira port, because every AI prompt consumes this text and a bad renderer
    degrades generation quality silently rather than loudly.
    """

    ref: Ref
    title: str
    description: str = ""
    acceptance_criteria: str = ""
    assignee: Optional[User] = None
    state: str = ""
    url: str = ""
    sprint_path: str = ""
    #: Parent Epic/Feature, used to GROUP the Regression/Sprint Plan (Azure fills
    #: these from System.Parent→Feature; Jira from the issue's parent epic). Empty
    #: when the story has no resolvable parent — the plan then groups it under
    #: "No Feature", exactly as before. `epic_id` is the parent's key/id,
    #: `epic_name` its title (carried inline so no extra lookup is needed).
    epic_id: str = ""
    epic_name: str = ""

    def __str__(self):
        return f"{self.ref.key} · {self.title}"


@dataclass(frozen=True)
class Step:
    """One test step. THE type that removes ADO XML from the AI layer.

    Azure  → <step><parameterizedString>action</…><parameterizedString>expected</…>
    Zephyr → {"inline": {"description": action, "expectedResult": expected,
                         "testData": data}}

    `data` (Zephyr's testData) has no first-class Azure equivalent; the Azure
    adapter folds it into the action text on write and leaves it empty on read.
    That asymmetry is deliberate and documented rather than hidden: round-trip
    fidelity is asserted by the contract suite.
    """

    action: str
    expected: str = ""
    data: str = ""
    index: int = 0
    #: Environmental/state precondition for this step, kept SEPARATE from the
    #: action. Azure has no precondition field, so `engine.build_steps_xml`
    #: folds it into the action text ("Precondition: … / Action: …"). Backends
    #: that DO have a dedicated field (TestRail's `custom_preconds`) should
    #: write this instead of inheriting Azure's folded string. Defaults to ""
    #: so every existing adapter and the contract suite are unaffected.
    pre: str = ""

    def is_empty(self):
        return not (self.action or "").strip() and not (self.expected or "").strip()


@dataclass(frozen=True)
class TestCase:
    ref: Ref
    title: str
    steps: List[Step] = field(default_factory=list)
    suite_ref: Optional[Ref] = None
    story_ref: Optional[Ref] = None
    url: str = ""

    def __str__(self):
        return f"{self.ref.key} · {self.title}"


@dataclass(frozen=True)
class Suite:
    """A container of test cases.

    Azure  → a requirement-based test suite under a test plan.
    Zephyr → a TEST_CASE folder (per decision D1 in TRACKER_BACKENDS_PLAN.md),
             optionally paired with an issue link for real Jira traceability.
    """

    ref: Ref
    name: str
    parent_ref: Optional[Ref] = None
    story_ref: Optional[Ref] = None

    def __str__(self):
        return self.name


@dataclass(frozen=True)
class Plan:
    """Azure → test plan. Zephyr → the root TEST_CASE folder standing in for
    one (see D1). `sprint_path` records what it was generated against."""

    ref: Ref
    name: str
    sprint_path: str = ""
    url: str = ""

    def __str__(self):
        return self.name


@dataclass(frozen=True)
class TaskStats:
    """Task Manager rollup for one assignee."""

    user: User
    total: int = 0
    completed: int = 0
    in_progress: int = 0
    remaining_hours: float = 0.0
    completed_hours: float = 0.0


@dataclass(frozen=True)
class ReportData:
    """Sprint-closure inputs. Deliberately flat: the existing PDF/email builders
    consume primitives, so keeping this dumb means Phase 6 is mostly wiring."""

    project: str = ""
    sprint: str = ""
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    not_run: int = 0
    stories: List[Story] = field(default_factory=list)
    per_story: List[Dict[str, Any]] = field(default_factory=list)
    plan_url: str = ""


__all__ = [
    "Ref", "User", "Project", "Sprint", "Story", "Step",
    "TestCase", "Suite", "Plan", "TaskStats", "ReportData",
]
