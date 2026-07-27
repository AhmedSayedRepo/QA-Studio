# ADR-001: Canonical string story identity in the Regression & Sprint-Plan layer

**Status:** Accepted
**Date:** 2026-07-25
**Deciders:** Ahmed (owner)

## Context

Story identifiers are a different *type* per backend:

- **Azure DevOps** — an integer work-item id (`101048`).
- **Jira / Xray** — a string issue key (`"SCRUM-1"`).

The generation path (Setup → `engine.run_titles/run_steps`) was already made backend-agnostic via the `generation_ops` seam, which builds `Ref(id=str(s), key=str(s))` — so it resolves either type through Jira's `key IN (…)`.

The **Regression** and **Sprint-Plan** *plan-generation* engines were not. They were written for Azure and treat a story id as an integer in ~15 places: dict keys (`{int(s["id"]): 0}`), cache load/save (`{int(k): v}`), Azure-suite matching (`int(req_id) in story_ids`), and feature resolution. On a Jira/Xray connection `int("SCRUM-1")` raises `ValueError`, so:

- the caught sites (`try: int(...) except: continue`) silently **drop every story**;
- the uncaught sites (effort counting at lines 725/790/804, cache load at 167-173) **crash** the moment the user clicks *Generate* with effort-hours on.

Symptom the user hit: the Regression story picker showed "0 stories selected" and *Generate* stayed disabled (the picker's own `int(key)` toggle threw before selection could register — already fixed separately).

### Forces

- **Azure must stay byte-identical.** It is the primary, live-verified backend; a regression there is unacceptable.
- **No UI-level verification is available in this environment.** The change must be reason-about-able and locally consistent, not "try it and see."
- **The fix must be a seam, not a sprinkle.** 15 scattered `int()→str()` edits would leave the same latent assumption for the next id-shaped feature (automation, exports, sprint plan) to trip over again.
- **Scope containment.** The shared Setup→run path already works (its own seam). The defect is confined to two screens' internal calc/persistence layers.

## Decision

**Within `regression.py` and `sprint_titles.py`, story identity is a canonical string.** A single helper `_sid(x) -> str(x).strip()` defines the canonical form. Every place that uses a story id as a dict key, a set member, a cache key, or a cross-collection comparison goes through `_sid`. `int()` is **never** applied to a story id.

Azure keeps working because a numeric id in string form (`"101048"`) is a valid Azure REST path/query value and a valid dict key — the Azure-only branches (work-item metadata fetch, requirement-suite matching, complexity fetch) simply compare and interpolate the canonical string. `int()` is retained **only** for values that are genuinely integers and unrelated to story identity — sprint *numbers* parsed for ordering (`int(m.group(1))`), pixel math, etc.

This is Option B below.

## Options Considered

### Option A: Scattered `int()` → `str()` at each site
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low per-site, high in aggregate (must find all 15) |
| Cost | Low now, recurring later |
| Correctness risk | High — miss one comparison and Azure or Jira silently mismatches |
| Durability | Poor — the assumption survives; the next feature reintroduces it |

**Pros:** smallest diff. **Cons:** no single source of truth; a missed site produces a *silent* key mismatch (cache miss, dropped story) that no compiler catches.

### Option B: Canonical string id via one `_sid` helper, scoped to the two screens (CHOSEN)
| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — one helper, mechanical application, Azure branches keep their REST calls |
| Cost | One focused pass |
| Correctness risk | Low — one canonical form; both backends key identically; JSON persistence already stringifies keys, so load/save becomes a no-op cast |
| Durability | Good — the layer is now type-agnostic; new id-shaped features inherit it |

**Pros:** single seam; Azure REST unaffected (numeric strings are valid); cache round-trips cleanly (JSON keys are strings anyway). **Cons:** touches many lines in one pass; Azure cache keys change int→str internally (safe as long as every read/write uses `_sid`).

### Option C: A `StoryId` value object carrying both native id and display key
| Dimension | Assessment |
|-----------|------------|
| Complexity | High — new type threaded through both screens + persistence |
| Cost | Largest |
| Correctness risk | Medium — serialization + equality semantics to get right |
| Durability | Best in theory |

**Pros:** most explicit. **Cons:** over-engineered for a two-screen calc layer; large blast radius for no functional gain over B; worse given no UI test loop.

## Trade-off Analysis

The real risk is **silent** mismatch, not a crash — a crash is at least visible. Option A maximizes silent-mismatch surface (every site is an independent chance to key with the wrong type). Option B collapses identity to one canonical form, so either everything matches or nothing does — and "nothing" surfaces immediately, not subtly. Option C buys explicitness the calc layer doesn't need and costs the most to verify without a UI. Given "Azure byte-identical" + "no UI verification," the option whose correctness is *local and uniform* wins: B.

Numeric-string ids are first-class in Azure REST (the app already builds `item_url` with `str(id)`), so B carries no Azure-side functional change — only the internal key *type* moves int→str, uniformly.

## Consequences

- **Easier:** Regression + Sprint Plan now run on any backend; the next id-shaped feature can't reintroduce the int assumption in these files.
- **Harder / to watch:** every id key/compare in these two files must route through `_sid` — a stray `int(s["id"])` or a raw `s["id"]` compared against a `_sid`-keyed dict is the one way to regress. Grep gate below.
- **Revisit:** Automation screen (`_reload_auto_plan_stories`, its own `int(key)` toggle at ~2505) shares this assumption and should adopt `_sid` in a follow-up; it is out of scope here.
- **Persistence:** existing on-disk regression caches keyed by bare int survive because JSON already stored them as strings; `_sid` on load matches them.

## Action Items

1. [ ] Add `_sid(x)` helper (module-level) in `regression.py`; reuse/import in `sprint_titles.py`.
2. [ ] Replace every story-id `int()` (keys, sets, comparisons, cache load/save) with `_sid` in `regression.py`.
3. [ ] Same pass in `sprint_titles.py` for its generation path.
4. [ ] Keep `int()` only for sprint-number parsing and non-identity integers.
5. [ ] Gate: `grep -nE "int\((s|r)\[.id.\]\)|\{int\(" regression.py sprint_titles.py` returns nothing story-id-related; `py_compile` clean.
6. [ ] User click-through on 3.12: generate a Regression plan and a Sprint plan on Jira+Xray **and** on Azure (confirm Azure output unchanged).
