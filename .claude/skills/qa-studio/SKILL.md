---
name: qa-studio
description: >-
  Use whenever working on QA Studio — a Flet desktop app (Python) that generates
  Azure DevOps test cases (titles + steps) via AI in Arabic/English, with a live
  Selenium explorer that captures locators, plus Regression and Sprint planning
  with Word/Excel/PDF/JSON exports. Trigger when editing engine.py, main.py, or
  regression.py, deploying/pushing, bumping VERSION, touching the AI providers,
  the intent-driven explorer, the planning exporters/email, or discussing QA
  Studio internals. Covers source-of-truth rules, the deploy ritual, the VERSION
  encoding gotcha, provider config, locator taxonomy, and Arabic-RTL output rules.
---

# QA Studio — project context

A Flet desktop app (Python) that generates Azure DevOps test cases via AI in
Arabic/English and uses a live Selenium explorer (logs into Keycloak) to capture
exact locators per step. Screens: Setup / Run / Report / Automation, plus
Regression Plan and Sprint Plan.

- Repo: `AhmedSayedRepo/QA-Studio`, branch `main`.
- Dev folder (Windows): `C:\Users\proga\Downloads\qa-studio`.
- **Always check the `VERSION` file each session — it changes.** (As of last
  read: 2.7.3.)

## Source-of-truth rule (important)

`engine.py`, `main.py`, and `regression.py` are the authoritative files and other
chats modify them. **Always read the current files before editing — never edit
from memory.** When the user uploads a file, treat THAT as the new baseline and
preserve their changes (e.g. hand-edited report-email layout). Confirm which file
is current by diffing rather than assuming.

Files:
- `engine.py` (~6.1k lines) — all logic: Azure DevOps REST, AI (`ai_complete`),
  title/step generation, the Selenium explorer, Java test generation, email report.
- `main.py` (~6.1k lines) — the Flet UI, one big stateful `QAStudio` class.
- `regression.py` (~3.6k lines) — Regression Plan + Sprint Plan screens
  (app-passing function style), data gathering/caching, effort weighting,
  resource assignment, the `_plan_html` email, and the JSON/XLSX/DOCX/PDF
  exporters.
- `theme.py` — design tokens `T.*` (colors, radii) and `T.disp_name(provider)`.
  Often NOT uploaded; don't invent token names — if unsure, ask or leave alone.
- `store.py` — local credential persistence.
- `VERSION` — single line, drives the in-app updater (compares against repo).
- `installer.py` / `install.bat` / `build.bat` / `release.bat` / `push.ps1` —
  packaging and deploy helpers. `REFACTOR_PLAN.md` documents a planned (not yet
  executed) mixin split of the two UI monoliths — read it before any structural work.

## Deploy ritual

Replace files → bump `VERSION` → clean build (only if cutting a new installer:
delete `build/` and `dist/` first) → `git add -A && git commit && git push`.

VERSION encoding gotcha (cost real debugging time):
- cmd.exe: `echo 2.7.3> VERSION` — **no space before `>`** (a space writes a
  trailing space into the file).
- PowerShell: do NOT use `>` (writes UTF-16, breaks the UTF-8 updater). Use
  `"2.7.3" | Out-File -FilePath VERSION -Encoding ascii -NoNewline`.

Windows icon cache: after changing the app icon, a same-path reinstall keeps the
old icon. Fix: `ie4uinit.exe -show`, or clear `IconCache.db`/`iconcache*`. Avoid
it long-term with a clean build + an exe file-version bump each release. End
users on fresh installs don't hit this.

Repo hygiene: keep `regression.py.bak`, `_mount_probe.txt`, `_sync_to_install.py`,
and `__pycache__/` out of commits (check `.gitignore`).

## AI providers

All providers route through `ai_complete()` — provider-agnostic. OpenAI-compatible
providers share one branch: `provider in ("openai","nvidia","deepseek","qwen")`.
Eight providers in `AI_CONFIG`: anthropic, openai, gemini, azure_openai, ollama,
nvidia, deepseek, qwen. The Setup dropdown is built from `AI_CONFIG.keys()`, so
adding a provider there surfaces it in the UI automatically. (`disp_name` also
maps a "manus" label; treat `AI_CONFIG.keys()` as the live list.)

- Default models live in `AI_CONFIG` + the per-provider model lists: anthropic
  `claude-sonnet-4-6` (also opus-4-7 / haiku-4-5), openai `gpt-4o`, gemini
  `gemini-1.5-pro` (list offers 2.5-pro/flash), qwen `qwen-plus`.
- DeepSeek: base `https://api.deepseek.com`, model `deepseek-chat`. The
  `deepseek-chat`/`deepseek-reasoner` aliases deprecate **2026-07-24** → switch
  to `deepseek-v4-flash` / `deepseek-v4-pro`. New accounts get a free token grant.
- Qwen (DashScope/Model Studio): international (Singapore) endpoint
  `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` is correct for Egypt;
  keys are region-specific. Default `qwen-plus`. Vision via `qwen-vl-max`.
- The `vision` flag in `AI_CONFIG` is metadata only (not gating logic).

## Output language

`_is_arabic_out()` gates Arabic output. Arabic deliverables are RTL, single
consolidated sheets (not multi-tab), alternating row colors, all content Arabic.
Keep generated test steps/titles in the selected language only. The planning
exporters (`export_xlsx/docx/pdf`) and `_plan_html` follow the same RTL rules
(`_ar()` handles Arabic shaping in the PDF).

## Intent-driven explorer architecture

`explore_and_map` does NOT ask the AI to pick 1-of-N elements per step. Stages:
1. `compile_test_case()` — one LLM call per case turns messy steps into typed
   **intents** (`precondition` / `action` / `assertion`) with page-language
   keywords + `from_steps` back-references. Collapses restated/duplicate steps;
   routes preconditions away from clicks. Falls back to `_intents_from_raw_steps`.
2. `_rank_candidates()` — deterministic scoring binds an intent to a live element
   by accessible-name/text/aria/placeholder/id/testid/kind (Arabic-normalized via
   `_norm`). The AI (`_tiebreak_with_ai`) is used ONLY to break ties among a ≤5
   shortlist — never to invent locators.
3. `_act()` — interception-proof: scroll to center, `_settle()` (waits out
   spinners/aria-busy), `_dismiss_overlays()`, `_topmost_ok()` via
   `elementFromPoint`, retry ladder (native → ActionChains → JS).
   Assertions bind by **DOM-diff** (snapshot before/after the action; the target
   is what newly appeared) — same path captures negative-login error locators.

Captured locators are written back onto the ORIGINAL steps via `from_steps`, so
generated Java still mirrors the authored test case (`generate_test_class`
unchanged).

- Locator taxonomy — `step["locator_src"]` ∈ `live` | `snapshot` | `guess` |
  `precondition`. Stats returned as `{"live","snapshot","guess"}` (main.py reads
  these as Live/Snap/Guess/TODO chips).
- `_classify_case()` → `negative_login` | `presence` | `interaction`
  (presence judged by TITLE only, to avoid false positives from "appears" in an
  expected result). Negative-login cases walk on a fresh login page (cookies
  cleared) and re-login afterward via `do_login(fresh=True)`.

## Regression & Sprint planning (regression.py)

- `build_rows()` + `assign_resources()` — weighted effort estimate (test cases ×
  minutes × Azure DevOps priority weight, via `_email_pri`/`_av`) balanced across
  named resources. Inline-editable plan table recalculates totals on delete.
- Sprint detection: `_sprint_num` / `_cp_is_sprint` / `_sprint_sort_key` parse and
  order sprint iteration paths.
- Exports: `export_json`, `export_xlsx`, `export_docx`, `export_pdf` (+ `_pdf_font`
  / `_ar` for Arabic). Email body via `_plan_html`. Caching via
  `_cache_load`/`_cache_save`; `clear_caches`/`set_perf` toggles.

## Step/title prompt rules (to avoid repeated steps)

`generate_steps`: one atomic action per step; outcomes go in `expected` (not new
steps); environmental conditions go in `precondition` only; fewest steps (2-6);
never restate the same action. `evaluate_existing_steps` flags
restated/duplicate/precondition-as-step cases as inadequate. `generate_titles`
already enforces strong de-duplication.

## Coding conventions for edits

- Make minimal, surgical diffs; preserve the user's hand-edits.
- These files are large — edit with the Read/Edit/Write tools in small moves, not
  shell read-modify-write (a mount cap truncated `main.py` once). Per-phase git
  commits make slips recoverable.
- Validate before delivering: `python -m py_compile engine.py main.py regression.py`;
  validate embedded harvest JS with `node --check`; unit-test pure functions
  (`_norm`, `_rank_candidates`, `_classify_case`, `build_rows`, …).
- Don't touch `theme.py`/`store.py` unless provided.
- Deliver files to the user; remind them of the deploy ritual + VERSION gotcha.
- To validate explorer/locator changes against real page HTML without a browser,
  use the `qa-studio-selfcheck` skill with fixtures from `qa-studio-fixtures`.
