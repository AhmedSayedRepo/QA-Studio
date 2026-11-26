"""jira_epic_check.py - which epic does QA Studio see for each story?

    py -3.12 jira_epic_check.py SS-11885 SS-17019 SS-20000

For each story it prints the status, the issue's `parent`, the classic
"Epic Link" field (if the site has one) and the epic QA Studio resolves, so
"No Feature" in the Regression Plan can be checked against Jira.

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


def main(keys):
    app = types.SimpleNamespace(creds=_saved_creds(), project=keys[0].split("-")[0])
    if B.active(app.creds) != "jira_zephyr":
        print("No saved Jira + Zephyr credentials found (connect in QA Studio, then retry).")
        return
    be = B.get_backend(app)
    jira = be._need_jira()
    fid = be._epic_link_field_id()
    print('"Epic Link" field on this site:', fid or "none (epics come from `parent`)")

    resolved = {}
    try:
        for st in be.fetch_stories([Ref(id=k, key=k) for k in keys]):
            resolved[st.ref.key or st.ref.id] = st
    except Exception as exc:
        print("QA Studio could not read the stories:", _err(exc))

    for k in keys:
        print("\nStory", k)
        try:
            fields = "summary,status,parent,issuetype" + ("," + fid if fid else "")
            row = jira.get("api/3/issue/%s" % k, params={"fields": fields}) or {}
            f = row.get("fields") or {}
            print("  type:  ", (f.get("issuetype") or {}).get("name") or "-")
            print("  status:", (f.get("status") or {}).get("name") or "-")
            par = f.get("parent") or {}
            if par:
                pf = par.get("fields") or {}
                print("  parent:", par.get("key"), "-",
                      (pf.get("issuetype") or {}).get("name") or "?", "-",
                      (pf.get("summary") or "")[:70])
            else:
                print("  parent: none")
            if fid:
                print("  Epic Link:", f.get(fid) or "none")
        except Exception as exc:
            print("  could not read it from Jira:", _err(exc))
        st = resolved.get(k)
        if st is None:
            print("  -> QA Studio could not read this story (deleted/moved or no access)")
        elif getattr(st, "epic_id", ""):
            print("  -> QA Studio groups it under epic", st.epic_id,
                  ("- " + st.epic_name[:70]) if getattr(st, "epic_name", "") else "")
        else:
            print('  -> QA Studio shows it under "No Feature" (no epic in Jira)')


if __name__ == "__main__":
    a = [x.strip() for x in sys.argv[1:] if x.strip()]
    if not a:
        print(__doc__)
    else:
        main(a)
