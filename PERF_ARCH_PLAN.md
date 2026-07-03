# QA Studio — Data-Loading & Render Performance Plan

## The problem (affects every user, not just Viewers)
Data loading is coupled to rendering. Three anti-patterns compound:

1. **Load-on-render.** Fetchers (`_cp_load_iterations`, `_cp_load_stories`, `_reload_plan_stories`, `_load_plans`, `_load_setup_stories`, `_fetch_models_async`, …) are *called from `screen()`/build functions*. Building the UI has the side effect of kicking off network fetches.
2. **Full-render-on-complete.** When a fetch finishes it calls `app.ui_safe(app.render)` — a full `page.controls.clear() + add()` rebuild (measured 0.6–5s on large trees). So each of a Member's normal steps (project → plan → stories) pays a full rebuild, and any open dropdown snaps shut.
3. **Falsy "loaded" guards.** `if not app._plans` / `if app._cp_iterations` treat an *empty result* as "never loaded", so a sparse project/plan re-fetches + re-renders on every pass (the loop we hot-patched with `connected`-guards).

Net effect for a **Member**: sluggish, flashy setup/regression/sprint flows and redundant network calls; for a **Viewer**, it was an outright infinite loop.

## Principles
- **Rendering is pure.** `screen()`/build functions read state and return controls. They **never** fetch.
- **Loads are events, not renders.** Fetch on explicit triggers (connect, project-change, plan-change, an initial "warm") — not as a side effect of drawing.
- **Completions update in place.** A finished fetch mutates only the control(s) that show it (`dd.options = …; dd.update()`), never `render()`.
- **Empty ≠ unloaded.** Explicit load state per resource.

## Target architecture

### 1. A tiny resource/state layer
Model each remote resource as a record, keyed by its inputs:
```
Resource = { status: UNLOADED|LOADING|LOADED|ERROR, data, error, key }
```
Resources and their keys:
| resource | key |
|---|---|
| projects | (connection) |
| plans | project id |
| stories | plan id |
| iterations | project id |
| iteration-stories | frozenset(sprint paths) |
| models | provider name |
`LOADED` with `data=[]` is a valid terminal state → **no reload loop**. Invalidate (→ `UNLOADED`) only on the events that change the key (provider/project change, connect/disconnect) — never on render.

### 2. One loader utility (owns async + cancellation)
```
app.load(resource, fetch_fn, on_done)
```
- Dedupe: if `status==LOADING` for the same key, no-op.
- Cancel stale: bump a per-resource generation token; a late result whose token is stale is dropped. (This already exists ad-hoc via `_cp_stories_gen` / `_reg_stories_gen` — centralize it.)
- Runs `fetch_fn` on a worker, then marshals `on_done(data)` to the UI thread.
- `on_done` does an **in-place** control update, not `render()`.
This replaces the copy-pasted `_loading=True` + `threading.Thread` + `ui_safe(render)` blocks scattered across the screens.

### 3. Keyed cache
Store loaded resources by key (e.g. `plans_by_project[pid]`). Re-selecting a previous project reuses the cache — zero refetch. Clear on connect/disconnect and on manual "refresh".

### 4. In-place update surface
Each screen exposes stable "holder" controls for the regions that show loaded data (project dd, plan dd, story picker, estimate, KPI strip, table) and a `refresh_region()` that mutates them. `render()` becomes rare — only for true screen/nav changes. This is the pattern already proven in `_apply_live_models`, `_fetch_estimate`, and regression's in-place `_refresh_*`; generalize it.

### 5. Gating falls out for free
With loads event-driven, a read-only Viewer (or any not-connected user) simply never triggers a load — no guards needed inside the fetchers, no loops. The screen renders empty + disabled.

## Migration (incremental, behavior-preserving)
1. **Kill the loops now (done as hot-patches):** `connected`-guards on the fetchers. Keep.
2. **Fix the guards:** replace every falsy `if not X` "loaded?" check with an explicit `status`/`loaded` flag. Removes the empty-result reloop for Members too. *(highest ROI, low risk)*
3. **Stop full renders on completion:** change each loader's `ui_safe(app.render)` to an in-place `refresh_region()`. *(biggest felt speedup)*
4. **Extract `app.load(...)`** and route the fetchers through it (dedupe + cancel + marshal in one place).
5. **Add the keyed cache** so back-navigation and re-selection don't refetch.
6. **Move the fetch triggers out of `screen()`** into the on-change handlers / an initial warm; make build functions pure.
7. Optional: **debounce** rapid triggers (sprint toggles, search) and use **skeletons** on the specific region rather than a whole-screen "scanning" state.

## Quick wins vs. the full refactor
- **This week:** steps 1–3 (guards → explicit flags; completions → in-place). Removes the Member lag and the loops with minimal surface change.
- **Next:** steps 4–6 (loader utility, cache, pure builds) — the durable architecture, and a natural companion to the `main.py` modular split in `REFACTOR_ROADMAP.md`.

## Why this is the right shape
The root cause is that `render()` is expensive **and** it's on the data-loading path. Every fix above pushes toward the same rule: **fetch on events, reflect via in-place updates, render only on navigation.** That removes the coupling entirely — performance stops depending on how often the screen redraws.
