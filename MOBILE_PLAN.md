# QA Studio — Mobile App Plan

## The short version
QA Studio is a good mobile candidate for its **monitor-and-orchestrate** value (start a Run, watch progress, read reports, get pause-on-error and Resume from your pocket) and a bad one for its **local-machine** features (Automation's generated projects, git pushes, self-update, browser runs). The app is Flet (Flutter under the hood), and Flet ships `flet build apk` / `flet build ipa` — so the same Python codebase CAN become the mobile app. The plan below keeps one codebase, carves out what can't come along, and defers the one genuinely hard mobile problem (OS suspending a live run) to its own phase.

## What transfers as-is vs. what can't
**Transfers (API-driven, no local dependencies):** Azure DevOps REST via `requests` (engine.py is pure-HTTP since the SDK removal — this refactor is what makes mobile possible at all), all AI provider adapters, Run titles/steps with worker pools, dedup, reports + email, Sprint Plan/Report, Regression Plan, Supabase auth, AI Usage screen.

**Cannot transfer:**
- `store.py` DPAPI — Windows-only credential encryption. Mobile needs Keystore/Keychain-backed storage.
- `Automation` — generates Selenium/Playwright/Cypress projects into local folders, runs git pushes, expects IntelliJ/npm on the machine. Meaningless on a phone.
- Self-update (`apply_update` zipball-over-install) — forbidden on iOS, fragile on Android; store/TestFlight distribution replaces it.
- `window_chrome.py`, `_win_clipboard_set`, `installer.py`/`install.bat`/`release.bat`, the pythonw launch path.
- `idle_watch.py` — mobile OS lifecycle replaces the idle-logout concept (re-auth on resume instead).

## Phase 0 — Platform isolation (in the current codebase, benefits desktop too)
1. Introduce `platform_caps.py`: one module answering `has_automation()`, `has_self_update()`, `secure_store()`, `is_mobile()` (Flet exposes `page.platform`). Every Windows-only call site routes through it.
2. `store.py`: add a backend interface — DPAPI on Windows (unchanged), `page.client_storage` + AES key held in OS keychain on mobile (Flet's `flet-secure-storage` extension, or PIN-derived key as fallback). The insecure-fallback warning mechanism already exists; reuse it.
3. Nav gating: `shell()` hides Automation / Check-updates / Useful-Links-git items when `not has_automation()`. The role-based gating pattern (Viewer vs Member) already exists — same mechanism, keyed on platform.
4. Kill remaining hard-coded desktop assumptions in shared code paths (file dialogs, `os.startfile`, local report exports → share-sheet on mobile via `page.launch_url`/share plugin).

## Phase 1 — First Android build (core screens only)
1. `flet build apk` with Setup, Run, Report, Sprint Plan/Report, Regression Plan, AI Usage. Target the pinned Flet 0.85.3 first; only bump if the build tooling demands it (the codebase carries several version-specific workarounds — DatePicker off-by-one, scroll bugs — that need re-verification on any bump).
2. Dependency audit: everything must be pure-Python (it already nearly is — the HTTP-adapter refactor removed the compiled tree; `requests`, `dotenv` etc. are fine). `pywin32`/DPAPI imports must be lazy/conditional (Phase 0.2).
3. Login: the Supabase auth gate works as-is; add biometric unlock in front of stored credentials (platform capability via Flet plugin).
4. Distribution: Play internal testing track (no review friction) for the team; sideloaded APK is fine for the first spins.

## Phase 2 — Responsive UI pass
The screens were built for a desktop window; the known offenders:
- Fixed widths everywhere: right rails `width=384`, email-style cards 640/680, dialogs `width=430`, log heights 380/240. Replace with breakpoint-driven sizing (`page.width < 700` → single-column stack; rails become bottom sheets or tabs).
- The Run screen's story grid + stats row need to collapse to a vertical list.
- Activity logs are already the best-behaved part (RTL-aware lines, in-place updates, ListView auto-scroll) — mostly reflow work.
- The header Pause/Resume/Stop buttons must stay reachable — pin as a bottom action bar on mobile.
Do this screen-by-screen (Run and Report first — they're the mobile value), using `page.on_resized` + a `T.is_narrow()` token rather than per-screen ad-hoc checks.

## Phase 3 — The hard problem: runs vs. mobile OS lifecycle
A Run is a long-lived local orchestration (worker threads calling Azure + AI for minutes). Phones suspend backgrounded apps; iOS will kill the sockets mid-run.
- **Step 1 (cheap):** keep-screen-awake during an active run (wakelock plugin) + warn on background attempt. Acceptable for v1: "keep the app open while a run is live" — same contract as the desktop today.
- **Step 2 (right answer, larger):** move run EXECUTION server-side — a small worker service (the engine already separates orchestration from UI via the `cb` event protocol; that protocol becomes the API: start-run → run-id, then poll/stream events). The mobile app becomes a viewer/controller: start, watch the same activity feed, Pause/Resume/Stop, get a push notification on completion or on the pause-on-provider-error gate. This also fixes desktop pain (close the laptop mid-run) and enables the existing report-email flow server-side. Supabase (already in the stack) can host the event stream; the pause/resume Condition maps to a run-state flag the worker polls.
- Decide Step 2 timing by usage: if mobile users mostly *monitor* runs started on desktop, build the viewer first and skip on-device execution entirely — that variant deletes Phase 3's risk and most of Phase 1's engine concerns.

## Phase 4 — iOS + store hardening
1. `flet build ipa`, Apple Developer account, TestFlight for the team. Self-update must already be gone from the mobile build (Phase 0).
2. App-review considerations: credential fields (Azure PAT, AI keys) need clear in-app purpose text; no dynamic code download (zipball updater), which is why it's excluded at the platform-caps level, not just hidden.
3. Push notifications (run finished / paused on error) — via Supabase Edge Function + FCM/APNs; the engine's `cb("done"…)`/pause events are the triggers.

## Recommended sequencing & scope guard
1. Phase 0 is safe to do now inside the existing app (pure refactor, desktop keeps working; every step verifiable on desktop).
2. Phase 1+2 give a usable Android monitor/run app with ~no engine changes.
3. Before investing in Phase 3 Step 2, ship Step 1 and measure whether on-device runs actually get used — the server-side worker is the biggest piece of work in this plan and might be justified on desktop grounds alone, or not at all.

## Open decisions (answer before Phase 1)
- Mobile = full runner, or monitor/controller-first? (Changes Phase 1 scope significantly.)
- Android-only for the team, or iOS required? (iOS adds the dev account, review, and stricter lifecycle handling.)
- Keep the pinned Flet 0.85.3 for the mobile build, or take the upgrade (which would also revisit this codebase's documented scroll/DatePicker workarounds)?
