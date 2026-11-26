"""jira_sprint_check.py - show where QA Studio reads Jira sprints and stories.

    py -3.12 jira_sprint_check.py SS

Uses the credentials already saved in QA Studio (nothing is typed or printed:
no tokens, only board / sprint names and counts). Read-only: it changes
nothing in Jira or Zephyr. Paste the output to Claude.
"""
import sys
import types

import backend_setup as B
import store


def _saved_creds():
    """The signed-in account's saved credentials: QA Studio keeps one file per
    account (creds_<user>.dat); use the newest one set to a Jira backend."""
    import glob
    import os
    files = sorted(glob.glob(os.path.join(store.CRED_DIR, "creds_*.dat")),
                   key=os.path.getmtime, reverse=True)
    for path in files + [store._DEFAULT_FILE]:
        store.CRED_FILE = path
        try:
            d = store.load() or {}
        except Exception:
            continue
        if B.active(d) != B.AZURE:
            return d
    return {}


def main(project_key):
    app = types.SimpleNamespace(creds=_saved_creds(), project=project_key)
    if B.active(app.creds) == B.AZURE:
        print("No saved Jira credentials found (open QA Studio, connect Jira + Zephyr, then retry).")
        return
    print("Backend:", B.active(app.creds))
    be = B.get_backend(app)
    jira = be._need_jira()
    for params in ({"projectKeyOrId": project_key, "type": "scrum", "maxResults": 50},
                   {"projectKeyOrId": project_key, "maxResults": 50}):
        rows = (jira.get("agile/1.0/board", params=params) or {}).get("values") or []
        print("\nBoards (%s): %d" % ("scrum" if "type" in params else "any type", len(rows)))
        for r in rows:
            loc = (r.get("location") or {})
            print("  #%s  %-40s type=%-6s located in: %s" % (
                r.get("id"), (r.get("name") or "")[:40], r.get("type"), loc.get("projectKey") or "-"))
        if rows:
            break
    print("\nBoards QA Studio uses:", be._boards_for(project_key))
    print("Story issue types:", be._story_issue_types(project_key))
    sprints = be.fetch_sprints(project_key)
    print("\nSprints: %d (active first)" % len(sprints))
    for sp in sprints[:12]:
        print("  %-8s id=%-6s %-45s board: %s" % (sp.state, sp.path, sp.name[:45], be.sprint_board(sp.path)))
    for sp in [s for s in sprints if s.state == "active"][:3]:
        all_issues = be._search('sprint = %s' % sp.path, fields=["issuetype", "project"], limit=200)
        types_seen = {}
        for i in all_issues:
            f = i.get("fields") or {}
            t = (f.get("issuetype") or {}).get("name") or "?"
            p = (f.get("project") or {}).get("key") or "?"
            types_seen[(p, t)] = types_seen.get((p, t), 0) + 1
        print("\nActive sprint %s (%s): %d issues in it" % (sp.name, sp.path, len(all_issues)))
        for (p, t), n in sorted(types_seen.items()):
            print("   %-6s %-25s %d" % (p, t, n))
        quoted = be._search('sprint = "%s"' % sp.path, fields=["issuetype"], limit=200)
        print("   sprint = %s finds %d, sprint = \"%s\" finds %d" % (sp.path, len(all_issues), sp.path, len(quoted)))
        print("   -> QA Studio's story query finds:", len(be.fetch_stories_in_sprint(project_key, sp.path)))
    print("\nBacklog stories (fallback):", len(be.fetch_backlog_stories(project_key, 100)))


if __name__ == "__main__":
    main((sys.argv[1] if len(sys.argv) > 1 else "SS").strip())
