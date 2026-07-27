"""tracker/fake.py — a complete in-memory Backend.

WHY THIS IS BUILT FIRST, NOT LAST
It is the highest-leverage file in the package and it pays off even if the Jira
work is never finished:

  * The generation/dedupe logic (run_titles, run_steps, dedupe_existing_suite,
    _ai_duplicate_clusters) currently cannot be tested without a live Azure
    org. That is why past bugs there — the overlapping-cluster KeyError, the
    Task Manager stuck-on-Calculating race — were all found in PRODUCTION and
    reproduced afterwards with bespoke one-off harnesses (drive_race.py,
    drive_watchdog.py). A fake backend turns those into ordinary tests.
  * It's the reference implementation of the contract. When the Jira adapter
    disagrees with the fake, the contract suite says so precisely.
  * It makes `Ref`'s id/key duality real: ids and keys here are deliberately
    DIFFERENT (`10001` vs `DEMO-1`), so any code that conflates them fails
    against the fake instead of silently mis-linking a real Jira issue.

Deterministic by construction — no clocks, no randomness, stable ordering — so
tests never flake.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Optional

from .base import Backend, Capability
from .errors import NotFound
from .models import (
    Plan, Project, Ref, ReportData, Sprint, Step, Story, Suite, TaskStats,
    TestCase, User,
)


class FakeBackend(Backend):
    """In-memory tracker. Supports everything, so the contract suite exercises
    every branch; construct with `capabilities=` to simulate a limited product
    (e.g. Zephyr Squad without nested suites) and verify graceful degradation."""

    name = "fake"
    label = "In-memory (test)"

    def __init__(self, capabilities=None, seed=True):
        self.capabilities = frozenset(capabilities) if capabilities is not None else Capability.ALL
        self._lock = threading.RLock()
        self._seq = 10000
        self._projects: Dict[str, Project] = {}
        self._sprints: Dict[str, List[Sprint]] = {}
        self._stories: Dict[str, Story] = {}          # by ref.id
        self._plans: Dict[str, Plan] = {}
        self._suites: Dict[str, Suite] = {}
        self._suite_by_story: Dict[str, str] = {}     # f"{plan}:{story}" -> suite id
        self._cases: Dict[str, TestCase] = {}
        self._steps: Dict[str, List[Step]] = {}
        self._cases_in_suite: Dict[str, List[str]] = {}
        self._members: Dict[str, List[User]] = {}
        self._screenshots: Dict[str, List[bytes]] = {}
        #: Every mutating call is appended here — lets a test assert on the
        #: SEQUENCE of backend calls (e.g. "dedupe deleted before it created").
        self.calls: List[str] = []
        if seed:
            self._seed()

    # ── internals ─────────────────────────────────────────────────────────
    def _next(self, prefix):
        with self._lock:
            self._seq += 1
            return str(self._seq), f"{prefix}-{self._seq - 10000}"

    def _record(self, what):
        self.calls.append(what)

    def _seed(self):
        p = Project(ref=Ref(id="1", key="DEMO"), name="Demo Project")
        self._projects[p.ref.key] = p
        self._sprints[p.ref.key] = [
            Sprint(ref=Ref(id="900", key="S1"), name="Sprint 1",
                   path=r"\Demo\Sprint 1", state="closed"),
            Sprint(ref=Ref(id="901", key="S2"), name="Sprint 2",
                   path=r"\Demo\Sprint 2", state="active"),
        ]
        for i, (title, ac) in enumerate([
            ("Login with valid credentials",
             "<p>Given a registered user<br>When they submit valid credentials"
             "<br>Then they reach the dashboard</p>"),
            ("Reject invalid password",
             "<p>Given a registered user<br>When the password is wrong"
             "<br>Then an error is shown and no session is created</p>"),
        ], start=1):
            sid, skey = str(10000 + i), f"DEMO-{i}"
            self._stories[sid] = Story(
                ref=Ref(id=sid, key=skey), title=title,
                description=f"<p>{title}</p>", acceptance_criteria=ac,
                url=f"https://fake.local/browse/{skey}",
                sprint_path=r"\Demo\Sprint 2")
        self._members[p.ref.key] = [
            User(id="u-1", display_name="Alice Tester", email="alice@example.com"),
            User(id="u-2", display_name="Bob Tester", email="bob@example.com"),
        ]

    # ── core ──────────────────────────────────────────────────────────────
    def validate_credentials(self):
        self._record("validate_credentials")
        return None

    def fetch_projects(self):
        self._record("fetch_projects")
        return sorted(self._projects.values(), key=lambda p: p.name)

    def fetch_sprints(self, project):
        self._record(f"fetch_sprints({project.ref.key})")
        return list(self._sprints.get(project.ref.key, []))

    def fetch_stories_in_sprint(self, project, sprint_path):
        self._record(f"fetch_stories_in_sprint({sprint_path})")
        return sorted((s for s in self._stories.values()
                       if s.sprint_path == sprint_path),
                      key=lambda s: s.ref.id)

    def fetch_stories(self, refs):
        wanted = [r.id if isinstance(r, Ref) else str(r) for r in refs]
        self._record(f"fetch_stories({len(wanted)})")
        out = []
        for rid in wanted:
            story = self._stories.get(rid)
            if story is None:
                raise NotFound(f"No story {rid}.", backend=self.name)
            out.append(story)
        return out

    def fetch_test_plans(self, project):
        self._record(f"fetch_test_plans({project.ref.key})")
        return sorted(self._plans.values(), key=lambda p: p.name)

    def create_test_plan(self, project, name, sprint_path=""):
        pid, pkey = self._next("PLAN")
        plan = Plan(ref=Ref(id=pid, key=pkey), name=name, sprint_path=sprint_path,
                    url=f"https://fake.local/plan/{pid}")
        self._plans[pid] = plan
        self._record(f"create_test_plan({name})")
        return plan

    def ensure_suite_for_story(self, project, plan, story):
        composite = f"{plan.ref.id}:{story.ref.id}"
        existing = self._suite_by_story.get(composite)
        if existing:
            # Idempotency is the contract's hard requirement here — regenerating
            # must land in the SAME suite or dedupe silently compares against an
            # empty one and re-creates every case.
            self._record(f"ensure_suite_for_story(HIT {story.ref.key})")
            return self._suites[existing]
        sid, skey = self._next("SUITE")
        suite = Suite(ref=Ref(id=sid, key=skey), name=story.title,
                      parent_ref=plan.ref, story_ref=story.ref)
        self._suites[sid] = suite
        self._suite_by_story[composite] = sid
        self._cases_in_suite[sid] = []
        self._record(f"ensure_suite_for_story(NEW {story.ref.key})")
        return suite

    def fetch_test_cases_for_suite(self, project, plan, suite):
        self._record(f"fetch_test_cases_for_suite({suite.ref.key})")
        return [self._cases[c] for c in self._cases_in_suite.get(suite.ref.id, [])]

    def create_test_case(self, project, plan, suite, title, story=None):
        cid, ckey = self._next("TC")
        case = TestCase(ref=Ref(id=cid, key=ckey), title=title,
                        suite_ref=suite.ref,
                        story_ref=(story.ref if story else suite.story_ref),
                        url=f"https://fake.local/case/{cid}")
        self._cases[cid] = case
        self._steps[cid] = []
        self._cases_in_suite.setdefault(suite.ref.id, []).append(cid)
        self._record(f"create_test_case({title})")
        return case

    def fetch_test_case_steps(self, ref):
        self._record(f"fetch_test_case_steps({ref.key})")
        if ref.id not in self._cases:
            raise NotFound(f"No test case {ref.key}.", backend=self.name)
        return list(self._steps.get(ref.id, []))

    def update_test_case_steps(self, ref, steps):
        if ref.id not in self._cases:
            raise NotFound(f"No test case {ref.key}.", backend=self.name)
        keep_data = self.supports(Capability.STEP_TEST_DATA)
        self._steps[ref.id] = [
            Step(action=s.action, expected=s.expected,
                 data=(s.data if keep_data else ""), index=i)
            for i, s in enumerate(steps, start=1)
        ]
        self._record(f"update_test_case_steps({ref.key}, {len(steps)})")

    def item_url(self, ref, project=None):
        return f"https://fake.local/browse/{ref.key}"

    # ── extended ──────────────────────────────────────────────────────────
    def delete_test_case(self, project, plan, suite, ref):
        self.require(Capability.DELETE_TEST_CASE, "deleting test cases")
        self._cases.pop(ref.id, None)
        self._steps.pop(ref.id, None)
        lst = self._cases_in_suite.get(suite.ref.id, [])
        if ref.id in lst:
            lst.remove(ref.id)
        self._record(f"delete_test_case({ref.key})")

    def fetch_story_screenshots(self, story):
        return list(self._screenshots.get(story.ref.id, []))

    def fetch_project_members(self, project):
        self.require(Capability.TESTER_FIELD, "listing project members")
        return list(self._members.get(project.ref.key, []))

    def tester_field_options(self, project):
        self.require(Capability.TESTER_FIELD, "the tester field")
        return [u.display_name for u in self._members.get(project.ref.key, [])]

    def assign_testers(self, project, assignments):
        self.require(Capability.TESTER_FIELD, "assigning testers")
        self._record(f"assign_testers({len(assignments)})")

    def create_child_tasks(self, project, items):
        self.require(Capability.CHILD_TASKS, "creating child tasks")
        made = []
        for _ in items:
            tid, tkey = self._next("TASK")
            made.append(Ref(id=tid, key=tkey))
        self._record(f"create_child_tasks({len(items)})")
        return made

    def fetch_task_stats(self, project, sprint_path="", assignee=""):
        self.require(Capability.TASK_STATS, "task statistics")
        return [TaskStats(user=u, total=4, completed=3, in_progress=1,
                          remaining_hours=6.0, completed_hours=12.0)
                for u in self._members.get(project.ref.key, [])]

    def sprint_report_data(self, project, sprint_path):
        self.require(Capability.SPRINT_REPORTS, "sprint reports")
        stories = self.fetch_stories_in_sprint(project, sprint_path)
        total = sum(len(v) for v in self._cases_in_suite.values())
        return ReportData(project=project.name, sprint=sprint_path,
                          total_cases=total, passed=total, stories=stories)

    # ── test conveniences (not part of the Backend contract) ──────────────
    def add_story(self, title, ac="", sprint_path=r"\Demo\Sprint 2"):
        sid, _ = self._next("DEMO")
        key = f"DEMO-{len(self._stories) + 1}"
        story = Story(ref=Ref(id=sid, key=key), title=title,
                      acceptance_criteria=ac, sprint_path=sprint_path,
                      url=f"https://fake.local/browse/{key}")
        self._stories[sid] = story
        return story

    def set_screenshots(self, story, blobs):
        self._screenshots[story.ref.id] = list(blobs)


__all__ = ["FakeBackend"]
