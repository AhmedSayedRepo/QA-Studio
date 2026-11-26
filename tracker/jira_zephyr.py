"""tracker/jira_zephyr.py — Jira Cloud + Zephyr Scale backend.

TWO SERVICES, ONE BACKEND
This adapter fronts two independent APIs with different hosts and different
auth, which is the main structural difference from Azure DevOps:

    Jira Cloud    https://{site}.atlassian.net/rest/...   Basic(email, api_token)
    Zephyr Scale  https://api.zephyrscale.smartbear.com/v2  Bearer {zephyr_token}

Requirements live in Jira (projects, sprints, stories, sub-tasks, users); the
test library lives in Zephyr (cases, steps, folders, cycles). One `Backend`
hides that split so callers never learn it.

DECISIONS IMPLEMENTED (TRACKER_BACKENDS_PLAN.md §4)
Applied as the plan's recommended defaults. Each is isolated to one method, so
reversing any of them is a local change:

  D1  Test Plan → a root TEST_CASE **folder**; Suite → a child folder per story.
      Chosen over "plan → test cycle" because cycles are execution runs: every
      regeneration would create another one, and dedupe would never find the
      previous cases. Folders are a stable library, which is what the generation
      loop assumes.
  D2  Traceability = folder placement **and** `createTestCaseIssueLink`. The
      folder gives structure; the issue link is what Jira-side coverage
      reporting actually reads. Costs one extra call per created case.
  D3  Sprints come from Agile **board** sprints (not Jira versions), because
      those match ADO iterations semantically. Boards are discovered per project
      and cached; the sprint custom-field id is discovered, never hardcoded.
  D4  Two tokens, stored per-account, never logged.

STATUS: endpoint shapes for Zephyr (folders, cases, steps, issue links) are
confirmed against the Zephyr Scale Cloud contract. The Jira REST v3 paths are
standard but should be verified live before this is enabled for users — see the
contract suite, which is the intended verification.
"""
from __future__ import annotations

import ipaddress
import re
import threading
from datetime import date
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from . import adf
from .base import Backend, Capability
from .errors import NotConfigured, NotFound, TrackerError
from .http import TrackerSession
from .models import (
    Plan, Project, Ref, ReportData, Sprint, Step, Story, Suite, TaskStats,
    TestCase, User,
)

ZEPHYR_BASE = "https://api.zephyrscale.smartbear.com/v2"
#: EU-hosted Zephyr Scale instances answer on a different host; a token minted on
#: one region returns 401 against the other — indistinguishable from a bad token.
#: validate_credentials auto-tries this if the global host rejects the token, so
#: an EU user doesn't read a region mismatch as a wrong token.
#: (https://support.smartbear.com/zephyr-scale-cloud/api-docs/)
ZEPHYR_BASE_EU = "https://eu.api.zephyrscale.smartbear.com/v2"

#: Hosts allowed without an explicit opt-in. Jira Cloud is always *.atlassian.net.
_CLOUD_SUFFIXES = (".atlassian.net", ".atlassian.com", ".jira.com")

#: Conservative default rates (requests/sec). Both services throttle harder than
#: Azure, and run_titles/run_steps issue bursts from a worker pool.
JIRA_RATE = 8.0
ZEPHYR_RATE = 4.0


# ══════════════════════════════════════════════════════════════════════════
#  Security helpers — exercised directly by tracker/security.py
# ══════════════════════════════════════════════════════════════════════════

def validate_site_url(url, allow_any_host=False):
    """Validate a user-supplied Jira site URL before any token is sent to it.

    This field is free text in Setup, which makes it the package's sharpest
    edge: a wrong (or socially-engineered) value both exfiltrates the user's
    API token to that host and turns the desktop app into an SSRF probe against
    the user's own network — cloud metadata endpoints especially.

    Rejects: non-HTTPS, loopback/private/link-local/reserved addresses,
    credentials embedded in the URL (`https://a@evil.test` reads as host
    `evil.test` while looking like `a`), and non-Atlassian hosts unless the
    caller explicitly opts in for Data Center.

    Returns the normalized origin.
    """
    raw = (url or "").strip()
    if not raw:
        raise NotConfigured("No Jira site URL configured.",
                            remedy="Enter your Jira site, e.g. https://your-team.atlassian.net")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)

    if parsed.scheme.lower() != "https":
        raise NotConfigured(f"Jira site must use HTTPS (got {parsed.scheme or 'none'}).",
                            remedy="Use an https:// URL — a token must never travel in plaintext.")
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        raise NotConfigured("Jira site URL must not contain credentials.",
                            remedy="Remove the user:pass@ portion; enter the site host only.")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise NotConfigured("Jira site URL has no host.",
                            remedy="Enter the full site, e.g. https://your-team.atlassian.net")

    # Literal IPs are never a legitimate Jira site and are the SSRF vector.
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        addr = None
    if addr is not None:
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            raise NotConfigured(f"Refusing to connect to internal address {host}.",
                                remedy="Enter your Jira site hostname, not an IP address.")
        raise NotConfigured("Enter a Jira hostname rather than an IP address.",
                            remedy="e.g. https://your-team.atlassian.net")
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        raise NotConfigured(f"Refusing to connect to {host}.",
                            remedy="Enter your Jira site, e.g. https://your-team.atlassian.net")

    if not allow_any_host and not host.endswith(_CLOUD_SUFFIXES):
        raise NotConfigured(
            f"{host} is not a recognized Jira Cloud site.",
            remedy="Use your *.atlassian.net site, or enable Data Center mode in Setup.")

    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return f"https://{host}{port}"


_JQL_ESCAPES = {
    "\\": "\\\\", '"': '\\"', "'": "\\'",
    "\n": " ", "\r": " ", "\t": " ", "\x00": "",
}


def jql_escape(value):
    """Escape a value for embedding in a JQL string literal.

    JQL cannot write data, so this is not "SQL injection" in impact — but an
    unescaped quote in a project key or sprint name lets the value break out of
    its literal and rewrite the predicate, which can widen a query to issues the
    user was never scoped to. Escape at the boundary, always.
    """
    out = []
    for ch in str(value or ""):
        out.append(_JQL_ESCAPES.get(ch, ch))
    return "".join(out)


def _looks_like_zephyr_jwt(tok):
    """A Zephyr Scale Cloud API token is a JWT: three base64url segments joined
    by two dots (conventionally starting 'eyJ'). The 36-char UUID shown in the
    tokens table is the token *ID*, not the token — it has no dots and fails
    this. Used only to shape the error message, never to block a token."""
    parts = (tok or "").strip().split(".")
    return len(parts) == 3 and all(len(p) >= 10 for p in parts)


def _zephyr_jwt_claims(tok):
    """Best-effort decode of a Zephyr JWT's PUBLIC claims (plain base64, NOT
    signature-verified) for diagnostics only — never for auth. A Zephyr token
    embeds `context.baseUrl` (the Jira site it was minted on) and `exp`/`iat`,
    which pinpoint the two silent 401 causes a bad-token message can't: a token
    created on the WRONG site, or an EXPIRED one. Returns {} on anything
    unexpected so the caller can treat it as "no info"."""
    import base64, json
    try:
        payload = (tok or "").split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        ctx = data.get("context") or {}
        return {"base_url": (ctx.get("baseUrl") or "").rstrip("/"),
                "exp": data.get("exp"), "iat": data.get("iat")}
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════
#  Backend
# ══════════════════════════════════════════════════════════════════════════

class JiraZephyrBackend(Backend):
    """Jira Cloud (requirements) + Zephyr Scale (test library)."""

    name = "jira_zephyr"
    label = "Jira + Zephyr Scale"
    capabilities = frozenset({
        Capability.REQUIREMENT_SUITES, Capability.NESTED_SUITES,
        Capability.STORY_LINKS, Capability.CHILD_TASKS,
        Capability.TESTER_FIELD, Capability.TASK_STATS,
        Capability.EXECUTION_STATUS, Capability.SPRINT_REPORTS,
        Capability.ATTACHMENTS,
        # NOT DELETE_TEST_CASE: Zephyr Scale Cloud's REST API v2 has no endpoint
        # to delete a test case (or a folder) - only the Zephyr UI can. Without
        # the capability, dedupe reports duplicates instead of failing on them.
        # Zephyr steps DO carry testData, unlike Azure's steps XML.
        Capability.STEP_TEST_DATA,
        # List a story's sub-tasks and edit their fields (Task Manager).
        Capability.EDIT_SUBTASKS,
    })

    def __init__(self, site="", email="", api_token="", zephyr_token="",
                 allow_any_host=False, jira=None, zephyr=None):
        self.site = validate_site_url(site, allow_any_host) if site else ""
        self._email = email or ""
        self._allow_any_host = bool(allow_any_host)
        self._lock = threading.RLock()
        self._board_cache: Dict[str, list] = {}   # project key -> [(board id, name)]
        self._sprint_field: Optional[str] = None
        self._folder_cache: Dict[str, int] = {}

        # Sessions are injectable so the contract suite can run against a stub.
        self._jira = jira if jira is not None else (
            TrackerSession(base_url=f"{self.site}/rest",
                           auth=(email, api_token), backend="jira",
                           rate=JIRA_RATE) if site else None)
        # Kept so a region retry can rebuild the Zephyr session against the EU
        # host without re-plumbing the token from Setup.
        self._zephyr_token = zephyr_token or ""
        self._zephyr = zephyr if zephyr is not None else (
            TrackerSession(base_url=ZEPHYR_BASE,
                           headers={"Authorization": f"Bearer {zephyr_token}"},
                           backend="zephyr", rate=ZEPHYR_RATE)
            if zephyr_token else None)

    # ── plumbing ──────────────────────────────────────────────────────────
    def _need_jira(self):
        if self._jira is None:
            raise NotConfigured("Jira is not configured.",
                                remedy="Enter your Jira site, email and API token in Setup.")
        return self._jira

    def _need_zephyr(self):
        if self._zephyr is None:
            raise NotConfigured("Zephyr Scale is not configured.",
                                remedy="Enter your Zephyr Scale API token in Setup.")
        return self._zephyr

    def _try_other_zephyr_region(self):
        """Zephyr Scale Cloud is region-split (global host vs an EU host). A token
        minted on one region returns 401 on the other. If the current host
        rejected the token, rebuild the session against the OTHER region once and
        re-probe; on success adopt it for the rest of the run and return True.
        No-op (False) when the token was injected as a stub (contract tests) or
        the current base isn't a known region — so it never changes stubbed
        behaviour."""
        if not self._zephyr_token or self._zephyr is None:
            return False
        cur = (getattr(self._zephyr, "base_url", "") or "").rstrip("/")
        other = (ZEPHYR_BASE_EU if cur == ZEPHYR_BASE
                 else ZEPHYR_BASE if cur == ZEPHYR_BASE_EU else None)
        if not other:
            return False
        alt = TrackerSession(base_url=other,
                             headers={"Authorization": f"Bearer {self._zephyr_token}"},
                             backend="zephyr", rate=ZEPHYR_RATE)
        try:
            alt.get("healthcheck")
        except TrackerError:
            alt.close()
            return False
        try:
            self._zephyr.close()
        except Exception:
            pass
        self._zephyr = alt
        return True

    @staticmethod
    def _project_key(project):
        if isinstance(project, Project):
            return project.ref.key or project.name
        return str(project)

    def close(self):
        for sess in (self._jira, self._zephyr):
            if sess is not None:
                sess.close()

    # ── core: connection & projects ───────────────────────────────────────
    def validate_credentials(self):
        # Check each credential separately and label the failure by service.
        # A single opaque "401" can't tell you whether Jira or Zephyr rejected
        # you, which turns a one-field fix into guesswork.
        try:
            me = self._need_jira().get("api/3/myself")
        except TrackerError as e:
            raise TrackerError(
                f"Jira rejected these credentials ({e.message})",
                remedy=("Check the Jira Site URL, account email, and API token. "
                        "The API token is created at "
                        "id.atlassian.com/manage-profile/security/api-tokens and "
                        "must belong to the same email you entered."),
                backend=self.name) from e
        if not (isinstance(me, dict) and (me.get("accountId") or me.get("emailAddress"))):
            raise TrackerError("Jira did not return an account for these credentials.",
                               remedy="Check the email and API token in Setup.",
                               backend=self.name)
        # Prove the SECOND credential too. Validating only Jira would let a bad
        # Zephyr token pass Setup and fail later mid-generation, which reads as
        # a broken run rather than a bad token.
        try:
            self._need_zephyr().get("healthcheck")
        except TrackerError as e:
            # A region mismatch (an EU token hitting the global host, or vice
            # versa) is also a 401 — indistinguishable from a bad token. Try the
            # other Zephyr region once before blaming the token; only if that
            # also fails is the token genuinely wrong.
            if not self._try_other_zephyr_region():
                tok = self._zephyr_token or ""
                n = len(tok)
                if not _looks_like_zephyr_jwt(tok):
                    # Wrong shape — almost always the token *ID* (UUID) from the
                    # tokens table rather than the long token from the pop-up.
                    remedy = (f"The field holds {n} characters and doesn't look like a "
                              f"Zephyr token. A Zephyr Scale token is a long JWT with "
                              f"two dots (eyJ….….signature). You've likely pasted the "
                              f"short token ID (UUID) from the tokens table — copy the "
                              f"LONG token shown once in the 'Create access token' pop-up "
                              f"instead.")
                else:
                    # Correct shape and full length — so the field is NOT truncating
                    # it; Zephyr itself rejected it. Decode the token's own claims to
                    # pinpoint the two silent causes (wrong site / expired) that a
                    # 401 alone can't distinguish from a revoked token.
                    import time as _t
                    claims = _zephyr_jwt_claims(tok)
                    now = int(_t.time())
                    extra = ""
                    if claims.get("exp") and claims["exp"] < now:
                        extra = " This token has EXPIRED — create a fresh one."
                    elif (claims.get("base_url") and self.site
                          and claims["base_url"] != self.site.rstrip("/")):
                        extra = (f" The token was created on {claims['base_url']}, not "
                                 f"{self.site} — create the Zephyr token on the SAME site "
                                 f"as your Jira Site URL.")
                    remedy = (f"The field captured the full token ({n} chars, JWT-shaped) — "
                              f"it isn't being truncated, so Zephyr itself rejected it.{extra} "
                              f"If it's not expired or from another site, the usual causes "
                              f"are: the token was revoked, a just-created token needs a "
                              f"minute to activate, or Zephyr Scale API access isn't enabled "
                              f"for this account. Test the token directly against "
                              f"api.zephyrscale.smartbear.com/v2/healthcheck to confirm it's "
                              f"the token, not the app.")
                raise TrackerError(
                    f"Zephyr Scale rejected the token ({e.message})",
                    remedy=remedy, backend=self.name) from e

    def fetch_projects(self):
        sess = self._need_jira()
        out = []
        for row in sess.paginate("api/3/project/search",
                                 extract=lambda p: (p or {}).get("values") or []):
            if not isinstance(row, dict):
                continue
            out.append(Project(ref=Ref(id=str(row.get("id") or ""),
                                       key=row.get("key") or ""),
                               name=row.get("name") or row.get("key") or ""))
        return out

    def zephyr_enabled_project_keys(self):
        """Jira project keys Zephyr is turned on for (Zephyr's GET /projects;
        a row without an `enabled` flag counts as enabled). None when it can't
        be read (no Zephyr token, network, an API change) - callers then just
        don't use it. Lets Connect land on a project that works instead of
        the first one in the list."""
        try:
            rows = list(self._need_zephyr().paginate(
                "projects", cursor="startAt", limit_param="maxResults",
                extract=lambda p: (p or {}).get("values") or []))
        except Exception:
            return None
        return [str(r.get("key")) for r in rows
                if isinstance(r, dict) and r.get("key") and r.get("enabled", True) is not False]

    # ── core: sprints (D3) ────────────────────────────────────────────────
    def _boards_for(self, project_key):
        """The project's boards as [(id, name)], best first. Boards LOCATED in
        the project (board.location.projectKey) win - a company site has many
        boards whose filters include a project's issues (old team boards,
        cross-project boards), and taking simply the first one returned showed
        another board's old sprints. Scrum boards are asked for first; if a
        project has none located in it, the first board returned is kept (the
        previous behaviour). Cached per project."""
        with self._lock:
            if project_key in self._board_cache:
                return self._board_cache[project_key]
        rows = []
        # Try scrum-typed boards first (only those have sprints), then ANY
        # board: a team-managed ("next-gen") project's board isn't always
        # returned under type=scrum by the classic Agile API.
        for params in ({"projectKeyOrId": project_key, "type": "scrum", "maxResults": 50},
                       {"projectKeyOrId": project_key, "maxResults": 50}):
            try:
                payload = self._need_jira().get("agile/1.0/board", params=params)
            except Exception:
                payload = None
            rows = [r for r in (payload or {}).get("values") or [] if isinstance(r, dict) and r.get("id")]
            if rows:
                break
        own = [r for r in rows
               if str(((r.get("location") or {}).get("projectKey") or "")).upper() == project_key.upper()]
        chosen = (own or rows[:1])[:5]
        boards = [(int(r["id"]), str(r.get("name") or r["id"])) for r in chosen]
        with self._lock:
            self._board_cache[project_key] = boards
        return boards

    def _board_for(self, project_key):
        """First (best) board id for a project, or None."""
        boards = self._boards_for(project_key)
        return boards[0][0] if boards else None

    @staticmethod
    def _sprint_date(value):
        try:
            return date.fromisoformat(str(value or "")[:10]) if value else None
        except ValueError:
            return None

    def sprint_board(self, sprint_id):
        """Board name a listed sprint came from ('' if unknown)."""
        return (self.__dict__.get("_sprint_boards") or {}).get(str(sprint_id), "")

    def fetch_sprints(self, project):
        """The project's sprints from its own board(s), de-duplicated (a sprint
        can show on more than one board): active first, then future (soonest
        first), then closed (most recent first). Full Jira names, dates and
        state are kept; the board each came from is in sprint_board()."""
        key = self._project_key(project)
        boards = self._boards_for(key)
        if not boards:
            # No board => no sprints. Empty list, not an error: a project
            # without a scrum board is a legitimate configuration.
            return []
        seen, out = {}, []
        names = self.__dict__.setdefault("_sprint_boards", {})
        for board_id, board_name in boards:
            try:
                rows = list(self._need_jira().paginate(
                    f"agile/1.0/board/{board_id}/sprint",
                    extract=lambda p: (p or {}).get("values") or []))
            except Exception:
                if board_id == boards[0][0] and len(boards) == 1:
                    raise
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                sid = str(row.get("id") or "")
                if not sid or sid in seen:
                    continue
                seen[sid] = True
                names[sid] = board_name
                out.append(Sprint(ref=Ref(id=sid, key=row.get("name") or sid),
                                  name=row.get("name") or sid,
                                  path=sid,          # opaque token: the sprint id
                                  state=(row.get("state") or "").lower(),
                                  start=self._sprint_date(row.get("startDate")),
                                  end=self._sprint_date(row.get("endDate") or row.get("completeDate"))))
        rank = {"active": 0, "future": 1}

        def order(sp):
            r = rank.get(sp.state, 2)
            if r == 1:
                return (r, sp.start or date.max, int(sp.path) if sp.path.isdigit() else 0)
            d = sp.end or sp.start or date.min
            return (r, -d.toordinal(), -(int(sp.path) if sp.path.isdigit() else 0))
        out.sort(key=order)
        return out

    def _sprint_field_id(self):
        """Discover the Sprint custom-field id. Never hardcode `customfield_10020`
        — it differs per Jira site, and a wrong id silently returns no stories."""
        with self._lock:
            if self._sprint_field:
                return self._sprint_field
        for row in self._need_jira().get("api/3/field") or []:
            if not isinstance(row, dict):
                continue
            if (row.get("name") or "").strip().lower() == "sprint":
                with self._lock:
                    self._sprint_field = row.get("id") or "sprint"
                return self._sprint_field
        with self._lock:
            self._sprint_field = "sprint"
        return self._sprint_field

    # ── core: stories ─────────────────────────────────────────────────────
    def _search(self, jql, fields=None, limit=None):
        """Run a JQL search via the CURRENT endpoint, /rest/api/3/search/jql.

        The old /rest/api/3/search was REMOVED from Jira Cloud (410 Gone —
        caught live against a real site; stubs had modelled the dead endpoint).
        The replacement differs in three ways this method has to handle:
          * token pagination (`nextPageToken`), not `startAt` — so the generic
            startAt-based paginate() can't be used here;
          * no `total` in the response;
          * `fields` must be requested explicitly or only ids come back.
        There is also a documented server-side bug where `nextPageToken` can
        chain forever without `isLast` ever turning true, so this caps pages
        AND refuses to follow a token it has already seen.
        """
        sess = self._need_jira()
        want_fields = fields or ["summary", "description", "status",
                                 "assignee", "issuetype"]
        issues = []
        token = None
        seen_tokens = set()
        for _ in range(200):                      # hard page cap (safety)
            params = {"jql": jql, "fields": ",".join(want_fields),
                      "maxResults": 100}
            if token:
                params["nextPageToken"] = token
            payload = sess.get("api/3/search/jql", params=params) or {}
            issues.extend(payload.get("issues") or [])
            if payload.get("isLast") or (limit and len(issues) >= limit):
                break
            token = payload.get("nextPageToken")
            if not token or token in seen_tokens:  # absent, or the loop-forever bug
                break
            seen_tokens.add(token)
        return issues

    # Acceptance criteria is a CUSTOM field whose id varies per site and which
    # /search/jql won't return unless asked for. Requesting "*all" is the only
    # reliable way to get it without first discovering the id — the story fetches
    # need it (every AI prompt reads it), so they pay for the wider payload; the
    # generic _search default stays lean for callers that don't.
    _STORY_FIELDS = ["*all"]
    #: Issue types that are never "stories" for test generation: bugs (the
    #: Sprint Report reads those separately), epics and test-management issue
    #: types (Xray's Test / Test Plan / …).
    _NOT_STORY_TYPES = {"bug", "epic", "sub-task", "subtask", "test", "test plan", "test set",
                        "test execution", "precondition", "pre-condition", "test case"}
    _DEFAULT_STORY_TYPES = ("Story", "User Story", "Task")

    def _story_issue_types(self, project_key):
        """The project's own story-level issue types (Story, Task, a custom
        "Feature" / "Requirement" / "قصة مستخدم" …): standard level, not a
        sub-task, not a bug / epic / test type. Falls back to the classic three
        when the project's types can't be read. Cached per project."""
        cache = self.__dict__.setdefault("_story_types_cache", {})
        if project_key in cache:
            return cache[project_key]
        names = []
        try:
            row = self._need_jira().get(f"api/3/project/{project_key}") or {}
            for it in row.get("issueTypes") or []:
                if not isinstance(it, dict) or it.get("subtask"):
                    continue
                level = it.get("hierarchyLevel")
                if level not in (None, 0):
                    continue
                name = (it.get("name") or "").strip()
                if name and name.lower() not in self._NOT_STORY_TYPES and name not in names:
                    names.append(name)
        except Exception:
            names = []
        out = tuple(names) or self._DEFAULT_STORY_TYPES
        cache[project_key] = out
        return out

    def _issuetype_clause(self, project_key):
        return "issuetype in (%s)" % ", ".join(
            '"%s"' % jql_escape(n) for n in self._story_issue_types(project_key))

    _SUMMARY_FIELDS = ["summary", "status", "assignee", "issuetype"]

    def fetch_story_summaries_in_sprint(self, project, sprint_path):
        """Like fetch_stories_in_sprint but only key / title / status / assignee
        (no "*all" payload with descriptions and every custom field) - what the
        Sprint Summary needs, several times faster on big sprints."""
        pkey = self._project_key(project)
        jql = (f'project = "{jql_escape(pkey)}" AND {self._sprint_clause(sprint_path)} '
               f'AND {self._issuetype_clause(pkey)} ORDER BY Rank ASC')
        try:
            issues = self._search(jql, fields=self._SUMMARY_FIELDS)
        except Exception:
            issues = self._search(jql.replace("ORDER BY Rank ASC", "ORDER BY key ASC"),
                                  fields=self._SUMMARY_FIELDS)
        return [self._to_story(i, sprint_path) for i in issues]

    def fetch_backlog_stories(self, project, limit=100):
        """Open stories of the project that aren't in a sprint view: the story
        picker's fallback when the board has no (active) sprint, or the sprint
        holds no stories. Board order (Rank) first, newest-updated as backup."""
        pkey = self._project_key(project)
        base = (f'project = "{jql_escape(pkey)}" AND statusCategory != Done '
                f'AND {self._issuetype_clause(pkey)}')
        try:
            issues = self._search(base + " ORDER BY Rank ASC", fields=self._STORY_FIELDS, limit=limit)
        except Exception:
            issues = []
        if not issues:
            issues = self._search(base + " ORDER BY updated DESC", fields=self._STORY_FIELDS, limit=limit)
        return [self._to_story(i, "") for i in issues[:max(1, int(limit))]]

    @staticmethod
    def _sprint_clause(sprint_path):
        """JQL for one sprint. A sprint is listed by its numeric ID, which must
        be UNQUOTED: `sprint = "526"` is read as a sprint NAMED "526" and finds
        nothing (confirmed on a live site: unquoted 200 issues, quoted 0)."""
        sp = str(sprint_path or "").strip()
        return f"sprint = {sp}" if sp.isdigit() else f'sprint = "{jql_escape(sp)}"'

    def fetch_stories_in_sprint(self, project, sprint_path):
        pkey = self._project_key(project)
        key = jql_escape(pkey)
        base = (f'project = "{key}" AND {self._sprint_clause(sprint_path)} '
                f'AND {self._issuetype_clause(pkey)}')
        # Order by the board's manual Rank so the multiselect / plan match the
        # sprint board's top-to-bottom order (the Azure board-order parity). Rank
        # is standard JQL on any sprint-using project, but guard defensively: if
        # the rank query raises OR comes back empty (a site without Rank), fall
        # back to the guaranteed-valid key order. A legitimately empty sprint just
        # runs the cheap second query and still returns [].
        try:
            issues = self._search(base + " ORDER BY Rank ASC", fields=self._STORY_FIELDS)
        except Exception:
            issues = []
        if not issues:
            issues = self._search(base + " ORDER BY key ASC", fields=self._STORY_FIELDS)
        return [self._to_story(i, sprint_path) for i in issues]

    def fetch_bugs_in_sprint(self, project, sprint_path):
        """Bugs in a sprint, in the Sprint Report's `{id,state,tags}` dict shape.
        Separate from fetch_stories_in_sprint (which excludes Bug issuetype) so
        the report can count bugs — and split regression vs sprint bugs by the
        "regression" label. `tags` is the space-joined Jira labels."""
        key = jql_escape(self._project_key(project))
        jql = (f'project = "{key}" AND {self._sprint_clause(sprint_path)} '
               f'AND issuetype = Bug ORDER BY key ASC')
        out = []
        for i in self._search(jql, fields=["status", "labels", "summary"]):
            f = i.get("fields") or {}
            key = i.get("key") or ""
            out.append({
                "id": key or str(i.get("id") or ""),
                "title": f.get("summary") or "",
                "state": ((f.get("status") or {}).get("name") or "") or "Unknown",
                "tags": " ".join(f.get("labels") or []),
                # The report lists each bug as a link to its Jira issue.
                "url": f"{self.site}/browse/{key}" if self.site and key else "",
            })
        return out

    #: Keys per `key IN (...)` query. Jira rejects the WHOLE query when any
    #: one key no longer exists (deleted / moved), so batches are kept small
    #: and a failing batch is split until the bad key is isolated and skipped.
    _KEY_BATCH = 50

    def fetch_stories(self, refs):
        keys = [(r.key or r.id) if isinstance(r, Ref) else str(r) for r in refs]
        keys = [k for k in keys if k]
        if not keys:
            return []
        issues = []
        for i in range(0, len(keys), self._KEY_BATCH):
            issues.extend(self._search_keys(keys[i:i + self._KEY_BATCH]))
        return [self._to_story(i) for i in issues]

    def _search_keys(self, keys):
        """`key IN (...)` for one batch; on an error, split it in halves so one
        missing key costs only itself (it's skipped and logged), not the batch."""
        joined = ", ".join(f'"{jql_escape(k)}"' for k in keys)
        try:
            return self._search(f"key IN ({joined})", fields=self._STORY_FIELDS)
        except Exception as exc:
            if len(keys) == 1:
                try:
                    import diag_log
                    diag_log.log("jira.fetch_stories skipped %s" % keys[0], exc)
                except Exception:
                    pass
                return []
            mid = len(keys) // 2
            return self._search_keys(keys[:mid]) + self._search_keys(keys[mid:])

    def _epic_link_field_id(self):
        """Company-managed projects that still use the classic "Epic Link"
        custom field (instead of `parent`): its id varies per site, so look it
        up by name once. "" = this site has none."""
        if getattr(self, "_epic_link_fid", None) is None:
            fid = ""
            try:
                for row in self._need_jira().get("api/3/field") or []:
                    if isinstance(row, dict) and (row.get("name") or "").strip().lower() == "epic link":
                        fid = row.get("id") or ""
                        break
            except Exception:
                fid = ""
            self._epic_link_fid = fid
        return self._epic_link_fid

    def _ac_field_id(self):
        """Acceptance-criteria is a custom field with no fixed id; match by name."""
        for row in self._need_jira().get("api/3/field") or []:
            if isinstance(row, dict) and "acceptance" in (row.get("name") or "").lower():
                return row.get("id")
        return None

    def _to_story(self, issue, sprint_path=""):
        if not isinstance(issue, dict):
            return Story(ref=Ref(id=str(issue)), title=str(issue))
        fields = issue.get("fields") or {}
        assignee = None
        raw = fields.get("assignee")
        if isinstance(raw, dict):
            assignee = User(id=raw.get("accountId") or "",
                            display_name=raw.get("displayName") or "",
                            email=raw.get("emailAddress") or "")
        # Both description and AC are ADF on Cloud — render, never str() them.
        description = adf.to_html(fields.get("description"))
        criteria = ""
        for fkey, fval in fields.items():
            if fkey.startswith("customfield_") and adf.is_adf(fval):
                criteria = adf.to_html(fval)
                break
        key = issue.get("key") or ""
        # Parent Epic → the "Feature" the Regression/Sprint Plan groups by. On
        # team-managed Jira the epic is the issue's `parent` (key + summary inline,
        # so we get id AND name in one shot, no extra call). Classic "Epic Link"
        # (a custom field) isn't resolved here — those stories fall back to
        # "No Feature", same as before. Fail-soft: any odd shape leaves it empty.
        epic_id, epic_name = "", ""
        _par = fields.get("parent")
        if isinstance(_par, dict):
            epic_id = _par.get("key") or ""
            epic_name = ((_par.get("fields") or {}).get("summary")) or ""
        if not epic_id:
            # Classic "Epic Link" (company-managed): the field holds the epic's
            # KEY only; the plan resolves its name (fetch_stories on that key).
            _fid = self._epic_link_field_id() if fields else ""
            _el = fields.get(_fid) if _fid else None
            if isinstance(_el, str) and _el.strip():
                epic_id = _el.strip()
            elif isinstance(_el, dict):
                epic_id = _el.get("key") or ""
        return Story(
            ref=Ref(id=str(issue.get("id") or key), key=key),
            title=fields.get("summary") or "",
            description=description,
            acceptance_criteria=criteria or description,
            assignee=assignee,
            state=((fields.get("status") or {}).get("name") or ""),
            url=f"{self.site}/browse/{key}" if self.site and key else "",
            sprint_path=sprint_path,
            epic_id=epic_id, epic_name=epic_name)

    # ── core: plans & suites (D1/D2) ──────────────────────────────────────
    _ZEPHYR_NO_PROJECT = re.compile(r"project\s+'?([^'\"]+?)'?\s+not\s+found", re.I)

    def _zephyr_project_error(self, exc, project_key=""):
        """Zephyr answers 400 "Project 'DA' Not Found" when Zephyr isn't turned
        on for that Jira project (it's enabled per project). Say that, with the
        fix, instead of the raw API text. Other errors pass through unchanged."""
        if not isinstance(exc, TrackerError):
            return exc
        m = self._ZEPHYR_NO_PROJECT.search(getattr(exc, "message", "") or str(exc))
        if not m:
            return exc
        key = (m.group(1) or project_key or "").strip()
        return NotFound(
            f"Zephyr isn't enabled for the Jira project {key}. Ask a project admin to turn it on: "
            f"Jira → project {key} → Project settings → Apps → Zephyr → enable Zephyr for this "
            f"project, then pick the project again.",
            backend="zephyr", status=getattr(exc, "status", None))

    def _folders(self, project_key, folder_type="TEST_CASE"):
        try:
            return list(self._need_zephyr().paginate(
                "folders", params={"projectKey": project_key, "folderType": folder_type},
                cursor="startAt", limit_param="maxResults",
                extract=lambda p: (p or {}).get("values") or []))
        except TrackerError as exc:
            raise self._zephyr_project_error(exc, project_key) from exc

    @staticmethod
    def _folder_names(name):
        """A story folder's name as written AND as Zephyr stores it:
        _create_folder replaces / and \\ (and trims to 255), so a story whose
        title has a slash was never found again — counted as 0 cases and given
        a duplicate folder on every run."""
        raw = str(name or "").strip()
        safe = (re.sub(r"[/\\]", "-", raw) or "Untitled")[:255]
        return {raw, safe}

    def _find_folder(self, project_key, name, parent_id=None):
        names = self._folder_names(name)
        for row in self._folders(project_key):
            if not isinstance(row, dict):
                continue
            if (row.get("name") or "") in names:
                pid = row.get("parentId")
                if parent_id is None or str(pid or "") == str(parent_id):
                    return row
        return None

    def find_suites_for_stories(self, project, plan, stories):
        """Batch form of find_suite_for_story: ONE folder listing for all the
        stories instead of a full listing per story (266 stories = 266 full
        downloads — what made Regression Plan's "reading test cases" crawl).
        Returns {story key: Suite}; stories without a folder are left out."""
        key = self._project_key(project)
        plan_id = str(getattr(plan.ref, "id", "") or "")
        by_name = {}
        for row in self._folders(key):
            if not isinstance(row, dict):
                continue
            if plan_id and str(row.get("parentId") or "") != plan_id:
                continue
            by_name.setdefault(row.get("name") or "", row)
        out = {}
        for story in stories or []:
            name = f"{story.ref.key} {story.title}".strip()
            row = next((by_name[n] for n in self._folder_names(name) if n in by_name), None)
            fid = str((row or {}).get("id") or "")
            if not fid:
                continue
            with self._lock:
                self._folder_cache[f"{key}:{plan.ref.id}:{story.ref.id}"] = fid
            out[story.ref.key or story.ref.id] = Suite(
                ref=Ref(id=fid), name=name, parent_ref=plan.ref, story_ref=story.ref)
        return out

    def _create_folder(self, project_key, name, parent_id=None,
                       folder_type="TEST_CASE"):
        # Zephyr rejects / and \ in folder names; story titles contain both.
        safe = re.sub(r"[/\\]", "-", str(name or "").strip()) or "Untitled"
        try:
            return self._need_zephyr().post("folders", json={
                "projectKey": project_key, "name": safe[:255],
                "parentId": parent_id, "folderType": folder_type})
        except TrackerError as exc:
            raise self._zephyr_project_error(exc, project_key) from exc

    def fetch_test_plans(self, project):
        key = self._project_key(project)
        out = []
        for row in self._folders(key):
            if isinstance(row, dict) and not row.get("parentId"):
                fid = str(row.get("id") or "")
                out.append(Plan(ref=Ref(id=fid, key=row.get("name") or fid),
                                name=row.get("name") or fid))
        return out

    def create_test_plan(self, project, name, sprint_path=""):
        key = self._project_key(project)
        existing = self._find_folder(key, name, parent_id=None)
        row = existing or self._create_folder(key, name) or {}
        fid = str(row.get("id") or "")
        return Plan(ref=Ref(id=fid, key=name), name=name, sprint_path=sprint_path)

    def find_suite_for_story(self, project, plan, story):
        """Non-creating lookup of the story's folder (see base.py) — the same
        name match as ensure_suite_for_story with the _create_folder call
        omitted, so read-only callers (existing-case counts, read-only suite
        discovery) can't create empty folders as a side effect."""
        key = self._project_key(project)
        name = f"{story.ref.key} {story.title}".strip()
        cache_key = f"{key}:{plan.ref.id}:{story.ref.id}"
        with self._lock:
            hit = self._folder_cache.get(cache_key)
        if hit:
            return Suite(ref=Ref(id=str(hit)), name=name,
                         parent_ref=plan.ref, story_ref=story.ref)
        try:
            row = self._find_folder(key, name, parent_id=plan.ref.id)
        except TrackerError:
            return None
        if not row:
            return None
        fid = str(row.get("id") or "")
        if not fid:
            return None
        with self._lock:
            self._folder_cache[cache_key] = fid
        return Suite(ref=Ref(id=fid), name=name,
                     parent_ref=plan.ref, story_ref=story.ref)

    def ensure_suite_for_story(self, project, plan, story):
        """Get-or-create the story's folder. MUST be idempotent — the contract
        suite asserts it, because a non-stable suite makes dedupe compare
        against an empty folder and recreate every case on every run."""
        key = self._project_key(project)
        name = f"{story.ref.key} {story.title}".strip()
        cache_key = f"{key}:{plan.ref.id}:{story.ref.id}"
        with self._lock:
            hit = self._folder_cache.get(cache_key)
        if hit:
            return Suite(ref=Ref(id=str(hit)), name=name,
                         parent_ref=plan.ref, story_ref=story.ref)

        row = self._find_folder(key, name, parent_id=plan.ref.id)
        if row is None:
            row = self._create_folder(key, name, parent_id=plan.ref.id) or {}
        fid = str(row.get("id") or "")
        with self._lock:
            self._folder_cache[cache_key] = fid
        return Suite(ref=Ref(id=fid), name=name,
                     parent_ref=plan.ref, story_ref=story.ref)

    # ── core: test cases ──────────────────────────────────────────────────
    def fetch_test_cases_for_suite(self, project, plan, suite):
        key = self._project_key(project)
        out = []
        try:
            rows = list(self._need_zephyr().paginate(
                "testcases", params={"projectKey": key, "folderId": suite.ref.id},
                cursor="startAt", limit_param="maxResults",
                extract=lambda p: (p or {}).get("values") or []))
        except TrackerError as exc:
            raise self._zephyr_project_error(exc, key) from exc
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(TestCase(
                ref=Ref(id=str(row.get("id") or ""), key=row.get("key") or ""),
                title=row.get("name") or "", suite_ref=suite.ref,
                story_ref=suite.story_ref,
                url=self._case_url(str(row.get("id") or ""), row.get("key") or "", key)))
        return out

    def create_test_case(self, project, plan, suite, title, story=None, labels=()):
        key = self._project_key(project)
        payload = {"projectKey": key, "name": (title or "").strip()[:255]}
        if labels:
            payload["labels"] = [str(x) for x in labels]
        if suite is not None and suite.ref.id:
            try:
                payload["folderId"] = int(suite.ref.id)
            except (TypeError, ValueError):
                pass
        if story is not None and story.acceptance_criteria:
            payload["objective"] = story.acceptance_criteria[:4000]
        try:
            row = self._need_zephyr().post("testcases", json=payload) or {}
        except TrackerError as exc:
            raise self._zephyr_project_error(exc, key) from exc
        case_key = row.get("key") or ""
        case = TestCase(ref=Ref(id=str(row.get("id") or case_key), key=case_key),
                        title=title, suite_ref=suite.ref if suite else None,
                        story_ref=(story.ref if story else None),
                        url=self._case_url(str(row.get("id") or ""), case_key, key))
        # D2: the issue link is what Jira-side coverage reporting reads. Failure
        # here must not lose the case we just created — the case is the valuable
        # artifact; the link is recoverable on a later run.
        if story is not None and case_key:
            # The case already exists, so a failed link must not fail the
            # case - but it must not be silent either: remember Zephyr's
            # reason for the run log (pop_link_error).
            try:
                self._need_zephyr().post(
                    f"testcases/{case_key}/links/issues",
                    json={"issueId": int(story.ref.id)})
            except (TrackerError, ValueError, TypeError) as exc:
                with self._lock:
                    self.__dict__.setdefault("_link_errors", {})[case_key] = (
                        f"{getattr(story.ref, 'key', '') or story.ref.id}: "
                        f"{getattr(exc, 'message', '') or exc}"[:300])
        return case

    # ── Zephyr test plans / test cycles (optional "add to a test cycle") ──
    def fetch_zephyr_test_plans(self, project):
        """Zephyr's own Test Plans (SS-P1 …) as [(key, name)]. These group
        test CYCLES; they are not the folders QA Studio writes cases into."""
        key = self._project_key(project)
        rows = list(self._need_zephyr().paginate(
            "testplans", params={"projectKey": key},
            cursor="startAt", limit_param="maxResults",
            extract=lambda p: (p or {}).get("values") or []))
        return [(str(r.get("key")), str(r.get("name") or r.get("key")))
                for r in rows if isinstance(r, dict) and r.get("key")]

    def create_cycle_for_cases(self, project, name, case_keys, plan_key=None, status="Not Executed"):
        """Create a Zephyr test cycle holding `case_keys` (one "Not Executed"
        execution each) and, when `plan_key` is given, link it to that Zephyr
        test plan. Returns {"cycle", "added", "failed": [(key, reason)],
        "plan_error"}; raises only when the cycle itself can't be created."""
        key = self._project_key(project)
        z = self._need_zephyr()
        try:
            row = z.post("testcycles", json={
                "projectKey": key, "name": (name or "QA Studio run").strip()[:255],
                "description": "Created by QA Studio for the test cases it generated."}) or {}
        except TrackerError as exc:
            raise self._zephyr_project_error(exc, key) from exc
        cycle = str(row.get("key") or row.get("id") or "")
        out = {"cycle": cycle, "added": 0, "failed": [], "plan_error": ""}
        for ck in dict.fromkeys(str(k) for k in case_keys if k):
            try:
                z.post("testexecutions", json={"projectKey": key, "testCaseKey": ck,
                                               "testCycleKey": cycle, "statusName": status})
                out["added"] += 1
            except TrackerError as exc:
                out["failed"].append((ck, (getattr(exc, "message", "") or str(exc))[:200]))
        if plan_key and cycle:
            try:
                z.post(f"testplans/{plan_key}/links/testcycles", json={"testCycleIdOrKey": cycle})
            except TrackerError as exc:
                out["plan_error"] = (getattr(exc, "message", "") or str(exc))[:200]
        return out

    def linked_test_case_count(self, story_key):
        """How many Zephyr test cases are linked to a Jira issue (Traceability)."""
        rows = self._need_zephyr().get(f"issuelinks/{story_key}/testcases") or []
        if isinstance(rows, dict):
            rows = rows.get("values") or []
        return len(rows)

    def linked_test_case_keys(self, story_key):
        """Keys of the Zephyr test cases linked to a Jira issue."""
        rows = self._need_zephyr().get(f"issuelinks/{story_key}/testcases") or []
        if isinstance(rows, dict):
            rows = rows.get("values") or []
        return [str(r.get("key")) for r in rows if isinstance(r, dict) and r.get("key")]

    def linked_test_case_titles(self, story_key, limit=80):
        """Names of the test cases linked to a story, wherever they sit - so a
        regeneration doesn't recreate tests the story already has."""
        from concurrent.futures import ThreadPoolExecutor
        keys = self.linked_test_case_keys(story_key)[:limit]
        z = self._need_zephyr()

        def _name(k):
            try:
                return ((z.get(f"testcases/{k}") or {}).get("name") or "").strip()
            except TrackerError:
                return ""
        if not keys:
            return []
        with ThreadPoolExecutor(max_workers=min(8, len(keys))) as ex:
            return [n for n in ex.map(_name, keys) if n]

    def pop_link_error(self, case_key):
        """Why linking `case_key` to its story failed ('' when it didn't)."""
        with self._lock:
            return (self.__dict__.get("_link_errors") or {}).pop(str(case_key), "")

    def fetch_case_labels(self, refs):
        out = {}
        for r in refs:
            k = r.key or r.id
            try:
                row = self._need_zephyr().get(f"testcases/{k}") or {}
                out[k] = [str(x) for x in (row.get("labels") or [])]
            except (TrackerError, ValueError, TypeError):
                out[k] = []
        return out

    def fetch_test_case_steps(self, ref):
        payload = self._need_zephyr().get(f"testcases/{ref.key or ref.id}/teststeps")
        rows = (payload or {}).get("values") or []
        out = []
        for i, row in enumerate(rows, start=1):
            inline = (row or {}).get("inline") or {}
            out.append(Step(action=inline.get("description") or "",
                            expected=inline.get("expectedResult") or "",
                            data=inline.get("testData") or "", index=i))
        return out

    def update_test_case_steps(self, ref, steps):
        items = [{"inline": {"description": s.action or "",
                             "expectedResult": s.expected or "",
                             "testData": s.data or ""}} for s in steps]
        # OVERWRITE, matching the Backend contract: the generation loop always
        # produces a complete list, and APPEND would duplicate on regeneration.
        self._need_zephyr().post(f"testcases/{ref.key or ref.id}/teststeps",
                                 json={"mode": "OVERWRITE", "items": items})

    def delete_test_case(self, project, plan, suite, ref):
        # Zephyr Scale Cloud's API can't delete test cases: say so plainly
        # (BackendUnsupported) instead of sending a DELETE that always fails.
        self.require(Capability.DELETE_TEST_CASE, "deleting test cases (Zephyr's API has no delete)")

    _ZEPHYR_CASE_KEY = re.compile(r"^([A-Z][A-Z0-9_]*)-T\d+$")

    def _case_url(self, case_id="", case_key="", project_key=""):
        """A Zephyr test case opens inside Jira's Zephyr app page, not at
        /browse/ (test case keys like PROJ-T12 aren't Jira issues)."""
        if not self.site:
            return ""
        m = self._ZEPHYR_CASE_KEY.match(case_key or "")
        proj = (m.group(1) if m else "") or (project_key or "")
        if not proj:
            return ""
        page = (f"{self.site}/projects/{proj}?selectedItem=com.atlassian.plugins."
                f"atlassian-connect-plugin:com.kanoah.test-manager__main-project-page")
        if str(case_id).isdigit() and case_id != case_key:
            return f"{page}#!/v2/testCase/{case_id}"
        if m:
            return f"{page}#!/testCase/{case_key}"
        return f"{page}#!/v2/testCases"

    def case_url(self, ref, project=None):
        """Link for a Zephyr test case (backend_setup.case_url prefers this).
        Subclasses whose test cases ARE Jira issues (Xray) keep item_url."""
        if self.name != "jira_zephyr":
            return self.item_url(ref, project)
        key = ref.key if self._ZEPHYR_CASE_KEY.match(ref.key or "") else ""
        cid = ref.id if (ref.id or "").isdigit() else ""
        if not key and not cid:
            return self.item_url(ref, project)
        return self._case_url(cid, key, self._project_key(project) if project else "")

    def item_url(self, ref, project=None):
        if not self.site:
            return ""
        if self.name == "jira_zephyr" and self._ZEPHYR_CASE_KEY.match(ref.key or ref.id or ""):
            return self._case_url(ref.id or "", ref.key or ref.id or "")
        return f"{self.site}/browse/{ref.key or ref.id}"

    # ── extended ──────────────────────────────────────────────────────────
    def fetch_project_members(self, project):
        rows = self._need_jira().get(
            "api/3/user/assignable/search",
            params={"project": self._project_key(project), "maxResults": 200}) or []
        return [User(id=r.get("accountId") or "",
                     display_name=r.get("displayName") or "",
                     email=r.get("emailAddress") or "")
                for r in rows if isinstance(r, dict)]

    def tester_field_options(self, project):
        return [u.display_name for u in self.fetch_project_members(project)]

    def assign_testers(self, project, assignments):
        sess = self._need_jira()
        for issue_key, account_id in (assignments or {}).items():
            sess.put(f"api/3/issue/{issue_key}/assignee",
                     json={"accountId": account_id or None})

    def _subtask_type_id(self, project_key):
        """Resolve THIS project's sub-task issue-type id by its `subtask: true`
        flag rather than a hardcoded name. Jira's sub-task type is named
        differently per project/site — "Sub-task", "Subtask", "Sub-Task", or a
        localized string — so `{"issuetype": {"name": "Sub-task"}}` fails with
        the opaque 400 "Specify a valid issue type" whenever the project's type
        is spelled any other way (e.g. Scrum's default is "Subtask", no hyphen).
        Matching on the boolean flag is naming- and locale-proof. Cached per
        project. Returns "" if the project has no sub-task type at all."""
        with self._lock:
            cache = getattr(self, "_subtask_type_cache", None)
            if cache and project_key in cache:
                return cache[project_key]
        try:
            payload = self._need_jira().get(
                f"api/3/issue/createmeta/{project_key}/issuetypes")
            types = ((payload or {}).get("issueTypes")
                     or (payload or {}).get("values") or [])
        except TrackerError:
            types = []
        match = next((t for t in types if t.get("subtask")), None)
        tid = str(match.get("id") or "") if match else ""
        with self._lock:
            if getattr(self, "_subtask_type_cache", None) is None:
                self._subtask_type_cache = {}
            self._subtask_type_cache[project_key] = tid
        return tid

    def create_child_tasks(self, project, items):
        sess = self._need_jira()
        key = self._project_key(project)
        type_id = self._subtask_type_id(key)
        if not type_id:
            raise TrackerError(
                f"Project '{key}' has no sub-task issue type.",
                remedy=("Enable sub-tasks for this project in Jira → Project "
                        "settings → Issue types, then retry."),
                backend=self.name)
        made = []
        for item in items or []:
            payload = {"fields": {
                "project": {"key": key},
                "summary": (item.get("title") or "Task")[:255],
                "issuetype": {"id": type_id},   # resolved by subtask flag, not name
                # The Task Manager passes the parent story as "story_id" (the
                # issue KEY on Jira, e.g. "SCRUM-1"); accept "parent" too for any
                # other caller. A sub-task MUST have a parent or Jira 400s.
                "parent": {"key": item.get("parent") or item.get("story_id")},
            }}
            row = sess.post("api/3/issue", json=payload) or {}
            made.append(Ref(id=str(row.get("id") or ""), key=row.get("key") or ""))
        return made

    # ── sub-task editing (EDIT_SUBTASKS) ────────────────────────────────────
    @staticmethod
    def _issue_key(ref):
        """Accept a Ref, a {id,key} dict, or a bare id/key string — the Task
        Manager holds stories as plain dicts, so this stays permissive."""
        if isinstance(ref, Ref):
            return ref.key or ref.id or ""
        if isinstance(ref, dict):
            return ref.get("key") or ref.get("id") or ""
        return str(ref or "")

    def fetch_subtasks(self, project, story):
        """A story's existing child sub-tasks. `story` may be a Story/Ref/dict/
        key — its issue key/id is used as the JQL `parent`."""
        parent = self._issue_key(getattr(story, "ref", story))
        if not parent:
            return []
        jql = f'parent = "{jql_escape(parent)}" ORDER BY key ASC'
        out = []
        for i in self._search(jql, fields=["summary", "assignee", "status", "labels"]):
            f = i.get("fields") or {}
            a = f.get("assignee") if isinstance(f.get("assignee"), dict) else {}
            key = i.get("key") or ""
            out.append({
                "id": str(i.get("id") or ""),
                "key": key,
                "summary": f.get("summary") or "",
                "assignee": (a or {}).get("displayName") or "",
                "status": ((f.get("status") or {}).get("name") or ""),
                "labels": ", ".join(f.get("labels") or []),
                "url": f"{self.site}/browse/{key}" if self.site and key else "",
            })
        return out

    def subtask_field_schema(self, ref):
        """Editable fields for one sub-task, from Jira's per-issue `editmeta`
        (so newly-added / custom fields appear automatically), plus `status`
        surfaced as a pseudo-field backed by the workflow transitions."""
        key = self._issue_key(ref)
        if not key:
            return []
        sess = self._need_jira()
        meta = sess.get(f"api/3/issue/{key}/editmeta") or {}
        fields = (meta or {}).get("fields") or {}
        # editmeta gives TYPES but not the sub-task's CURRENT values — fetch the
        # issue once so each field can be pre-filled in the editor. `value` is a
        # display string; `value_id` is the id to pre-select a dropdown/user.
        try:
            cur = ((sess.get(f"api/3/issue/{key}", params={"fields": "*all"})
                    or {}).get("fields")) or {}
        except TrackerError:
            cur = {}
        out = []
        # status first — it's a transition, not a normal field write.
        try:
            tr = sess.get(f"api/3/issue/{key}/transitions") or {}
            tlist = tr.get("transitions") or []
        except TrackerError:
            tlist = []
        if tlist:
            out.append({
                "id": "status", "name": "Status", "type": "status",
                "items": None, "required": False,
                "value": (cur.get("status") or {}).get("name") or "",
                "value_id": "",   # status is set by transition id, not a stored id
                "allowed": [{"id": str(t.get("id") or ""),
                             "label": ((t.get("to") or {}).get("name")
                                       or t.get("name") or "")}
                            for t in tlist],
            })
        for fid, spec in fields.items():
            if not isinstance(spec, dict):
                continue
            schema = spec.get("schema") or {}
            allowed = []
            for a in (spec.get("allowedValues") or []):
                if isinstance(a, dict):
                    allowed.append({
                        "id": str(a.get("id") or a.get("accountId")
                                  or a.get("key") or ""),
                        "label": (a.get("name") or a.get("value")
                                  or a.get("displayName") or ""),
                    })
            disp, vid = self._display_value(cur.get(fid))
            out.append({
                "id": fid,
                "name": spec.get("name") or fid,
                "type": schema.get("type") or "string",
                "items": schema.get("items"),
                "required": bool(spec.get("required")),
                "value": disp,
                "value_id": vid,
                "allowed": allowed,
                "adf": self._is_adf(schema),
            })
        # Work type is editable, but Jira only accepts switching a sub-task to
        # ANOTHER sub-task type — restrict the issuetype field's options to the
        # project's sub-task types (derived from the sub-task's own project).
        it = next((s for s in out if s.get("id") == "issuetype"), None)
        if it is not None:
            proj_key = (((cur.get("project") or {}).get("key"))
                        or (key.split("-")[0] if "-" in key else ""))
            if proj_key:
                try:
                    subs = self.subtask_work_types(proj_key)
                    if subs:
                        it["allowed"] = subs
                except Exception:
                    pass
        return out

    @staticmethod
    def _display_value(raw):
        """(display_string, id) for a current Jira field value of any shape —
        user object, option, list, or scalar — so the editor can both SHOW the
        current value and PRE-SELECT the right dropdown/user id."""
        if raw is None:
            return "", ""
        if isinstance(raw, dict):
            label = (raw.get("displayName") or raw.get("name")
                     or raw.get("value") or "")
            vid = str(raw.get("accountId") or raw.get("id") or raw.get("key") or "")
            return label, vid
        if isinstance(raw, list):
            labels, ids = [], []
            for a in raw:
                if isinstance(a, dict):
                    labels.append(a.get("displayName") or a.get("name")
                                  or a.get("value") or "")
                    ids.append(str(a.get("accountId") or a.get("id")
                                   or a.get("key") or ""))
                else:
                    labels.append(str(a))
                    ids.append(str(a))
            return ", ".join(x for x in labels if x), ",".join(x for x in ids if x)
        return str(raw), str(raw)

    @staticmethod
    def _shape_subtask_field(spec, value):
        """Turn a picker value (an id, text, or list) into the JSON shape Jira's
        edit endpoint expects for that field's type."""
        if value in (None, ""):
            return None
        if (spec or {}).get("adf"):
            # Rich-text field — wrap plain text into an ADF doc (one paragraph
            # per line) so Jira Cloud accepts it instead of 400-ing. Empty lines
            # become bare paragraphs (an empty content[] can fail ADF validation).
            paras = []
            for ln in str(value).split("\n"):
                if ln:
                    paras.append({"type": "paragraph",
                                  "content": [{"type": "text", "text": ln}]})
                else:
                    paras.append({"type": "paragraph"})
            return {"type": "doc", "version": 1, "content": paras or [{"type": "paragraph"}]}
        t = (spec or {}).get("type") or "string"
        items = (spec or {}).get("items")
        if t == "user":
            return {"accountId": str(value)}
        if t in ("option", "priority", "resolution", "version",
                 "component", "securitylevel", "project", "issuetype"):
            return {"id": str(value)}
        if t == "number":
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        if t == "array":
            vals = (value if isinstance(value, list)
                    else [v.strip() for v in str(value).split(",") if v.strip()])
            if items == "string":            # labels, etc.
                return vals
            if items == "user":
                return [{"accountId": str(v)} for v in vals]
            return [{"id": str(v)} for v in vals]   # arrays of option/version
        if t == "datetime":
            # Jira wants ISO 8601 with offset. Accept "YYYY-MM-DD" (→ midnight)
            # or "YYYY-MM-DD HH:MM"/"...THH:MM" from the picker + time field.
            v = str(value).strip().replace(" ", "T")
            if len(v) == 10:                  # date only
                v += "T00:00:00.000+0000"
            elif len(v) == 16:                # + HH:MM
                v += ":00.000+0000"
            return v
        return value                          # string / date / text

    def update_subtask(self, ref, fields):
        """Write chosen field values back. `status` (a transition id) is applied
        via the transitions endpoint; everything else is one PUT, each value
        shaped to its schema type."""
        key = self._issue_key(ref)
        if not key:
            return
        sess = self._need_jira()
        raw = dict(fields or {})
        status_tid = raw.pop("status", None)
        if raw:
            by_id = {s.get("id"): s for s in self.subtask_field_schema(ref)}
            body = {}
            for fid, val in raw.items():
                shaped = self._shape_subtask_field(by_id.get(fid), val)
                if shaped is not None:
                    body[fid] = shaped
            if body:
                sess.put(f"api/3/issue/{key}", json={"fields": body})
        if status_tid:
            sess.post(f"api/3/issue/{key}/transitions",
                      json={"transition": {"id": str(status_tid)}})

    # ── sub-task CREATE support (work type + createmeta fields) ─────────────
    def subtask_work_types(self, project):
        """The project's SUB-TASK work types as [{id,name}] (createmeta,
        subtask=true). Usually one ('Subtask'); a project may define more."""
        key = self._project_key(project)
        try:
            payload = self._need_jira().get(
                f"api/3/issue/createmeta/{key}/issuetypes")
            types = ((payload or {}).get("issueTypes")
                     or (payload or {}).get("values") or [])
        except TrackerError:
            types = []
        return [{"id": str(t.get("id") or ""), "name": t.get("name") or ""}
                for t in types if t.get("subtask")]

    def subtask_create_field_schema(self, project, type_id):
        """Fields available when CREATING a sub-task of `type_id`, from Jira
        createmeta — same normalized shape as subtask_field_schema but with no
        current values (nothing exists yet)."""
        key = self._project_key(project)
        try:
            payload = self._need_jira().get(
                f"api/3/issue/createmeta/{key}/issuetypes/{type_id}")
            values = ((payload or {}).get("values")
                      or (payload or {}).get("fields") or [])
        except TrackerError:
            values = []
        # granular createmeta returns a LIST of field defs (each carrying its own
        # fieldId); some proxies hand back a legacy dict keyed by fieldId.
        pairs = (list(values.items()) if isinstance(values, dict)
                 else [((v.get("fieldId") or v.get("key") or ""), v) for v in values])
        out = []
        for fid, spec in pairs:
            if not isinstance(spec, dict) or not fid:
                continue
            schema = spec.get("schema") or {}
            allowed = []
            for a in (spec.get("allowedValues") or []):
                if isinstance(a, dict):
                    allowed.append({"id": str(a.get("id") or a.get("accountId")
                                              or a.get("key") or ""),
                                    "label": (a.get("name") or a.get("value")
                                              or a.get("displayName") or "")})
            out.append({"id": fid, "name": spec.get("name") or fid,
                        "type": schema.get("type") or "string",
                        "items": schema.get("items"),
                        "required": bool(spec.get("required")),
                        "value": "", "value_id": "", "allowed": allowed,
                        "adf": self._is_adf(schema)})
        return out

    @staticmethod
    def _is_adf(schema):
        """True for Jira Cloud rich-text fields that must be sent as Atlassian
        Document Format, not a plain string — description/environment (system)
        and 'Paragraph' custom fields (textarea). Sending a bare string to these
        returns 400 'not valid ADF content'."""
        if not isinstance(schema, dict):
            return False
        return (schema.get("system") in ("description", "environment")
                or "textarea" in str(schema.get("custom") or ""))

    def create_subtask(self, project, parent, type_id, summary, fields=None):
        """Create ONE sub-task under `parent` (story key) as work type `type_id`,
        with `fields` ({field_id: picker value}) shaped to Jira's create payload.
        Falls back to the project's default sub-task type if `type_id` is blank."""
        sess = self._need_jira()
        key = self._project_key(project)
        if not type_id:
            type_id = self._subtask_type_id(key)
        if not type_id:
            raise TrackerError(
                f"Project '{key}' has no sub-task work type.",
                remedy=("Enable sub-tasks for this project in Jira → Project "
                        "settings → Issue types, then retry."),
                backend=self.name)
        body = {"project": {"key": key},
                "summary": (summary or "Sub-task")[:255],
                "issuetype": {"id": str(type_id)},
                "parent": {"key": self._issue_key(parent)}}
        by_id = {s["id"]: s
                 for s in self.subtask_create_field_schema(project, type_id)}
        for fid, val in (fields or {}).items():
            if fid in ("summary", "issuetype", "parent", "project") or val in (None, ""):
                continue
            shaped = self._shape_subtask_field(by_id.get(fid), val)
            if shaped is not None:
                body[fid] = shaped
        row = sess.post("api/3/issue", json={"fields": body}) or {}
        return Ref(id=str(row.get("id") or ""), key=row.get("key") or "")

    def fetch_task_stats(self, project, sprint_path="", assignee=""):
        key = jql_escape(self._project_key(project))
        clauses = [f'project = "{key}"']
        if sprint_path:
            clauses.append(self._sprint_clause(sprint_path))
        if assignee:
            clauses.append(f'assignee = "{jql_escape(assignee)}"')
        issues = self._search(" AND ".join(clauses),
                              fields=["assignee", "status", "timeestimate"])
        buckets: Dict[str, Dict[str, Any]] = {}
        for issue in issues:
            fields = issue.get("fields") or {}
            who = fields.get("assignee") or {}
            uid = who.get("accountId") or "unassigned"
            slot = buckets.setdefault(uid, {
                "user": User(id=uid, display_name=who.get("displayName") or "Unassigned"),
                "total": 0, "done": 0, "wip": 0, "remaining": 0.0})
            slot["total"] += 1
            category = (((fields.get("status") or {}).get("statusCategory") or {})
                        .get("key") or "").lower()
            if category == "done":
                slot["done"] += 1
            elif category == "indeterminate":
                slot["wip"] += 1
            slot["remaining"] += float(fields.get("timeestimate") or 0) / 3600.0
        return [TaskStats(user=v["user"], total=v["total"], completed=v["done"],
                          in_progress=v["wip"], remaining_hours=round(v["remaining"], 2))
                for v in buckets.values()]

    def sprint_report_data(self, project, sprint_path):
        stories = self.fetch_stories_in_sprint(project, sprint_path)
        counts = {"passed": 0, "failed": 0, "blocked": 0, "not_run": 0}
        total = 0
        for row in self._need_zephyr().paginate(
                "testexecutions",
                params={"projectKey": self._project_key(project)},
                cursor="startAt", limit_param="maxResults",
                extract=lambda p: (p or {}).get("values") or []):
            if not isinstance(row, dict):
                continue
            total += 1
            status = ((row.get("testExecutionStatus") or {}).get("name")
                      or row.get("statusName") or "").strip().lower()
            if status.startswith("pass"):
                counts["passed"] += 1
            elif status.startswith("fail"):
                counts["failed"] += 1
            elif status.startswith("block"):
                counts["blocked"] += 1
            else:
                counts["not_run"] += 1
        return ReportData(project=self._project_key(project), sprint=sprint_path,
                          total_cases=total, stories=stories, **counts)


__all__ = ["JiraZephyrBackend", "validate_site_url", "jql_escape", "ZEPHYR_BASE"]
