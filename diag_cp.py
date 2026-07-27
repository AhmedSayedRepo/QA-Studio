"""diag_cp.py — throwaway, read-only. Shows what the Sprint-Plan complexity
scorer actually sees for each story, so we can tell whether the inverted effort
estimate is a content-fetch problem or an AI-scoring problem.

    set JIRA_SITE=... JIRA_EMAIL=... JIRA_TOKEN=...
    set XRAY_CLIENT_ID=... XRAY_CLIENT_SECRET=...
    py -3.12 diag_cp.py SCRUM-1 SCRUM-2 SCRUM-3
"""
import os, re, sys
from tracker.xray import XrayBackend
from tracker.models import Ref

keys = sys.argv[1:] or ["SCRUM-1", "SCRUM-2", "SCRUM-3"]
be = XrayBackend(
    site=os.environ["JIRA_SITE"], email=os.environ["JIRA_EMAIL"],
    api_token=os.environ["JIRA_TOKEN"],
    client_id=os.environ["XRAY_CLIENT_ID"],
    client_secret=os.environ["XRAY_CLIENT_SECRET"])


def plain(html):
    s = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", s).strip()


stories = be.fetch_stories([Ref(id=k, key=k) for k in keys]) or []
print(f"fetched {len(stories)} stories\n")
for st in stories:
    title = getattr(st, "title", "") or ""
    desc = plain(getattr(st, "description", "") or "")
    crit = plain(getattr(st, "acceptance_criteria", "") or "")
    wt, wd, wc = len(title.split()), len(desc.split()), len(crit.split())
    # exact heuristic from regression._fetch_cp_complexity
    u = wc * 1.0 + wd * 0.4 + wt * 0.2
    units = max(1.0, u)
    print(f"=== {st.ref.key}  ({st.ref.id})")
    print(f"    title      : {title!r}  ({wt} words)")
    print(f"    description: {wd} words  -> {desc[:120]!r}")
    print(f"    accept.crit: {wc} words  -> {crit[:120]!r}")
    print(f"    heuristic units = {units:.2f}  (crit*1 + desc*0.4 + title*0.2)")
    print()
print("Higher units SHOULD map to more hours. If an empty story shows high units, "
      "the content fetch is wrong; if units look right here but the app's hours are "
      "inverted, the AI facet blend is the culprit.")
