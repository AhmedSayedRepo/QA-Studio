---
name: qa-studio
description: >-
  Use whenever working on QA Studio — a Flet desktop app (Python) that generates
  test cases (titles + steps) via AI for Azure DevOps, Jira (+Xray / Zephyr Scale)
  and TestRail, with a live Selenium explorer that captures locators and builds
  self-healing Selenium/TestNG, Playwright or Cypress projects, plus Regression
  and Sprint planning, performance testing, failure analysis and AI-usage
  tracking. Trigger when editing engine.py, main.py, regression.py, automation*.py,
  tracker/, perf/ or failure_analysis/, deploying/pushing, bumping VERSION,
  touching AI providers, the explorer, generated-test templates, or discussing
  QA Studio internals. Covers source-of-truth and git rules, the deploy ritual,
  the VERSION encoding gotcha, module map, provider config, locator taxonomy and
  RTL output rules.
---

# QA Studio — project context

A Flet desktop app (Python, Windows; Android build exists) that generates test
cases via AI and writes them into the connected tracker, and uses a live Selenium
explorer (logs into Keycloak) to capture exact locators per step, then generates
self-healing automation projects.

- Repo: `AhmedSayedRepo/QA-Studio`, branch `main` is the source of truth.
- Dev folder (Windows): `D:\qa-studio`.
- **Read the `VERSION` file each session** — never trust a number written here.

## Source-of-truth and git rules (important)

Other chats/editors modify the same files. **Always read the current files
before editing — never edit from memory.** When the user uploads a file, treat
THAT as the baseline and preserve their hand-edits.

Before any edit session:
1. `cd /d D:\qa-studio` — in cmd.exe a plain `cd D:\...` from `C:` does NOT
   switch drives (git then reports "not a git repository").
2. `git status` FIRST. If there are uncommitted changes, park them on their own
   branch (`git switch -c feat/<name>` → add → commit). **Never run
   `git reset --hard origin/main` with uncommitted work** — it destroys it.
3. `git switch main && git pull --ff-only`.
4. Do new work on a feature branch; one editor at a time.

Comparing a local copy with GitHub: Windows checkouts use CRLF, so compare with
line endings stripped (or `git diff`), otherwise every file looks changed.

While the folder is linked to the Claude desktop app, Windows may refuse to
delete/unlink files in it ("Unlink of file … failed", "Access is denied").
Answer `n` to git's retry prompt — the switch still completes and the file is
left untracked; delete it later after closing whatever holds it.

## Module map (approximate sizes — they grow)

Core:
- `engine.py` (~12.5k lines) — all non-UI logic: tracker REST, AI
  (`ai_complete`, usage/cost tracking), title/step generation + dedupe,
  `run_titles`/`run_steps`, the Selenium explorer (`explore_and_map`), sequencing
  (`validate_and_sequence_suite`), the **Java/TestNG self-healing templates**
  (`_sh_*`, `generate_selfhealing_test_class`, `build_selfhealing_project`),
  git push, email reports, the self-updater.
- `main.py` (~10.5k) — Flet UI, one big stateful `QAStudio` class (Setup / Run /
  Report and screen routing).
- `regression.py` (~6.5k) — Regression Plan + Sprint Plan screens, effort
  weighting, resource assignment, `_plan_html`, JSON/XLSX/DOCX/PDF exporters,
  shared widgets (`_checkbox_multiselect`, `locked_state`, `_auto_init`).
- `strings.py` — all UI strings (`strings.t(key)`), multi-language.
- `theme.py` (tokens `T.*`), `ui.py` (buttons/cards), `dialogs.py`, `modals.py`.
  Don't invent token names — check `theme.py` first (a missing `T.CYAN` crashed
  the release notice once).

Automation:
- `automation.py` — Automation screen (extracted from main.py).
- `automation_targets.py` — Playwright and Cypress project builders, each with
  its own JS self-healing `Healer`.
- `automation_file_source.py` — CSV/XLSX test-case source (no tracker needed).
- `failure_analysis/` — deterministic diagnostics registry (see
  `FAILURE_ANALYSIS.md`); only `TimeoutAnalyzer` exists so far.
- `run_worker.py`, `remote_runs_screen.py`, `run_contract.py` — remote runs
  (`REMOTE_RUNS.md`).

Trackers / backends:
- `tracker/` — `azure`, `jira_zephyr`, `xray`, `testrail`, `composite` (hybrid
  read-from/write-to), `contract.py`, `security.py`, `fake.py`.
- `backend_setup.py` — backend picker + connection forms (permission-filtered).
- `run_*_probe.py` — manual connectivity probes per backend.

Other:
- `performance.py`, `perf_ui.py`, `perf/` — performance testing (JMeter etc.,
  `ADR-003`, `PERF_ARCH_PLAN.md`).
- `auth_supabase.py`, `login.py`, `orgs_screen.py`, `users_screen.py`,
  `audit_screen.py`, `ai_usage_screen.py`, `supabase/` — accounts, orgs, roles,
  audit log, AI usage/cost (`ADMIN_USERS_SETUP.md`, `TENANT_ADMIN_SETUP.md`).
- `task_manager.py`, `sprint_titles.py` — task and sprint-title screens.
- `store.py` — local credential vault. `settings.py`, `help_guide.py`,
  `useful_links.py`, `updater_ui.py`, `idle_watch.py`, `window_chrome.py`.
- `mobile_*`, `secure_store_mobile.py`, `mobile_ext/` — Android build.
- Packaging/deploy: `installer.py`, `install.bat`, `build.bat`, `release.bat`,
  `push.ps1`, `release_notes.py`, `_sync_to_install.py`.
- Plans/ADRs: `ADR-001..003`, `*_PLAN.md`, `SECURITY_REVIEW.md`,
  `DEV_ROADMAP.md` (huge — grep it, don't read it whole).

## Deploy ritual

Replace files → bump `VERSION` → clean build (only if cutting a new installer:
delete `build/` and `dist/` first) → commit on a branch → merge/push to `main`.

VERSION encoding gotcha (cost real debugging time):
- cmd.exe: `echo 3.9.3> VERSION` — **no space before `>`** (a space writes a
  trailing space into the file).
- PowerShell: do NOT use `>` (writes UTF-16, breaks the UTF-8 updater). Use
  `"3.9.3" | Out-File -FilePath VERSION -Encoding ascii -NoNewline`.

Windows icon cache: after changing the app icon, a same-path reinstall keeps the
old icon. Fix: `ie4uinit.exe -show`, or clear `IconCache.db`/`iconcache*`.

Repo hygiene: keep `*.bak`, `*.log`, `_mount_probe.txt`, `__pycache__/` and
`login_bg_assets*_backup.py` out of commits (check `.gitignore`).

## AI providers

All providers route through `ai_complete()` — provider-agnostic, with
`classify_ai_error`, retry/backoff and per-call usage logging. The Setup
dropdown is built from `AI_CONFIG.keys()`; treat that as the live list (currently
anthropic, openai, gemini, azure_openai, ollama, nvidia, deepseek, qwen, manus,
groq, cerebras, openrouter, mistral, minimax, glm). Check `AI_CONFIG` for
default models rather than trusting this file.

- Known stale defaults to watch: DeepSeek `deepseek-chat`/`deepseek-reasoner`
  aliases were scheduled to deprecate 2026-07-24 (→ `deepseek-v4-flash` /
  `deepseek-v4-pro`); Gemini `gemini-1.5-pro` is a retired generation.
- Qwen: international endpoint
  `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` (correct for Egypt).
- The `vision` flag in `AI_CONFIG` is metadata only.

## Output language

`LANGUAGES` in engine.py (en, ar, fr, tr, es, de, nl); `set_output_lang`,
`out_is_rtl()`, `_is_arabic_out()`. Arabic deliverables are RTL, single
consolidated sheets, alternating row colors, all content in Arabic. Keep
generated steps/titles in the selected language only. Exporters and
`_plan_html` follow the same RTL rules (`_ar()` shapes Arabic in PDF).

## Intent-driven explorer architecture

`explore_and_map` does NOT ask the AI to pick 1-of-N elements per step:
1. `compile_test_case()` — one LLM call per case → typed **intents**
   (`precondition` / `action` / `assertion`) with keywords + `from_steps`.
   Falls back to `_intents_from_raw_steps`.
2. `_rank_candidates()` — deterministic scoring binds an intent to a live element
   (Arabic-normalized via `_norm`). `_tiebreak_with_ai` only breaks ties among a
   ≤5 shortlist — never invents locators.
3. `_act()` — interception-proof: scroll, settle, dismiss overlays,
   `elementFromPoint` check, retry ladder (native → ActionChains → JS).
   Assertions bind by **DOM-diff** (before/after snapshot).

Locator taxonomy — `step["locator_src"]` ∈ `live` | `snapshot` | `guess` |
`precondition`; stats `{"live","snapshot","guess"}` drive the
Live/Snap/Guess/TODO chips. `_classify_case()` → `negative_login` | `presence` |
`interaction`. Unresolved steps feed `failure_analysis` (UNKNOWN unless an
analyzer matches).

## Generated Selenium/TestNG project (engine.py `_sh_*` templates)

- Deterministic Java from intents (no LLM writes Java). One browser per test
  class; cases ordered logged-out → login → app.
- `Healer.find` order: saved `locators.json` → seed locator → AI heal (saves
  back). Wait rules: `BaseTest` keeps **implicit wait at 0**; `find` first waits
  for `document.readyState` (not aria-busy — some apps leave it set); a saved
  locator gets a 2s probe when a seed exists; seed and AI-healed locators get the
  full 12s wait. `hover` uses `Actions.moveToElement`, never a click.
- Template tests: `tests/test_selfhealing_templates.py`,
  `tests/test_automation_compiler.py`. Keep Playwright/Cypress healers in
  `automation_targets.py` behaviorally in step with the Java one.

## Regression & Sprint planning (regression.py)

`build_rows()` + `assign_resources()` — test cases × minutes × priority weight
(`_email_pri`/`_av`), balanced across named resources. Sprint detection via
`_sprint_num` / `_cp_is_sprint` / `_sprint_sort_key`. Exports `export_json/xlsx/
docx/pdf`; email via `_plan_html`; caching `_cache_load`/`_cache_save`.

## Step/title prompt rules

`generate_steps`: one atomic action per step; outcomes go in `expected`;
environmental conditions in `precondition` only; 2-6 steps; never restate an
action. `evaluate_existing_steps` flags restated/duplicate/precondition-as-step
cases. `generate_titles` enforces de-duplication.

## Coding conventions for edits

- Minimal, surgical diffs; preserve hand-edits. Prefer `engine.py`-only changes
  where possible; deliver full drop-in replacement files.
- Large files: edit with Read/Edit in small moves, not shell read-modify-write
  (a mount cap truncated `main.py` once).
- Recurring crash pattern: calling UI helpers with unsupported kwargs
  (`green_btn` has no `disabled`; `searchable_dropdown` uses
  `hint_text`/`on_select`) — check the signature in `ui.py`/`regression.py`.
- Validate before delivering:
  `python -m py_compile engine.py main.py regression.py` and
  `python -m pytest tests perf/tests -q` (+ `python -m tracker.contract`,
  `python -m tracker.security`, `python verify_ops.py`). Tests that import
  `performance`/UI modules need `flet` installed.
- Explorer/locator changes: validate offline with `qa-studio-selfcheck` using
  fixtures from `qa-studio-fixtures`.
