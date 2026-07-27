"""run_xray_probe.py — verify the Xray (Cloud) write path against a live instance.

    set JIRA_SITE=https://your-team.atlassian.net
    set JIRA_EMAIL=you@company.com
    set JIRA_TOKEN=your-jira-api-token
    set XRAY_CLIENT_ID=your-xray-api-key-client-id
    set XRAY_CLIENT_SECRET=your-xray-api-key-secret
    py -3.12 run_xray_probe.py SCRUM

The Xray API Key (client id + secret) is created in Jira → Apps → Xray →
(global) Settings → API Keys. Xray Cloud auth exchanges those for a 24h bearer
token; this probe never prints any of them.

Exercises the write sequence generation depends on, against Xray's own model
(Test Plan / Test Set / Test issues + GraphQL steps):
  auth (Jira + Xray) → real story from Jira → Test Plan → Test Set (idempotent)
  → Test (createTest) → steps OVERWRITE via GraphQL, read back incl. testData →
  list tests in the set → requirement-coverage link → delete.

⚠️  WRITES to Jira/Xray: creates a Test Plan issue, a Test Set issue, and a Test
issue; deletes the Test. The Test Plan / Test Set Jira issues are LEFT BEHIND
(the generation loop reuses them) — delete by hand if you like. Scratch project.

Runs on any Python 3.8+ (does not import engine).
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
    env = {n: _get(n) for n in ("JIRA_SITE", "JIRA_EMAIL", "JIRA_TOKEN",
                                "XRAY_CLIENT_ID", "XRAY_CLIENT_SECRET")}
    missing = [n for n, v in env.items() if not v]
    if missing:
        print(f"\n  Set these first: {', '.join(missing)}")
        return 2
    if not argv:
        print("\n  Pass a project key:  py -3.12 run_xray_probe.py SCRUM")
        return 2
    project_key = argv[0]

    from tracker.xray import XrayBackend
    from tracker.jira_zephyr import jql_escape
    from tracker.models import Ref, Step

    be = XrayBackend(site=env["JIRA_SITE"], email=env["JIRA_EMAIL"],
                     api_token=env["JIRA_TOKEN"],
                     client_id=env["XRAY_CLIENT_ID"],
                     client_secret=env["XRAY_CLIENT_SECRET"])
    results, state = [], {}
    tag = time.strftime("%H%M%S")
    print(f"\n  Xray write probe — project {project_key} — run {tag}\n" + "─" * 62)

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

    def _creds():
        be.validate_credentials()
        return "Jira + Xray both accepted"

    def _project():
        for p in be.fetch_projects():
            if project_key in (p.ref.key, p.ref.id, p.name):
                state["project"] = p
                return f"{p.ref.key} · {p.name}"
        raise Exception(f"project {project_key!r} not found")

    def _story():
        jql = (f'project = "{jql_escape(project_key)}" '
               f'AND issuetype in (Story,"User Story",Task,Bug) ORDER BY created DESC')
        issues = be._search(jql, fields=be._STORY_FIELDS)
        if not issues:
            raise _Skip("no issues to cover — add a story")
        state["story"] = be._to_story(issues[0])
        return f"{state['story'].ref.key} (id {state['story'].ref.id})"

    def _plan():
        state["plan"] = be.create_test_plan(state["project"], f"QA Studio probe {tag}")
        if not state["plan"].ref.id:
            raise Exception("Test Plan has no id")
        return f"Test Plan {state['plan'].ref.key or state['plan'].ref.id}"

    def _suite():
        if "story" not in state:
            raise _Skip("no story")
        a = be.ensure_suite_for_story(state["project"], state["plan"], state["story"])
        b = be.ensure_suite_for_story(state["project"], state["plan"], state["story"])
        if a.ref.id != b.ref.id:
            raise Exception(f"Test Set not idempotent: {a.ref.id} != {b.ref.id}")
        state["suite"] = a
        return f"Test Set {a.ref.key or a.ref.id} (stable)"

    def _case():
        c = be.create_test_case(state["project"], state["plan"], state.get("suite"),
                                f"Probe case {tag}", state.get("story"))
        state["case"] = c
        if not c.ref.id:
            raise Exception("Test has no issueId")
        return f"Test {c.ref.key or c.ref.id}"

    def _steps():
        sent = [Step(action="Open login", expected="Form shows", index=1),
                Step(action="Submit creds", expected="Dashboard", data="user=alice", index=2)]
        be.update_test_case_steps(state["case"].ref, sent)
        got = be.fetch_test_case_steps(state["case"].ref)
        if len(got) != len(sent):
            raise Exception(f"wrote {len(sent)}, read {len(got)}")
        for i, (a, b) in enumerate(zip(sent, got), 1):
            if (a.action or "") != (b.action or ""):
                raise Exception(f"step {i} action changed")
            if (a.expected or "") != (b.expected or ""):
                raise Exception(f"step {i} result changed")
            if (a.data or "") != (b.data or ""):
                raise Exception(f"step {i} data lost: {a.data!r}->{b.data!r}")
        return f"{len(got)} steps round-tripped (incl. data)"

    def _list():
        cases = be.fetch_test_cases_for_suite(state["project"], state["plan"], state["suite"])
        ids = [c.ref.id for c in cases]
        if state["case"].ref.id not in ids:
            raise Exception("created Test not in the Test Set")
        return f"{len(cases)} test(s) in set"

    def _delete():
        be.delete_test_case(state["project"], state["plan"], state["suite"], state["case"].ref)
        return "deleted"

    for n, fn in [("credentials (Jira + Xray)", _creds), ("project found", _project),
                  ("real story from Jira", _story), ("Test Plan created", _plan),
                  ("Test Set idempotent", _suite), ("Test created", _case),
                  ("steps OVERWRITE round-trip", _steps), ("test listed in set", _list),
                  ("test deleted", _delete)]:
        step(n, fn)
        if not results[-1][1] and n in ("credentials (Jira + Xray)", "project found"):
            break

    p = sum(1 for _, ok, sk in results if ok and not sk)
    s = sum(1 for _, _, sk in results if sk)
    f = sum(1 for _, ok, sk in results if not ok and not sk)
    print("─" * 62)
    print(f"  {p} passed · {s} skipped · {f} failed")
    if f:
        print("\n  Failures are Xray write-path bugs to fix — paste this back.")
    else:
        print("\n  Xray write path verified end-to-end.")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
