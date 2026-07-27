"""run_testrail_probe.py — verify the TestRail write path against a live instance.

    set TESTRAIL_URL=https://your-org.testrail.io
    set TESTRAIL_EMAIL=you@company.com
    set TESTRAIL_KEY=your-api-key
    py -3.12 run_testrail_probe.py            (lists projects, then stops)
    py -3.12 run_testrail_probe.py 1          (probe project id 1 — or a name)

Get the API key: TestRail → Administration → Site Settings → API (tick "Enable
API" AND "Enable API for individual users"), then My Settings → API Keys.

Unlike Zephyr/Xray, TestRail is standalone — no Jira, no marketplace install, no
provisioning wait. This is the fastest route to a LIVE-verified write path.

Exercises: auth → project → suite (plan) → section (suite, idempotent) → case →
steps OVERWRITE via custom_steps_separated, read back → list → delete.
TestRail has no stories, so a SYNTHETIC story just names the section — the write
contract is what's under test here.

SECURITY: URL/email/key from the environment; the key is never printed.
⚠️  WRITES: creates a suite (multi-suite projects only), a section, and a case;
deletes the case. Section/suite are left behind. Use a scratch project.
Runs on any Python 3.8+.
"""
from __future__ import annotations

import os
import sys
import time
import traceback


def _get(n):
    return (os.environ.get(n) or "").strip()


class _Skip(Exception):
    pass


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    url, email, key = _get("TESTRAIL_URL"), _get("TESTRAIL_EMAIL"), _get("TESTRAIL_KEY")
    missing = [n for n, v in (("TESTRAIL_URL", url), ("TESTRAIL_EMAIL", email),
                              ("TESTRAIL_KEY", key)) if not v]
    if missing:
        print(f"\n  Set these first: {', '.join(missing)}")
        return 2

    from tracker.testrail import TestRailBackend, validate_testrail_url
    from tracker.models import Ref, Step, Story

    try:
        normalized = validate_testrail_url(url)
        print(f"\n  url          : accepted -> {normalized}")
    except Exception as exc:
        print(f"\n  url          : REJECTED -> {exc}")
        return 1

    be = TestRailBackend(base_url=url, email=email, api_key=key)

    try:
        be.validate_credentials()
        print("  auth         : OK")
    except Exception as exc:
        print(f"  auth         : FAILED — {exc}")
        return 1

    try:
        projects = be.fetch_projects()
        print(f"  projects     : {len(projects)} found")
        for p in projects[:15]:
            print(f"                   [{p.ref.id}] {p.name}")
    except Exception as exc:
        print(f"  projects     : FAILED — {exc}")
        return 1

    if not argv:
        print("\n  Pass a project id (or name) to run the write probe:")
        print("    py -3.12 run_testrail_probe.py <id>")
        return 0

    want = argv[0]
    project = next((p for p in projects if want in (p.ref.id, p.name)), None)
    if project is None:
        print(f"\n  Project {want!r} not in the list above.")
        return 1

    results, state = [], {"project": project}
    tag = time.strftime("%H%M%S")
    print(f"\n  TestRail write probe — project [{project.ref.id}] {project.name} — run {tag}")
    print("─" * 62)

    def step(name, fn):
        try:
            d = fn()
            results.append((name, True, False))
            print(f"  [PASS] {name}" + (f" — {d}" if d else ""))
        except _Skip as s:
            results.append((name, True, True))
            print(f"  [SKIP] {name} — {s}")
        except Exception as e:
            results.append((name, False, False))
            print(f"  [FAIL] {name} — {type(e).__name__}: {e}")
            print("        " + traceback.format_exc(limit=2).replace("\n", "\n        "))

    def _plan():
        state["plan"] = be.create_test_plan(project, f"QA Studio probe {tag}")
        if not state["plan"].ref.id:
            raise Exception("no suite id returned")
        return f"suite {state['plan'].ref.id}"

    def _suite():
        story = Story(ref=Ref(id="0", key="PROBE"), title=f"Probe story {tag}")
        state["story"] = story
        a = be.ensure_suite_for_story(project, state["plan"], story)
        b = be.ensure_suite_for_story(project, state["plan"], story)
        if a.ref.id != b.ref.id:
            raise Exception(f"section not idempotent: {a.ref.id} != {b.ref.id}")
        state["suite"] = a
        return f"section {a.ref.id} (stable)"

    def _case():
        c = be.create_test_case(project, state["plan"], state["suite"],
                                f"Probe case {tag}", state["story"])
        state["case"] = c
        if not c.ref.id:
            raise Exception("no case id")
        return f"case {c.ref.id}"

    def _steps():
        sent = [Step(action="Open the login page", expected="Login form shown", index=1),
                Step(action="Submit valid credentials", expected="Dashboard loads", index=2)]
        be.update_test_case_steps(state["case"].ref, sent)
        got = be.fetch_test_case_steps(state["case"].ref)
        if len(got) != len(sent):
            raise Exception(f"wrote {len(sent)}, read {len(got)}")
        for i, (a, b) in enumerate(zip(sent, got), 1):
            if (a.action or "").strip() != (b.action or "").strip():
                raise Exception(f"step {i} action changed: {a.action!r} -> {b.action!r}")
            if (a.expected or "").strip() != (b.expected or "").strip():
                raise Exception(f"step {i} expected changed")
        return f"{len(got)} steps round-tripped"

    def _list():
        cases = be.fetch_test_cases_for_suite(project, state["plan"], state["suite"])
        if state["case"].ref.id not in [c.ref.id for c in cases]:
            raise Exception("created case not listed in section")
        return f"{len(cases)} case(s) in section"

    def _delete():
        be.delete_test_case(project, state["plan"], state["suite"], state["case"].ref)
        remaining = [c.ref.id for c in be.fetch_test_cases_for_suite(
            project, state["plan"], state["suite"])]
        if state["case"].ref.id in remaining:
            raise Exception("case still present after delete")
        return "deleted and verified"

    for n, fn in [("suite (plan) created", _plan), ("section idempotent", _suite),
                  ("case created", _case), ("steps OVERWRITE round-trip", _steps),
                  ("case listed in section", _list), ("case deleted", _delete)]:
        step(n, fn)

    p = sum(1 for _, ok, sk in results if ok and not sk)
    s = sum(1 for _, _, sk in results if sk)
    f = sum(1 for _, ok, sk in results if not ok and not sk)
    print("─" * 62)
    print(f"  {p} passed · {s} skipped · {f} failed")
    if f:
        print("\n  Failures are TestRail write-path bugs — paste this back.")
    else:
        print("\n  TestRail write path verified end-to-end. (First live write-path"
              "\n  verification of any backend.) One section/suite left behind.")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
