"""zephyr_link_check.py - is a Zephyr test case linked to its Jira story?

    py -3.12 zephyr_link_check.py SS-T1167 SS-30552

Uses the credentials already saved in QA Studio (no tokens are typed or
printed). Read-only: changes nothing in Jira or Zephyr. Paste the output to
Claude.
"""
import sys
import types

import backend_setup as B
from jira_sprint_check import _saved_creds
from tracker.models import Ref


def _err(exc):
    return (getattr(exc, "message", "") or str(exc))[:200]


def main(case_key, story_key=""):
    app = types.SimpleNamespace(creds=_saved_creds(), project=case_key.split("-")[0])
    if B.active(app.creds) != "jira_zephyr":
        print("No saved Jira + Zephyr credentials found (connect in QA Studio, then retry).")
        return
    be = B.get_backend(app)
    z, jira = be._need_zephyr(), be._need_jira()

    print("Test case", case_key)
    try:
        row = z.get("testcases/%s" % case_key) or {}
        print("  name:  ", (row.get("name") or "")[:90])
        print("  folder:", (row.get("folder") or {}).get("id"))
    except Exception as exc:
        print("  could not read it:", _err(exc))
    try:
        links = z.get("testcases/%s/links" % case_key) or {}
        issues = links.get("issues") or []
        print("  linked Jira issues: %d" % len(issues))
        for it in issues:
            iid = it.get("issueId")
            try:
                key = (jira.get("api/3/issue/%s" % iid, params={"fields": "summary"}) or {}).get("key")
            except Exception:
                key = "?"
            print("    issueId=%s  ->  %s  (link type: %s)" % (iid, key, it.get("type") or "-"))
    except Exception as exc:
        print("  could not read its links:", _err(exc))

    if story_key:
        print("\nStory", story_key)
        try:
            found = be.fetch_stories([Ref(id=story_key, key=story_key)])
        except Exception as exc:
            print("  could not read the story:", _err(exc))
            found = []
        if found:
            print("  numeric id QA Studio links with:", found[0].ref.id)
        else:
            print("  QA Studio could not read this story from Jira")
        try:
            keys = be.linked_test_case_keys(story_key)
            print("  test cases Zephyr shows as linked: %d" % len(keys))
            print("   ", ", ".join(keys[:40]) or "-")
            if case_key in keys:
                print("  -> %s IS linked to %s" % (case_key, story_key))
            else:
                print("  -> %s is NOT linked to %s" % (case_key, story_key))
        except Exception as exc:
            print("  could not read the story's links:", _err(exc))


if __name__ == "__main__":
    a = [x.strip() for x in sys.argv[1:]]
    if not a:
        print(__doc__)
    else:
        main(a[0], a[1] if len(a) > 1 else "")
