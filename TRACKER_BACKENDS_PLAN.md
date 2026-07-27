# Tracker & Test-Management Backends — one switch point, many tools

**Goal:** a single **switch point** so QA Studio can run its full feature set against several test-management systems, user-selectable per connection. Azure DevOps stays fully supported; Jira + Zephyr Scale is the first added target; Xray and TestRail are planned. All share one `tracker/` adapter boundary so adding a tool never touches callers.

**v1 scope (agreed):** full parity — core generation loop, test plans & suite structure, Task Manager & tester assignment, and sprint closure reports & email.

---

## 0. Test-Management Backends (canonical list)

This is the **single source of truth** for which systems QA Studio targets and their status. Every other section refers back here rather than re-listing — one place, no duplication.

Each backend is one implementation of the `Backend` ABC (§2). "Read reuse" = how much of the already live-verified Jira read path (projects, sprints, stories, ADF) it inherits.

| # | Backend | Kind | Test cases live in | Status | Read reuse | Notes |
|---|---|---|---|---|---|---|
| 1 | **Azure DevOps** | ALM | ADO Test Plans (suite→case, steps XML) | 🟢 **Live-verified** (read + generation + Stop) | — (native) | The original. `AzureBackend` wraps `engine.py`. |
| 2 | **Jira + Zephyr Scale** | Jira-native | Zephyr folders / test cases / steps JSON | 🟡 read **live-verified**; write **stub-only** (blocked on Zephyr install) | full | Primary added target. Cloud first; DC/Server + Squad can slot in later. |
| 3 | **Xray** | Jira-native | Jira issues (Test type) + Xray REST/GraphQL | 🔵 **Planned** | full | Biggest Zephyr rival. Reuses the entire Jira read path; only the write target differs. Strongest API of the Jira-native group (REST **and** GraphQL). Lowest-effort next backend. |
| 4 | **TestRail** | Standalone | TestRail sections (folders) + cases + steps | 🔵 **Planned** | partial | Independent of Jira: still pulls *requirements* from Jira/Azure, but writes into its own project→section→case model. More work than Xray (own suite hierarchy), but the most-requested standalone tool; well-documented REST API. |

**Explicitly out of scope (decided):** "Jira standalone" (plain Jira, no test-management add-on). Jira has no native test-case object, so it would mean repurposing generic issues/sub-tasks — a lower-fidelity path with no structured steps, execution status, or test cycles. Dropped in favor of doing the real test-management tools well.

### Mobile

The multi-backend feature is **platform-agnostic**: the mobile APK runs the same `main.py` Setup screen and the same `tracker/` + `backend_setup` code, so Azure / Jira+Zephyr / (future) Xray / TestRail all work on mobile with **no mobile-specific code** — the switch, credential fields, and adapters are shared. But mobile has three things that are NOT automatic and must be actively remembered:

1. **Packaging.** `tracker/` is a package DIRECTORY. The APK staging (`build-apk.yml`) and the dev sync (`_sync_to_install.py`) both historically copied only top-level `*.py`, so the package was silently omitted → `import tracker` crash on the Jira path (Azure phones wouldn't notice, since Azure never imports `tracker`). Both fixed to stage `tracker/` explicitly. **Any future package dir must be added to both.** (The `install.bat` release path uses `robocopy /E` and is already recursive.)
2. **Credential storage.** Mobile persists creds via `secure_store_mobile.py` (OS keychain), not the desktop file. `store.py`'s mobile defaults path was patched to seed the new per-backend keys, but the new `jira_site`/`jira_token`/`zephyr_token` values have **not been verified to round-trip through the keychain on a real device.**
3. **Layout.** The new Setup "Test Management" section avoids fixed widths (only the 34px icon chip; all fields `expand=True`), so it *should* reflow on a narrow phone — but per this repo's long history of mobile layout bugs, that is **verify-on-device, not confirmed.**

None of the desktop-only gates (idle-logout, window chrome, remote runs) interact with the backend switch.

**Ordering rationale:** Jira-native tools (#2, #3) are cheapest because they inherit the live-verified Jira read path — a new backend is just a new *write* implementation against the same ABC. Standalone tools (#4) must additionally rebuild the folder/suite layer. So the build order is Zephyr → Xray → TestRail, not by popularity.

---

## 1. The problem

There is no seam. Azure DevOps isn't *a* backend — it's baked into the app's vocabulary at every layer:

| Coupling | Evidence |
|---|---|
| Global org | `AZURE_ORG` module global, **66 references across 5 files** — `engine.py`, `main.py`, `regression.py`, `report.py`, `run_worker.py`. *(The hardcoded customer-org **default** is now removed — see Phase 0 progress below. The global and its 66 call sites still need to move behind the adapter.)* |
| Raw ADO REST | 30 `_apis/…` URL builds in `engine.py`, all via `_azure_get` + PAT basic-auth |
| ADO Python SDK | `_wit_client` / `connect_azure_sdk` — `azure-devops==7.1.0b4` + `azure-core==1.41.0` in `requirements.txt`; used for all work-item CRUD (`create_work_item`, `update_work_item`, `get_work_item`) while test-plan/WIQL calls go over raw HTTP |
| ADO field names in AI code | `Microsoft.VSTS.TCM.Steps`, `Microsoft.VSTS.Common.AcceptanceCriteria`, `System.Title` reach as far as `evaluate_existing_steps(tc_title, criteria, existing_steps_xml, …)` — an **AI** function that takes ADO **XML** |
| ADO data shapes | WIQL queries, iteration *paths* (`\Project\Sprint 3`), steps XML, `_workitems/edit/{id}` URLs |

~40 ADO-facing functions in `engine.py` are called directly by the UI. Any backend work that doesn't first create a seam will fork the whole app.

**The good news:** this codebase already solved this exact shape of problem once. `PROVIDERS_REFACTOR_PLAN.md` collapsed 6 AI vendor SDKs onto one normalized adapter layer, incrementally, behind a flag, with zero public-behavior change — and finished. **This plan reuses that proven pattern.** Two useful seams already exist: `parse_steps_xml()` / `build_steps_xml()` (engine.py:2593/2989) are already the boundary between "steps as data" and "steps as ADO XML".

---

## 2. Target architecture

A `tracker/` package. Callers ask the registry for the active backend and talk to it in **normalized DTOs only** — no caller ever sees an ADO work item or a Zephyr test case.

```
tracker/
  __init__.py     # get_backend() -> Backend      ← THE SWITCH POINT
  base.py         # Backend ABC + capability flags
  models.py       # normalized DTOs (frozen dataclasses)
  http.py         # shared session: retry, 429/backoff, pagination, timeouts
  azure.py        # existing ADO code, moved ~verbatim
  jira_zephyr.py  # new: Jira (issues/sprints/users) + Zephyr Scale (cases/steps/cycles)
  fake.py         # in-memory backend for tests (no network)
```

```
UI  (main.py, regression.py, report.py, run_worker.py)
        │  normalized DTOs only
        ▼
engine.py  generation/dedupe/report logic — BACKEND-AGNOSTIC
        │
        ▼
tracker.get_backend()          ← reads creds["backend"]: "azure" | "jira_zephyr"
        │
        ├── AzureBackend       → dev.azure.com REST
        └── JiraZephyrBackend  → {site}.atlassian.net  +  api.zephyrscale.smartbear.com/v2
```

**The design rule that makes this worth doing:** every AI/generation function (`run_titles`, `run_steps`, `generate_titles`, `dedupe_*`, `compile_test_case`, `evaluate_existing_steps`) must operate on DTOs and never on vendor payloads. Today `evaluate_existing_steps` takes raw XML; that's the canonical violation to fix.

### Normalized DTOs (`tracker/models.py`)

```python
@dataclass(frozen=True)
class Project:   id: str; key: str; name: str
@dataclass(frozen=True)
class Sprint:    id: str; name: str; state: str; start: date|None; end: date|None; raw_ref: str
@dataclass(frozen=True)
class Story:     id: str; key: str; title: str; description: str  # normalized to HTML
                 acceptance_criteria: str; assignee: User|None; url: str
@dataclass(frozen=True)
class Step:      index: int; action: str; expected: str; data: str = ""
@dataclass(frozen=True)
class TestCase:  id: str; key: str; title: str; steps: list[Step]; suite_ref: str; story_ref: str|None
@dataclass(frozen=True)
class Suite:     id: str; name: str; parent_id: str|None; story_ref: str|None
```

`Step` is the important one: it kills the XML leak. Azure serializes it via the existing `build_steps_xml`/`parse_steps_xml`; Zephyr maps it straight onto its native JSON steps.

### The `Backend` interface

Derived from the actual call sites in `engine.py`, grouped:

```python
class Backend(ABC):
    name: str; capabilities: set[str]

    # connection
    def validate_credentials(self) -> None      # raises with a user-facing message
    def fetch_projects(self) -> list[Project]

    # planning / read
    def fetch_sprints(self, project) -> list[Sprint]
    def fetch_stories_in_sprint(self, project, sprint) -> list[Story]
    def fetch_stories(self, ids) -> list[Story]
    def fetch_story_screenshots(self, story) -> list[bytes]
    def resolve_ac_links(self, story) -> str

    # structure  (ADO plan/suite  ⇄  Zephyr folders/cycles)
    def fetch_test_plans(self, project) -> list[Plan]
    def create_test_plan(self, project, name, sprint) -> Plan
    def ensure_suite_for_story(self, project, plan, story) -> Suite
    def discover_suites_for_stories(self, project, plan, story_ids) -> dict

    # test cases
    def fetch_test_cases_for_suite(self, project, plan, suite) -> list[TestCase]
    def fetch_existing_titles_for_suite(self, project, plan, suite) -> list[str]
    def create_test_case(self, project, plan, suite, title, story) -> TestCase
    def update_test_case_steps(self, tc_ref, steps: list[Step]) -> None
    def fetch_test_case_steps(self, tc_ref) -> list[Step]
    def delete_test_case(self, project, plan, suite, tc_ref) -> None
    def count_test_cases(...); def count_existing_steps(...)

    # people / tasks   (Task Manager)
    def fetch_project_members(self, project) -> list[User]
    def tester_field_options(self, project) -> list[str]
    def assign_testers(self, assignments) -> None
    def create_child_tasks(self, project, items) -> list[str]
    def fetch_user_task_stats(self, project, sprint=None, assignee=None) -> TaskStats

    # reporting
    def sprint_report_data(self, project, sprint) -> ReportData
    def sprint_summary(self, project, plan) -> Summary

    # misc
    def item_url(self, item_id) -> str
```

**Capability flags** handle honest asymmetry rather than pretending the products are identical — e.g. `"requirement_suites"`, `"child_tasks"`, `"tester_field"`, `"execution_status"`. The UI greys out what a backend can't do instead of failing at runtime.

---

## 3. Domain mapping

Zephyr Scale rows below are **confirmed** against the live Zephyr Scale Cloud API contract (field names, folder types, step shape, key formats). Jira rows are standard Cloud REST v3 and should be **confirmed against docs during Phase 2** — they're the lower-risk half.

| QA Studio concept (ADO today) | Jira + Zephyr Scale Cloud |
|---|---|
| `AZURE_ORG` | Jira site base URL (`https://{site}.atlassian.net`) |
| Project (by **name**) | Jira project by **`projectKey`** — *note the identifier change* |
| Iteration node tree (`/wit/classificationnodes/iterations`, `$depth=10`) | Jira **Sprint** via Agile API (`/rest/agile/1.0/board/{id}/sprint`) — **requires board discovery first**; or Jira Version (`jiraProjectVersion`) |
| User Story via **WIQL** | Jira issue via **JQL** (`/rest/api/3/search`) |
| `Microsoft.VSTS.Common.AcceptanceCriteria` (HTML) | Jira description / custom field — **ADF JSON on Cloud**, needs a renderer |
| Test Plan (`/testplan/plans`) | **No direct equivalent.** Root `TEST_CASE` folder (+ a `TEST_CYCLE` for execution) — see §4 |
| Test Suite (requirement-based, per story) | `TEST_CASE` **sub-folder** per story + `createTestCaseIssueLink` for traceability |
| Test Case | Zephyr test case, key format `[A-Z]+-T[0-9]+` |
| Steps XML (`Microsoft.VSTS.TCM.Steps`) | Zephyr steps resource: `{inline:{description, expectedResult, testData}}`, mode `APPEND`\|`OVERWRITE` |
| Child tasks | Jira **sub-tasks** (issuetype subtask + `parent`) |
| `Assigned To Tester` custom field | Jira `customfield_XXXXX` (discover via `/rest/api/3/field`) or Zephyr case `ownerId` |
| Project members / teams | `/rest/api/3/user/assignable/search` or project role actors |
| Test outcome / execution status | Zephyr **test executions** + `/statuses` |
| Story attachments (screenshots) | Jira issue attachments |
| `…/_workitems/edit/{id}` | `{site}/browse/{KEY}` |

Useful confirmed detail: Zephyr multi-line text fields **accept HTML** (newlines as `<br>`), so the existing HTML-shaped AC/objective content survives the crossing without a lossy conversion.

---

## 4. The four decisions that need making before coding

These are genuine product choices, not implementation details. Recommendations given, but they're yours.

**D1 — What is a "Test Plan" in Zephyr?** Zephyr Scale has folder trees (typed `TEST_CASE` / `TEST_PLAN` / `TEST_CYCLE`), test cycles, and a native Test Plan object. ADO's plan→suite→case tree has no 1:1 match.
> **Recommend:** map plan → a root **`TEST_CASE` folder** named after the plan, suite → a **child folder per story**. That preserves the existing tree shape and the "regenerate into the same suite" behaviour the dedupe logic depends on. Additionally create a **`TEST_CYCLE`** per sprint when reports need execution status. Rejected alternative: plan → test cycle (cycles are execution runs, not a case library — re-running generation would multiply cycles).

**D2 — Traceability: folder-only, or issue links?**
> **Recommend both.** Folder-per-story gives structure; `createTestCaseIssueLink(testCaseKey, issueId)` gives real Jira-side traceability and is what makes coverage reporting work. Costs one extra call per created case.

**D3 — Sprint source: Agile board sprints, or Jira versions?** Board sprints match ADO iterations semantically, but a project can have several boards and the sprint field ID varies per site.
> **Recommend:** board sprints, with a **board picker in Setup** when a project has >1 board (cache the choice). Versions as a documented fallback for teams not using boards.

**D4 — Where do the two tokens live?** Jira Cloud needs `email + API token` (Basic); Zephyr Scale Cloud needs a **separate bearer token** against a **different host**.
> **Recommend:** a per-backend credential block in `store.py` + the Supabase `user_credentials` vault, with the same "values invisible by design" handling the ADO PAT already gets.

---

## 4b. Progress (updated 2026-07-22)

Backend numbers below refer to §0's canonical list.

| Phase | Status | Evidence |
|---|---|---|
| 0 — seam extraction | 🟢 **Done (UI layer)** | `tracker/` landed; `azure.py` wraps `engine.py`. **Azure read path LIVE-VERIFIED 14/14** (#107). Step 3b: display URLs + sprint/story loading routed (#111); **#122 finished the UI layer** — `modals` create-plan, `regression` plan-stories/meta/features/suite-map/members, `main` automation discovery, `task_manager` members/stats/child-tasks all routed or Azure-guarded; new `reads_stories_from_azure()` fixes the hybrid story-source class of bug. Remaining refs are Azure-guarded or genuinely Azure-only (PAT help text, remote-run sync). |
| 1 — switch point | 🟢 **Done** | Setup backend picker + per-backend creds + help (#104); connect/plan/sprint/story loading routed (#105–106, #111). Legacy creds default to `azure`. **Renders live.** |
| 2 — Jira read path (#2) | 🟢 **LIVE-verified** | Against `qastudio.atlassian.net` (#112): auth, projects, board discovery, sprints, `/search/jql`, `_to_story`, ADF. Found + fixed 2 live-only bugs (removed search API; board discovery). |
| 3 — Zephyr structure (#2) | 🟡 **Stub-verified** | D1 folders, idempotent `ensure_suite_for_story`, D2 links. `run_zephyr_probe.py` ready; blocked on Zephyr install. |
| 4 — Zephyr write path (#2) | 🟡 **Stub-verified** | `create_test_case`, steps `OVERWRITE`, titles, delete. Same probe gate. |
| — generation core | 🟢 **Done + Azure-verified** | `engine.run_titles`/`run_steps` now take `ops=None`; 15 Azure calls routed through an injectable seam (#108). `verify_ops.py` proves the default path is identical for Azure; Azure generation confirmed working in-app. `backend_setup.generation_ops` implements the Jira/Zephyr side. |
| — Azure write path | 🔴 **Unverified** | `create_test_plan`/`ensure_suite`/`create_test_case`/steps/`delete` never run live. Read path found 3 bugs — expect bugs. Needs scratch project + `run_contract.py` (no `--read-only`). |
| 5 — Task Manager (#2) | 🟢 **Routed (unverified live)** | Adapter methods both sides; **#122 routed all three UI call sites** through new `backend_setup.fetch_project_members` / `fetch_task_stats` / `create_child_tasks` (Azure unchanged, non-Azure via adapter). Known gap: the tracker `TaskStats` DTO is a per-assignee ROLLUP with no per-task rows, so non-Azure returns `partial=True` + `unsupported_detail` and the UI shows an amber note instead of an empty table. A `fetch_tasks`-style interface method would close it. |
| 6 — Reports (#2) | 🟡 **Partial** | `sprint_report_data` rolls up Zephyr executions; email/PDF path routed for URLs (#111). |
| 7 — Hardening | 🟡 **Partial** | `http.py` throttle + 429 backoff + timeouts; **security suite 10/10**. Live rate-limit tuning outstanding. |
| 8 — Xray (#3) | 🟡 **Stub-verified** | `tracker/xray.py` — subclasses `JiraZephyrBackend`, **inherits 7/7 Jira read methods, overrides 9/9 writes** (no Zephyr path reachable). Client-id/secret → 24h bearer; tests via GraphQL `createTest`/`addTestStep`; Test Plan/Test Set as Jira issues; coverage via a "Test" issue link. Full write path passes stub-driven test (idempotent Test Set, step round-trip incl. testData, coverage link, delete). `run_xray_probe.py` ready. **Never run against a live Xray.** |
| 9 — TestRail (#4) | 🟢 **LIVE-VERIFIED (write path)** | `tracker/testrail.py` — standalone `Backend`. **6/6 against a real trial instance** (`qastudio.testrail.io`): suite→section(idempotent)→case→steps OVERWRITE→list→delete. Live bug found+fixed (steps-template discovery). First live write path of ANY backend. Read side (stories) is N/A — TestRail has none; it's a write target. **Setup UI rows + requirement-source pairing are DONE** (verified #122): `testrail_rows` / `hybrid_rows` exist, are branched correctly from `main.py`, and `hybrid_rows` renders the story-source ↔ test-target pairing notes. Standalone TestRail is now hidden from the picker (#119) since it has no story source — reach it via the Azure→/Jira→TestRail hybrids. |

**Security gate:** `python -m tracker.security` — run after every phase. **Parity gate:** `python -m tracker.contract <backend> [project]`, or the live probes (`run_contract.py`, `run_jira_probe.py`, `run_zephyr_probe.py`).

**D1–D4 are implemented as the recommended defaults** (see `jira_zephyr.py` module docstring). They remain reversible: each is confined to a single method.

---

## 5. Phasing

Phased by **risk**, not by feature. Phase 0 is the whole bet: it ships with **zero behaviour change** and is provable by the existing app continuing to work.

### Phase 0 — Extract the seam (Azure only, no new backend)
The largest and most valuable phase. If this is done well, everything after it is additive.

1. Create `tracker/` with `base.py`, `models.py`, `http.py`, `fake.py`.
2. Move existing ADO code into `tracker/azure.py` **verbatim**, wrapped to return DTOs.
3. Kill the `AZURE_ORG` global — 66 refs across 5 files become `backend.item_url(...)` / injected config. *(Do this as its own commit; it's mechanical but wide.)*
   - ✅ **Step 3a DONE (2026-07-22)** — removed the hardcoded `"worldofsystemsmyportal"` default; `AZURE_ORG` now defaults to blank (env-seedable via `AZURE_ORG` for headless/CI), and a new `_require_org()` raises a clear "configure Setup → Azure Organization" error at the two choke points (`_azure_get`, covering all 30 read sites; `connect_azure_sdk`, covering the SDK write path). No behaviour change for configured users — the org was **already** supplied per-account from saved creds. This is the prerequisite for step 3b: with no implicit default, nothing can silently depend on a global org value.
   - ⬜ **Step 3b TODO** — move the remaining 66 refs behind the backend (`backend.item_url()` / injected connection config) and delete the global.
     - ⚠️ **Hybrid double-source bug (found in live testing 2026-07-24, see roadmap #120).** On **Azure→TestRail**, the run **double-sources** a single story: `run_steps`/`run_titles` build `story_suite_map = _o.discover_suites(...)` using the **Azure** `discover_suites_for_stories` AND the run writes/reads the TestRail suite, so `total = len(suite_test_cases)` counts BOTH (34 Azure + 34 TestRail = 68), the Run UI shows two passes over the same "suite 44" (the TestRail write-target id leaking in), and `per_story_stats` (keyed by `sid`, engine.py:4627) lists the same story twice. Report item links use Azure-centric `_wi_url`/hardcoded plan URL (engine.py:5774/5872), not a backend-aware `item_url`. **Fix under Step 3b:** for hybrids, discovery/count/links must go through the SINGLE active backend (one suite per story), normalize+dedupe the `sid` key, and use `backend.item_url()`/`plan_url()`. Gate on the Azure regression (`verify_ops.py` + `run_contract.py`).
4. Fix the XML leak: `evaluate_existing_steps` and friends take `list[Step]`, not XML. `parse_steps_xml`/`build_steps_xml` become Azure-internal.
5. **Collapse the ADO SDK to raw HTTP** while the code is already open — same reasoning that drove `PROVIDERS_REFACTOR_PLAN.md` (drops `azure-devops` + `azure-core`, removes a Python-version-fragile dep, one place to own retries). ~8 `_wit_client` call sites.
6. `get_backend()` returns `AzureBackend` unconditionally.

**Exit criteria:** app is byte-for-byte behaviourally identical; no file outside `tracker/` mentions `dev.azure.com`, `Microsoft.VSTS.*`, or `_apis/`.

### Phase 1 — The switch point
Backend registry; `creds["backend"]` (defaulting to `"azure"` so **every existing user is untouched**); Setup-screen backend picker + per-backend credential form; capability-flag-driven UI enable/disable; migration for existing stored creds.

### Phase 2 — Jira read path
Auth + `validate_credentials`; projects; board discovery + sprints (D3); JQL story fetch; **ADF→HTML normalizer**; AC field discovery; attachments. *Read-only — safe to test against a real project.*

### Phase 3 — Zephyr structure
Folder tree per D1; `ensure_suite_for_story`; plan/suite listing; issue links per D2.

### Phase 4 — Zephyr write path + generation loop
`create_test_case`; steps via `APPEND`/`OVERWRITE`; existing-title fetch for dedupe; delete; counts. **At the end of this phase the core value prop works on Jira.**

### Phase 5 — Task Manager & tester assignment
Sub-tasks; member/assignable-user search; tester custom-field discovery; `fetch_user_task_stats` via JQL.

### Phase 6 — Reports & email
`sprint_report_data` / `sprint_summary` from Zephyr executions + Jira sprint data; reuse the existing PDF/email path unchanged (it should already be DTO-fed after Phase 0).

### Phase 7 — Hardening
Shared 429/backoff throttle; contract-test every backend; docs; `README` + `DEV_ROADMAP` entries.

### Phase 8 — Xray backend (backend #3)
Second Jira-native tool, and the payoff of the seam: **almost no new read code.** Xray stores tests as Jira issues, so `XrayBackend` reuses the live-verified Jira read path (projects, sprints, stories, ADF, JQL, the `/search/jql` migration) wholesale and implements only the write half against the Xray API.
- Auth: Xray Cloud uses a **client-id/client-secret → bearer token** exchange against `xray.cloud.getxray.com` (distinct from the Jira token). Add its own credential block.
- Test cases → Jira issues of the **Test** issue type; steps via Xray's `importTestStructure` / GraphQL `createTest` (structured step fields: action / data / result — a close map to `Step`, so likely `Capability.STEP_TEST_DATA` = true, unlike Azure).
- Suites → **Test Sets** (or Test Plans); requirement coverage → the native `tests`/`defect` links (D2 analogue, cleaner than Zephyr's since it's first-class in Jira).
- Run the contract suite against a real Xray trial before enabling — same live-verification gate that caught 3 Azure and 2 Jira bugs.

### Phase 9 — TestRail backend (backend #4)
First **standalone** tool — the seam's harder case, because reads don't come for free. TestRail has no Jira stories of its own; requirements are still pulled from Jira/Azure, but cases are written into TestRail's own hierarchy.
- Auth: TestRail API key + base URL (`https://<org>.testrail.io`), Basic auth. New credential block; **SSRF-guard the base URL** exactly like the Jira site field.
- Project → TestRail **project**; suite/folder → **sections** (nested); case → **case** with a `custom_steps_separated` structured-steps field (content/expected per row — good `Step` fit).
- No Jira-style issue key on cases; `Ref.id` is the numeric case id. Requirement traceability is a case **reference** field pointing back at the Jira/ADO story key.
- Pagination is offset-based (`limit`/`offset`) — different again from Jira's `nextPageToken` and Zephyr's `startAtId`; `tracker/http.py`'s `paginate()` cursor strategy must cover it.
- Capability flags advertise the honest gaps (its execution model differs from Jira's; Task Manager / tester-assignment map poorly).

---

## 6. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | **ADF (Atlassian Document Format).** Jira Cloud descriptions/AC are ADF JSON, not HTML — and *every AI prompt consumes AC text*. Routinely underestimated. | Write and unit-test an `adf_to_html()` / `adf_to_text()` renderer **early in Phase 2**, against real issues. Budget real time. Fallback: request `renderedFields` from the API. |
| 2 | **Rate limits.** Jira Cloud and Zephyr Scale Cloud throttle far harder than ADO — and `run_titles`/`run_steps` run **worker pools** issuing bulk calls. | Centralize in `tracker/http.py`: shared session, token-bucket throttle, honour `Retry-After`, exponential backoff on 429. Make worker count backend-aware. |
| 3 | **ID vs Key duality.** Jira issues have numeric `id` *and* `KEY-123`; `createTestCaseIssueLink` needs the **numeric `issueId`** while everything user-facing is a key. Recurring bug source. | DTOs carry **both** (`id` and `key`) from day one. Never derive one from the other ad hoc. |
| 4 | **Steps round-trip fidelity.** Dedupe/evaluate compare existing steps; XML↔`Step`↔Zephyr JSON must be lossless. | Property test: `parse(build(steps)) == steps` for both backends, incl. HTML entities and empty expected-results. |
| 5 | **Sprint field varies per site.** The Jira sprint custom field ID isn't stable across instances. | Discover via `/rest/api/3/field` and cache per connection; never hardcode `customfield_100xx`. |
| 6 | **Pagination models differ** — ADO continuation tokens, Jira `startAt`, Zephyr `startAtId`+`limit` (max 1000). | One `paginate()` helper in `tracker/http.py`; backends supply a cursor strategy. |
| 7 | **Phase 0 is a wide diff in fragile files.** `main.py` (8.2k lines) and `engine.py` (10.4k) are already flagged as the app's most fragile — see cont'd #98's tech-debt note. | Land Phase 0 in small, individually-verifiable commits (move → DTO-ize → de-global → SDK-collapse). Sync + full relaunch between each (loose-`.py` install). |
| 8 | **Zephyr flavor still undecided.** Scale Cloud vs DC vs Squad have materially different APIs. | The `Backend` ABC is the insurance. Confirm before Phase 3 — Squad in particular would force D1 to be revisited (no folder tree). |

---

## 7. Testing

Mirrors the harness style already used in this repo (`drive_race.py`, `drive_watchdog.py` — pure-logic, no network):

- **`tracker/fake.py`** — an in-memory `Backend`. Lets `run_titles`, `run_steps`, and the whole dedupe suite be tested with **zero network**, which is a standalone win regardless of Jira. Build it in Phase 0.
- **Contract suite** — one test file run against *every* backend (`fake`, `azure`, `jira_zephyr`): create suite → create case → write steps → read back → dedupe → delete. This is what actually proves "parity".
- **Round-trip property tests** for `Step` serialization (risk #4).
- **ADF renderer unit tests** against captured real payloads (risk #1).
- **Live smoke test** per backend before each phase closes.
- Re-run the existing dedupe/race/watchdog suites after Phase 0 to prove no regression.

---

## 8. Effort shape

Not calendar estimates — relative weight, to guide sequencing:

| Phase | Weight | Note |
|---|---|---|
| 0 — seam extraction | **XL** | The bet. ~60% of total work. Zero user-visible change. |
| 1 — switch point | S | Mostly UI + creds |
| 2 — Jira read | **L** | ADF renderer + board/sprint discovery are the cost |
| 3 — Zephyr structure | M | Blocked on D1 |
| 4 — Zephyr write | M | Core value lands here |
| 5 — Task Manager | M | |
| 6 — Reports | M | Cheap *if* Phase 0 was done properly |
| 7 — hardening | S–M | |
| 8 — Xray (#3) | **S–M** | Write half only — the whole read path is inherited and live-verified. This is the dividend the seam was built to pay. |
| 9 — TestRail (#4) | **M–L** | Standalone: must rebuild the project→section→case hierarchy and offset pagination. Reads still come from Jira/Azure, but nothing test-side is inherited. |

**Strong recommendation:** do **not** start Phase 2 before Phase 0 is complete and the app is verified working on Azure through the new seam. Building the Jira adapter against a half-extracted interface is how this becomes a permanent fork. The same rule applies to #3/#4: don't start Xray or TestRail until Jira+Zephyr is live-verified end-to-end — they're meant to *reuse* a proven read path, not debug it in parallel.

---

## 9. Bottom line

The work isn't "integrate Jira" — it's **"stop being an Azure DevOps app internally."** Once `engine.py`'s generation logic speaks normalized DTOs, each test-management tool (§0) is just one more implementation of the `Backend` ABC. The seam's whole payoff is visible in the effort table: Jira+Zephyr is Large, but **Xray after it is Small-to-Medium** — it inherits an already live-verified read path and writes only. TestRail costs more only because, as a standalone tool, its test-side hierarchy can't be inherited. Every tool added after the first Jira-native one gets cheaper, not more expensive.

This codebase already ran this exact play with AI providers (`PROVIDERS_REFACTOR_PLAN.md`). Same pattern, bigger surface.

**Immediate next step:** finish live-verifying Jira+Zephyr (write path — `run_zephyr_probe.py`, blocked only on the Zephyr install). Xray (§5 Phase 8) is the first backend to build once that's green.
