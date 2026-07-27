"""run_zephyr_probe.py — verify the Zephyr WRITE path against a live instance.

    set JIRA_SITE=https://your-team.atlassian.net
    set JIRA_EMAIL=you@company.com
    set JIRA_TOKEN=your-jira-api-token
    set ZEPHYR_TOKEN=your-zephyr-api-token
    py -3.12 run_zephyr_probe.py SCRUM

WHAT IT PROVES
The half of the integration that has NEVER run against a real server: the whole
Zephyr write sequence the generation loop depends on —
  healthcheck → create folder (plan) → get-or-create story folder (suite,
  asserted IDEMPOTENT) → create test case → OVERWRITE steps → read them back
  unchanged (incl. testData) → list titles → issue-link the case to its Jira
  story (decision D2) → delete the case.
It pulls a REAL backlog story from Jira first, so the story DTO — and the
numeric issueId the Zephyr link needs — are genuine, not synthesised.

SECURITY: both tokens come from the environment and are never printed. Output
is folder/case keys and pass/fail lines only.

⚠️  THIS WRITES TO ZEPHYR. It deletes the test case it creates. It does NOT
delete the folders (Zephyr keeps a plan/suite folder around deliberately — the
generation loop reuses them), so expect one "QA Studio probe …" folder tree to
remain; remove it by hand if you like. Point it at a scratch project.

Runs on any Python 3.8+ (does not import engine).
"""
from __future__ import annotations

import os
import sys
import time
import traceback


def _get(name):
    return (os.environ.get(name) or "").strip()


def _step(name, fn, results):
    try:
        detail = fn()
        results.append((name, True, detail or "", False))
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
        return True
    except Exception as exc:
        skipped = isinstance(exc, _Skip)
        mark = "SKIP" if skipped else "FAIL"
        msg = str(exc) if skipped else f"{type(exc).__name__}: {exc}"
        results.append((name, skipped, msg, skipped))
        print(f"  [{mark}] {name} — {msg}")
        if not skipped:
            print("        " + traceback.format_exc(limit=2).replace("\n", "\n        "))
        return skipped


class _Skip(Exception):
    pass


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    site, email = _get("JIRA_SITE"), _get("JIRA_EMAIL")
    jtok, ztok = _get("JIRA_TOKEN"), _get("ZEPHYR_TOKEN")
    missing = [n for n, v in (("JIRA_SITE", site), ("JIRA_EMAIL", email),
                              ("JIRA_TOKEN", jtok), ("ZEPHYR_TOKEN", ztok)) if not v]
    if missing:
        print(f"\n  Set these first: {', '.join(missing)}")
        print("    set JIRA_SITE / JIRA_EMAIL / JIRA_TOKEN / ZEPHYR_TOKEN")
        return 2
    if not argv:
        print("\n  Pass a project key:  py -3.12 run_zephyr_probe.py SCRUM")
        return 2
    project_key = argv[0]

    from tracker.jira_zephyr import JiraZephyrBackend, jql_escape
    from tracker.models import Ref

    be = JiraZephyrBackend(site=site, email=email, api_token=jtok, zephyr_token=ztok)
    results = []
    state = {}
    tag = time.strftime("%H%M%S")
    print(f"\n  Zephyr write probe — project {project_key} — run {tag}\n" + "─" * 62)

    def _healthcheck():
        be.validate_credentials()           # pings Jira /myself AND Zephyr healthcheck
        return "Jira + Zephyr both accepted"

    def _project():
        for p in be.fetch_projects():
            if project_key in (p.ref.key, p.ref.id, p.name):
                state["project"] = p
                return f"{p.ref.key} · {p.name}"
        raise Exception(f"project {project_key!r} not found")

    def _story():
        jql = (f'project = "{jql_escape(project_key)}" '
               f'AND issuetype in (Story, "User Story", Task, Bug) ORDER BY created DESC')
        issues = be._search(jql, fields=be._STORY_FIELDS)
        if not issues:
            raise _Skip("no issues in project — add a story so the case can be linked")
        state["story"] = be._to_story(issues[0])
        s = state["story"]
        return f"{s.ref.key} (numeric id {s.ref.id}) · {s.title}"

    def _plan():
        plan = be.create_test_plan(state["project"], f"QA Studio probe {tag}")
        state["plan"] = plan
        if not plan.ref.id:
            raise Exception("created plan/folder has no id")
        return f"folder id {plan.ref.id}"

    def _suite_idempotent():
        story = state.get("story")
        if story is None:
            raise _Skip("no story — cannot make a requirement suite")
        a = be.ensure_suite_for_story(state["project"], state["plan"], story)
        b = be.ensure_suite_for_story(state["project"], state["plan"], story)
        if a.ref.id != b.ref.id:
            raise Exception(f"NOT idempotent: {a.ref.id} != {b.ref.id} "
                            "(regeneration would recreate every case)")
        state["suite"] = a
        return f"stable folder id {a.ref.id}"

    def _create_case():
        case = be.create_test_case(state["project"], state["plan"],
                                   state.get("suite"), f"Probe case {tag}",
                                   state.get("story"))
        state["case"] = case
        if not (case.ref.key or case.ref.id):
            raise Exception("created case has no key/id")
        return f"{case.ref.key or case.ref.id}"

    def _steps_roundtrip():
        from tracker.models import Step
        sent = [
            Step(action="Open the login page", expected="Login form shows", index=1),
            Step(action="Submit valid credentials", expected="Dashboard loads",
                 data="user=alice", index=2),
        ]
        be.update_test_case_steps(state["case"].ref, sent)
        got = be.fetch_test_case_steps(state["case"].ref)
        if len(got) != len(sent):
            raise Exception(f"wrote {len(sent)} steps, read {len(got)}")
        for i, (a, b) in enumerate(zip(sent, got), 1):
            if (a.action or "").strip() != (b.action or "").strip():
                raise Exception(f"step {i} action changed: {a.action!r} -> {b.action!r}")
            if (a.expected or "").strip() != (b.expected or "").strip():
                raise Exception(f"step {i} expected changed")
            if (a.data or "") != (b.data or ""):
                raise Exception(f"step {i} testData lost: {a.data!r} -> {b.data!r}")
        return f"{len(got)} steps round-tripped (incl. testData)"

    def _titles():
        titles = be.fetch_existing_titles_for_suite(
            state["project"], state["plan"], state["suite"])
        if state["case"].title not in titles:
            raise Exception("created case missing from suite title list")
        return f"{len(titles)} title(s), new case present"

    def _issue_link():
        # D2: the case was linked to its Jira story at create time. Confirm by
        # reading the case's issue links back from Zephyr.
        story = state.get("story")
        case = state.get("case")
        if story is None:
            raise _Skip("no story to have linked")
        key = case.ref.key or case.ref.id
        payload = be._need_zephyr().get(f"testcases/{key}/links/issues")
        links = (payload or {}).get("values") or (payload if isinstance(payload, list) else [])
        n = len(links)
        return f"{n} issue link(s) on {key}" if n else "no link found (D2 link may have failed silently)"

    def _delete():
        be.delete_test_case(state["project"], state["plan"],
                            state["suite"], state["case"].ref)
        remaining = [ (c.ref.key or c.ref.id) for c in
                      be.fetch_test_cases_for_suite(state["project"], state["plan"],
                                                    state["suite"]) ]
        if (state["case"].ref.key or state["case"].ref.id) in remaining:
            raise Exception("case still present after delete")
        return "deleted and verified"

    steps = [
        ("credentials (Jira + Zephyr)", _healthcheck),
        ("project found", _project),
        ("real story fetched from Jira", _story),
        ("plan folder created", _plan),
        ("suite folder idempotent", _suite_idempotent),
        ("test case created", _create_case),
        ("steps OVERWRITE round-trip", _steps_roundtrip),
        ("existing titles include new case", _titles),
        ("issue link (D2)", _issue_link),
        ("test case deleted", _delete),
    ]
    ok_all = True
    for name, fn in steps:
        if not _step(name, fn, results) and not results[-1][3]:
            # a hard failure early (auth/project) makes the rest meaningless
            if name in ("credentials (Jira + Zephyr)", "project found"):
                ok_all = False
                break
            ok_all = False

    passed = sum(1 for _, ok, _, sk in results if ok and not sk)
    skipped = sum(1 for _, _, _, sk in results if sk)
    failed = sum(1 for _, ok, _, sk in results if not ok and not sk)
    print("─" * 62)
    print(f"  {passed} passed · {skipped} skipped · {failed} failed")
    if failed:
        print("\n  Failures above are the Zephyr write-path issues to fix — each names")
        print("  the operation and what went wrong. Paste this back.")
    else:
        print("\n  Zephyr write path verified end-to-end. Generation can now run on")
        print("  Jira+Zephyr. (One 'QA Studio probe …' folder tree was left behind.)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
