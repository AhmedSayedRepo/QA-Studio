"""tracker/xray.py — Xray (Cloud) backend.

THE PAYOFF OF THE SEAM
Xray stores its tests AS JIRA ISSUES, so the entire Jira read path — projects,
sprints, stories, ADF acceptance-criteria, the `/search/jql` migration, board
discovery — is inherited UNCHANGED and already live-verified (cont'd #112).
`XrayBackend` subclasses `JiraZephyrBackend` purely to reuse that read code and
overrides only the WRITE half against Xray's own API. The inherited Zephyr
session is never created (`zephyr=None`), and every Zephyr-specific write is
overridden, so no Zephyr code path is reachable here.

Subclassing (rather than extracting a shared base) is deliberate: the Jira read
methods in jira_zephyr.py are LIVE-VERIFIED, and this session's rule has been to
never refactor verified code to add a feature. This file is purely additive.

TWO APIs, LIKE ZEPHYR, BUT DIFFERENT ONES
  Jira Cloud   https://{site}.atlassian.net/rest/...   Basic(email, api_token)
                 — reads (inherited) + issue delete + issue links
  Xray Cloud   https://xray.cloud.getxray.app/api/v2   Bearer (from client-id/secret)
                 — test creation, STEPS (GraphQL only), test sets

KEY API FACTS (confirmed against Xray Cloud docs):
  * Auth: POST /api/v2/authenticate {client_id, client_secret} -> a bearer token
    (the JSON body IS the token string). Expires in 24h; we fetch once and cache.
  * Steps CANNOT be set over REST — only via the GraphQL `addTestStep` /
    `removeTestStep` mutations. This is the whole reason Xray needs GraphQL.
  * A Test is created with GraphQL `createTest` (which also creates the Jira
    issue). A Test Set groups tests (`createTestSet` + `addTestsToTestSet`).

STATUS: written from the documented contract, NOT yet run against a live Xray.
`run_xray_probe.py` is the live-verification gate — same gate that found 3 Azure
and 2 Jira bugs. Expect bugs here too, especially in the GraphQL step round-trip.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

from .base import Capability
from .errors import AuthFailed, NotConfigured, TrackerError
from .http import TrackerSession
from .jira_zephyr import JiraZephyrBackend, jql_escape
from .models import Plan, Project, Ref, Step, Story, Suite, TestCase

XRAY_BASE = "https://xray.cloud.getxray.app"


class XrayClient:
    """Xray Cloud REST+GraphQL client with lazy, cached bearer auth.

    Injectable (so the probe/tests can stub it). The token exchange is done once
    on first use and reused; a 401 anywhere triggers exactly one re-auth in case
    the 24h token lapsed mid-session.
    """

    def __init__(self, client_id, client_secret, base=XRAY_BASE, session=None):
        self._id = client_id
        self._secret = client_secret
        self._base = (base or XRAY_BASE).rstrip("/")
        self._token = None
        self._lock = threading.Lock()
        # A bare session for the auth call + a token-bearing one for the rest.
        self._auth = session or TrackerSession(base_url=self._base, backend="xray")
        self._api = None

    def _authenticate(self):
        # Body IS the token, JSON-encoded as a quoted string.
        raw = self._auth.post("/api/v2/authenticate",
                              json={"client_id": self._id,
                                    "client_secret": self._secret})
        token = raw.strip('"') if isinstance(raw, str) else raw
        if not token or not isinstance(token, str):
            raise AuthFailed("Xray did not return a token for these credentials.",
                             remedy="Check the Xray API Key (client id + secret).",
                             backend="xray")
        self._token = token
        self._api = TrackerSession(
            base_url=self._base, backend="xray",
            headers={"Authorization": f"Bearer {token}"})

    def _ensure(self):
        with self._lock:
            if self._token is None:
                self._authenticate()

    def graphql(self, query, variables=None, _retry=True):
        self._ensure()
        try:
            payload = self._api.post("/api/v2/graphql",
                                     json={"query": query, "variables": variables or {}})
        except AuthFailed:
            if _retry:                      # token may have lapsed — re-auth once
                with self._lock:
                    self._token = None
                return self.graphql(query, variables, _retry=False)
            raise
        if isinstance(payload, dict) and payload.get("errors"):
            msg = "; ".join(e.get("message", "") for e in payload["errors"])
            raise TrackerError(f"Xray GraphQL error: {msg}", backend="xray")
        return (payload or {}).get("data") or {}

    def close(self):
        for s in (self._auth, self._api):
            if s is not None:
                s.close()


class XrayBackend(JiraZephyrBackend):
    """Xray Cloud, reusing the inherited Jira read path; Xray writes."""

    name = "xray"
    label = "Xray"
    capabilities = frozenset({
        Capability.REQUIREMENT_SUITES, Capability.NESTED_SUITES,
        Capability.STORY_LINKS, Capability.CHILD_TASKS,
        Capability.TESTER_FIELD, Capability.TASK_STATS,
        Capability.EXECUTION_STATUS,
        # NOTE: SPRINT_REPORTS deliberately NOT declared — sprint_report_data()
        # below raises _unsupported (Xray's Test-Run rollups are deferred). A
        # capability must match reality or gated callers trust a method that
        # throws (ADR-002).
        Capability.ATTACHMENTS, Capability.DELETE_TEST_CASE,
        # Xray steps carry a `data` field (action / data / result), so testData
        # survives a round-trip — unlike Azure.
        Capability.STEP_TEST_DATA,
        # Sub-task listing/editing is pure Jira (inherited), so Xray has it too.
        Capability.EDIT_SUBTASKS,
    })

    def __init__(self, site="", email="", api_token="",
                 client_id="", client_secret="", xray_base=XRAY_BASE,
                 allow_any_host=False, jira=None, xray=None):
        # Reuse the parent's Jira setup; explicitly NO Zephyr session.
        super().__init__(site=site, email=email, api_token=api_token,
                         zephyr_token="", allow_any_host=allow_any_host,
                         jira=jira, zephyr=None)
        self._xray = xray if xray is not None else (
            XrayClient(client_id, client_secret, xray_base)
            if client_id and client_secret else None)

    def _need_xray(self):
        if self._xray is None:
            raise NotConfigured("Xray is not configured.",
                                remedy="Enter your Xray API Key (client id + secret) in Setup.")
        return self._xray

    def close(self):
        super().close()
        if self._xray is not None:
            self._xray.close()

    # ── connection ────────────────────────────────────────────────────────
    def validate_credentials(self):
        # Prove BOTH halves, like the Zephyr backend: Jira reads and Xray auth.
        me = self._need_jira().get("api/3/myself")
        if not (isinstance(me, dict) and (me.get("accountId") or me.get("emailAddress"))):
            raise TrackerError("Jira did not return an account for these credentials.",
                               remedy="Check the email and API token in Setup.", backend=self.name)
        # A trivial GraphQL call forces the token exchange and proves it works.
        self._need_xray().graphql("{ getTests(limit: 1) { total } }")

    # ── plans & suites (Xray: Test Plan / Test Set Jira issues) ───────────
    def _issuetype_id(self, project_key, name):
        """Resolve a Jira issue-type NAME to its id for THIS project.

        Creating with {"issuetype": {"name": "Test Plan"}} fails with Jira's
        opaque 400 "Specify a valid issue type" whenever the project's issue-type
        scheme doesn't expose that exact type — the usual cause being that the
        Xray issue types (Test Plan / Test Set / Test) were never added to this
        project. Looking the type up via createmeta lets us (a) match by id so
        casing/registration quirks don't matter, and (b) fail with an actionable
        message that names what to fix, instead of the raw 400."""
        ck = f"{project_key}:{name.strip().lower()}"
        with self._lock:
            cache = getattr(self, "_type_cache", None)
            if cache and ck in cache:
                return cache[ck]
        try:
            payload = self._need_jira().get(
                f"api/3/issue/createmeta/{project_key}/issuetypes")
            # The granular endpoint returns the list under "issueTypes"
            # (confirmed live: keys = startAt/maxResults/total/issueTypes).
            # Keep "values" as a fallback in case a future API rev uses it.
            types = ((payload or {}).get("issueTypes")
                     or (payload or {}).get("values") or [])
        except TrackerError:
            types = []
        match = next((t for t in types
                      if (t.get("name") or "").strip().lower() == name.strip().lower()), None)
        if not match:
            avail = ", ".join(sorted(t.get("name") or "" for t in types if t.get("name"))) or "none"
            raise TrackerError(
                f"Jira project '{project_key}' has no '{name}' issue type.",
                remedy=(f"Add the Xray issue types (Test Plan, Test Set, Test) to this "
                        f"project's issue-type scheme in Jira → Project settings → Issue "
                        f"types, then retry. Issue types available on this project: {avail}."),
                backend=self.name)
        tid = str(match.get("id") or "")
        with self._lock:
            if getattr(self, "_type_cache", None) is None:
                self._type_cache = {}
            self._type_cache[ck] = tid
        return tid

    def _create_issue(self, project_key, issuetype, summary):
        # Use the resolved issue-type ID (not the bare name) so create works
        # regardless of how the project registers the type — and surfaces a clear
        # message when the type isn't in the project's scheme at all.
        type_id = self._issuetype_id(project_key, issuetype)
        row = self._need_jira().post("api/3/issue", json={"fields": {
            "project": {"key": project_key},
            "summary": summary[:255],
            "issuetype": {"id": type_id}}}) or {}
        return Ref(id=str(row.get("id") or ""), key=row.get("key") or "")

    def _find_issue(self, project_key, issuetype, summary):
        jql = (f'project = "{jql_escape(project_key)}" '
               f'AND issuetype = "{jql_escape(issuetype)}" '
               f'AND summary ~ "{jql_escape(summary)}"')
        for issue in self._search(jql, fields=["summary"]):
            if ((issue.get("fields") or {}).get("summary") or "") == summary:
                return Ref(id=str(issue.get("id") or ""), key=issue.get("key") or "")
        return None

    def fetch_test_plans(self, project):
        key = self._project_key(project)
        jql = f'project = "{jql_escape(key)}" AND issuetype = "Test Plan" ORDER BY created DESC'
        out = []
        for issue in self._search(jql, fields=["summary"]):
            out.append(Plan(ref=Ref(id=str(issue.get("id") or ""), key=issue.get("key") or ""),
                            name=(issue.get("fields") or {}).get("summary") or ""))
        return out

    def create_test_plan(self, project, name, sprint_path=""):
        key = self._project_key(project)
        ref = self._find_issue(key, "Test Plan", name) or self._create_issue(key, "Test Plan", name)
        return Plan(ref=ref, name=name, sprint_path=sprint_path)

    def find_suite_for_story(self, project, plan, story):
        """Non-creating lookup of the story's Test Set (see base.py) — same name
        match as ensure_suite_for_story without the _create_issue call, so
        read-only callers can't create empty Test Set issues as a side effect."""
        key = self._project_key(project)
        name = f"{story.ref.key} {story.title}".strip()
        cache_key = f"{key}:{plan.ref.id}:{story.ref.id}"
        with self._lock:
            hit = self._folder_cache.get(cache_key)
        if hit:
            return Suite(ref=Ref(id=str(hit)), name=name,
                         parent_ref=plan.ref, story_ref=story.ref)
        try:
            ref = self._find_issue(key, "Test Set", name)
        except TrackerError:
            return None
        if ref is None or not ref.id:
            return None
        with self._lock:
            self._folder_cache[cache_key] = ref.id
        return Suite(ref=ref, name=name, parent_ref=plan.ref,
                     story_ref=story.ref)

    def ensure_suite_for_story(self, project, plan, story):
        """Idempotent get-or-create of a Test Set for the story."""
        key = self._project_key(project)
        name = f"{story.ref.key} {story.title}".strip()
        cache_key = f"{key}:{plan.ref.id}:{story.ref.id}"
        with self._lock:
            hit = self._folder_cache.get(cache_key)
        if hit:
            return Suite(ref=Ref(id=str(hit)), name=name, parent_ref=plan.ref, story_ref=story.ref)
        ref = self._find_issue(key, "Test Set", name) or self._create_issue(key, "Test Set", name)
        with self._lock:
            self._folder_cache[cache_key] = ref.id
        return Suite(ref=ref, name=name, parent_ref=plan.ref, story_ref=story.ref)

    # ── test cases (GraphQL) ──────────────────────────────────────────────
    def fetch_test_cases_for_suite(self, project, plan, suite):
        data = self._need_xray().graphql(
            "query($id:String!){ getTestSet(issueId:$id){ tests(limit:100){ "
            "results{ issueId jira(fields:[\"key\",\"summary\"]) } } } }",
            {"id": suite.ref.id})
        results = (((data.get("getTestSet") or {}).get("tests") or {}).get("results")) or []
        out = []
        for t in results:
            j = t.get("jira") or {}
            out.append(TestCase(ref=Ref(id=str(t.get("issueId") or ""), key=j.get("key") or ""),
                                title=j.get("summary") or "", suite_ref=suite.ref,
                                story_ref=suite.story_ref,
                                url=self.item_url(Ref(id="", key=j.get("key") or ""), project)))
        return out

    def create_test_case(self, project, plan, suite, title, story=None):
        key = self._project_key(project)
        data = self._need_xray().graphql(
            "mutation($s:String!,$p:String!){ createTest("
            "testType:{name:\"Manual\"}, "
            "jira:{fields:{summary:$s, project:{key:$p}}}){ "
            "test{ issueId jira(fields:[\"key\"]) } warnings } }",
            {"s": title, "p": key})
        t = ((data.get("createTest") or {}).get("test")) or {}
        case = TestCase(ref=Ref(id=str(t.get("issueId") or ""),
                                key=(t.get("jira") or {}).get("key") or ""),
                        title=title, suite_ref=suite.ref if suite else None,
                        story_ref=(story.ref if story else None))
        # Add to the Test Set (suite) and cover the requirement (story).
        if suite is not None and case.ref.id:
            try:
                self._need_xray().graphql(
                    "mutation($ts:String!,$t:[String]!){ addTestsToTestSet("
                    "issueId:$ts, testIssueIds:$t){ warning } }",
                    {"ts": suite.ref.id, "t": [case.ref.id]})
            except TrackerError:
                pass
        if story is not None and case.ref.key and story.ref.key:
            # Xray requirement coverage is a Jira issue link (type "Test").
            try:
                self._need_jira().post("api/3/issueLink", json={
                    "type": {"name": "Test"},
                    "inwardIssue": {"key": case.ref.key},
                    "outwardIssue": {"key": story.ref.key}})
            except TrackerError:
                pass
        return case

    def _xray_issue_id(self, ref):
        """Numeric Xray issueId for a test Ref.

        Xray's GraphQL identifies issues ONLY by the internal numeric issueId
        (e.g. 10082), never the Jira key. The probe passes a Ref straight from
        create_test_case (ref.id IS that numeric id), but the generation loop
        routes case ids through engine as the human-readable KEY ("SCRUM-10")
        — cases_for_suite/create_case return `ref.key or ref.id` for a readable
        log — so the Ref that reaches fetch/update steps carries the key in
        BOTH fields. Handing that key to getTest/addTestStep fails with
        "Get my permissions failed! - issueId provided is not valid".

        Resolve here: a numeric id is used as-is (zero extra calls — the probe
        path is unchanged); a key is looked up once via Jira REST and cached.
        """
        rid = str(getattr(ref, "id", "") or "").strip()
        if rid.isdigit():
            return rid
        key = str(getattr(ref, "key", "") or "").strip() or rid
        if not key:
            return rid
        with self._lock:
            cache = getattr(self, "_issueid_cache", None)
            if cache and key in cache:
                return cache[key]
        row = self._need_jira().get(f"api/3/issue/{key}?fields=id") or {}
        iid = str(row.get("id") or "") or key
        with self._lock:
            if getattr(self, "_issueid_cache", None) is None:
                self._issueid_cache = {}
            self._issueid_cache[key] = iid
        return iid

    def fetch_test_case_steps(self, ref):
        iid = self._xray_issue_id(ref)
        data = self._need_xray().graphql(
            "query($id:String!){ getTest(issueId:$id){ steps{ id action data result } } }",
            {"id": iid})
        steps = ((data.get("getTest") or {}).get("steps")) or []
        return [Step(action=s.get("action") or "", expected=s.get("result") or "",
                     data=s.get("data") or "", index=i)
                for i, s in enumerate(steps, start=1)]

    def update_test_case_steps(self, ref, steps):
        """OVERWRITE: Xray has no bulk replace, so remove existing steps then add.
        (`createTest` can take steps inline, but the generation loop creates the
        test first and writes steps later, so this is the path that matters.)"""
        xray = self._need_xray()
        iid = self._xray_issue_id(ref)          # key → numeric issueId (see helper)
        existing = xray.graphql(
            "query($id:String!){ getTest(issueId:$id){ steps{ id } } }",
            {"id": iid})
        for s in ((existing.get("getTest") or {}).get("steps")) or []:
            if s.get("id"):
                xray.graphql("mutation($s:String!){ removeTestStep(stepId:$s) }",
                             {"s": s["id"]})
        for st in steps:
            xray.graphql(
                "mutation($id:String!,$a:String!,$d:String!,$r:String!){ addTestStep("
                "issueId:$id, step:{action:$a, data:$d, result:$r}){ id } }",
                {"id": iid, "a": st.action or "", "d": st.data or "",
                 "r": st.expected or ""})

    def delete_test_case(self, project, plan, suite, ref):
        # Deleting the Jira issue removes the Xray test with it.
        self._need_jira().delete(f"api/3/issue/{ref.key or ref.id}")

    def sprint_report_data(self, project, sprint_path):
        # Execution rollups need Xray's test-run model — deferred; declare honestly.
        self._unsupported("sprint reports")


__all__ = ["XrayBackend", "XrayClient", "XRAY_BASE"]
