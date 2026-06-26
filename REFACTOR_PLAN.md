# QA Studio — Modularization Plan

Goal: split the two monoliths (`main.py` ≈ 5,975 lines, `regression.py` ≈ 3,540 lines)
into focused modules — one concern per file — without changing behavior.

---

## 0. Why a mixin split (not "rewrite into functions")

`QAStudio` is one big stateful class (137 methods) where almost everything reads/writes
`self.*` and calls `self.render()`. The lowest‑risk way to break that up **without touching
a single call site** is to group methods into **mixin classes**, one per area, then compose:

```python
# app.py
class QAStudio(
        ShellMixin, SetupMixin, RunMixin, ReportMixin, AutomationMixin,
        SettingsMixin, PaletteMixin, OnboardingMixin, LinksMixin, DialogMixin):
    def __init__(self, page): ...
    def render(self): ...      # the screen dispatcher stays here
    def goto(self, screen): ...
```

Each mixin keeps methods exactly as they are (`def run_screen(self): ...`) — no signature
changes, no behavioral changes, `self` still resolves across mixins at runtime. Module‑level
UI helpers (`card`, `green_btn`, `empty_state`, …) move to a shared `widgets` module and are
imported where needed. `regression.py` already uses the `app`‑passing function style, so it
needs only to be split, not restructured.

---

## 1. Target layout

```
qa_studio/
  __init__.py
  __main__.py            # main(page), _launch(), if __name__ == "__main__"
  app.py                 # QAStudio core: __init__, _build, render() dispatch, goto,
                         #   window lifecycle, update-check, _track_scroll
  theme.py               # (unchanged) design tokens
  store.py               # (unchanged) credential persistence
  engine.py              # (unchanged for now; optional split in Phase 6)

  ui/
    __init__.py
    widgets.py           # card, empty_state, grad_text, field_label, stat_tile,
                         #   _grad_button, primary/green/ghost/danger_btn, _btn_shadow,
                         #   _wrap_btn, _shadow_wrap, _ic, grad, logo helpers
    shell.py             # ShellMixin: rail(), topbar(), shell(), _install_top_gap
    dialogs.py           # DialogMixin: _show_dialog, _close_dialog, _toast, _busy,
                         #   _err, _ask_reeval, _open_create_plan, _open_sprint_summary
    palette.py           # PaletteMixin: _on_key, _open_palette, _palette_commands, _close_palette
    onboarding.py        # OnboardingMixin: _maybe_show_onboarding, _open_onboarding, _finish_onboarding

  screens/
    __init__.py
    setup.py             # SetupMixin: setup_screen, _setup_right, _task_card/_task_locked,
                         #   credential handlers, _set_tool, _lang_segment, _set_lang,
                         #   _start_run, _launch_run, provider helpers
    run.py               # RunMixin: run_screen, _launch_run/_refresh_run, story cards,
                         #   log rendering, _run_meta_line, _fmt_dur, _stop_run, _new_run
    report.py            # ReportMixin: report_screen, _relative_time, email/_open_azure
    automation.py        # AutomationMixin: automation_screen, _auto_field(_change),
                         #   _start_automation, _auto_project_dir, _auto_log*, _save_git_creds
    settings.py          # SettingsMixin: settings_screen, _set_perf, _clear_caches,
                         #   _reset_prefs, _toggle_theme
    links.py             # LinksMixin: useful_links_screen, link CRUD helpers
    regression.py        # regression + sprint plan SCREENS (app-passing functions),
                         #   _checkbox_multiselect, locked_state, caching, data-gathering
    exporters.py         # xlsx / docx / pdf exporters pulled out of today's regression.py
```

Imports stay shallow: `ui/*` and `screens/*` import from `theme`, `engine`, `store`,
`ui.widgets`. `app.py` imports every mixin. No mixin imports another mixin (they only call
sibling methods through `self`), which avoids import cycles.

---

## 2. Symbol → file mapping (from today's `main.py`)

| Current symbol(s) | Moves to |
|---|---|
| `card, empty_state, grad_text, field_label, stat_tile, grad, _ic` | `ui/widgets.py` |
| `_grad_button, primary/green/ghost/danger_btn, _btn_shadow, _wrap_btn, _shadow_wrap, _disabled_wrap` | `ui/widgets.py` |
| logo: `logo_img, _logo_b64, _logo_path, _LOGO_CTL` | `ui/widgets.py` |
| `rail, topbar, shell, _install_top_gap` | `ui/shell.py` |
| `_show_dialog, _close_dialog, _toast, _busy, _unbusy, _err, _ask_reeval, _open_create_plan, _open_sprint_summary` | `ui/dialogs.py` |
| `_on_key, _open_palette, _palette_commands, _close_palette` | `ui/palette.py` |
| `_maybe_show_onboarding, _open_onboarding, _finish_onboarding` | `ui/onboarding.py` |
| `setup_screen, _setup_right, _task_card, _task_locked, _lang_segment, _set_lang, _set_tool, _start_run, _launch_run`, all credential `_save_*`/`_connect`/provider handlers | `screens/setup.py` |
| `run_screen, _refresh_run, _build_story_cards, _render_log_lines, _render_one_log, _log_icon, _run_meta_line, _fmt_dur, _stop_run, _set_run_active, _new_run` | `screens/run.py` |
| `report_screen, _relative_time, _open_azure`, email senders | `screens/report.py` |
| `automation_screen, _auto_field, _auto_field_change, _start_automation, _auto_project_dir, _auto_log*, _auto_counts_header, _save_git_creds` | `screens/automation.py` |
| `settings_screen, _set_perf, _clear_caches, _reset_prefs, _toggle_theme` | `screens/settings.py` |
| `useful_links_screen` + link helpers | `screens/links.py` |
| `__init__, _build, render, goto, _track_scroll, window/update lifecycle` | `app.py` |
| `main, _launch, __main__` | `__main__.py` |

`regression.py` today → `screens/regression.py` (screens + caching + data gathering +
`_checkbox_multiselect` + `locked_state`) and `screens/exporters.py` (the `.xlsx/.docx/.pdf`
builders). `engine.py` stays one file for now.

---

## 3. Phased sequence (each phase is independently shippable)

**Phase 0 — Safety net (do first).**
`git add -A && git commit -m "pre-refactor snapshot"`. Every phase below ends with its own
commit so any step is trivially reversible. (This is exactly the restore point that saved us
during the recent truncation incident.)

**Phase 1 — Extract `ui/widgets.py`.** Pure leaf helpers, zero behavior change. Move the
module‑level functions, add `from ui.widgets import *` (or explicit names) at the top of
`main.py`. Compile + launch smoke test. Lowest risk; do it first to prove the package wiring.

**Phase 2 — Extract the self‑contained mixins:** `links.py`, `settings.py`, `onboarding.py`,
`palette.py`. These barely touch other screens. Convert each to `class XMixin:` and add to the
`QAStudio` bases. Launch, click each, commit per mixin.

**Phase 3 — `ui/shell.py` (ShellMixin)** and **`ui/dialogs.py` (DialogMixin).** Shared by every
screen, so move them before the big screens. Verify nav + a dialog (create plan) still work.

**Phase 4 — Big screens:** `setup.py`, `run.py`, `report.py`, `automation.py` (one mixin per
commit). These hold the most `self.*` state; move them last when the scaffolding is proven.

**Phase 5 — Split `regression.py`** into `screens/regression.py` + `screens/exporters.py`.
Mechanical: exporters are already standalone functions.

**Phase 6 (optional) — Split `engine.py`** into `engine/azure.py` (REST client),
`engine/ai.py` (provider adapters), `engine/generate.py` (titles/steps), `engine/selfheal.py`
(Java project builder). Only if the file keeps growing.

---

## 4. Verification after every phase

1. `python -c "import ast,glob; [ast.parse(open(f).read()) for f in glob.glob('qa_studio/**/*.py', recursive=True)]"` — syntax of every module.
2. Launch the app and walk the pipeline: Setup → Run → Report, then Regression, Sprint,
   Automation, Settings, Useful Links, and Ctrl‑K.
3. `git commit` only after both pass. If anything breaks, `git checkout -- .` and retry the
   single phase.

---

## 5. Risks & guardrails

- **The truncating sandbox mount.** Files this size can't be safely rewritten by shell
  read‑modify‑write (that's what truncated `main.py`). This refactor must be done with the
  **Edit/Write tools** in small moves, or **locally on your machine** where there is no mount
  cap. Per‑phase git commits make any slip recoverable.
- **Import cycles.** Avoid by the rule "mixins never import mixins; they only import
  `widgets`/`theme`/`engine`/`store`." Cross‑screen calls go through `self`.
- **Shared module globals** (`T`, `ft`, `E`, `store`): import them at the top of each new
  module rather than relying on `main.py`'s namespace.
- **Method-name collisions across mixins:** none today (verified 137 unique method names), but
  keep that invariant — a duplicate would silently shadow via MRO.
- **`page.on_keyboard_event`** is set once in `_build`; keep that single owner even after
  `PaletteMixin` moves out.

---

## 6. Effort estimate

| Phase | Scope | Rough effort |
|---|---|---|
| 0 | git snapshot | 2 min |
| 1 | widgets.py | 30 min |
| 2 | links/settings/onboarding/palette | 1–2 h |
| 3 | shell + dialogs | 1–2 h |
| 4 | setup/run/report/automation | 3–5 h |
| 5 | regression + exporters | 1–2 h |
| 6 | engine split (optional) | 2–3 h |

Net: roughly a day of careful, test‑after‑each‑step work for Phases 0–5. No behavior change —
purely structural, which is why it pays to commit between every phase.
