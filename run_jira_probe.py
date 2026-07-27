"""run_jira_probe.py — verify the Jira READ path against a live site, with NO
Zephyr required.

    set JIRA_SITE=https://your-team.atlassian.net
    set JIRA_EMAIL=you@company.com
    set JIRA_TOKEN=your-api-token
    py -3.12 run_jira_probe.py               (lists projects, then stops)
    py -3.12 run_jira_probe.py PROJ          (probes sprints + stories in PROJ)

WHY THIS, AND WHY NOW
Zephyr can still be installing — this needs only Jira. It exercises exactly the
half of the integration that has never run against a real server: site-URL
validation, auth, projects, board discovery, sprints, JQL story search, and —
the important one — the ADF→HTML renderer on REAL acceptance-criteria text. ADF
is the piece most likely to be subtly wrong, because stubs only ever fed it the
shapes I expected.

SECURITY: the token is read from the environment and never printed. Output
contains project/sprint/story names and rendered HTML only — safe to paste
back. If any rendered field still looks like raw JSON (`{"type":"doc"...}`),
that's the finding: the ADF renderer missed a node type.

READ-ONLY. Creates/updates/deletes nothing.
Requires Python 3.12 (engine.py's f-strings) only if you import engine — this
script does NOT, so it runs on any Python 3.8+.
"""
from __future__ import annotations

import os
import sys


def _get(name):
    return (os.environ.get(name) or "").strip()


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    site, email, token = _get("JIRA_SITE"), _get("JIRA_EMAIL"), _get("JIRA_TOKEN")
    missing = [n for n, v in (("JIRA_SITE", site), ("JIRA_EMAIL", email),
                              ("JIRA_TOKEN", token)) if not v]
    if missing:
        print(f"\n  Set these environment variables first: {', '.join(missing)}")
        print("    set JIRA_SITE=https://your-team.atlassian.net")
        print("    set JIRA_EMAIL=you@company.com")
        print("    set JIRA_TOKEN=your-api-token")
        return 2

    from tracker.jira_zephyr import JiraZephyrBackend, validate_site_url
    from tracker import adf

    # 1) Site-URL / SSRF validation (the guard that runs before any token flies).
    try:
        normalized = validate_site_url(site)
        print(f"\n  site URL     : accepted -> {normalized}")
    except Exception as exc:
        print(f"\n  site URL     : REJECTED -> {exc}")
        return 1

    # Zephyr deliberately left unconfigured — only the Jira half is exercised.
    be = JiraZephyrBackend(site=site, email=email, api_token=token, zephyr_token="")

    # 2) Auth (Jira /myself only — NOT validate_credentials, which also pings Zephyr).
    try:
        me = be._need_jira().get("api/3/myself")
        who = (me or {}).get("displayName") or (me or {}).get("emailAddress") or "?"
        print(f"  auth         : OK — signed in as {who}")
    except Exception as exc:
        print(f"  auth         : FAILED — {exc}")
        return 1

    # 3) Projects.
    try:
        projects = be.fetch_projects()
        print(f"  projects     : {len(projects)} found")
        for p in projects[:15]:
            print(f"                   {p.ref.key:12} {p.name}")
        if len(projects) > 15:
            print(f"                   … and {len(projects) - 15} more")
    except Exception as exc:
        print(f"  projects     : FAILED — {exc}")
        return 1

    project_key = argv[0] if argv else None
    if not project_key:
        print("\n  Pass a project key to probe sprints + stories (and ADF):")
        print("    py -3.12 run_jira_probe.py <KEY>")
        return 0

    proj = next((p for p in projects if project_key in (p.ref.key, p.ref.id, p.name)),
                None)
    if proj is None:
        print(f"\n  Project {project_key!r} not in the list above.")
        return 1
    print(f"\n  probing project: {proj.ref.key} · {proj.name}")

    # 4) Board discovery, reported SEPARATELY from sprints so "no board" and
    #    "board exists but no sprints yet" are distinguishable — one is an
    #    adapter problem, the other is just an un-set-up project.
    try:
        board_id = be._board_for(proj.ref.key)
        print(f"  scrum board  : {'found (id ' + str(board_id) + ')' if board_id else 'none'}")
    except Exception as exc:
        print(f"  scrum board  : lookup FAILED — {exc}")
        board_id = None

    try:
        sprints = be.fetch_sprints(proj)
        print(f"  sprints      : {len(sprints)} found")
        for s in sprints[:8]:
            print(f"                   [{s.state or '?':7}] {s.name}  (id {s.path})")
    except Exception as exc:
        print(f"  sprints      : FAILED — {exc}")
        sprints = []

    # 5) Stories + ADF. Prefer the newest sprint; if there are none (fresh
    #    project), fall back to ANY backlog issue via JQL — the ADF renderer,
    #    JQL search and story-DTO conversion are the real targets here and
    #    don't need a sprint to exercise.
    stories = []
    source = ""
    if sprints:
        target = sprints[-1]
        source = f"sprint {target.name}"
        try:
            stories = be.fetch_stories_in_sprint(proj, target.path)
        except Exception as exc:
            print(f"  stories      : sprint fetch FAILED — {exc}")
    if not stories:
        source = "backlog (JQL, no sprint needed)"
        try:
            from tracker.jira_zephyr import jql_escape
            jql = (f'project = "{jql_escape(proj.ref.key)}" '
                   f'AND issuetype in (Story, "User Story", Task, Bug) '
                   f'ORDER BY created DESC')
            issues = be._search(jql, fields=be._STORY_FIELDS)
            # Also pull the discovered AC custom field per issue via _to_story.
            stories = [be._to_story(i) for i in issues]
        except Exception as exc:
            print(f"  stories      : JQL fetch FAILED — {exc}")
            return 1

    print(f"  stories      : {len(stories)} from {source}")
    if not stories:
        print("\n  Project has no issues yet. Add a story or two (with a description /")
        print("  acceptance criteria, ideally with a bullet list or bold text), then")
        print("  re-run — that's what exercises the ADF renderer.")
        return 0

    # --raw dumps the actual ADF JSON Jira stored, so we can tell a RENDER bug
    # (my code flattened good structure) apart from a DATA bug (the content was
    # pasted/stored oddly). Prints the raw description + any ADF custom field
    # for the first issue only.
    if "--raw" in argv:
        import json as _json
        raw = be._search(f'key = "{stories[0].ref.key}"', fields=be._STORY_FIELDS)
        f = (raw[0].get("fields") or {}) if raw else {}
        print(f"\n  RAW ADF for {stories[0].ref.key}:")
        print("  description:")
        print(_json.dumps(f.get("description"), indent=2, ensure_ascii=False)[:2500])
        for k, v in f.items():
            if k.startswith("customfield_") and isinstance(v, dict) and v.get("type") == "doc":
                print(f"  {k}:")
                print(_json.dumps(v, indent=2, ensure_ascii=False)[:2500])
        return 0

    raw_json_seen = False
    for story in stories[:3]:
        print(f"\n    {story.ref.key} · {story.title}")
        ac = story.acceptance_criteria or story.description or "(no description/AC)"
        looks_raw = ac.lstrip().startswith(('{"', '{ "')) or '"type":' in ac[:40]
        raw_json_seen = raw_json_seen or looks_raw
        preview = ac[:400] + ("…" if len(ac) > 400 else "")
        print(f"      AC/desc (rendered): {preview}")
        if looks_raw:
            print("      ^^ LOOKS LIKE RAW ADF JSON — renderer missed a node type (the finding).")

    print("\n" + "─" * 62)
    if raw_json_seen:
        print("  RESULT: Jira reads work, but ADF rendering is INCOMPLETE — paste the")
        print("  raw-JSON block above and I'll extend tracker/adf.py to handle it.")
        return 1
    print("  RESULT: Jira read path verified end-to-end (auth, projects, sprints,")
    print("  stories, ADF). This is the half that never touched a live server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
