"""backend_setup.py — the Setup-screen half of the tracker switch point.

Follows the same app-shell + delegate-module pattern already used for
idle_watch.py / dialogs.py / window_chrome.py / login.py: all the real logic
lives here, and `main.py` keeps a few thin call sites. That matters more than
usual here — `main.py` is ~8.2k lines and is documented in DEV_ROADMAP as one
of the app's two most fragile files, so the goal is the smallest reviewable
diff there that still delivers a working switch.

WHAT THIS OWNS
  * the backend picker (Azure DevOps ⇄ Jira + Zephyr Scale)
  * the per-backend credential rows
  * per-backend validation on Save/Connect
  * pushing the active credentials into engine/tracker
  * credential defaults + migration

BACKWARD COMPATIBILITY IS THE POINT
A creds file written before any of this existed has no "backend" key. It
resolves to "azure", renders exactly the Azure rows it always did, and takes
exactly the old code path. An existing user should not be able to tell this
module was added.
"""
from __future__ import annotations
import strings  # i18n

# NOTE: flet / theme / ui are imported LAZILY inside the UI functions, never at
# module scope. store.load() calls defaults() on every credential read, so a
# module-level UI import would put flet on the credential path — and on any
# machine or context where flet isn't importable (headless run_worker.py, a
# test harness, CI) that would degrade credential loading for a module whose
# logic half needs no UI at all. Keeping the split means active()/defaults()/
# validate_for_connect() are pure and independently testable.

AZURE = "azure"
JIRA_ZEPHYR = "jira_zephyr"
XRAY = "xray"
TESTRAIL = "testrail"
AZURE_TESTRAIL = "azure_testrail"      # hybrid: read Azure, write TestRail
JIRA_TESTRAIL = "jira_testrail"        # hybrid: read Jira, write TestRail

_NAMES = (AZURE, JIRA_ZEPHYR, XRAY, TESTRAIL, AZURE_TESTRAIL, JIRA_TESTRAIL)
#: Jira-native backends share the Jira read path (site/email/token).
_JIRA_NATIVE = (JIRA_ZEPHYR, XRAY)
#: Hybrids read from a source (Azure/Jira) and write to TestRail.
_HYBRIDS = (AZURE_TESTRAIL, JIRA_TESTRAIL)

#: (name, label, blurb) for the picker.
BACKENDS = [
    (AZURE, "Azure DevOps", "Work items, test plans & suites on dev.azure.com"),
    (JIRA_ZEPHYR, "Jira + Zephyr Scale", "Jira issues & sprints, Zephyr test library"),
    (XRAY, "Jira + Xray", "Jira issues & sprints, Xray tests (REST + GraphQL)"),
    (TESTRAIL, "TestRail", "Standalone test-case library (your-org.testrail.io)"),
    (AZURE_TESTRAIL, "Azure → TestRail", "Read stories from Azure, write cases to TestRail"),
    (JIRA_TESTRAIL, "Jira → TestRail", "Read stories from Jira, write cases to TestRail"),
]


def is_hybrid(creds):
    return active(creds) in _HYBRIDS


def hybrid_source(creds):
    """The read-source backend name for a hybrid ('azure' or 'jira_zephyr')."""
    return AZURE if active(creds) == AZURE_TESTRAIL else JIRA_ZEPHYR

#: Per-backend creds keys, seeded by defaults(). The Jira site/email/token are
#: SHARED by every Jira-native backend (Zephyr, Xray) — same read path.
_JIRA_CORE_KEYS = ("jira_site", "jira_email", "jira_token")
_ZEPHYR_KEYS = ("zephyr_token",)
_XRAY_KEYS = ("xray_client_id", "xray_client_secret", "xray_base")
_TESTRAIL_KEYS = ("testrail_url", "testrail_email", "testrail_key")


def defaults(d):
    """Seed missing keys on a creds dict. Called from store.load().

    `backend` deliberately defaults to AZURE rather than being left unset, so
    every downstream reader gets a definite answer without each having to
    re-implement the fallback.
    """
    if not isinstance(d, dict):
        return d
    d.setdefault("backend", AZURE)
    for k in _JIRA_CORE_KEYS + _ZEPHYR_KEYS + _XRAY_KEYS + _TESTRAIL_KEYS:
        d.setdefault(k, "")
    d.setdefault("jira_allow_any_host", False)
    d.setdefault("testrail_allow_any_host", False)
    return d


def active(creds):
    """Resolve the active backend name from creds (never raises)."""
    try:
        name = (creds or {}).get("backend") or AZURE
    except Exception:
        name = AZURE
    name = str(name).strip().lower()
    return name if name in _NAMES else AZURE


def is_azure(creds):
    return active(creds) == AZURE


def reads_stories_from_azure(creds):
    """True when STORIES are read from Azure: the Azure backend itself, or the
    Azure→TestRail hybrid (whose WRITE target is TestRail but whose story source
    is still Azure). Several call sites need exactly this distinction — the
    write target says nothing about where requirements live — and were each
    spelling it out inline (or, worse, only checking `is_azure` and so breaking
    on the hybrid)."""
    return is_azure(creds) or active(creds) == AZURE_TESTRAIL


def label_for(name):
    for key, label, _ in BACKENDS:
        if key == name:
            return label
    return name


# ══════════════════════════════════════════════════════════════════════════
#  Validation
# ══════════════════════════════════════════════════════════════════════════

def validate_for_connect(app):
    """Check the ACTIVE backend's required credentials before connecting.

    Returns (ok, error_message). Mirrors main._connect's existing convention of
    reporting the first missing required field rather than raising.
    """
    creds = getattr(app, "creds", {}) or {}
    if is_azure(creds):
        return True, ""          # main.py keeps its own Azure org/PAT checks

    name = active(creds)

    if name in _HYBRIDS:
        # Hybrid: validate the SOURCE creds + the TestRail target creds.
        if name == AZURE_TESTRAIL:
            org = (_field_or_saved(app, "hy_org_field", creds.get("org", "")) or "").strip()
            pat = (_field_or_saved(app, "hy_pat_field", creds.get("pat", "")) or "").strip()
            if not org:
                return False, strings.t("bset_field_required_src", field=strings.t("bset_azure_org"))
            if not pat:
                return False, strings.t("bset_field_required_src", field=strings.t("bset_azure_pat"))
            creds["org"] = org
            creds["pat"] = pat
        else:   # JIRA_TESTRAIL
            site = (_field_or_saved(app, "jira_site_field", creds.get("jira_site", "")) or "").strip()
            email = (_field_or_saved(app, "jira_email_field", creds.get("jira_email", "")) or "").strip()
            token = (_field_or_saved(app, "jira_token_field", creds.get("jira_token", "")) or "").strip()
            if not site:
                return False, strings.t("bset_field_required_src", field=strings.t("bset_jira_site"))
            if not email:
                return False, strings.t("bset_field_required_src", field=strings.t("bset_jira_email"))
            if not token:
                return False, strings.t("bset_field_required_src", field=strings.t("bset_jira_token"))
            try:
                from tracker.jira_zephyr import validate_site_url
                creds["jira_site"] = validate_site_url(site, bool(creds.get("jira_allow_any_host")))
            except Exception as exc:
                return False, str(exc)
            creds["jira_email"] = email
            creds["jira_token"] = token
        # TestRail target creds
        url = (_field_or_saved(app, "testrail_url_field", creds.get("testrail_url", "")) or "").strip()
        temail = (_field_or_saved(app, "testrail_email_field", creds.get("testrail_email", "")) or "").strip()
        key = (_field_or_saved(app, "testrail_key_field", creds.get("testrail_key", "")) or "").strip()
        if not url:
            return False, strings.t("bset_field_required_tgt", field=strings.t("bset_tr_url"))
        if not temail:
            return False, strings.t("bset_field_required_tgt", field=strings.t("bset_tr_email"))
        if not key:
            return False, strings.t("bset_field_required_tgt", field=strings.t("bset_tr_key"))
        try:
            from tracker.testrail import validate_testrail_url
            creds["testrail_url"] = validate_testrail_url(url, bool(creds.get("testrail_allow_any_host")))
        except Exception as exc:
            return False, str(exc)
        creds["testrail_email"] = temail
        creds["testrail_key"] = key
        return True, ""

    if name == TESTRAIL:
        # Standalone — no Jira fields at all.
        url = (_field_or_saved(app, "testrail_url_field", creds.get("testrail_url", "")) or "").strip()
        email = (_field_or_saved(app, "testrail_email_field", creds.get("testrail_email", "")) or "").strip()
        key = (_field_or_saved(app, "testrail_key_field", creds.get("testrail_key", "")) or "").strip()
        if not url:
            return False, strings.t("bset_field_required", field=strings.t("bset_tr_url"))
        if not email:
            return False, strings.t("bset_field_required", field=strings.t("bset_tr_email"))
        if not key:
            return False, strings.t("bset_field_required", field=strings.t("bset_tr_key"))
        try:
            from tracker.testrail import validate_testrail_url
            normalized = validate_testrail_url(url, bool(creds.get("testrail_allow_any_host")))
        except Exception as exc:
            return False, str(exc)
        creds["testrail_url"] = normalized
        creds["testrail_email"] = email
        creds["testrail_key"] = key
        return True, ""

    # Every Jira-native backend shares site/email/token (the read path).
    site = (_field_or_saved(app, "jira_site_field", creds.get("jira_site", "")) or "").strip()
    email = (_field_or_saved(app, "jira_email_field", creds.get("jira_email", "")) or "").strip()
    token = (_field_or_saved(app, "jira_token_field", creds.get("jira_token", "")) or "").strip()
    if not site:
        return False, strings.t("bset_field_required", field=strings.t("bset_jira_site"))
    if not email:
        return False, strings.t("bset_field_required", field=strings.t("bset_jira_email"))
    if not token:
        return False, strings.t("bset_field_required", field=strings.t("bset_jira_token"))

    # Validate the site URL BEFORE any request is made with it. This is the
    # SSRF/token-exfiltration guard: the field is free text, so a wrong or
    # socially-engineered value would otherwise send the user's bearer token
    # to an attacker-controlled host and probe their internal network.
    try:
        from tracker.jira_zephyr import validate_site_url
        normalized = validate_site_url(site, bool(creds.get("jira_allow_any_host")))
    except Exception as exc:
        return False, str(exc)

    creds["jira_site"] = normalized
    creds["jira_email"] = email
    creds["jira_token"] = token

    name = active(creds)
    if name == XRAY:
        cid = (_field_or_saved(app, "xray_client_id_field", creds.get("xray_client_id", "")) or "").strip()
        secret = (_field_or_saved(app, "xray_client_secret_field", creds.get("xray_client_secret", "")) or "").strip()
        if not cid:
            return False, strings.t("bset_field_required", field=strings.t("bset_xray_cid_name"))
        if not secret:
            return False, strings.t("bset_field_required", field=strings.t("bset_xray_secret_name"))
        creds["xray_client_id"] = cid
        creds["xray_client_secret"] = secret
    else:   # jira_zephyr
        zephyr = (_field_or_saved(app, "zephyr_token_field", creds.get("zephyr_token", "")) or "").strip()
        if not zephyr:
            return False, strings.t("bset_field_required", field=strings.t("bset_zephyr_token"))
        creds["zephyr_token"] = zephyr
    return True, ""


def _field_or_saved(app, attr, saved):
    """Read a live TextField if it's on screen, else fall back to saved creds."""
    try:
        fn = getattr(app, "_field_or_saved", None)
        if callable(fn):
            return fn(attr, saved)
    except Exception:
        pass
    try:
        field = getattr(app, attr, None)
        val = (getattr(field, "value", None) or "").strip() if field is not None else ""
        return val or saved
    except Exception:
        return saved


def get_backend(app):
    """Build the tracker Backend for the active account.

    Constructed per call from THIS account's creds — never cached — which is
    what keeps an account switch from leaving the previous user's site/token
    live (the cross-account leak class documented in DEV_ROADMAP).
    """
    import tracker
    creds = getattr(app, "creds", {}) or {}
    return tracker.get_backend(creds, name=active(creds), config=creds)


def connect_and_list_projects(app):
    """Validate the connection and return the selectable project list.

    Returns a list of STRINGS, matching what `engine.fetch_projects` already
    returns and what `app.project` is downstream. Azure supplies project NAMES;
    Jira supplies project KEYS — both are the identifier their own API expects,
    and `JiraZephyrBackend._project_key` accepts the key directly, so the rest
    of the app keeps treating `app.project` as an opaque string exactly as it
    always has. Changing that shape would ripple into regression.py/report.py
    and is explicitly not part of this step.

    Raises on failure; the caller renders the message.
    """
    # An Azure-sourced hybrid reads via engine's Azure globals, so push the
    # saved org/PAT into engine first (same as the standalone Azure connect).
    if active(getattr(app, "creds", {}) or {}) == AZURE_TESTRAIL:
        try:
            import engine as E
            creds = app.creds
            E.set_credentials(org=creds.get("org") or None, pat=creds.get("pat"))
        except Exception:
            pass
    backend = get_backend(app)
    backend.validate_credentials()
    projects = backend.fetch_projects()
    return sorted({(p.ref.key or p.name) for p in projects if (p.ref.key or p.name)})


def fetch_plans(app, project):
    """Test plans for `project`, in main.py's existing `[{"id","name"}]` shape.

    Azure keeps calling engine directly so its behaviour is bit-for-bit what it
    was. Only the non-Azure branch goes through the tracker adapter, where a
    `Plan` DTO is flattened into the same dict shape the Setup dropdown and
    regression.py already consume.
    """
    creds = getattr(app, "creds", {}) or {}
    if is_azure(creds):
        import engine as E
        return E.fetch_test_plans(project)
    backend = get_backend(app)
    return [{"id": p.ref.id, "name": p.name}
            for p in backend.fetch_test_plans(_as_project(backend, project))]


def fetch_stories_for_plan(app, plan_id):
    """Stories for a test plan, in main.py's legacy `[{"id","title"}]` shape.

    This consolidates a two-step lookup that was duplicated inline at two
    call sites in main.py — including a raw `E._azure_get(...)` building a
    dev.azure.com URL directly in the UI layer, which is exactly the kind of
    leak the tracker seam exists to remove.

    Azure keeps both steps verbatim (plan's requirement suites, falling back to
    the plan's iteration when it has none — the case that used to leave the
    story picker empty/disabled).

    For non-Azure backends a plan/folder is a test-case container and does not
    track which stories belong to it, so there is no honest plan→stories map.
    Stories live in a SPRINT on those backends, so we populate from the
    project's active sprint(s) via the backend's fetch_stories_in_sprint — the
    same source the Regression screen uses — rather than inventing a mapping.
    """
    creds = getattr(app, "creds", {}) or {}
    project = getattr(app, "project", "") or ""
    # Azure — either the single-backend, OR the Azure→TestRail hybrid, whose
    # STORIES come from Azure (only the WRITE target is TestRail). Both read the
    # plan's requirement-suite stories the same way.
    if reads_stories_from_azure(creds):
        import engine as E
        stories = E.fetch_stories_in_plan(project, plan_id)
        if not stories:
            plan = E._azure_get(
                f"https://dev.azure.com/{E.AZURE_ORG}/{project}"
                f"/_apis/testplan/plans/{plan_id}?api-version=7.0")
            itr = plan.get("iteration")
            stories = E.fetch_stories_in_iteration(project, itr) if itr else []
        return stories
    # Non-Azure (Jira+Zephyr, Jira+Xray, TestRail): a plan/folder is a test-case
    # container and does NOT track which stories belong to it, so there is no
    # honest plan→stories mapping. On these backends stories live in a SPRINT
    # (the plan is only the write target), so populate the picker from the
    # project's ACTIVE sprint(s) — the same source the Regression screen already
    # uses via fetch_stories_in_sprint. Active-only keeps this to a single JQL
    # search in the common case; if nothing is active we fall back to scanning
    # all sprints so a between-sprints project isn't left with an empty picker.
    try:
        backend = get_backend(app)
        proj = _as_project(backend, project)
        sprints = backend.fetch_sprints(proj) or []
        active = [s for s in sprints
                  if (getattr(s, "state", "") or "").lower() == "active"]
        chosen = active or sprints
        seen: dict = {}
        for sp in chosen:
            for st in (backend.fetch_stories_in_sprint(proj, sp.path) or []):
                sid = st.ref.key or st.ref.id
                if sid and sid not in seen:
                    seen[sid] = {"id": sid, "title": st.title}
        return list(seen.values())
    except Exception as exc:
        try:
            import diag_log
            diag_log.log("backend_setup.fetch_stories_for_plan", exc)
        except Exception:
            pass
        return []


def validate_stories_in_plan(app, plan_id, story_ids):
    """(found, missing) plan-membership pre-run check, backend-aware.

    Azure maps each story to a requirement suite already in the plan, so a story
    NOT in the plan is a real error worth blocking on. Other backends don't tie
    stories to a plan at all — the plan/folder is only the WRITE target and the
    stories come from a sprint — so this membership check doesn't apply and its
    Azure-only suites endpoint 404s on them. Treat every story as found there;
    real story existence is still validated by fetch_stories when the run starts.
    Hybrids whose stories come from Azure (AZURE_TESTRAIL) keep the Azure check,
    since their plan lives on the Azure source.
    """
    creds = getattr(app, "creds", {}) or {}
    if reads_stories_from_azure(creds):
        import engine as E
        return E.validate_stories_in_plan(
            getattr(app, "project", ""), plan_id, story_ids)
    return list(story_ids), []


def count_existing_steps(app, plan_id, story_ids):
    """(have, total) for the pre-run 'some cases already have steps' popup.

    Azure counts its plan's cases. Other backends now report a real TOTAL (see
    count_test_cases) so the "THIS RUN" estimate isn't stuck at ~0, but `have`
    stays 0: on those targets existing steps are handled per-case as an
    OVERWRITE during the run rather than by a pre-run gate, so claiming a
    have-count would drive a popup whose choices don't apply."""
    creds = getattr(app, "creds", {}) or {}
    if is_azure(creds):
        import engine as E
        return E.count_existing_steps(getattr(app, "project", ""), plan_id, story_ids)
    return 0, count_test_cases(app, plan_id, story_ids)


def count_test_cases(app, plan_id, story_ids):
    """Estimated test-case count for the 'this run' panel. Azure counts its
    plan; other backends have no cheap equivalent, so return 0 (shown as no
    estimate) rather than a misleading Azure number."""
    creds = getattr(app, "creds", {}) or {}
    if is_azure(creds):
        import engine as E
        return E.count_test_cases(getattr(app, "project", ""), plan_id, story_ids)
    # Non-Azure: count the cases already sitting in each story's EXISTING suite.
    # Uses the NON-creating find_suite_for_story — ensure_suite_for_story would
    # create an empty suite/section per story just to count it, i.e. merely
    # opening Setup would litter the tracker. A story with no suite yet counts 0.
    # Any adapter that hasn't implemented the lookup returns None from the base
    # default, so this degrades to the previous behaviour (0) instead of lying.
    try:
        backend = get_backend(app)
        project = getattr(app, "project", "") or ""
        proj = _as_project(backend, project)
        plan = _plan_dto(backend, plan_id, app)   # app ⇒ carries the real name
        from tracker.models import Ref
        stories = backend.fetch_stories([Ref(id=str(s), key=str(s))
                                         for s in (story_ids or [])]) or []
        total = 0
        for story in stories:
            suite = backend.find_suite_for_story(proj, plan, story)
            if suite is None:
                continue
            total += len(backend.fetch_test_cases_for_suite(proj, plan, suite) or [])
        return total
    except Exception as exc:
        # Was a bare `return 0`, which made a REAL failure (bad creds, an
        # un-delegated interface method, a tracker outage) indistinguishable from
        # a genuine "no existing cases" — the estimate just read ~0 with no
        # error, no log, nothing to chase. Log it; still fail-soft, because a
        # best-effort estimate must never block Setup.
        try:
            import diag_log
            diag_log.log("backend_setup.count_test_cases", exc)
        except Exception:
            pass
        return 0


def sprint_reports_available(app):
    """Whether the Sprint Report / Sprint Summary screens can actually GENERATE
    on the active backend (ADR-002).

    These two screens still generate via the Azure engine (E.sprint_report_data /
    E.sprint_summary) — the only path wired end-to-end in the UI today — so this
    returns is_azure(). It is the single predicate both screens gate on, so when
    the non-Azure data path is routed through the seam (Jira+Zephyr's own
    sprint_report_data; Xray Test-Run rollups), this flips to a capability check
    (`Capability.SPRINT_REPORTS in get_backend(app).capabilities`) in ONE place
    and both screens follow. Until then the honest answer for every non-Azure
    connection is "not here", which the screens surface as a backend-named
    unavailable state instead of a misleading Azure 404.
    """
    return is_azure(getattr(app, "creds", {}) or {})


def sprint_report_data(app, sprint_path):
    """`{"stories":[{id,title,state}], "bugs":[{id,state,tags}]}` for the Sprint
    Report screen (sprint_titles). Unlike the execution-rollup Sprint SUMMARY,
    this report only needs the sprint's stories (translated titles, split by
    completed/carried) + bug counts — no pass/fail data — so it CAN work on every
    backend, not just Azure.

    Azure (and Azure→TestRail) use the engine's own sprint_report_data. Every
    other backend pulls the sprint's stories (with state) straight from the READ
    source via fetch_stories_in_sprint. Bugs aren't separable from stories on a
    plain Jira source without an issuetype query the Backend interface doesn't
    expose, so they're returned empty there — the report still lists and
    translates the stories, which is its main job."""
    creds = getattr(app, "creds", {}) or {}
    project = getattr(app, "project", "") or ""
    if reads_stories_from_azure(creds):
        import engine as E
        return E.sprint_report_data(project, sprint_path)
    try:
        backend = get_backend(app)
        proj = _as_project(backend, project)
        stories = [{"id": s.ref.key or s.ref.id,
                    "title": getattr(s, "title", "") or "",
                    "state": getattr(s, "state", "") or ""}
                   for s in backend.fetch_stories_in_sprint(proj, sprint_path)]
        try:
            bugs = backend.fetch_bugs_in_sprint(proj, sprint_path) or []
        except Exception:
            bugs = []       # optional — a backend without bugs just reports none
        return {"stories": stories, "bugs": bugs}
    except Exception as exc:
        try:
            import diag_log
            diag_log.log("backend_setup.sprint_report_data", exc)
        except Exception:
            pass
        return {"stories": [], "bugs": []}


def fetch_sprints(app, project=None):
    """Sprints/iterations for the active backend.

    Azure returns its own iteration rows unchanged. Other backends return the
    same `[{"name","path"}]` shape so a caller can show `name` and pass `path`
    straight back into fetch_stories_in_sprint().
    """
    creds = getattr(app, "creds", {}) or {}
    project = project or getattr(app, "project", "") or ""
    if is_azure(creds):
        import engine as E
        return E.fetch_iterations(project) or []
    backend = get_backend(app)
    # Full engine.fetch_iterations shape — name/path/id/start_date/finish_date —
    # not just the two keys the first caller happened to read. Consumers filter
    # and sort on these (regression._cp_is_sprint, task_manager._sprint_num read
    # name AND path), so a partial dict would work until the day it silently
    # didn't. Dates are "" when the backend doesn't supply them, matching how
    # engine's own fallback row does it.
    return [{"name": s.name, "path": s.path, "id": s.ref.id,
             "start_date": s.start.isoformat() if s.start else "",
             "finish_date": s.end.isoformat() if s.end else ""}
            for s in backend.fetch_sprints(_as_project(backend, project))]


def fetch_stories_in_sprint(app, sprint_path, project=None):
    """Stories in a sprint, in the legacy `[{"id","title"}]` shape.

    `id` is the identifier the rest of the app will hand back to the backend:
    the work-item id on Azure, the issue KEY on Jira (`fetch_stories` resolves
    keys via `key IN (…)`). The numeric Jira id is still carried on the DTO's
    Ref for the places that need it — notably Zephyr issue links, which require
    the numeric form.
    """
    creds = getattr(app, "creds", {}) or {}
    project = project or getattr(app, "project", "") or ""
    if is_azure(creds):
        import engine as E
        return E.fetch_stories_in_iteration(project, sprint_path) or []
    backend = get_backend(app)
    return [{"id": s.ref.key or s.ref.id, "title": s.title}
            for s in backend.fetch_stories_in_sprint(
                _as_project(backend, project), sprint_path)]


def item_url(app, item_id, project=None):
    """Browser link to a work item / issue, for the ACTIVE backend.

    Replaces the `https://dev.azure.com/{org}/{project}/_workitems/edit/{id}`
    f-strings that were hand-built at six call sites across main.py, report.py,
    regression.py and run_worker.py. Those are DISPLAY links — they end up in
    the Report screen, the sprint-summary email and the run report — so on a
    Jira connection every one of them pointed at a dev.azure.com URL that
    simply doesn't exist.
    """
    creds = getattr(app, "creds", {}) or {}
    project = project or getattr(app, "project", "") or ""
    if not item_id:
        return ""
    # A work item / story belongs to the READ SOURCE. On a hybrid the active
    # backend is the WRITE target (TestRail), so routing story links through it
    # sent every per-story link in the Report + emails to TestRail even though
    # the story lives in Azure/Jira (roadmap #120). Resolve against the source.
    if is_hybrid(creds):
        src = hybrid_source(creds)
        if src == AZURE:
            import engine as E
            org = (getattr(E, "AZURE_ORG", "") or "").strip()
            return (f"https://dev.azure.com/{org}/{project}/_workitems/edit/{item_id}"
                    if org else "")
        site = (creds.get("jira_site") or "").strip().rstrip("/")
        return f"{site}/browse/{item_id}" if site else ""
    if is_azure(creds):
        import engine as E
        org = (getattr(E, "AZURE_ORG", "") or "").strip()
        if not org:
            return ""
        return f"https://dev.azure.com/{org}/{project}/_workitems/edit/{item_id}"
    from tracker.models import Ref
    backend = get_backend(app)
    return backend.item_url(Ref(id=str(item_id), key=str(item_id)),
                            _as_project(backend, project))


def plan_url(app, plan_id, project=None):
    """Browser link to a test plan.

    Azure deep-links straight to the plan. Zephyr has no stable per-folder URL
    that's safe to synthesise, so the Jira path returns the PROJECT link rather
    than guessing a deep link — a correct-but-shallow link beats a fabricated
    one that 404s in a stakeholder's email. Returns "" when there's nothing
    honest to point at; every caller already handles an absent plan link.
    """
    creds = getattr(app, "creds", {}) or {}
    project = project or getattr(app, "project", "") or ""
    if not plan_id or not project:
        return ""
    # The test PLAN lives with the READ SOURCE, not the write target: the Setup
    # dropdown lists source plans and `plan_id` is a SOURCE id (an Azure plan id
    # on Azure→TestRail). `is_azure` alone missed the hybrid, so its plan link
    # fell through to the Jira branch and opened a Jira URL for an Azure plan
    # (reported live). reads_stories_from_azure covers Azure AND Azure→TestRail.
    if reads_stories_from_azure(creds):
        import engine as E
        org = (getattr(E, "AZURE_ORG", "") or "").strip()
        if not org:
            return ""
        return (f"https://dev.azure.com/{org}/{project}"
                f"/_testPlans/define?planId={plan_id}")
    site = (creds.get("jira_site") or "").strip().rstrip("/")
    return f"{site}/projects/{project}" if site else ""


def fetch_project_members(app, project=None):
    """Assignable users in engine's EXACT dict shape: `{"name", "email"}`.

    That shape is a hard contract, not a convenience — every consumer indexes it
    directly (`regression.email_recipient_picker` does `m["email"]`/`m["name"]`,
    `task_manager` builds dropdown options the same way), so any other keys
    raise `KeyError: 'email'` while merely DRAWING the Setup screen. An earlier
    version of this router returned the adapter's `displayName`/`uniqueName`
    naming and did exactly that — caught live.

    Azure calls engine directly (unchanged). Other backends map their User DTOs
    into the same two keys, dropping members with no usable email since every
    consumer keys on it, and sorting by name to match engine's own ordering.
    """
    creds = getattr(app, "creds", {}) or {}
    project = project or getattr(app, "project", "") or ""
    if is_azure(creds):
        import engine as E
        return E.fetch_project_members(project)
    backend = get_backend(app)
    out = []
    for u in backend.fetch_project_members(_as_project(backend, project)) or []:
        email = (getattr(u, "email", "") or "").strip()
        name = (getattr(u, "display_name", "") or "").strip()
        if not email:
            # No address to send to / key on — every consumer indexes m["email"].
            continue
        out.append({"name": name or email, "email": email})
    return sorted(out, key=lambda r: r["name"].lower())


def create_child_tasks(app, items, project=None):
    """Create child/sub tasks. Returns engine's {"ok": int, "created": [...],
    "errors": [...]} shape, which the Task Manager UI checks directly. Azure
    unchanged; other backends map their returned Refs into the same shape."""
    creds = getattr(app, "creds", {}) or {}
    project = project or getattr(app, "project", "") or ""
    if is_azure(creds):
        import engine as E
        return E.create_child_tasks(project, items)
    backend = get_backend(app)
    made, errors = [], []
    try:
        _items = list(items or [])
        refs = backend.create_child_tasks(_as_project(backend, project), items) or []
        # Match engine's per-entry keys EXACTLY — {"story_id","task_id","title"}.
        # The UI only reads "ok"/"errors" today, but a missing key here is the
        # same latent KeyError that fetch_project_members shipped with. Adapters
        # return refs in submission order, so pair them back to their source item
        # for story_id/title rather than inventing values from the ref.
        for i, ref in enumerate(refs):
            src = _items[i] if i < len(_items) else {}
            made.append({"story_id": src.get("story_id"),
                         "task_id": getattr(ref, "id", "") or "",
                         "title": (src.get("title") or getattr(ref, "key", "") or "")})
    except Exception as exc:                      # noqa: BLE001 - surfaced to UI
        # TrackerError carries a user-facing remedy; anything else falls back
        # to str(). Imported lazily so this module stays import-light (the
        # tracker package is only pulled in on the non-Azure path).
        msg = getattr(exc, "full_message", None)
        errors.append(msg() if callable(msg) else str(exc))
    return {"ok": len(made), "created": made, "errors": errors}


def fetch_task_stats(app, iteration_path=None, assignee=None,
                     start_date=None, end_date=None, project=None):
    """Task Manager stats in engine's dict shape.

    Azure calls engine directly — unchanged, and it is the only backend that
    supplies PER-TASK rows (id/title/state/estimate/completed), which the Excel
    and PDF exports iterate.

    Other backends: the tracker interface's `fetch_task_stats` returns a
    per-assignee ROLLUP (TaskStats), which genuinely cannot supply those rows.
    Rather than fabricate them or silently export an empty table, this fills the
    totals it can prove and flags `partial=True` + `unsupported_detail` so the
    caller can say "per-task detail isn't available on this backend" instead of
    showing a blank list as if there were no tasks.
    """
    creds = getattr(app, "creds", {}) or {}
    project = project or getattr(app, "project", "") or ""
    if is_azure(creds):
        import engine as E
        return E.fetch_user_task_stats(project, iteration_path=iteration_path,
                                       assignee=assignee, start_date=start_date,
                                       end_date=end_date)
    backend = get_backend(app)
    rows = backend.fetch_task_stats(_as_project(backend, project),
                                    sprint_path=iteration_path or "",
                                    assignee=assignee or "") or []
    total = sum(int(getattr(r, "total", 0) or 0) for r in rows)
    est = sum(float(getattr(r, "remaining_hours", 0.0) or 0.0) for r in rows)
    done = sum(float(getattr(r, "completed_hours", 0.0) or 0.0) for r in rows)
    return {"iteration": iteration_path or "", "date_range": "",
            "assignee": assignee or "", "tasks": [], "count": total,
            "total_original_estimate": est, "total_completed_work": done,
            "partial": True,
            "unsupported_detail": "Per-task detail isn't available on this backend."}


def story_row_url(app, story_id, suite_id=None, plan_id=None):
    """Link for the Report's PER-STORY breakdown row.

    Deliberately NOT the same as `item_url` (which always points at the story in
    its read source). What the user wants from this row is "show me what this run
    produced", so it follows the WRITE target:

      * Azure DevOps           → the Azure work item (cases live in Azure too).
      * Azure/Jira → TestRail  → the TestRail SECTION holding that story's cases.
        In TestRail the per-story "suite" is a SECTION id and `plan_id` is the
        SUITE id (see tracker/testrail.ensure_suite_for_story), so the link is
        the suite view scoped to that section. The extra group_* params degrade
        gracefully: if TestRail ignores them the user still lands on the suite.
      * Anything else          → fall back to `item_url`.
    """
    creds = getattr(app, "creds", {}) or {}
    name = active(creds)
    if name in (AZURE_TESTRAIL, JIRA_TESTRAIL):
        base = (creds.get("testrail_url") or "").strip().rstrip("/")
        suite = plan_id if plan_id is not None else getattr(app, "plan_id", None)
        if not base or not suite:
            return item_url(app, story_id) or ""
        url = f"{base}/index.php?/suites/view/{suite}"
        if suite_id:
            url += (f"&group_by=cases:section_id&group_order=asc"
                    f"&group_id={suite_id}")
        return url
    return item_url(app, story_id) or ""


#: Display names, keyed by backend id.
_STORE_NAMES = {AZURE: "Azure DevOps", JIRA_ZEPHYR: "Zephyr", XRAY: "Xray",
                TESTRAIL: "TestRail", AZURE_TESTRAIL: "TestRail",
                JIRA_TESTRAIL: "TestRail"}


def case_store_label(app):
    """Where TEST CASES live — for user-facing copy ("reading test cases from …").

    On a hybrid this is the WRITE target (TestRail): Azure/Jira is the read side
    for stories and acceptance criteria only, so telling the user we're "reading
    test cases from Azure DevOps" during a hybrid generate is simply wrong."""
    return _STORE_NAMES.get(active(getattr(app, "creds", {}) or {}), "the tracker")


def saved_cred_fields(creds):
    """(label, secret) pairs for the connected-Setup summary of the ACTIVE
    backend, so it stops hardcoding an "Azure DevOps PAT" row on every non-Azure
    connection (e.g. Jira → TestRail showed Azure PAT · Valid, which is wrong and
    confusing). Returns the raw secrets; the UI masks them. Mirrors the same
    branch order the edit form (_connection_edit) already uses."""
    name = active(creds)
    g = (creds or {}).get
    if is_azure(creds):
        return [("Azure DevOps PAT", g("pat", ""))]
    if name == XRAY:
        return [("Jira API token", g("jira_token", "")),
                ("Xray API Key", g("xray_client_secret", ""))]
    if name == TESTRAIL:
        return [("TestRail API key", g("testrail_key", ""))]
    if name == AZURE_TESTRAIL:
        return [("Azure DevOps PAT", g("pat", "")),
                ("TestRail API key", g("testrail_key", ""))]
    if name == JIRA_TESTRAIL:
        return [("Jira API token", g("jira_token", "")),
                ("TestRail API key", g("testrail_key", ""))]
    # JIRA_ZEPHYR or plain Jira
    rows = [("Jira API token", g("jira_token", ""))]
    if g("zephyr_token"):
        rows.append(("Zephyr Scale API token", g("zephyr_token", "")))
    return rows


def story_store_label(app):
    """Where USER STORIES live — the opposite half of case_store_label().

    Story id links, priority, assignee and team-member lists all come from here:
    Azure DevOps for Azure and Azure→TestRail, Jira for the Jira-native backends
    and Jira→TestRail."""
    creds = getattr(app, "creds", {}) or {}
    if is_hybrid(creds):
        return "Azure DevOps" if hybrid_source(creds) == AZURE else "Jira"
    return "Azure DevOps" if is_azure(creds) else "Jira"


def case_url(app, case_id, project=None):
    """Browser link to a TEST CASE — the counterpart to item_url (a STORY).

    They must not share a router. `item_url` resolves a hybrid to the READ
    SOURCE, because a story id is an Azure/Jira work item. A test-case id is
    the opposite: it belongs to the WRITE target, so on Azure→TestRail this has
    to be the TestRail case, not a dev.azure.com work item bearing the TestRail
    case number (which is what the Report's "Needs your review" panel linked to).
    CompositeBackend.item_url already delegates to the target, so non-Azure just
    goes through the adapter.
    """
    creds = getattr(app, "creds", {}) or {}
    project = project or getattr(app, "project", "") or ""
    if not case_id:
        return ""
    if is_azure(creds):
        return item_url(app, case_id, project)      # Azure: cases are work items
    try:
        from tracker.models import Ref
        backend = get_backend(app)
        return backend.item_url(Ref(id=str(case_id), key=str(case_id)),
                                _as_project(backend, project)) or ""
    except Exception:
        return ""


def plan_link_label(app):
    """Label for the Report's 'Open plan' button, naming the system the plan
    actually lives in. It was hardcoded "Open plan in Azure", which is wrong on
    every non-Azure backend (roadmap #120). NOTE: unlike item_url (a STORY,
    which belongs to the read source), the PLAN is written to the active/write
    target — so a hybrid names TestRail here and Azure/Jira for the story links.
    """
    creds = getattr(app, "creds", {}) or {}
    name = active(creds)
    # Name where the PLAN lives (its id opens THERE), which is the read source
    # for a hybrid — matching plan_url. Earlier this said "TestRail" for the
    # hybrids, so the button read "Open plan in TestRail" but opened the Azure
    # plan; the plan_id is a source id, so TestRail was never the right target.
    where = {AZURE: "Azure DevOps", JIRA_ZEPHYR: "Zephyr", XRAY: "Xray",
             TESTRAIL: "TestRail",
             AZURE_TESTRAIL: "Azure DevOps", JIRA_TESTRAIL: "Jira"}.get(name, "")
    return f"Open plan in {where}" if where else "Open plan"


def generation_ops(app):
    """Build the ops object `engine.run_titles`/`run_steps` need, for the
    ACTIVE backend. Returns None for Azure, meaning "use engine's defaults" —
    so the Azure generation path stays literally unchanged.

    This is the piece that lets generation run on a non-Azure tracker. The two
    generators interleave AI work with 15 tracker calls; rather than fork them,
    engine routes those through an injectable object (`engine._TrackerOps`).
    Here we implement the same nine operations against a tracker Backend,
    translating engine's positional/ID-based conventions to DTOs.
    """
    creds = getattr(app, "creds", {}) or {}
    if is_azure(creds):
        return None                      # engine._default_ops() — unchanged path

    import engine as E
    from tracker.models import Plan, Ref, Step, Story, Suite

    backend = get_backend(app)

    def _proj(project):
        return _as_project(backend, project)

    def _plan(plan_id):
        # name= the REAL plan name, not the id: on a hybrid the composite maps
        # source plan → TestRail suite BY NAME and creates one when it doesn't
        # match, so name=str(plan_id) silently wrote every case into a junk
        # suite called e.g. "103151" instead of the plan's own suite.
        # See plan_name_for().
        return Plan(ref=Ref(id=str(plan_id)), name=plan_name_for(app, plan_id))

    def _suite(suite_id):
        return Suite(ref=Ref(id=str(suite_id)), name=str(suite_id))

    def connect(project):
        # Azure returns (wit, test) SDK clients; nothing equivalent exists here
        # and the generators only bind them, never use them directly.
        return (None, None)

    def discover_suites(project, plan_id, story_ids, create_missing=True):
        """engine returns {story_id: suite_id} and later does
        `story_suite_map.get(sid)` with the ORIGINAL sid from the caller — which
        may be an INT. Keying only by the DTO's string ref.id made that lookup
        miss (reported live: "no suite found/created, skipped" even though the
        section WAS created). So key by the original sid, str(sid), AND the DTO's
        id/key — the lookup then matches regardless of int-vs-str.

        `create_missing=False` is READ-ONLY discovery (the Automation screen,
        and any pre-run count): it now uses the non-creating
        `find_suite_for_story` (added #127) so it returns each story's EXISTING
        suite without writing anything — a story with no suite yet simply maps to
        nothing. `create_missing=True` keeps the get-or-CREATE
        `ensure_suite_for_story`. Previously create_missing=False returned {}
        (before find_suite_for_story existed), which left the Automation screen
        with no suites → no test cases on any tracker backend."""
        proj, plan = _proj(project), _plan(plan_id)
        out = {}
        for sid in story_ids:
            found = backend.fetch_stories([Ref(id=str(sid), key=str(sid))])
            if not found:
                continue
            story = found[0]
            if create_missing:
                suite = backend.ensure_suite_for_story(proj, plan, story)
            else:
                suite = backend.find_suite_for_story(proj, plan, story)
            if suite is None:
                continue
            for k in (sid, str(sid), story.ref.id, story.ref.key):
                out[k] = suite.ref.id
        return out

    def fetch_stories(story_ids):
        """engine reads `story.id` and `story.fields.get("System.Title")` as
        ATTRIBUTES (it was written for Azure SDK WorkItem objects), so return
        objects, NOT dicts. Returning dicts is what caused the live
        `'dict' object has no attribute 'fields'` crash."""
        import types
        stories = backend.fetch_stories([Ref(id=str(s), key=str(s)) for s in story_ids])
        # engine's story flows to three consumers, each reading a DIFFERENT SDK
        # attribute: run_titles/run_steps read .id + .fields; fetch_story_screenshots
        # reads .relations (SDK attachment relations). `relations=[]` makes the
        # screenshot pass find no attachments and degrade to AC-only generation
        # (a non-Azure story DTO carries no attachment relations anyway).
        return [types.SimpleNamespace(
            id=s.ref.key or s.ref.id,
            relations=[],
            fields={
                "System.Id": s.ref.key or s.ref.id,
                "System.Title": s.title,
                "System.Description": s.description,
                "Microsoft.VSTS.Common.AcceptanceCriteria": s.acceptance_criteria,
                "System.State": s.state,
            }) for s in stories]

    def dedupe_suite(project, plan_id, suite_id, cb=None, do_delete=True, **kw):
        """Remove duplicate test cases from a suite, for ANY backend.

        Deliberately DETERMINISTIC — exact-title matching via engine's own
        `_norm_title`, with NO AI clustering. Azure's `dedupe_existing_suite`
        additionally asks the model to merge near-duplicates; that half is not
        ported, so this catches the same-title case (the common one, and the
        one regeneration creates) without an AI call that could delete a
        genuinely distinct case on a bad day. Deleting test data is
        irreversible, so the conservative rule is the right default.

        Keeper rule matches Azure: most steps wins, tie-break LOWEST id. Note
        most adapters don't populate `steps` on a list call, so in practice this
        keeps the oldest case — deterministic, and the one other things are most
        likely to already reference.

        Returns Azure's exact contract: {"removed", "kept", "groups",
        "keeper_ids"} — `keeper_ids` holds ONLY winners of groups that really had
        duplicates (the caller uses it to skip shrinking a case that just
        absorbed a duplicate's scope), and MUST be a dict, never None.
        """
        import engine as E
        empty = {"removed": [], "kept": [], "groups": 0, "keeper_ids": []}
        proj, plan, suite = _proj(project), _plan(plan_id), _suite(suite_id)
        try:
            cases = backend.fetch_test_cases_for_suite(proj, plan, suite) or []
        except Exception as exc:
            if cb:
                cb("log", {"msg": f"Couldn't read the suite to check duplicates: "
                                  f"{str(exc)[:120]}", "tone": "warn", "ico": "⚠"})
            return empty
        buckets = {}
        for tc in cases:
            norm = ""
            try:
                norm = E._norm_title(tc.title or "")
            except Exception:
                norm = (tc.title or "").strip().casefold()
            if not norm:
                continue
            buckets.setdefault(norm, []).append(tc)

        def _id_of(tc):
            raw = getattr(tc.ref, "id", "") or ""
            try:
                return int(str(raw))
            except (TypeError, ValueError):
                return 0

        removed, kept, keeper_ids, dup_groups = [], [], [], 0
        for members in buckets.values():
            if len(members) < 2:
                continue
            dup_groups += 1
            members.sort(key=lambda m: (-len(getattr(m, "steps", []) or []), _id_of(m)))
            keeper = members[0]
            kept.append({"id": _id_of(keeper), "title": keeper.title or ""})
            keeper_ids.append(_id_of(keeper))
            for victim in members[1:]:
                if do_delete:
                    try:
                        backend.delete_test_case(proj, plan, suite, victim.ref)
                    except Exception as exc:
                        if cb:
                            cb("log", {"msg": f"Couldn't delete duplicate "
                                              f"{(victim.title or '')[:60]}: "
                                              f"{str(exc)[:100]}",
                                       "tone": "warn", "ico": "⚠"})
                        continue
                removed.append({"id": _id_of(victim), "title": victim.title or "",
                                "kept_id": _id_of(keeper)})
                if cb:
                    cb("log", {"msg": f"  {'removed' if do_delete else 'duplicate'}: "
                                      f"{(victim.title or '')[:70]} "
                                      f"(same as #{_id_of(keeper)})", "tone": "dim"})
        if cb:
            if dup_groups:
                _n = len(removed)
                cb("log", {"msg": (f"{'Removed' if do_delete else 'Found'} {_n} duplicate "
                                   f"test case{'s' if _n != 1 else ''} "
                                   f"across {dup_groups} group"
                                   f"{'s' if dup_groups != 1 else ''}."),
                           "tone": "ok", "ico": "✓"})
            else:
                cb("log", {"msg": "No duplicate test cases found in the suite.",
                           "tone": "dim"})
        return {"removed": removed, "kept": kept, "groups": dup_groups,
                "keeper_ids": keeper_ids}

    def existing_titles(project, plan_id, suite_id):
        return backend.fetch_existing_titles_for_suite(
            _proj(project), _plan(plan_id), _suite(suite_id))

    def create_case(project, plan_id, suite_id, title, story_id):
        story = None
        if story_id:
            found = backend.fetch_stories([Ref(id=str(story_id), key=str(story_id))])
            story = found[0] if found else None
        case = backend.create_test_case(_proj(project), _plan(plan_id),
                                        _suite(suite_id), title, story)
        return case.ref.key or case.ref.id      # engine expects a bare id

    def cases_for_suite(project, plan_id, suite_id):
        # engine reads each case as tc.get("workItem", {}).get("id"/"name") —
        # Azure's test-case-in-suite shape — NOT {"id","title"}. Match it, or
        # run_steps reads no id/title and processes nothing.
        return [{"workItem": {"id": c.ref.key or c.ref.id, "name": c.title}}
                for c in backend.fetch_test_cases_for_suite(
                    _proj(project), _plan(plan_id), _suite(suite_id))]

    def case_title(tc_id):
        for c in backend.fetch_test_cases_for_suite(
                _proj(getattr(app, "project", "")), _plan(""), _suite("")):
            if (c.ref.key or c.ref.id) == str(tc_id):
                return c.title
        return ""

    def write_steps(tc_id, steps_xml, project=None, story_id=None):
        """engine hands us Azure steps XML. Parse it back into Step DTOs — the
        XML never reaches the backend."""
        parsed = E.parse_steps_xml(steps_xml) or []
        steps = []
        for i, row in enumerate(parsed, start=1):
            if isinstance(row, dict):
                action, expected = row.get("action", ""), row.get("expected", "")
            elif isinstance(row, (list, tuple)):
                action = row[0] if len(row) > 0 else ""
                expected = row[1] if len(row) > 1 else ""
            else:
                action, expected = str(row), ""
            pre, action = _unfold_precondition(action)
            steps.append(Step(action=action, expected=expected, pre=pre, index=i))
        backend.update_test_case_steps(Ref(id=str(tc_id), key=str(tc_id)), steps)

    def case_detail(tc_id):
        """(title, steps) for one test case — the Automation screen's Phase B.
        engine's Azure fetch_test_case_detail returns (title, parse_steps_xml(...))
        i.e. a list of {"index","action","expected"}; match that shape exactly so
        the self-healing generator downstream is backend-agnostic. Title is left
        blank here because Automation already has it from cases_for_suite (Phase
        A) and falls back to it; the step READ is the part the backend owns.
        Xray resolves a key→numeric issueId inside fetch_test_case_steps, so a
        tc_id that is an issue KEY works."""
        try:
            raw = backend.fetch_test_case_steps(
                Ref(id=str(tc_id), key=str(tc_id))) or []
        except Exception:
            return "", []
        steps = [{"index": getattr(s, "index", i) or i,
                  "action": getattr(s, "action", "") or "",
                  "expected": getattr(s, "expected", "") or ""}
                 for i, s in enumerate(raw, start=1)]
        return "", steps

    return E._TrackerOps(
        connect=connect, discover_suites=discover_suites,
        fetch_stories=fetch_stories, dedupe_suite=dedupe_suite,
        existing_titles=existing_titles, create_case=create_case,
        cases_for_suite=cases_for_suite, case_title=case_title,
        write_steps=write_steps, case_detail=case_detail)


#: Labels engine.build_steps_xml uses when folding a step's precondition into
#: the action text (Azure has no precondition field). Both language variants,
#: because the pair chosen depends on E._is_arabic_out() at generation time.
_PRE_LABELS = (("الشرط المسبق:", "الإجراء:"), ("Precondition:", "Action:"))


def _unfold_precondition(action_text):
    """Split a folded "Precondition: X / Action: Y" action back into (pre, action).

    WHY this exists: engine hands non-Azure backends **Azure steps XML**, and by
    that point `build_steps_xml` has already merged the precondition INTO the
    action string with a label — the XML intermediate is lossy for any backend
    that has a real precondition field (TestRail's `custom_preconds`). Rather
    than let TestRail inherit Azure's formatting, recover the two halves here.

    This is a WORKAROUND for that lossy hand-off; the proper fix is passing
    `list[Step]` to the generators instead of XML (TRACKER_BACKENDS_PLAN Phase 0
    item 4, "fix the XML leak"). Returns ("", original) unchanged when no label
    is present, so an unfolded action is never altered.
    """
    text = action_text or ""
    for pre_label, act_label in _PRE_LABELS:
        if pre_label not in text:
            continue
        after = text.split(pre_label, 1)[1]
        if act_label in after:
            pre, action = after.split(act_label, 1)
            return pre.strip(), action.strip()
        # Precondition label with no action label: the whole remainder is the
        # precondition, and there's no action text to keep.
        return after.strip(), ""
    return "", text


def _as_project(backend, project):
    """Wrap a bare project key/name into the Project DTO the adapters expect."""
    from tracker.models import Project, Ref
    if isinstance(project, Project):
        return project
    key = str(project or "")
    return Project(ref=Ref(id=key, key=key), name=key)


def suite_map_for_stories(app, plan_id, story_ids):
    """{story_id: suite_id} for the backend that OWNS THE TEST CASES.

    The distinction that matters on a hybrid: Azure is the READ side for user
    stories + acceptance criteria ONLY. The test cases — and therefore the
    suites/sections holding them — live in the WRITE target (TestRail). So
    anything counting or listing existing cases (Setup's estimate, the
    Regression Plan) must resolve suites from TestRail, NOT from Azure.

    Azure-only backend: unchanged, resolved by the caller's own Azure lookup
    (this returns {} so the caller keeps its existing path). Everything else:
    per-story non-creating lookup through the active backend.
    """
    creds = getattr(app, "creds", {}) or {}
    if is_azure(creds):
        return {}
    out = {}
    try:
        backend = get_backend(app)
        proj = _as_project(backend, getattr(app, "project", "") or "")
        plan = _plan_dto(backend, plan_id, app)
        from tracker.models import Ref
        stories = backend.fetch_stories([Ref(id=str(s), key=str(s))
                                         for s in (story_ids or [])]) or []
        for story in stories:
            suite = backend.find_suite_for_story(proj, plan, story)
            if suite is None:
                continue
            # Key by BOTH int and str: callers index with the original story id,
            # which may be either (the same int-vs-str trap that broke
            # discover_suites — see roadmap #125).
            for k in (story.ref.id, story.ref.key):
                if k in (None, ""):
                    continue
                out[str(k)] = suite.ref.id
                try:
                    out[int(k)] = suite.ref.id
                except (TypeError, ValueError):
                    pass
    except Exception as exc:
        try:
            import diag_log
            diag_log.log("backend_setup.suite_map_for_stories", exc)
        except Exception:
            pass
    return out


def count_cases_in_suite(app, plan_id, suite_id):
    """Number of test cases in one suite, from the backend that owns them.

    Azure counts its own suite; a hybrid counts the TestRail section (see
    suite_map_for_stories for why Azure is never the source of case counts on a
    hybrid). Returns 0 rather than raising — callers use this for estimates."""
    creds = getattr(app, "creds", {}) or {}
    project = getattr(app, "project", "") or ""
    try:
        if is_azure(creds):
            import engine as E
            return len(E.fetch_test_cases_for_suite(project, plan_id, suite_id) or [])
        backend = get_backend(app)
        return len(backend.fetch_test_cases_for_suite(
            _as_project(backend, project), _plan_dto(backend, plan_id, app),
            _suite_dto(backend, suite_id)) or [])
    except Exception as exc:
        try:
            import diag_log
            diag_log.log("backend_setup.count_cases_in_suite", exc)
        except Exception:
            pass
        return 0


def plan_name_for(app, plan_id):
    """The plan's REAL name for an id, from the already-loaded plan list.

    Critical for HYBRIDS: `CompositeBackend._target_plan()` maps a source plan to
    a TestRail suite **by NAME**, and CREATES one when there's no match. So a
    Plan DTO carrying `name=str(plan_id)` ("103151") never matches the real suite
    and makes the composite create a junk suite literally named after the id —
    which also means the existing-case count then reads that empty suite and
    reports 0. Falls back to the id only when the name genuinely isn't known.
    """
    for p in (getattr(app, "_plans", None) or []):
        try:
            if str(p.get("id")) == str(plan_id):
                return p.get("name") or str(plan_id)
        except Exception:
            continue
    return str(plan_id)


def _plan_dto(backend, plan_id, app=None):
    """Wrap a bare plan id into the Plan DTO the adapters expect.

    Pass `app` whenever it's available so the DTO carries the real plan NAME —
    see plan_name_for() for why the name (not just the id) decides which
    TestRail suite a hybrid resolves to."""
    from tracker.models import Plan, Ref
    if plan_id is not None and hasattr(plan_id, "ref"):
        return plan_id
    _name = plan_name_for(app, plan_id) if app is not None else str(plan_id)
    return Plan(ref=Ref(id=str(plan_id)), name=_name)


def _suite_dto(backend, suite_id):
    """Wrap a bare suite id into the Suite DTO the adapters expect."""
    from tracker.models import Ref, Suite
    if suite_id is not None and hasattr(suite_id, "ref"):
        return suite_id
    return Suite(ref=Ref(id=str(suite_id)), name=str(suite_id))


def apply_credentials(app):
    """Push the active backend's credentials into the engine layer.

    Azure keeps flowing through engine.set_credentials (unchanged). For
    Jira/Zephyr there is nothing to push into engine — the tracker backend
    reads creds directly — so this is a no-op there by design.
    """
    creds = getattr(app, "creds", {}) or {}
    if not is_azure(creds):
        return
    try:
        import engine as E
        E.set_credentials(org=creds.get("org") or None, pat=creds.get("pat"))
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
#  UI
# ══════════════════════════════════════════════════════════════════════════

def _text_field(value, hint, password=False, read_only=False):
    import flet as ft
    import theme as T
    return ft.TextField(
        value=value or "", hint_text=hint, password=password,
        can_reveal_password=password, read_only=read_only,
        bgcolor=(T.CARD_2 if read_only else T.CARD),
        border_color=T.BORDER, focused_border_color=T.VIOLET,
        border_radius=T.R, text_size=13,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
        expand=True)


def _creds_present(creds):
    """True when every REQUIRED credential for the active backend is saved."""
    name = active(creds)
    def have(*keys):
        return all((creds.get(k) or "").strip() for k in keys)
    if name == TESTRAIL:
        return have("testrail_url", "testrail_email", "testrail_key")
    if name == JIRA_ZEPHYR:
        return have("jira_site", "jira_email", "jira_token", "zephyr_token")
    if name == XRAY:
        return have("jira_site", "jira_email", "jira_token",
                    "xray_client_id", "xray_client_secret")
    if name == AZURE_TESTRAIL:
        return have("org", "pat", "testrail_url", "testrail_email", "testrail_key")
    if name == JIRA_TESTRAIL:
        return have("jira_site", "jira_email", "jira_token",
                    "testrail_url", "testrail_email", "testrail_key")
    return False


def _bk_locked(app):
    """Whether the backend credential fields should render LOCKED (saved, read-
    only, with an 'Update' button) — the same Save→Update pattern the Azure org
    field uses. Editable when creds aren't fully saved yet, or the user clicked
    Update."""
    if getattr(app, "_bk_unlocked", False):
        return False
    return _creds_present(getattr(app, "creds", {}) or {})


def _save_or_update_btn(app):
    """Green 'Save' when editable; ghost 'Update' when locked (unlocks on click)."""
    from ui import green_btn, ghost_btn
    if _bk_locked(app):
        return ghost_btn(strings.t("bset_update"), on_click=lambda e: _bk_unlock(app))
    return green_btn(strings.t("bset_save"), on_click=lambda e: _save_creds(app))


def _bk_unlock(app):
    app._bk_unlocked = True
    try:
        app.render()
    except Exception:
        pass


def _section_header(ft, T, current_label):
    """A distinct, titled banner that marks the Test Management section apart
    from the AI-provider fields below it — an accent-tinted card with an icon
    chip, the section title, and the currently-selected backend echoed as a
    badge. This is what makes the section 'unique' rather than just one more
    labelled dropdown in a flat column."""
    return ft.Container(
        ft.Row([
            ft.Container(
                ft.Icon(ft.Icons.HUB_OUTLINED, size=18, color=T.VIOLET_INK),
                width=34, height=34, bgcolor=T.VIOLET_SOFT, border_radius=9,
                alignment=ft.Alignment.CENTER),
            ft.Column([
                ft.Text(strings.t("bset_test_mgmt"), size=14, weight=ft.FontWeight.W_800,
                        color=T.INK),
                ft.Text(strings.t("bset_test_mgmt_sub"),
                        size=11, color=T.INK_2, weight=ft.FontWeight.W_500),
            ], spacing=1, expand=True),
            ft.Container(
                ft.Text(current_label, size=11, weight=ft.FontWeight.BOLD,
                        color=T.VIOLET_INK),
                bgcolor=T.VIOLET_SOFT, border_radius=8,
                padding=ft.Padding.symmetric(vertical=5, horizontal=10)),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=T.CARD_2, border=ft.Border.all(1, T.BORDER), border_radius=T.R_LG,
        padding=14, margin=ft.Margin.only(bottom=14))


def picker_rows(app, field_label, hover_field):
    """The 'Test management backend' selector, led by a distinct section header."""
    import flet as ft
    import theme as T
    creds = getattr(app, "creds", {}) or {}
    current = active(creds)

    # Mirrors main.py's project_dd/prov_dd EXACTLY. Flet 0.85 specifics that
    # each cost a render crash when guessed instead of copied:
    #   * ft.DropdownOption — NOT ft.dropdown.Option (renamed; AttributeError)
    #   * on_select        — NOT on_change (Dropdown.__init__ TypeError)
    #   * filled=True with bgcolor, as every other dropdown here does
    # Picker options: hide the standalone TestRail (it has no story source of its
    # own — TestRail is reached via the Azure→/Jira→TestRail hybrids), and sort by
    # the test-management tool (the label) so the list reads alphabetically.
    # Jira + Zephyr Scale is ALSO hidden for now — the Zephyr Scale REST auth
    # (401) isn't resolved yet, so it can't be shipped as a working option. The
    # backend, registry and code stay intact; this only drops it from the picker.
    # It's still shown if it's the CURRENTLY active backend, so an already-
    # connected user isn't stranded on an option they can't reselect.
    _hidden = {TESTRAIL}
    if current != JIRA_ZEPHYR:
        _hidden.add(JIRA_ZEPHYR)
    _pick = sorted((b for b in BACKENDS if b[0] not in _hidden),
                   key=lambda b: b[1].lower())
    app.backend_dd = ft.Dropdown(
        value=current,
        options=[ft.DropdownOption(key=key, text=label) for key, label, _ in _pick],
        on_select=lambda e: _on_pick(app, e),
        border_color=T.BORDER, focused_border_color=T.VIOLET,
        border_radius=T.R, text_size=13, filled=True, bgcolor=T.CARD,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
        expand=True)

    blurb = strings.t("bset_blurb_" + current)
    return [
        _section_header(ft, T, label_for(current)),
        field_label(strings.t("bset_backend"), req=True,
                    hint=strings.t("bset_backend_hint")),
        ft.Container(hover_field(app.backend_dd), padding=ft.Padding.only(top=4)),
        ft.Container(ft.Text(blurb, size=11, color=T.INK_2),
                     padding=ft.Padding.only(left=2, top=2, bottom=12)),
    ]


def _on_pick(app, e):
    """Persist the choice and re-render so the right credential rows show.

    The credentials themselves are NOT cleared on switch: a user flipping back
    and forth while setting things up would otherwise lose what they'd already
    typed, and both credential sets are scoped to this account anyway.
    """
    # Read from the stored control ref, not e.control — this is how every other
    # handler in main.py does it (_on_project_change reads self.project_dd.value),
    # and it doesn't depend on what shape Flet hands the select event.
    try:
        chosen = (getattr(app.backend_dd, "value", None) or AZURE).strip().lower()
    except Exception:
        return
    # Every registered backend, not just the first two — this guard was left at
    # (AZURE, JIRA_ZEPHYR) when Xray and TestRail were added, so picking either
    # was silently rejected: the dropdown changed visually but nothing saved or
    # re-rendered, leaving the fields showing the previous backend.
    if chosen not in _NAMES:
        return
    app.creds["backend"] = chosen
    # Reset the edit-lock so the newly-selected backend shows its saved creds
    # locked ('Update') rather than inheriting the previous backend's edit state.
    app._bk_unlocked = False
    # Isolate the 'THIS RUN' test-case estimate from the previous backend. The
    # estimate is backend-specific (non-Azure backends can't cheaply count, so
    # count_existing_steps/count_test_cases return 0 = "unknown"). Without this,
    # switching from Azure to Jira/Xray/TestRail left the stale Azure count (e.g.
    # "~39") showing under a fresh backend that has no such cases. Clear it, then
    # recompute (a no-op until connected + stories + plan are set).
    app._estimated_tc = None
    try:
        app._fetch_estimate()
    except Exception:
        pass
    try:
        import store
        store.save(app.creds)
    except Exception:
        pass
    try:
        app._toast(strings.t("bset_backend_set", label=label_for(chosen)))
    except Exception:
        pass
    try:
        app.render()
    except Exception:
        pass


def _jira_core_rows(app, field_label, hover_field):
    """Site / email / token — the credentials EVERY Jira-native backend shares
    (Zephyr and Xray both read Jira the same way). Extracted so a second Jira
    backend never re-implements these three fields or their help wiring."""
    import flet as ft
    creds = getattr(app, "creds", {}) or {}
    lk = _bk_locked(app)
    app.jira_site_field = _text_field(creds.get("jira_site", ""),
                                      "https://your-team.atlassian.net", read_only=lk)
    app.jira_email_field = _text_field(creds.get("jira_email", ""),
                                       "you@company.com", read_only=lk)
    app.jira_token_field = _text_field(creds.get("jira_token", ""),
                                       "Jira API token", password=True, read_only=lk)
    return [
        field_label(strings.t("bset_jira_site"), req=True,
                    info=strings.t("bset_jira_site_info"),
                    on_info=lambda e: _help(app, "jira_site")),
        ft.Container(hover_field(app.jira_site_field),
                     padding=ft.Padding.only(top=4, bottom=12)),
        field_label(strings.t("bset_jira_email"), req=True,
                    info=strings.t("bset_jira_email_info"),
                    on_info=lambda e: _help(app, "jira_email")),
        ft.Container(hover_field(app.jira_email_field),
                     padding=ft.Padding.only(top=4, bottom=12)),
        field_label(strings.t("bset_jira_token"), req=True,
                    info=strings.t("bset_jira_token_info"),
                    on_info=lambda e: _help(app, "jira_token")),
        ft.Container(hover_field(app.jira_token_field),
                     padding=ft.Padding.only(top=4, bottom=12)),
    ]


def jira_rows(app, field_label, hover_field):
    """Jira + Zephyr Scale: shared Jira core + the Zephyr token."""
    import flet as ft
    from ui import green_btn
    creds = getattr(app, "creds", {}) or {}
    app.zephyr_token_field = _text_field(creds.get("zephyr_token", ""),
                                         strings.t("bset_zephyr_token"), password=True,
                                         read_only=_bk_locked(app))
    save_btn = _save_or_update_btn(app)
    return _jira_core_rows(app, field_label, hover_field) + [
        field_label(strings.t("bset_zephyr_token"), req=True,
                    info=strings.t("bset_zephyr_token_info"),
                    on_info=lambda e: _help(app, "zephyr_token")),
        ft.Container(ft.Row([hover_field(app.zephyr_token_field), save_btn], spacing=8),
                     padding=ft.Padding.only(top=4, bottom=12)),
    ]


def xray_rows(app, field_label, hover_field):
    """Xray: shared Jira core + the Xray API Key (client id + secret).

    Xray reads Jira exactly like Zephyr, but its writes authenticate with a
    separate API Key that exchanges a client-id/secret pair for a bearer token
    — two fields, not one, which is why Xray needs its own row set."""
    import flet as ft
    from ui import green_btn
    creds = getattr(app, "creds", {}) or {}
    _lk = _bk_locked(app)
    app.xray_client_id_field = _text_field(creds.get("xray_client_id", ""),
                                           strings.t("bset_xray_cid_hint"), read_only=_lk)
    app.xray_client_secret_field = _text_field(creds.get("xray_client_secret", ""),
                                               strings.t("bset_xray_secret_hint"), password=True,
                                               read_only=_lk)
    save_btn = _save_or_update_btn(app)
    return _jira_core_rows(app, field_label, hover_field) + [
        field_label(strings.t("bset_xray_cid"), req=True,
                    info=strings.t("bset_xray_apikey_info"),
                    on_info=lambda e: _help(app, "xray_client_id")),
        ft.Container(hover_field(app.xray_client_id_field),
                     padding=ft.Padding.only(top=4, bottom=12)),
        field_label(strings.t("bset_xray_secret"), req=True,
                    info=strings.t("bset_xray_apikey_info"),
                    on_info=lambda e: _help(app, "xray_client_secret")),
        ft.Container(ft.Row([hover_field(app.xray_client_secret_field), save_btn], spacing=8),
                     padding=ft.Padding.only(top=4, bottom=12)),
    ]


def testrail_rows(app, field_label, hover_field):
    """Standalone TestRail — just the shared URL/email/key rows + Save."""
    return _testrail_field_rows(app, field_label, hover_field, with_save=True)


def _testrail_field_rows(app, field_label, hover_field, with_save=True):
    """The TestRail URL/email/key rows, shared by the standalone TestRail
    backend and both hybrids (their write target)."""
    import flet as ft
    creds = getattr(app, "creds", {}) or {}
    lk = _bk_locked(app)
    app.testrail_url_field = _text_field(creds.get("testrail_url", ""),
                                         "https://your-org.testrail.io", read_only=lk)
    app.testrail_email_field = _text_field(creds.get("testrail_email", ""),
                                           "you@company.com", read_only=lk)
    app.testrail_key_field = _text_field(creds.get("testrail_key", ""),
                                         strings.t("bset_tr_key"), password=True, read_only=lk)
    key_row = [hover_field(app.testrail_key_field)]
    if with_save:
        key_row.append(_save_or_update_btn(app))
    return [
        field_label(strings.t("bset_tr_url"), req=True, info=strings.t("bset_tr_url_info"),
                    on_info=lambda e: _help(app, "testrail_url")),
        ft.Container(hover_field(app.testrail_url_field),
                     padding=ft.Padding.only(top=4, bottom=12)),
        field_label(strings.t("bset_tr_email"), req=True,
                    info=strings.t("bset_tr_email_info"),
                    on_info=lambda e: _help(app, "testrail_email")),
        ft.Container(hover_field(app.testrail_email_field),
                     padding=ft.Padding.only(top=4, bottom=12)),
        field_label(strings.t("bset_tr_key"), req=True,
                    info=strings.t("bset_tr_key_info"),
                    on_info=lambda e: _help(app, "testrail_key")),
        ft.Container(ft.Row(key_row, spacing=8),
                     padding=ft.Padding.only(top=4, bottom=12)),
    ]


def hybrid_rows(app, field_label, hover_field):
    """Combined form for a hybrid: SOURCE creds (Azure org/PAT or Jira core) to
    read stories, then the TestRail target creds to write cases into.

    The source rows here are built by backend_setup (not main.py's own Azure
    fields) so the hybrid is fully self-contained and can't disturb the single-
    backend Azure form."""
    import flet as ft
    creds = getattr(app, "creds", {}) or {}
    rows = []
    if active(creds) == AZURE_TESTRAIL:
        # Azure source: org + PAT. Saved into the SAME creds keys the standalone
        # Azure backend uses (org/pat) — same account, same values.
        _lk = _bk_locked(app)
        app.hy_org_field = _text_field(creds.get("org", ""), strings.t("bset_azure_org_hint"),
                                       read_only=_lk)
        app.hy_pat_field = _text_field(creds.get("pat", ""), strings.t("bset_azure_pat"),
                                       password=True, read_only=_lk)
        rows += [
            _story_source_note(ft, strings.t("bset_story_source", name="Azure DevOps")),
            field_label(strings.t("bset_azure_org"), req=True,
                        info=strings.t("bset_azure_org_info"),
                        on_info=lambda e: _help(app, "org")),
            ft.Container(hover_field(app.hy_org_field),
                         padding=ft.Padding.only(top=4, bottom=12)),
            field_label(strings.t("bset_azure_pat"), req=True,
                        info=strings.t("bset_azure_pat_info"),
                        on_info=lambda e: _help(app, "pat")),
            ft.Container(hover_field(app.hy_pat_field),
                         padding=ft.Padding.only(top=4, bottom=12)),
        ]
    else:   # JIRA_TESTRAIL — Jira core (no Zephyr)
        rows += [_story_source_note(ft, strings.t("bset_story_source", name="Jira"))]
        rows += _jira_core_rows(app, field_label, hover_field)
    rows += [_story_source_note(ft, strings.t("bset_test_target"))]
    rows += _testrail_field_rows(app, field_label, hover_field, with_save=True)
    return rows


def _story_source_note(ft, text):
    import theme as T
    return ft.Container(
        ft.Text(text, size=11, weight=ft.FontWeight.W_800, color=T.VIOLET_INK),
        bgcolor=T.VIOLET_SOFT, border_radius=8,
        padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        margin=ft.Margin.only(top=4, bottom=8))


def _save_creds(app):
    ok, err = validate_for_connect(app)
    if not ok:
        try:
            app._err(err)
        except Exception:
            pass
        return
    try:
        import store
        store.save(app.creds)
        app._err("")
        app._toast(strings.t("bset_creds_saved", label=label_for(active(app.creds))))
        # Re-lock: saved creds now render read-only with an 'Update' button, in
        # place — the Save→Update switch the standalone fields already have.
        app._bk_unlocked = False
        try:
            app.render()
        except Exception:
            pass
    except Exception as exc:
        try:
            app._err(strings.t("bset_save_err", err=str(exc)[:90]))
        except Exception:
            pass


def _help(app, topic):
    try:
        app._show_help(topic)
    except Exception:
        pass


#: Merged into main.py's help-topic dict so the info icons resolve.
HELP = {
    "jira_site": {
        "title": "Jira Site URL",
        "steps": [
            "Open Jira in your browser and copy the address bar host.",
            "Example: https://my-team.atlassian.net (no trailing path).",
            "It must be HTTPS — a token is sent with every request.",
            "Self-hosted (Data Center) sites need Data Center mode enabled first.",
        ],
        "url": "https://admin.atlassian.com",
        "url_label": "Open Atlassian admin",
    },
    "jira_email": {
        "title": "Jira account email",
        "steps": [
            "The email address of the Atlassian account the API token belongs to.",
            "Jira Cloud authenticates as email + API token together.",
            "Using a different email than the token's owner will fail with 401.",
        ],
        "url": "https://id.atlassian.com/manage-profile/profile-and-visibility",
        "url_label": "Open Atlassian profile",
    },
    "jira_token": {
        "title": "Jira API token",
        "steps": [
            "Go to Atlassian account → Security → Create and manage API tokens.",
            "Create a token, name it 'QA Studio', and copy it once — it isn't shown again.",
            "Paste it here. This is NOT your Atlassian password.",
            "The token inherits your own Jira permissions.",
        ],
        "url": "https://id.atlassian.com/manage-profile/security/api-tokens",
        "url_label": "Create a Jira API token",
    },
    "zephyr_token": {
        "title": "Zephyr Scale API token",
        "steps": [
            "Opens your Jira personal Apps settings — pick 'Zephyr API Access Tokens'.",
            "Click 'Create access token', then copy it — it's shown only once.",
            "This is SEPARATE from the Jira API token: Zephyr has its own API host.",
            "Both are required — test cases live in Zephyr, stories live in Jira.",
        ],
        # Personal-settings Apps page (where Zephyr's token screen lives). The old
        # Connect servlet path (/plugins/servlet/ac/com.kanoah.test-manager/...)
        # no longer resolves. {site} is substituted at click time by help_topic().
        "url": "{site}/jira/settings/personal/apps",
        "url_label": "Open Zephyr API tokens",
    },
    "xray_client_id": {
        "title": "Xray API Key (Client ID + Secret)",
        "steps": [
            "This opens the Xray API Keys page ON YOUR SITE (Jira admin → Xray).",
            "Create an API Key for your user — you get a Client ID AND a Client Secret.",
            "Copy BOTH: the Client ID goes here, the Secret in the next field.",
            "Xray exchanges them for a 24h token; QA Studio does that for you.",
        ],
        "url": "{site}/plugins/servlet/ac/com.xpandit.plugins.xray/xray-global-settings-api-keys?s=com.xpandit.plugins.xray__xray-global-settings-api-keys",
        "url_label": "Open Xray API keys",
    },
}
# Client Secret shares the API-Key help (they're created together).
HELP["xray_client_secret"] = dict(HELP["xray_client_id"])

HELP["testrail_url"] = {
    "title": "TestRail URL",
    "steps": [
        "Your TestRail Cloud address, e.g. https://your-org.testrail.io",
        "It's the host in your browser's address bar when using TestRail.",
        "Must be HTTPS — the API key is sent with every request.",
        "Self-hosted (Server) needs self-hosted mode enabled in Setup.",
    ],
    "url": "https://www.gurock.com/testrail/",
    "url_label": "About TestRail",
}
HELP["testrail_email"] = {
    "title": "TestRail account email",
    "steps": [
        "The email address you sign into TestRail with.",
        "The API key authenticates as this user, with this user's permissions.",
        "A mismatch between email and key owner fails with 401.",
    ],
    "url": "{site_testrail}/index.php?/mysettings",
    "url_label": "Open TestRail My Settings",
}
HELP["testrail_key"] = {
    "title": "TestRail API key",
    "steps": [
        "First enable the API: Administration → Site Settings → API → Enable API (Save).",
        "Then this opens My Settings → API Keys ON YOUR INSTANCE.",
        "Click 'Add Key', copy it, then 'Save Configuration'.",
        "This is NOT your TestRail password — it's a separate key.",
    ],
    "url": "{site_testrail}/index.php?/mysettings",
    "url_label": "Open TestRail API keys",
}


def help_topic(key, creds=None):
    """Return the help topic for `key` with any `{site}` placeholder in its URL
    filled from the saved jira_site — or None if it's not a backend_setup topic.

    This is what makes the token/key buttons open the REAL generation page on
    the user's own site (Zephyr/Xray token pages are site-specific) instead of a
    generic docs/admin page. main._show_help falls back to this before its own
    static HELP dict.
    """
    topic = HELP.get(key)
    if not topic:
        return None
    site = ""
    try:
        site = (str((creds or {}).get("jira_site") or "")).strip().rstrip("/")
    except Exception:
        site = ""
    tr = ""
    try:
        tr = (str((creds or {}).get("testrail_url") or "")).strip().rstrip("/")
    except Exception:
        tr = ""
    out = dict(topic)
    url = out.get("url") or ""
    if "{site}" in url:
        # No site saved yet → fall back to the Atlassian admin landing so the
        # button is never a dead/blank link.
        out["url"] = url.replace("{site}", site) if site else "https://admin.atlassian.com"
    elif "{site_testrail}" in url:
        out["url"] = url.replace("{site_testrail}", tr) if tr else "https://www.gurock.com/testrail/"
    return out


__all__ = [
    "AZURE", "JIRA_ZEPHYR", "XRAY", "TESTRAIL", "BACKENDS", "HELP",
    "defaults", "active", "is_azure", "label_for",
    "validate_for_connect", "get_backend", "apply_credentials",
    "picker_rows", "jira_rows", "xray_rows", "testrail_rows", "help_topic",
]
