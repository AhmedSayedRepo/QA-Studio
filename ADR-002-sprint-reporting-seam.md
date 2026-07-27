# ADR-002: Sprint Report & Sprint Summary — seam routing + honest capability gating

**Status:** Accepted
**Date:** 2026-07-25
**Deciders:** Ahmed (owner)

## Context

Two screens produce sprint-level reports:

- **Sprint Report** (`sprint_titles.py`, nav "titles"/SR) — translated titles of a sprint's stories/bugs, grouped by state.
- **Sprint Summary** (modal opened from Setup) — per-story execution rollup for a test plan, rendered + emailable.

Both call **Azure engine functions directly** — `E.fetch_iterations`, `E.sprint_report_data`, `E.sprint_summary` — bypassing the `backend_setup` seam every other screen now uses. On a Jira/Xray connection:

- the sprint dropdown was empty (`E.fetch_iterations` is Azure-only) — already fixed to `backend_setup.fetch_sprints`;
- clicking Sprint Summary produced **"Could not load summary: Not found (404). Check the project name spelling."** — a *misleading* message: the project name is fine; the Azure REST endpoint simply doesn't exist for a Jira project.

There is also a **capability-vs-reality defect**: `XrayBackend.capabilities` declares `Capability.SPRINT_REPORTS` (xray.py:118), but `XrayBackend.sprint_report_data` raises `_unsupported` (xray.py:387). Xray inherits the Jira read path but has no Zephyr execution model, and its own Test-Run rollups are deferred — so the capability claim is false. Azure and Jira+Zephyr implement `sprint_report_data` honestly; TestRail correctly does not declare it.

### Forces

- The seam (`backend_setup`) is the established architecture; these two screens are the last direct-to-Azure callers.
- Xray genuinely defers execution rollups. A feature needing pass/fail data **cannot** be truthfully implemented on Xray without building Test-Run querying — a separate feature, not a bug fix.
- A capability flag that lies is worse than an absent one: it makes any capability-gated caller trust a method that throws.
- Azure must stay byte-identical; no UI verification available here; Jira+Zephyr routing can't be live-verified from this environment either.
- A misleading error ("check spelling") is itself a defect — it sends the user to fix the wrong thing.

## Decision

1. **Capabilities must be honest.** Remove `SPRINT_REPORTS` from `XrayBackend.capabilities` until Xray actually implements `sprint_report_data`. (The declaration, not the method, was the bug.)
2. **The UI gates on a single seam predicate, never on a direct Azure call.** Add `backend_setup.sprint_reports_available(app)`. Today only the Azure path is wired end-to-end in the UI, so the predicate returns `is_azure(...)`; it is written to flip to capability-driven the moment the non-Azure generation is routed through the seam (documented follow-up). Where unavailable, both screens render an **honest, backend-named "not available"** state instead of calling `E.sprint_*`.
3. **The sprint dropdown routes through the seam** (`fetch_sprints`) regardless — listing a backend's sprints is harmless and correct even when the *report* isn't wired.

This is Option B below.

## Options Considered

### Option A: Route the report generators through the seam for ALL backends now
| Dimension | Assessment |
|-----------|------------|
| Complexity | High — ReportData DTO ↔ legacy dict adaptation, bug fetching, Xray Test-Run rollups |
| Correctness risk | High — unverifiable from here; Xray execution model is a real build |
| Honesty | Good |

**Cons:** ships large, unverifiable feature code (Xray execution rollups) to satisfy a report the user can't yet validate — the exact "claimed verified but wasn't" trap.

### Option B: Honest capability + seam-predicate gate; degrade gracefully; defer the non-Azure data build (CHOSEN)
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — one predicate, two guarded call-sites, one capability line removed |
| Correctness risk | Low — pure gating; reason-verifiable, no live-data dependency |
| Honesty | Best — no faked data, no lying capability, accurate UX message |

**Pros:** removes the architecture violation and the misleading error; fixes the false capability; Azure untouched. **Cons:** Sprint Report/Summary remain unavailable on non-Azure until the follow-up builds the data path — but they say so truthfully.

### Option C: Leave direct Azure calls, just reword the error
Rejected — keeps the architecture violation and the lying capability; only masks the symptom.

## Trade-off Analysis

The honest question is "does this backend actually produce this report?" — a *capability* question. Option A answers it by building the capability (large, unverifiable, Xray-deferred). Option C ignores it. Option B makes the capability declaration truthful and gates the UI on it, which is the minimum that is both clean (no direct-Azure calls, no lying flags) and safe (no unverifiable feature code). Users on Xray get a truthful "not available here" plus a working sprint dropdown, instead of a 404 that blames their spelling.

## Consequences

- **Easier:** no screen calls Azure engine functions directly for sprint data; capability flags can be trusted by any future gated caller.
- **Harder / to revisit:** Sprint Report/Summary on non-Azure need a real data path — Jira+Zephyr routing of `sprint_report_data` (ReportData→dict), and Xray Test-Run rollups — tracked as follow-up; when built, `sprint_reports_available` flips to capability-driven.
- **Behavioral:** Azure unchanged. Xray no longer advertises `SPRINT_REPORTS`.

## Action Items
1. [x] Sprint dropdown → `backend_setup.fetch_sprints` (done).
2. [ ] Remove `SPRINT_REPORTS` from `XrayBackend.capabilities`.
3. [ ] Add `backend_setup.sprint_reports_available(app)`.
4. [ ] Gate Sprint Report generation + Sprint Summary load; honest backend-named message when unavailable.
5. [ ] Follow-up (needs live verification): route non-Azure `sprint_report_data` through the seam (Jira+Zephyr), build Xray Test-Run rollups, then flip the predicate to capability-driven.
