# QA Studio — Dev Folder (roadmap & handoff)

**Folder:** `C:\Users\proga\Downloads\qa-studio` — the **live source of truth** for the app. Edits here get synced to the installed copy and released.

**Version:** `2.8.1` (see `VERSION`).

---

## How to read this file
Single source of truth for the dev app's state and backlog. A new chat can continue **without reading all of main.py** — use this file plus targeted reads of the specific method being changed. The big architectural refactor is happening in a separate folder (see "Modular refactor" below); do not re-derive it.

---

## What this app is
Flet **0.85.3** (pinned) desktop app — an Azure DevOps test-case / test-plan generator. Core files:

- `main.py` (~7,260 lines) — `class QAStudio`: all screens, shell/nav, window chrome, dialogs, self-update, login gate.
- `engine.py` (`E`) — providers/config (`E.AI_CONFIG`), generation, `apply_update`.
- `regression.py` — Regression + Test-Plan screens; `_checkbox_multiselect` (the custom inline dropdown used on Setup too).
- `sprint_titles.py`, `users_screen.py` — those screens.
- `theme.py` (`T`) — design tokens (`T.VIOLET`, `T.INK`, `T.R`, …) + `apply_theme`.
- `store.py` — DPAPI-encrypted creds (`store.load()` / `store.save(self.creds)`).
- `auth_supabase.py` (`auth`) — external auth gate + per-user permissions.
- `installer.py`, `install.bat`, `release.bat`, `_sync_to_install.py`.

### Toolchain / processes
- **Run (dev):** `C:\Users\proga\AppData\Local\Programs\Python\Python312\python.exe main.py` (GUI: `pythonw.exe`). This 3.12 has Flet; a co-installed 3.14 does NOT — desktop shortcuts must point at the 3.12 `pythonw`.
- **Sync to installed app** (`%LOCALAPPDATA%\QA Studio`): `python _sync_to_install.py` (uses Python shutil — does NOT truncate; never sync large files via shell `cp`, which has truncated `main.py` and broken launches before).
- **Release:** `release.bat` with a version bump.
- **Self-update:** `engine.apply_update` pulls the branch zipball (`AhmedSayedRepo/qa-studio`, branch `main`) and clears `__pycache__`.

---

## Recent changes already in this folder
- **"What to generate" default toggle** in Settings (Appearance card): choose Titles vs Steps as the default generator. Persists via `self.creds["tool"]` (`_set_tool` saves; loaded at init); `_tool_segment(compact=True)` renders it.
- **Dropdown click-away fix** (`shell()`): body wrapped in `SelectionArea(GestureDetector(content=body, on_tap=self._close_dropdowns))` so clicking empty space closes open dropdowns. The `SelectionArea` (added earlier for copyable text) was intercepting the taps; a child `GestureDetector` wins the empty-space tap while checkboxes/buttons keep theirs and drag-to-select still works.
- **Close-during-run confirm** (`_with_window_chrome` → `_close`): now shows `_confirm_close()` ("stop after current test case?") when `_run_active`/`_auto_running`, else `_force_close()`. The frameless window has no native X, so the custom close button is the only close path and previously force-closed mid-run without asking (`_confirm_close` was unwired).

---

## Post-merge changes (2026-07-03)
- **Viewer / read-only = view-only** — `shell()` sets `body.disabled = self.readonly`, which propagates to every button/dropdown/field/toggle in the content (nav + header stay live so they can still browse + sign out; scrolling unaffected). Read-only is already true for Viewers on every action screen (they lack the `act.*` cap).
- **Viewer content-view** — read-only users now **skip the "connect first" gate and see the real screen content, disabled** (empty states, no data — no creds/stories used). Each gated screen's condition became `if not app.readonly and not (connected...)` (Automation, Regression, Sprint Plan, Sprint Report). **Loader loop fixed properly (PERF-plan step 1):** the 3 build-time auto-loaders (`_cp_load_iterations`, sprint-report `_load_iterations`, regression plans loader) used a *falsy* "loaded" guard (`if app._cp_iterations` / `if not app._plans`), so an empty/sparse result reloaded + full-rendered forever — hit **Members too**, not just Viewers. Now they use a **key-based guard** (`_cp_iter_for`/`_st_iter_for`/`_reg_plans_for == app.project`): empty counts as loaded, reload only on project change, stale-drop on mid-load project change. Plus `connected`-guard (Viewers never fetch) and key/cache reset in `_switch_user_creds`. Story loaders are event-triggered, not build — unaffected. See `PERF_ARCH_PLAN.md`. Also: screens with **no action cap** (Useful Links) are now read-only for Viewers — `render()` sets `readonly = user has no act.* caps` when `_screen_action_cap` is None, so `shell()`'s `body.disabled` kicks in. (The earlier view-only placeholder was reverted.)
- **Per-user credentials + links** — `store.set_user(uid)` points load/save at `creds_<uid>.dat`; `main._switch_user_creds()` (called on sign-in / session-restore / sign-out) reloads that user's creds, resets connection, and re-applies their theme/lang/tool. `_links_path()` is now `links_<uid>.json` and the `_links` cache is dropped on user switch. Signed-out → shared default file.
- **Links open in front** — `_open_url` uses `os.startfile` on Windows (ShellExecute foregrounds the browser over the app); Useful Links routes through `_open_url`.
- **Landing page** — redesigned to match the app (dark neon/glass, v2.8.1) in `landing.html` — replace the GitHub Pages `index.html` with it.
- **Repo private?** — NOT safe as-is: install.bat, self-update, version-check, and GitHub Pages all rely on public unauthenticated access. Go private only after moving distribution (installer + VERSION + release zips + Pages) to a small PUBLIC repo, keeping source private. Audit for secrets first (anon key OK public, service-role NOT).
- **Enter submits login** — `login.py` wires `on_submit = _submit` on the email/password/name fields, so Enter triggers Sign in / Sign up.

- **Nav badges** → unique mnemonics across ALL tabs: Setup=S, Run=Ru, Report=Rp, Regression=Rg, Sprint Plan=SP, Sprint Report=SR, Automation=A, Useful Links=L, Users=U (pipeline in `theme.py NAV`; aux in `main.py __init__` NAV.insert). No collisions.
- **Existing-steps modal** (`modals.py`) — Cancel/Continue wrapped in one Row so Flet's action bar stops stacking them.
- **Users screen** (`users_screen.py`) — taller list container; each row has a red **Revoke access** action (confirm dialog). Soft revoke = strip all caps via `auth.admin_revoke_access` (new; uses the existing `admin-users` Edge Function, reversible by setting a role). No hard delete. Revoked rows show an **ACCESS REVOKED** pill, highlight no role, and disable the revoke button. **Revocation now takes effect within ~25s on the signed-in user** (was: only on next re-login — the JWT caches `app_metadata`): `render()` throttle-calls `auth.revalidate()` (GET `/auth/v1/user` → live DB `app_metadata`, not the stale JWT payload), updates `self.user`, and re-gates. A zero-cap user gets a locked **"Access revoked"** screen (`_no_access_screen`) with only Sign-out — no usable Setup/Settings/dropdowns/updates. `_first_allowed_screen` returns `None` when nothing is permitted (was: fell back to `setup`).
- **Providers refactor — COMPLETE + CLEANED** (`engine.py`) — pure-HTTP adapters (`requests`) for ALL providers (openai/nvidia/deepseek/qwen, anthropic, azure, gemini, manus; ollama already HTTP), across generate (`_ai_call_once`), connect (`validate_api_key`), and model-list (`list_models`). **Flag removed** — HTTP is the only path; all SDK branches deleted; no vendor SDK imported. `requirements.txt` trimmed by 16 pkgs (anthropic/openai/google-generativeai + grpcio/protobuf/google-* tree) — kills the Python/dep-mismatch install failures at the root. See `PROVIDERS_REFACTOR_PLAN.md`.

## Modular refactor — MERGED ✅ (2026-07-03)
The refactor is merged into this folder: `main.py` is now **4,334 lines** and imports the 12 modules (`ui`, `useful_links`, `settings`, `run`, `report`, `automation`, `setup`, `dialogs`, `updater_ui`, `window_chrome`, `login`, `modals`), all passing the AST gate here. Dead files removed: `auth.py` (unused), `qa_perf.log`, stale `__pycache__` (pyc for deleted `web_login`/`auth_screen`/`login_assets`). The 3 in-flight fixes (click-away, confirm-close, what-to-generate) are present post-merge.

**Next:** launch on 3.12 and click every screen; then `python _sync_to_install.py`, bump `VERSION`, `release.bat`.

Superseded planning docs removed: `REFACTOR_PLAN.md`, `AUTH_PLAN.md`, `AUTH_EXTERNAL_PLAN.md`. Remaining docs: `README.md`, `DEV_ROADMAP.md` (this file), `ADMIN_USERS_SETUP.md`.

## Modular refactor (historical notes)
`main.py` is being split into per-screen / per-concern modules in **`C:\Users\proga\Downloads\qa-studio-refactor`** (a standalone copy). Done there: `ui.py`, `useful_links.py`, `settings.py`, `run.py`, `report.py`, `automation.py`, `setup.py`, `dialogs.py`, `updater_ui.py`, `window_chrome.py`, `login.py`, `modals.py` (12 modules) — `main.py` shrunk ~7,250 → ~4,334 lines with **no intended behavior change**. All screens, cross-cutting concerns, and the dialog/modal builders are extracted; the dead WebView2 cluster is removed (117 lines). What remains in `main.py` is the app shell + state + orchestration + interdependent runtime glue (kept intentionally). The refactor is **complete**; next is the merge-back. See that folder's `REFACTOR_ROADMAP.md`.

**Merge plan (when validated):**
1. Copy the new module files + refactored `main.py` from the refactor folder into this folder.
2. Run the AST gate (in `REFACTOR_ROADMAP.md`) here; then launch on 3.12 and click every screen.
3. `python _sync_to_install.py`, bump `VERSION`, `release.bat`.

Until merged, apply small fixes to **both** folders (like the click-away fix) or note the drift.

---

## Backlog / open items
- Merge the modular refactor back (above) once the refactor folder is fully validated.
- ~~Remove leftover WebView2 dead code~~ — **done in the refactor folder** (all four fns were unused); arrives in dev via the merge-back.
- Watch: `regression.py` appeared to differ from the refactor copy during this work (possible earlier truncation of the dev copy around the EXPORT card, ~line 3924). Verify `regression.py` here is complete before releasing.

---

## Gotchas (learned the hard way)
- **Never** copy/modify large files (`main.py`, `regression.py`, `engine.py`) via shell `cp`/`sed -i` on mounted folders — it can silently truncate. Use the Read/Edit/Write tools (host) or `_sync_to_install.py`.
- Flet 0.85 specifics: async window methods (`window.center()`/`destroy()` are coroutines), `HoverEvent.local_position` (Offset `.x`/`.y`, not `local_x`), `page.window.title_bar_hidden` for frameless.
- Class-level attributes (`HELP`, `PROVIDER_KEY_HELP`) sit between methods in `main.py` — mind them when moving code.
