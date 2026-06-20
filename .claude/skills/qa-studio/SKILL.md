---
name: qa-studio
description: >-
  Use whenever working on QA Studio — a Flet desktop app (Python) that generates
  Azure DevOps test cases (titles + steps) via AI in Arabic/English, with a live
  Selenium explorer that captures locators. Trigger when editing engine.py or
  main.py, deploying/pushing, bumping VERSION, touching the AI providers, the
  intent-driven explorer, the report email, or discussing QA Studio internals.
  Covers source-of-truth rules, the deploy ritual, the VERSION encoding gotcha,
  provider config, locator taxonomy, and Arabic-RTL output rules.
---

# QA Studio — project context

A Flet desktop app (Python) that generates Azure DevOps test cases via AI in
Arabic/English and uses a live Selenium explorer (logs into Keycloak) to capture
exact locators per step. Four screens: Setup / Run / Report / Automation.

- Repo: `AhmedSayedRepo/qa-studio`, branch `main`.
- Dev folder (Windows): `C:\Users\proga\Downloads\qa-studio`.
- Current version: **1.7.0** (check the `VERSION` file each session — it changes).

## Source-of-truth rule (important)

`engine.py` and `main.py` are the authoritative files and other chats modify
them. **Always read the uploaded/current files before editing — never edit from
memory.** When the user uploads a file, treat THAT as the new baseline and
preserve their changes (e.g. they hand-edited the report-email layout). Confirm
which file is current by diffing rather than assuming.

Files:
- `engine.py` — all logic: Azure DevOps, AI (`ai_complete`), title/step
  generation, the Selenium explorer, Java test generation, email report.
- `main.py` — the Flet UI (4 screens, Setup form, provider dropdown).
- `theme.py` — design tokens `T.*` (colors, radii) and `T.disp_name(provider)`.
  Often NOT uploaded; don't invent token names — if unsure, ask or leave alone.
- `store.py` — local credential persistence.
- `VERSION` — single line, drives the in-app updater (compares against repo).

## Deploy ritual

Replace files → bump `VERSION` → clean build (only if cutting a new installer:
delete `build/` and `dist/` first) → `git add -A && git commit && git push`.

VERSION encoding gotcha (cost real debugging time):
- cmd.exe: `echo 1.7.0> VERSION` — **no space before `>`** (a space writes a
  trailing space into the file).
- PowerShell: do NOT use `>` (writes UTF-16, breaks the UTF-8 updater). Use
  `"1.7.0" | Out-File -FilePath VERSION -Encoding ascii -NoNewline`.

Windows icon cache: after changing the app icon, a same-path reinstall keeps the
old icon. Fix: `ie4uinit.exe -show`, or clear `IconCache.db`/`iconcache*`. Avoid
it long-term with a clean build + an exe file-version bump each release. End
users on fresh installs don't hit this.

## AI providers

All providers route through `ai_complete()` — provider-agnostic. OpenAI-compatible
providers share one branch: `provider in ("openai","nvidia","deepseek","qwen")`.
Eight providers in `AI_CONFIG`: anthropic, openai, gemini, azure_openai, ollama,
nvidia, deepseek, qwen. The Setup dropdown is built from `AI_CONFIG.keys()`, so
adding a provider there surfaces it in the UI automatically.

- DeepSeek: base `https://api.deepseek.com`, model `deepseek-chat`. The
  `deepseek-chat`/`deepseek-reasoner` aliases deprecate **2026-07-24** → switch
  to `deepseek-v4-flash` / `deepseek-v4-pro`. New accounts get a free token grant.
- Qwen (DashScope/Model Studio): international (Singapore) endpoint
  `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` is correct for Egypt;
  keys are region-specific. Default model `qwen-plus`. Vision via `qwen-vl-max`.
- The `vision` flag in `AI_CONFIG` is metadata only (not gating logic).

## Output language

`_is_arabic_out()` gates Arabic output. Arabic deliverables are RTL, single
consolidated sheets (not multi-tab), alternating row colors, all content Arabic.
Keep generated test steps/titles in the selected language only.

## Intent-driven explorer (v1.7.0 architecture)

`explore_and_map` no longer asks the AI to pick 1-of-N elements per step. Stages:
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

## Step/title prompt rules (to avoid repeated steps)

`generate_steps`: one atomic action per step; outcomes go in `expected` (not new
steps); environmental conditions go in `precondition` only; fewest steps (2-6);
never restate the same action. `evaluate_existing_steps` flags
restated/duplicate/precondition-as-step cases as inadequate. `generate_titles`
already enforces strong de-duplication.

## Coding conventions for edits

- Make minimal, surgical diffs; preserve the user's hand-edits.
- Validate before delivering: `python -m py_compile engine.py main.py`; validate
  any embedded harvest JS with `node --check`; unit-test pure functions
  (`_norm`, `_rank_candidates`, `_classify_case`, …).
- Don't touch `theme.py`/`store.py` unless provided.
- Deliver files to the user; remind them of the deploy ritual + VERSION gotcha.
- To validate explorer/locator changes against real page HTML without a browser,
  use the `qa-studio-selfcheck` skill with fixtures from `qa-studio-fixtures`.
