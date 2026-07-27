"""tracker/contract.py — the parity suite. One set of assertions, run against
ANY backend.

WHAT "PARITY" ACTUALLY MEANS
"Jira does the same as Azure" is untestable as stated. This file turns it into
a list of executable claims: create a plan, get-or-create a suite for a story,
create a case, write steps, read them back unchanged, list titles, delete.
A backend either satisfies them or reports exactly which claim it failed.

Run against `FakeBackend` it is a fast, offline regression test of the contract
itself. Run against `AzureBackend` (with real creds and a scratch project) it
proves the wrapper is faithful. Run later against the Jira/Zephyr adapter, it
is the definition of done for Phase 4.

Every test is written to be SAFE TO RE-RUN: names are suffixed with a run tag,
and anything created is torn down when the backend supports deletion.

Deliberately dependency-free — no pytest — so it can run inside the app's
sandbox, in CI, or from a plain `python -m tracker.contract`, matching the
existing drive_race.py / drive_watchdog.py harness style.
"""
from __future__ import annotations

import time
import traceback
from typing import Any, Callable, List, Optional, Tuple

from .base import Backend, Capability
from .errors import BackendUnsupported, TrackerError
from .models import Ref, Step


class Result:
    __slots__ = ("name", "ok", "detail", "skipped")

    def __init__(self, name, ok, detail="", skipped=False):
        self.name, self.ok, self.detail, self.skipped = name, ok, detail, skipped

    def __str__(self):
        mark = "SKIP" if self.skipped else ("PASS" if self.ok else "FAIL")
        return f"[{mark}] {self.name}" + (f" — {self.detail}" if self.detail else "")


class _Runner:
    def __init__(self):
        self.results: List[Result] = []

    def check(self, name, fn):
        try:
            detail = fn()
            self.results.append(Result(name, True, detail or ""))
        except BackendUnsupported as exc:
            # A declared capability gap is a legitimate outcome, not a failure.
            self.results.append(Result(name, True, str(exc), skipped=True))
        except AssertionError as exc:
            self.results.append(Result(name, False, str(exc) or "assertion failed"))
        except Exception as exc:
            self.results.append(
                Result(name, False, f"{type(exc).__name__}: {exc}\n"
                                    f"{traceback.format_exc(limit=3)}"))


def run_contract(backend: Backend, project=None, cleanup=True, read_only=False):
    """Execute the suite. Returns (results, ok).

    `read_only=True` runs ONLY the non-mutating checks: nothing is created,
    updated or deleted. That is not a lesser version of the suite for the case
    that matters most — the conversions it covers (projects, sprints, stories,
    plans, item_url) are precisely the ones the app's connect / plan-load /
    story-load paths were rerouted through, so it is the regression test for
    existing users. The write checks verify the generation path, which is where
    new work lands.

    Use it when the only available project is a real one: it gives most of the
    signal with none of the residue (the write suite leaves a test plan behind).
    """
    r = _Runner()
    tag = time.strftime("%H%M%S")
    state = {}

    def _interface():
        missing = [m for m in (
            "validate_credentials", "fetch_projects", "fetch_sprints",
            "fetch_stories_in_sprint", "fetch_stories", "fetch_test_plans",
            "create_test_plan", "ensure_suite_for_story",
            "fetch_test_cases_for_suite", "create_test_case",
            "fetch_test_case_steps", "update_test_case_steps", "item_url")
            if not callable(getattr(backend, m, None))]
        assert not missing, f"missing methods: {missing}"
        assert backend.name, "backend.name must be set"
        unknown = set(backend.capabilities) - set(Capability.ALL)
        assert not unknown, f"unknown capabilities declared: {sorted(unknown)}"
        return f"{backend.name} · {len(backend.capabilities)} capabilities"

    def _credentials():
        backend.validate_credentials()
        return "accepted"

    def _projects():
        projects = backend.fetch_projects()
        assert isinstance(projects, list), "fetch_projects must return a list"
        chosen = None
        if project is not None:
            for p in projects:
                if project in (p.name, p.ref.key, p.ref.id):
                    chosen = p
                    break
            assert chosen is not None, f"project {project!r} not found"
        else:
            assert projects, "no projects returned and none specified"
            chosen = projects[0]
        state["project"] = chosen
        return f"{len(projects)} project(s), using {chosen}"

    def _refs_carry_both_ids():
        # Guards the id/key duality (models.Ref): a backend that fills only one
        # of them will mis-link Zephyr test cases to Jira issues later.
        p = state["project"]
        assert p.ref.id, "project ref.id is empty"
        assert p.ref.key, "project ref.key is empty"
        return f"id={p.ref.id} key={p.ref.key}"

    def _sprints():
        sprints = backend.fetch_sprints(state["project"])
        assert isinstance(sprints, list), "fetch_sprints must return a list"
        if sprints:
            state["sprint"] = sprints[-1]
            assert state["sprint"].path, "sprint.path must be a usable token"
        return f"{len(sprints)} sprint(s)"

    def _stories():
        sprint = state.get("sprint")
        if sprint is None:
            return "no sprint available — skipped"
        stories = backend.fetch_stories_in_sprint(state["project"], sprint.path)
        assert isinstance(stories, list), "must return a list"
        if stories:
            state["story"] = stories[0]
        return f"{len(stories)} story(ies) in {sprint.name}"

    def _story_roundtrip():
        story = state.get("story")
        if story is None:
            return "no story available — skipped"
        again = backend.fetch_stories([story.ref])
        assert again, "fetch_stories returned nothing for a known ref"
        assert again[0].ref.id == story.ref.id, "fetch_stories returned the wrong story"
        return f"{story.ref.key}"

    def _plan():
        # Read-only: exercise fetch_test_plans (a real DTO conversion) and adopt
        # an existing plan instead of creating one. Creating is the only way to
        # test create_test_plan, so that claim is simply not made here.
        plans = backend.fetch_test_plans(state["project"])
        assert isinstance(plans, list), "fetch_test_plans must return a list"
        if read_only:
            if not plans:
                return "no existing plans — nothing to adopt (read-only)"
            state["plan"] = plans[0]
            return f"read {len(plans)} plan(s), adopted {plans[0]}"
        plan = backend.create_test_plan(state["project"], f"QA Studio contract {tag}")
        assert plan.ref.id, "created plan has no id"
        state["plan"] = plan
        return f"{plan}"

    def _suite_idempotent():
        if read_only:
            return "skipped — read-only (suite creation mutates)"
        story = state.get("story")
        if story is None:
            return "no story available — skipped"
        first = backend.ensure_suite_for_story(state["project"], state["plan"], story)
        second = backend.ensure_suite_for_story(state["project"], state["plan"], story)
        # THE critical assertion. If this is not idempotent, regeneration lands
        # in a fresh suite, dedupe compares against an empty one, and every case
        # is recreated on every run.
        assert first.ref.id == second.ref.id, (
            f"ensure_suite_for_story is not idempotent: {first.ref.id} != {second.ref.id}")
        state["suite"] = first
        return f"stable suite {first.ref.id}"

    def _create_case():
        if read_only:
            return "skipped — read-only (test case creation mutates)"
        if "suite" not in state:
            return "no suite — skipped"
        case = backend.create_test_case(state["project"], state["plan"],
                                        state["suite"], f"Contract case {tag}",
                                        state.get("story"))
        assert case.ref.id, "created case has no id"
        state["case"] = case
        return f"{case}"

    def _steps_roundtrip():
        if read_only:
            return "skipped — read-only (step writes mutates)"
        case = state.get("case")
        if case is None:
            return "no case — skipped"
        sent = [
            Step(action="Open the login page", expected="Login form is shown", index=1),
            Step(action="Submit valid credentials", expected="Dashboard loads",
                 data="user=alice", index=2),
        ]
        backend.update_test_case_steps(case.ref, sent)
        got = backend.fetch_test_case_steps(case.ref)
        assert len(got) == len(sent), f"wrote {len(sent)} steps, read back {len(got)}"
        for i, (a, b) in enumerate(zip(sent, got), start=1):
            assert (a.action or "").strip() == (b.action or "").strip(), \
                f"step {i} action changed: {a.action!r} -> {b.action!r}"
            assert (a.expected or "").strip() == (b.expected or "").strip(), \
                f"step {i} expected changed: {a.expected!r} -> {b.expected!r}"
            # Only asserted where the backend CLAIMS to support it — this is
            # what keeps Azure (no testData field in its steps XML) honest
            # instead of quietly losing the value.
            if backend.supports(Capability.STEP_TEST_DATA):
                assert (a.data or "") == (b.data or ""), \
                    f"step {i} testData lost: {a.data!r} -> {b.data!r}"
        return f"{len(got)} steps round-tripped"

    def _titles():
        if read_only:
            return "skipped — read-only (needs a created suite)"
        if "suite" not in state:
            return "no suite — skipped"
        titles = backend.fetch_existing_titles_for_suite(
            state["project"], state["plan"], state["suite"])
        assert isinstance(titles, list), "must return a list"
        case = state.get("case")
        if case is not None:
            assert case.title in titles, "created case missing from title list"
        return f"{len(titles)} title(s)"

    def _item_url():
        case = state.get("case")
        ref = case.ref if case else Ref(id="1")
        url = backend.item_url(ref, state.get("project"))
        assert isinstance(url, str), "item_url must return a string"
        return url or "(empty — org not configured)"

    def _delete():
        if read_only:
            return "skipped — read-only (delete mutates)"
        case = state.get("case")
        if case is None or not cleanup:
            return "nothing to delete"
        backend.require(Capability.DELETE_TEST_CASE, "deleting test cases")
        backend.delete_test_case(state["project"], state["plan"],
                                 state["suite"], case.ref)
        remaining = [c.ref.id for c in backend.fetch_test_cases_for_suite(
            state["project"], state["plan"], state["suite"])]
        assert case.ref.id not in remaining, "case still present after delete"
        state.pop("case", None)
        return "deleted and verified"

    for name, fn in [
        ("interface is complete", _interface),
        ("credentials accepted", _credentials),
        ("projects listed", _projects),
        ("refs carry id AND key", _refs_carry_both_ids),
        ("sprints listed", _sprints),
        ("stories in sprint", _stories),
        ("story fetched by ref", _story_roundtrip),
        ("test plans read" if read_only else "test plan created", _plan),
        ("suite creation is idempotent", _suite_idempotent),
        ("test case created", _create_case),
        ("steps round-trip unchanged", _steps_roundtrip),
        ("existing titles include new case", _titles),
        ("item_url builds", _item_url),
        ("test case deleted", _delete),
    ]:
        r.check(name, fn)

    ok = all(res.ok for res in r.results)
    return r.results, ok


def main(argv=None):
    """`python -m tracker.contract [backend] [project]` — defaults to the fake."""
    import sys
    argv = list(argv if argv is not None else sys.argv[1:])
    name = argv[0] if argv else "fake"
    project = argv[1] if len(argv) > 1 else None

    from . import get_backend
    backend = get_backend(name=name)
    results, ok = run_contract(backend, project=project)

    print(f"\ntracker contract suite — backend: {backend.name}\n" + "─" * 60)
    for res in results:
        print(res)
    passed = sum(1 for x in results if x.ok and not x.skipped)
    skipped = sum(1 for x in results if x.skipped)
    failed = sum(1 for x in results if not x.ok)
    print("─" * 60)
    print(f"{passed} passed · {skipped} skipped · {failed} failed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
