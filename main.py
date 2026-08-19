"""main.py — QA Studio (Flet desktop app).
Run:  pip install flet pillow anthropic openai azure-devops requests
      flet run main.py        (or)   python main.py
"""
# Copyright © 2026 Ahmed Sayed. All rights reserved.
# QA Studio is proprietary software; see the LICENSE file. No part of this source
# may be used, copied, modified, or distributed without prior written permission.
# ── Stale-bytecode guard ───────────────────────────────────────────────────
# This folder commonly lives under Downloads, which is often OneDrive-synced.
# mtime-based .pyc cache invalidation can misfire across cloud sync/clock skew,
# so Python can silently keep executing an OLD cached __pycache__/*.pyc even
# after the matching .py source was edited and saved on disk — every source
# edit then appears to have "no effect" no matter how the file is rewritten.
# Wipe any cached bytecode and skip writing new cache for this run, BEFORE any
# local module below is imported, so every import always compiles from the
# current source on disk. Must stay above every `import <local module>` line.
import os as _os, shutil as _shutil, sys as _sys
_sys.dont_write_bytecode = True
_pc = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "__pycache__")
if _os.path.isdir(_pc):
    _shutil.rmtree(_pc, ignore_errors=True)
del _pc, _os, _shutil, _sys

# ── GLOBAL HOME FIX (mobile) — must run BEFORE anything computes a path ──────
# On an Android Flet build `os.path.expanduser("~")` resolves to /data, which is
# NOT writable. We already route our own modules through
# platform_caps.app_data_dir(), but THIRD-PARTY libraries compute their own
# paths from ~ and we cannot patch them: the azure-devops SDK caches under
# ~/.azure-devops, which failed live with
#   "Couldn't check for existing test case steps ([Errno 13] Permission denied:
#    '/data/.azure-devops')"
# and took the existing-test-case count with it (it reported 0 cases).
#
# Repairing ~ ITSELF fixes this class everywhere at once — our code, the Azure
# SDK, and any future dependency — instead of chasing call sites one at a time
# (this bug has now surfaced six separate times; see DEV_ROADMAP #95).
#
# Deliberately gated on ~ being genuinely UNWRITABLE rather than on
# is_mobile(): platform detection needs page.platform, which doesn't exist at
# import time, and this must land before any import-time path computation.
# Desktop keeps its real home directory untouched.
def _repair_home_if_broken():
    import os
    appdata = os.environ.get("FLET_APP_STORAGE_DATA")
    if not appdata:
        return
    home = os.path.expanduser("~")
    try:
        os.makedirs(home, exist_ok=True)
        probe = os.path.join(home, ".qa_write_probe")
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return                      # ~ is fine (desktop) — change nothing
    except Exception:
        pass
    try:
        os.makedirs(appdata, exist_ok=True)
        os.environ["HOME"] = appdata
        os.environ["USERPROFILE"] = appdata
    except Exception:
        pass


_repair_home_if_broken()

import threading, traceback, time
import flet as ft

import theme as T
import store
import strings
import engine as E
import regression
import sprint_titles
import task_manager
import auth_supabase as auth
import users_screen
import orgs_screen
import ai_usage_screen
import remote_runs_screen
import useful_links
import settings
import run
import report
import automation
import performance
import setup
import dialogs
import updater_ui
import window_chrome
import platform_caps
import login
import modals
import idle_watch
import backend_setup

# ── Flet version-compatibility shim ───────────────────────────────────────────
# Flet renamed ft.icons→ft.Icons and ft.colors→ft.Colors around 0.25+. Support both.
if not hasattr(ft, "icons") and hasattr(ft, "Icons"):
    ft.icons = ft.Icons
if not hasattr(ft, "colors") and hasattr(ft, "Colors"):
    ft.colors = ft.Colors
# And the reverse for older code paths
if not hasattr(ft, "Icons") and hasattr(ft, "icons"):
    ft.Icons = ft.icons
if not hasattr(ft, "Colors") and hasattr(ft, "colors"):
    ft.Colors = ft.colors


# Shared, stateless UI builders now live in ui.py (Step-1 modular refactor).
# Re-imported here so main.py code and `from main import ...` in screen modules
# keep working unchanged.
from ui import (
    _ic, card, empty_state, grad_text, skeleton_rows, sec_head, field_label,
    grad, _grad_button, _btn_shadow, _shadow_wrap, _wrap_btn, _logo_path,
    _logo_b64, logo_img, primary_btn, _disabled_wrap, green_btn, ghost_btn,
    danger_btn, searchable_dropdown, hover_field, progress_ring, stat_tile, badge,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  APP
# ═══════════════════════════════════════════════════════════════════════════════
class QAStudio:
    def __init__(self, page: ft.Page):
        self.page = page
        # Serializes the actual page-mutation step (controls.clear/add/update)
        # across threads — see _render_body()'s critical section and
        # ui_safe()'s docstring. Deliberately does NOT wrap view construction
        # (regression.screen(self) etc., the genuinely slow ~0.5-1.6s+ part) —
        # only the brief clear/add/update calls need to be serialized, so two
        # renders can still BUILD their control trees fully in parallel and
        # only briefly wait on each other at the actual hand-off to Flet.
        self._render_lock = threading.Lock()
        # BRANDED START-UP SPLASH (mobile): render() shows _boot_splash() until
        # _BOOT_SPLASH_SECONDS have passed. A timer fires the hand-off render,
        # so the splash always clears even if nothing else happens to re-render
        # in that window.
        # ONLY when biometric unlock is OFF. With it ON the native
        # fingerprint/PIN prompt already covers the whole start-up window — it
        # sits over the app until the user authenticates, and the session
        # restore is gated behind it — so a splash underneath would add 3s of
        # dead time before the prompt without hiding anything the prompt isn't
        # hiding already. read from mobile_prefs, which is synchronous and
        # therefore available this early (the vault read is not).
        self._boot_at = None
        _splash_wanted = False
        if platform_caps.is_mobile():
            try:
                import mobile_prefs as _mp_boot
                _splash_wanted = not bool(_mp_boot.get("require_biometric", False))
            except Exception:
                _splash_wanted = True     # can't tell → assume no biometrics
        if _splash_wanted:
            self._boot_at = time.time()

            def _end_splash():
                try:
                    time.sleep(self._BOOT_SPLASH_SECONDS)
                    self._boot_at = None
                    self.ui_safe(self.render)
                except Exception:
                    self._boot_at = None
            try:
                threading.Thread(target=_end_splash, daemon=True).start()
            except Exception:
                self._boot_at = None
        # BIOMETRIC-LOGIN HOLD (mobile, "Require biometric/PIN unlock" ON):
        # with the gate on, the native fingerprint prompt covers start-up, but
        # AFTER it's passed there's a second, invisible wait — acquire_silent()
        # must REFRESH the Supabase access token over the network whenever it's
        # expired (i.e. any launch after ~1h idle). That refresh happens on the
        # bare login screen with no indication, so a long-idle relaunch looks
        # stuck on "sign in" for a few seconds before the app appears, while a
        # quick relaunch (token still valid, no refresh) snaps in — exactly the
        # reported "keeps on the sign-in screen longer the longer it's been
        # closed". Hold a branded "Signing you in…" frame across the WHOLE
        # restore (prompt → vault read → token refresh) instead of flashing the
        # login form; _on_secure_creds_ready clears it the instant the gate
        # resolves. Deadline-bounded so a hung network can never strand it.
        self._awaiting_bio_login = self._biometric_login_gate_active()
        self._bio_login_deadline = time.time() + 20.0
        # STARTUP BREADCRUMB — one line per app process, with the OS pid.
        #
        # This is what makes "I see 2 instances in background apps" answerable
        # instead of a guess: a GHOST task in recents leaves ONE startup line,
        # whereas two genuinely running processes leave TWO lines with
        # DIFFERENT pids seconds apart. It also finally pins down which build a
        # phone is actually running — a question that has repeatedly wasted time
        # here (DEV_ROADMAP #141/#166 stale-APK false alarms) and that the APK
        # itself couldn't answer while it shipped versionName 1.0.0.
        try:
            import diag_log as _dl_start, os as _os_start
            _dl_start.log_warn(
                "startup",
                f"pid={_os_start.getpid()} version={E.local_version()} "
                f"platform={getattr(self.page, 'platform', '?')} "
                f"home={_os_start.path.expanduser('~')}")
        except Exception:
            pass
        # Mobile only (no-op on desktop — see secure_store_mobile.py): kick
        # off the async OS-keychain read BEFORE the first store.load() below,
        # so the moment its bootstrap lands, _on_secure_creds_ready() has a
        # real page/render to refresh. store.load() itself never blocks on
        # this — it falls back to the legacy file until the callback fires.
        if platform_caps.is_mobile():
            try:
                import secure_store_mobile
                secure_store_mobile.init(
                    self.page,
                    on_ready=lambda: self.ui_safe(self._on_secure_creds_ready))
            except Exception:
                pass
            # MOBILE_PLAN.md Phase 3 Step 1 — keep-screen-awake during an
            # active run. Attaching the Wakelock service now (cheap,
            # side-effect-free until enable() is actually called) means
            # _set_run_active() below never needs to touch page.services
            # from inside a run's hot path.
            try:
                import mobile_wakelock
                mobile_wakelock.init(self.page)
            except Exception:
                pass
            # Login screen's device-tilt backdrop parallax (mobile equivalent
            # of the desktop's mouse-move parallax — see mobile_tilt.py and
            # login.py's login_gate()). Attached disabled here; login_gate()
            # enables it only while that screen is actually showing.
            try:
                import mobile_tilt
                mobile_tilt.init(self.page)
            except Exception:
                pass
        # Persistent UrlLauncher service (see mobile_url_launcher.py) — the
        # deprecated `page.launch_url()` convenience wrapper this app used
        # to fall back to on mobile constructs a throwaway, never-attached
        # UrlLauncher on every call, which silently fails to reach the
        # Flutter client. Attached on BOTH platforms: desktop's _open_url()
        # still tries webbrowser.open() first, this is only its fallback.
        try:
            import mobile_url_launcher
            mobile_url_launcher.init(self.page)
        except Exception:
            pass
        self.creds = store.load()
        self._migrate_key_slots()      # legacy per-provider keys → per-model slots
        # Restore the last-selected AI provider so it persists across app restarts.
        # Always resolve to a REAL provider (never None) — _connection_edit and
        # others use _provider_choice directly.
        _sp = self.creds.get("provider")
        self._provider_choice = (_sp if _sp in E.AI_CONFIG
                                 else (E.active_providers()[:1] or ["anthropic"])[0])
        # Apply the saved theme (light default, dark secondary) before any UI builds.
        #
        # MOBILE: read it from mobile_prefs, which is a small SYNCHRONOUS local
        # file. self.creds comes from the Keystore vault, whose read is async and
        # has NOT landed yet at this point — so it returns the empty default and
        # this line applied "light" on every launch, painting the whole first
        # frame light before _on_secure_creds_ready() corrected it a moment
        # later. That is the light-theme FLASH on relaunch (reported live).
        # mobile_prefs exists precisely for values needed before the async
        # bootstrap (same reason require_biometric lives there); _persist_theme()
        # keeps it in step with every theme change.
        _startup_theme = self.creds.get("theme", "light")
        if platform_caps.is_mobile():
            try:
                import mobile_prefs as _mp_theme
                _cached_theme = _mp_theme.get("theme", None)
                if _cached_theme in ("dark", "light"):
                    _startup_theme = _cached_theme
            except Exception:
                pass
        try:
            T.apply_theme(_startup_theme)
            # theme_mode drives Flet's BUILT-IN controls (dropdown menus, pickers,
            # text fields) so they get readable dark surfaces — without it the
            # dropdown popups render dark-on-dark.
            self.page.theme_mode = (ft.ThemeMode.DARK if T.MODE == "dark"
                                    else ft.ThemeMode.LIGHT)
            # UI language drives page direction (RTL for Arabic interface).
            self.page.rtl = strings.ui_is_rtl()
        except Exception:
            pass
        # Apply saved performance-logging preference (Settings screen).
        try:
            regression.set_perf(self.creds.get("perf", True))
        except Exception:
            pass
        # Apply saved org / email sender to the engine immediately so they
        # persist across restarts without needing to reconnect first.
        try:
            _saved_org = (self.creds.get("org") or "").strip()
            _saved_sender = (self.creds.get("gmail_sender") or "").strip()
            E.set_credentials(org=_saved_org or None,
                              gmail_sender=_saved_sender or None,
                              gmail_sender_name=self.creds.get("gmail_sender_name"),
                              gmail=self.creds.get("gmail") or None)
        except Exception:
            pass
        self.connected = False
        self.active = "setup"          # setup | run | report
        self.tool = "steps"            # steps | titles
        self.lang = "ar"               # DEFAULT output language (persisted via Settings)
        try:
            _saved = str(self.creds.get("lang") or "").strip().lower()
            self.lang = _saved if _saved in E.LANGUAGES else "ar"
            # remember the default generator (what to generate) from Settings
            _t = self.creds.get("tool")
            self.tool = _t if _t in ("titles", "steps") else "steps"
        except Exception:
            self.lang = "ar"
        # Per-RUN override (Setup's picker): starts at the saved default each
        # launch, is never persisted, so Setup never changes the Settings default.
        self.run_lang = self.lang
        # Interface (UI/chrome) language — a SEPARATE axis from output
        # language above. Persisted via Settings; controls nav/labels + RTL.
        self.ui_lang = "en"
        try:
            _uil = str(self.creds.get("ui_lang") or "").strip().lower()
            self.ui_lang = _uil if _uil in E.LANGUAGES else "en"
        except Exception:
            self.ui_lang = "en"
        try:
            strings.set_ui_lang(self.ui_lang)
        except Exception:
            pass
        self.nav_state = {"setup": "active"}

        # task selections
        self.project = None
        self.plan_id = None
        self.plan_name = None
        self.story_ids = []
        self._setup_story_open = False
        self._dd_closers = []  # in-place close callables, reset each render
        self._dd_syncers = {}  # in-place "untick from selection" callables, by key
        self.emails = ""
        self.existing_mode = "evaluate"

        # run state
        self.stop_flag = False
        self.last_report = None

        # cached azure lookups
        self._projects = []
        self._plans = []
        self._setup_stories = None        # stories of the selected plan's sprint
        self._setup_stories_loading = False
        # unlock flags (survive re-render)
        self._key_unlocked = False
        self._pat_unlocked = False
        self._gmail_unlocked = False
        self._org_unlocked = False
        self._sender_unlocked = False
        self._sender_name_unlocked = False
        # connect loading state
        self._connecting = False
        self._connect_status = ""

        # ── automation feature state ──
        self.auto_site_url = self.creds.get("auto_site_url", "")
        self.auto_login_url = self.creds.get("auto_login_url", "")
        self.auto_login_user = self.creds.get("auto_login_user", "")
        self.auto_login_pass = self.creds.get("auto_login_pass", "")
        self.auto_git_url = self.creds.get("git_url", "")
        self.auto_git_branch = self.creds.get("git_branch", "") or "main"
        self.auto_git_token = self.creds.get("git_token", "")
        self.auto_headless = True
        self.auto_local_path = self.creds.get("auto_local_path", "")
        self._auto_target = self.creds.get("auto_target", "") or "selenium"
        self._auto_log = []
        # Serializes the Activity-log column catch-up in _auto_logmsg's upd() below.
        # HISTORY: ui_safe() used to dispatch via Flet's page.run_thread — a NEW
        # executor thread per call, with NO ordering guarantee — and this lock was
        # the band-aid for the resulting races (two upd() calls reading the same
        # "have", or appending out of order). ui_safe() now serializes everything
        # FIFO on the session event loop (see its docstring), which fixes the same
        # class of bug globally; the lock stays as a harmless defensive layer for
        # the run_thread/direct-call fallback paths.
        self._auto_log_ui_lock = threading.Lock()
        self._auto_running = False
        self._auto_stop = False
        self._auto_paused = False
        self._auto_cond = threading.Condition()
        self._auto_out_dir = None
        self._auto_built = False
        self._run_active = False

        # update-check state
        self._update_info = None     # set by background check_for_update
        self._last_nav_update_check = 0
        self._update_check_inflight = False
        self._release_notice_scheduled = False
        self._updating = False
        self._update_dismissed = False
        self._closing = False        # set on close to stop background loops

        # Regression Plan tab (after Report)
        if not any(n.get("id") == "regression" for n in T.NAV):
            _ri = next((i for i, n in enumerate(T.NAV) if n.get("id") == "report"), len(T.NAV) - 1)
            T.NAV.insert(_ri + 1, {"id": "regression", "label": "Regression Plan",
                                   "icon": "FACT_CHECK", "ix": "Rg"})

        # Sprint Plan tab (after Regression Plan)
        if not any(n.get("id") == "testplan" for n in T.NAV):
            _ti = next((i for i, n in enumerate(T.NAV) if n.get("id") == "regression"), len(T.NAV) - 1)
            T.NAV.insert(_ti + 1, {"id": "testplan", "label": "Sprint Plan",
                                   "icon": "ASSIGNMENT", "ix": "SP"})

        # Sprint Titles tab (after Sprint Plan)
        if not any(n.get("id") == "titles" for n in T.NAV):
            _si = next((i for i, n in enumerate(T.NAV) if n.get("id") == "testplan"),
                       len(T.NAV) - 1)
            T.NAV.insert(_si + 1, {"id": "titles", "label": "Sprint Report",
                                   "icon": "SUMMARIZE", "ix": "SR"})

        # Task Manager tab (after Sprint Report) — per-user task workload
        # report (Original Estimate / Completed Work, scoped to a sprint) +
        # bulk "create a child task under each selected story" tool.
        if not any(n.get("id") == "task_manager" for n in T.NAV):
            _tmi = next((i for i, n in enumerate(T.NAV) if n.get("id") == "titles"),
                       len(T.NAV) - 1)
            T.NAV.insert(_tmi + 1, {"id": "task_manager", "label": "Task Manager",
                                    "icon": "TASK_ALT", "ix": "TM"})

        # Useful Links tab (last in the rail)
        if not any(n.get("id") == "links" for n in T.NAV):
            T.NAV.append({"id": "links", "label": "Useful Links",
                          "icon": "BOOKMARKS", "ix": "L"})

        # Users tab (admin-only; rail() hides it for non-admins).
        if not any(n.get("id") == "users" for n in T.NAV):
            T.NAV.append({"id": "users", "label": "Users",
                          "icon": "GROUP", "ix": "U"})

        # Organizations tab (SuperAdmin or org-scoped Organization Manager;
        # rail() applies the navigation capability and role boundary).
        if not any(n.get("id") == "orgs" for n in T.NAV):
            T.NAV.append({"id": "orgs", "label": "Organizations",
                          "icon": "BUSINESS", "ix": "Or"})

        # Audit is separate from Organizations so the configuration surface
        # remains focused even as the administrative history grows.
        if not any(n.get("id") == "audit" for n in T.NAV):
            T.NAV.append({"id": "audit", "label": "Audit",
                          "icon": "FACT_CHECK", "ix": "Au"})

        # AI Usage tab — visible to every signed-in user (nav.ai_usage is in
        # every role preset): a Member/Viewer sees their OWN usage, an Admin
        # sees everyone's. Only the "see everyone" scope is admin-gated
        # (server-side, in the ai-usage Edge Function), not the tab itself.
        if not any(n.get("id") == "ai_usage" for n in T.NAV):
            T.NAV.append({"id": "ai_usage", "label": "AI Usage",
                          "icon": "QUERY_STATS", "ix": "AI"})

        # External-auth (Supabase): None until restored / signed in. When auth is
        # not configured, this stays None and the app is un-gated (runs as before).
        self.user = None
        # Signed, short-lived URLs for the currently authenticated account's
        # avatar and organization logo.  These are intentionally memory-only:
        # the paths remain private in Supabase Storage and are re-signed after
        # each session restore or account switch.
        self._identity_visuals = {"avatar_url": "", "organization_logo_url": ""}
        # Restore a cached session SYNCHRONOUSLY before the first render. Right
        # after the WebView2 login the session is already on disk, so this makes
        # the app open straight into the signed-in UI — no flash of a sign-in
        # screen. The just-issued token is unexpired, so this is a local read (no
        # network). The async restore below still covers the token-refresh case.
        #
        # Mobile + "Require biometric/PIN unlock": skip this entirely. The
        # cached-session file (auth_supabase.py's _load_session/_save_session)
        # is a plain encrypted FILE, unrelated to secure_store_mobile's OS-
        # keychain vault — silently restoring straight from it would let
        # anyone with the phone unlocked skip past the sign-in screen with no
        # fingerprint/PIN check at all, defeating the whole point of turning
        # that setting on ("I need it to be used in login"). Deferred instead
        # to _on_secure_creds_ready(), gated on secure_store_mobile's own
        # enforce_biometrics=True read actually succeeding THIS launch — see
        # that method's biometric-gate block and secure_store_mobile.py's
        # bio_required()/bio_gate_passed().
        try:
            if auth.configured() and not self._biometric_login_gate_active():
                _u0 = auth.acquire_silent()
                if _u0:
                    self.user = _u0
        except Exception:
            pass

        self._build()
        # Resume a prior session in the background so a returning user skips the
        # gate without blocking startup on the network.
        self._restore_session_async()
        # Idle auto-logout: each signed-in user chooses their own timeout.
        self._last_activity = time.time()
        self._start_idle_watch()
        # Just-in-time shared-sender refresh: engine calls this right before any
        # email send so an admin's org-wide Gmail creds set AFTER this user signed
        # in are picked up without needing a re-login (see _ensure_org_email_fresh).
        try:
            E.set_sender_refresher(self._ensure_org_email_fresh)
        except Exception:
            pass

    # Providers with a GENUINELY ongoing free tier — permanent + rate-limited (or
    # local), NOT expiring trial credits. Trial-credit providers (nvidia, minimax,
    # glm, qwen, deepseek) hand out a one-time grant and then reject calls with
    # "insufficient balance / out of credit", so labelling them "free" promises
    # something that breaks — they're intentionally treated as PAID here. Everything
    # NOT listed is treated as paid. Drives the free/paid grouping in the provider
    # dropdown — adjust as providers change their pricing.
    FREE_PROVIDERS = {"gemini", "groq", "cerebras", "openrouter",
                      "mistral", "ollama"}

    # ---- credential helpers ----
    def _provider_options(self):
        names = list(E.AI_CONFIG.keys())
        orig_index = {n: i for i, n in enumerate(names)}   # stable order captured first
        def _is_active(n):
            return (n in E.active_providers()) or bool(self.creds["keys"].get(self._cred_slot(n)))
        def _grp(n):
            return 0 if n in self.FREE_PROVIDERS else 1    # 0 = free (shown first), 1 = paid
        def _grp_header(label, accent):
            """Styled, non-selectable section label: accent dot · UPPERCASE title ·
            divider rule. Falls back to plain text on Flet builds without Option
            content (see below)."""
            return ft.Container(
                content=ft.Row([
                    ft.Container(width=7, height=7, border_radius=4, bgcolor=accent),
                    ft.Text(label.upper(), size=10.5, weight=ft.FontWeight.W_800,
                            color=accent),
                    ft.Container(height=1, bgcolor=T.BORDER, expand=True,
                                 margin=ft.Margin.only(left=4)),
                ], spacing=7, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.only(left=4, right=10, top=9, bottom=5))
        # Group by free/paid; within a group: the active provider first (it's
        # the one you'll most likely pick), then MOST POWERFUL first
        # (E.POWER_RANK), then original config order as a stable tiebreaker.
        names.sort(key=lambda n: (_grp(n), not _is_active(n),
                                  E.POWER_RANK.get(n, 99), orig_index[n]))
        opts, cur_grp = [], None
        for name in names:
            g = _grp(name)
            if g != cur_grp:                                # insert a group header row
                cur_grp = g
                label, accent = (("Free tier", T.GREEN) if g == 0
                                 else ("Paid", T.AMBER))
                header = ft.DropdownOption(
                    key=f"__grp_{g}__",
                    text=f"──  {label.upper()}  ──")         # fallback if content unsupported
                try:
                    header.disabled = True                  # non-selectable where supported
                except Exception:
                    pass
                try:
                    header.content = _grp_header(label, accent)   # rich header where supported
                except Exception:
                    pass
                opts.append(header)
            active = _is_active(name)
            dot = "●" if active else "○"
            opts.append(ft.DropdownOption(key=name,
                text=f"{dot}  {T.disp_name(name)}"
                     + ("  (local — needs Ollama running)" if name == "ollama"
                        else f"  ({'active' if active else 'inactive'})")))
        return opts

    # Providers that issue a DISTINCT API key per model (build.nvidia.com does).
    # Only these store their key per model ("<provider>::<model>"); every other
    # provider uses ONE key for all its models. Add a provider here if it, too,
    # hands out a separate key per model.
    PER_MODEL_KEY_PROVIDERS = {"nvidia"}

    def _cred_slot(self, name):
        """Credential storage slot for a provider. Providers in
        PER_MODEL_KEY_PROVIDERS store their key per model ("<provider>::<model>"),
        so switching the model switches the saved key. All other providers use a
        single key per provider (the bare provider name)."""
        if name in self.PER_MODEL_KEY_PROVIDERS:
            m = (self._saved_model(name) or "").strip()
            return f"{name}::{m}" if m else name
        return name

    def _migrate_key_slots(self):
        """One-time upgrade of legacy per-provider keys to per-model slots, but ONLY
        for PER_MODEL_KEY_PROVIDERS. A legacy key saved under the bare provider name
        is attached to that provider's current model, then the bare entry dropped —
        so existing NVIDIA keys keep working. Single-key providers are left alone."""
        keys = (self.creds or {}).get("keys")
        if not isinstance(keys, dict):
            return
        changed = False
        for name in list(keys.keys()):
            if "::" in name or name not in self.PER_MODEL_KEY_PROVIDERS:
                continue                       # per-model slot, or single-key provider
            val = (keys.get(name) or "").strip()
            slot = self._cred_slot(name)
            if slot == name:
                continue                       # no model resolved → leave as-is
            if val and not (keys.get(slot) or "").strip():
                keys[slot] = val               # carry the key onto the current model
            keys.pop(name, None)
            changed = True
        if changed:
            try:
                store.save(self.creds)
            except Exception:
                pass

    def _saved_key(self, name):
        s = (self.creds["keys"].get(self._cred_slot(name)) or "").strip()
        if s: return s
        cfg = E.AI_CONFIG.get(name, {})
        k = (cfg.get("api_key") or "").strip()
        if k and not k.startswith("your-") and "-here" not in k:
            return k
        return ""

    def _saved_model(self, name):
        """The user's chosen model for a provider, or the engine default."""
        m = (self.creds.get("models", {}).get(name) or "").strip()
        if m:
            return m
        return E.current_model(name) or ""

    def _disconnect(self, reason=None):
        """Drop the active connection so the user must reconnect. Called when the
        provider or model changes while connected (running against a stale
        provider/model would be wrong)."""
        if not getattr(self, "connected", False):
            return
        self.connected = False
        self._projects = []
        self.project = None
        self.plan_id = None
        self._connect_status = ""
        # keep last_report so the Report tab still works; only the live link drops
        if reason:
            self._toast(reason)

    def _provider_active(self, name):
        # A provider is "active" only if it has a saved key in the credential store.
        # "Connected" status is tracked separately via self.connected.
        return bool((self.creds["keys"].get(self._cred_slot(name)) or "").strip())

    # ---- external auth (Supabase) ----
    def _restore_session_async(self):
        """Silently restore a signed-in session on startup (off the UI thread)."""
        if not auth.configured():
            return
        # Same biometric gate as __init__'s synchronous restore above — this
        # background path calls acquire_silent() unconditionally otherwise,
        # which would silently sign the user back in (bypassing biometrics
        # entirely) moments after the sync path deliberately skipped it.
        # _on_secure_creds_ready() performs the actual gated restore instead.
        if self._biometric_login_gate_active():
            return
        def work():
            try:
                u = auth.acquire_silent()
                if u:
                    self.user = u
                    self._switch_user_creds()   # load this user's own creds
                    self.ui_safe(self.render)
            except Exception:
                pass
        try:
            self._bg(work)
        except Exception:
            threading.Thread(target=work, daemon=True).start()

    # Branded start-up splash duration (mobile). A flat, unconditional window —
    # NOT the earlier conditional "theme gate", which a second-opinion review
    # showed was misguided: it only engaged on a first-ever launch and was
    # released with the wrong theme anyway, so it never prevented the flash.
    # The actual flash cause is fixed in _on_secure_creds_ready; this splash is
    # simply a clean, branded window that also covers the async settling of the
    # vault/session/theme reads, so nothing half-initialised is ever on screen.
    _BOOT_SPLASH_SECONDS = 3.0

    def _boot_splash(self, signing_in=False):
        """The app's own start-up screen: logo, name, version, spinner, on the
        current page background. Used by render()'s theme gate so the first
        frame is BRANDED rather than a blank container (or, worse, a
        wrong-theme paint of the real UI).

        `signing_in`: when the biometric-login hold is active, add a "Signing
        you in…" line so the post-fingerprint token-refresh wait reads as
        progress rather than a stuck login screen."""
        try:
            _logo = ft.Container(
                logo_img(64), width=64, height=64,
                bgcolor=(None if _logo_b64() else T.VIOLET),
                gradient=(None if _logo_b64() else grad(T.GRAD_LOGO)),
                border_radius=18, alignment=ft.Alignment.CENTER)
        except Exception:
            _logo = ft.Container(width=64, height=64)
        _foot = (ft.Text(strings.t("main_signing_in"), size=12, color=T.RAIL_DIM,
                         weight=ft.FontWeight.W_600)
                 if signing_in else
                 ft.Text(f"v{E.local_version()}", size=11, color=T.RAIL_DIM,
                         weight=ft.FontWeight.BOLD))
        return ft.Container(
            ft.Column([
                _logo,
                ft.Container(height=16),
                ft.Text("QA Studio", size=17, weight=ft.FontWeight.BOLD,
                        color=T.RAIL_INK),
                _foot,
                ft.Container(height=18),
                ft.ProgressRing(width=22, height=22, stroke_width=2.4,
                                color=T.VIOLET),
            ], alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2),
            expand=True, alignment=ft.Alignment.CENTER,
            bgcolor=(self.page.bgcolor or T.RAIL))

    def _persist_theme(self, mode):
        """Mirror the chosen theme into mobile_prefs (synchronous + durable) so
        the NEXT launch can paint the right theme on the very first frame.

        The authoritative copy still lives in creds/the vault; this is purely a
        startup cache. Without it __init__ has nothing to read (the vault is
        async) and always applied "light", which is the light flash on relaunch.
        No-op on desktop, where creds load synchronously."""
        if not platform_caps.is_mobile():
            return
        try:
            import mobile_prefs
            if mode in ("dark", "light"):
                mobile_prefs.set("theme", mode)
        except Exception:
            pass

    def _biometric_login_gate_active(self):
        """True when a returning user's cached session must NOT be silently
        restored yet — mobile only, only while "Require biometric/PIN
        unlock" is on (Settings > DEVICE SECURITY). mobile_prefs is
        synchronous/durable (see mobile_prefs.py) so this is safe to call
        from __init__ before anything async has landed."""
        if not platform_caps.is_mobile():
            return False
        try:
            import mobile_prefs
            return bool(mobile_prefs.get("require_biometric", False))
        except Exception:
            return False

    def can(self, capability):
        """True if the current user may use `capability`. When auth is unconfigured
        the app is un-gated, so everything is allowed."""
        if not auth.configured():
            return True
        return auth.has(getattr(self, "user", None), capability)

    def _is_admin(self):
        """True for an Admin user (or when auth is unconfigured — local/dev use)."""
        if not auth.configured():
            return True
        try:
            return auth.is_admin(getattr(self, "user", None))
        except Exception:
            return False

    def _can_manage_org_email(self):
        """True when this user may configure an organization sender.

        Auth-disabled desktop use keeps the old local-only credential flow.
        With Supabase enabled, the Edge Function derives the Organization
        Manager's target from the signed JWT and permits a SuperAdmin to select
        an existing target organization explicitly.
        """
        if not auth.configured():
            return True
        try:
            return auth.can_manage_org_settings(getattr(self, "user", None))
        except Exception:
            return False

    def _is_super_admin_org_email_manager(self):
        """Whether this Setup view needs an organization selector."""
        if not auth.configured():
            return False
        try:
            return auth.is_super_admin(getattr(self, "user", None))
        except Exception:
            return False

    def _org_email_target_id(self):
        """The sender-settings target currently displayed in Setup.

        Organization Managers never provide a target to the function: its
        server-side authorization derives their scope from the JWT. A
        SuperAdmin sends the selected id, which the function validates against
        the organizations table before it reads or writes anything.
        """
        if self._is_super_admin_org_email_manager():
            return str(getattr(self, "_org_email_target", "") or "").strip()
        try:
            return auth.org_id_of(getattr(self, "user", None))
        except Exception:
            return ""

    def _tenant_project_scope_org_id(self):
        """Effective organization displayed by Setup's project-scope card.

        A SuperAdmin uses the same selected organization as the sender picker;
        organization managers remain bound to the org in their verified token.
        """
        target = self._org_email_target_id()
        return target or auth.current_org_id()

    def _load_org_email_targets(self):
        """Load the SuperAdmin's organization list without blocking Setup."""
        if (not self._is_super_admin_org_email_manager()
                or getattr(self, "_org_email_targets_loading", False)
                or getattr(self, "_org_email_targets_loaded", False)):
            return
        self._org_email_targets_loading = True
        self._org_email_targets_error = ""

        def work():
            try:
                ok, result = auth.list_orgs()
            except Exception as ex:
                ok, result = False, str(ex)

            def apply():
                self._org_email_targets_loading = False
                # One attempt per Setup view avoids a render → failed request →
                # render retry loop while the device is offline.
                self._org_email_targets_loaded = True
                if ok and isinstance(result, list):
                    self._org_email_targets = result
                else:
                    self._org_email_targets = []
                    self._org_email_targets_error = str(result or "Unknown error")
                if getattr(self, "active", "") == "setup":
                    self._refresh_org_sender_scope()
            self.ui_safe(apply)

        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception as ex:
            self._org_email_targets_loading = False
            self._org_email_targets_error = str(ex)

    def _on_org_email_target_change(self, e=None):
        """Select and securely load a SuperAdmin's target organization sender."""
        target = str(getattr(getattr(e, "control", None), "value", "") or "").strip()
        self._org_email_target = target
        self._org_email_target_error = ""
        self._org_email_target_loading = bool(target)
        self._org_email_sync_status = strings.t("org_sender_syncing") if target else ""
        self._org_email_inherited_from = ""
        self._org_email_audit = []
        # Project scope follows the selected SuperAdmin organization too.
        self._tenant_project_scope_loaded = False
        self._tenant_project_scope_projects = []
        self._tenant_project_scope_teams = []
        self._tenant_project_scope_error = ""

        # Do not leave a previously selected organization's sender visible while
        # the new settings are loading; that could be accidentally saved into
        # the new organization. These are in-memory values until the user saves.
        for key in ("gmail_sender", "gmail_sender_name", "gmail"):
            self.creds[key] = ""
        try:
            # engine.set_credentials intentionally ignores a blank sender during
            # normal field edits; here we are switching security scopes, so it
            # must be cleared before any new organization's response arrives.
            E.set_credentials(gmail="", gmail_sender_name="")
            E.GMAIL_SENDER = ""
        except Exception:
            pass
        self._sender_unlocked = self._sender_name_unlocked = self._gmail_unlocked = False
        if getattr(self, "active", "") == "setup":
            self._refresh_org_sender_scope()
            self._refresh_org_sender_fields()
            self._refresh_tenant_project_scope_cell()
        if not target:
            return

        def work():
            try:
                ok, result = auth.get_org_settings(org_id=target, include_audit=True)
            except Exception as ex:
                ok, result = False, str(ex)

            def apply():
                # A slower response for an earlier choice must never overwrite
                # the values of the organization currently selected in the UI.
                if target != self._org_email_target_id():
                    return
                self._org_email_target_loading = False
                if ok and isinstance(result, dict):
                    email = (result.get("settings") or {}).get("email") or {}
                    if isinstance(email, dict):
                        self.creds["gmail_sender"] = str(email.get("sender") or "")
                        self.creds["gmail_sender_name"] = str(email.get("sender_name") or "")
                        self.creds["gmail"] = str(email.get("app_password") or "")
                        self._org_email_inherited_from = str(
                            email.get("inherited_from_org_id") or "")
                        self._org_email_audit = (result.get("audit")
                                                 if isinstance(result.get("audit"), list) else [])
                        self._org_email_sync_status = strings.t("org_sender_synced")
                        try:
                            E.set_credentials(
                                gmail=self.creds["gmail"] or None,
                                gmail_sender=self.creds["gmail_sender"] or None,
                                gmail_sender_name=self.creds["gmail_sender_name"] or None)
                        except Exception:
                            pass
                else:
                    self._org_email_target_error = str(result or "Unknown error")
                    self._org_email_sync_status = strings.t(
                        "org_sender_sync_failed", err=self._org_email_target_error)
                if getattr(self, "active", "") == "setup":
                    self._refresh_org_sender_scope()
                    self._refresh_org_sender_fields()
                    self._refresh_tenant_project_scope_cell()
            self.ui_safe(apply)

        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception as ex:
            self._org_email_target_loading = False
            self._org_email_target_error = str(ex)

    def _org_email_audit_control(self):
        events = {
            "email_settings_saved": strings.t("org_sender_audit_saved"),
            "email_sender_inherited": strings.t("org_sender_audit_inherited"),
            "email_auto_synced": strings.t("org_sender_audit_auto_synced"),
            "email_test_succeeded": strings.t("org_sender_audit_test_ok"),
            "email_test_failed": strings.t("org_sender_audit_test_failed"),
        }
        rows = []
        visible_audit = [item for item in (getattr(self, "_org_email_audit", []) or [])
                         if isinstance(item, dict) and item.get("event") != "email_auto_synced"][:5]
        for item in visible_audit:
            if not isinstance(item, dict):
                continue
            # Older deployments may contain automatic-sync records. They are
            # intentionally not user-facing activity: a normal refresh must
            # not push useful sender saves/tests out of this compact panel.
            event = events.get(str(item.get("event") or ""), "Sender activity")
            stamp = str(item.get("created_at") or "").replace("T", " ")[:19]
            rows.append(ft.Text((f"{stamp}  —  " if stamp else "") + event,
                                size=11, color=T.INK_3))
        if not rows:
            rows = [ft.Text(strings.t("org_sender_audit_empty"), size=11, color=T.INK_3)]
        return ft.Column(rows, spacing=4, tight=True)

    def _load_tenant_project_scope(self):
        """Load the signed-in user's visible organization project scope.

        This is display-only and runs outside the Setup render. Azure/Jira
        project discovery remains the authority for the selectable project
        list after Connect, while this card makes the configured tenant scope
        visible before a provider connection exists.
        """
        org_id = self._tenant_project_scope_org_id()
        if not org_id or getattr(self, "_tenant_project_scope_loading", False):
            return
        if (getattr(self, "_tenant_project_scope_org", "") == org_id
                and getattr(self, "_tenant_project_scope_loaded", False)):
            return
        self._tenant_project_scope_loading = True
        self._tenant_project_scope_org = org_id

        def work():
            ok, result = auth.get_tenant_overview(org_id)
            self._tenant_project_scope_loading = False
            self._tenant_project_scope_loaded = True
            if ok and isinstance(result, dict):
                self._tenant_project_scope_projects = (
                    result.get("projects", []) if isinstance(result.get("projects"), list) else [])
                self._tenant_project_scope_teams = (
                    result.get("teams", []) if isinstance(result.get("teams"), list) else [])
                self._tenant_project_scope_error = ""
            else:
                self._tenant_project_scope_projects = []
                self._tenant_project_scope_teams = []
                self._tenant_project_scope_error = str(result or "")
            if getattr(self, "active", "") == "setup":
                self.ui_safe(self._refresh_tenant_project_scope_cell)
        self._bg(work)

    def _tenant_project_scope_card(self):
        """Small, pre-connect view of projects configured for this tenant."""
        org_id = self._tenant_project_scope_org_id()
        if not org_id:
            return None
        self._load_tenant_project_scope()
        loading = bool(getattr(self, "_tenant_project_scope_loading", False))
        error = str(getattr(self, "_tenant_project_scope_error", "") or "")
        projects = getattr(self, "_tenant_project_scope_projects", []) or []
        teams = getattr(self, "_tenant_project_scope_teams", []) or []
        is_admin = auth.can_manage_users(getattr(self, "user", None))
        if loading:
            content = ft.Row([
                ft.ProgressRing(width=15, height=15, stroke_width=2, color=T.VIOLET),
                ft.Text(strings.t("tenant_setup_projects_loading"), size=11.5, color=T.INK_3),
            ], spacing=8)
        elif error:
            content = ft.Text(strings.t("tenant_setup_projects_error"), size=11.5, color=T.INK_3,
                              no_wrap=False, tooltip=error)
        elif not projects:
            # Members see only projects assigned to them; distinguish that
            # intentional empty scope from an administrator having configured
            # no projects at all.  It prevents a member from assuming Setup is
            # broken when access simply has not been granted yet.
            content = ft.Container(ft.Row([
                ft.Container(ft.Icon(ft.Icons.FOLDER_SHARED_OUTLINED, size=20,
                                     color=T.VIOLET_INK), width=36, height=36,
                             alignment=ft.Alignment.CENTER, bgcolor=T.VIOLET_SOFT,
                             border_radius=10),
                ft.Column([
                    ft.Text(strings.t("tenant_setup_projects_empty_title"), size=12,
                            weight=ft.FontWeight.W_700, color=T.INK),
                    ft.Text(strings.t("tenant_setup_projects_empty_admin" if is_admin
                                      else "tenant_setup_projects_empty_member"),
                            size=11, color=T.INK_3, no_wrap=False),
                ], spacing=3, expand=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=12, horizontal=12), bgcolor=T.CARD_2,
                border=ft.Border.all(1, T.BORDER), border_radius=T.R)
        else:
            rows = []
            for project in projects:
                if not isinstance(project, dict):
                    continue
                name = str(project.get("name") or project.get("external_key") or "—")
                key = str(project.get("external_key") or "")
                rows.append(ft.Container(ft.Row([
                    ft.Icon(ft.Icons.FOLDER_OUTLINED, size=15, color=T.VIOLET_INK),
                    ft.Text(name, size=12, weight=ft.FontWeight.W_700, color=T.INK, expand=True),
                    ft.Text(key, size=10.5, color=T.INK_3, tooltip=key or None),
                ], spacing=8), padding=ft.Padding.symmetric(vertical=7, horizontal=9),
                   bgcolor=T.CARD_2, border=ft.Border.all(1, T.BORDER), border_radius=T.R,
                   # ``strings.t`` itself uses ``key`` as its first argument;
                   # keep the project value distinct so Python never receives
                   # two values for that parameter while building Setup.
                   tooltip=strings.t("tenant_setup_project_tip", name=name,
                                     project_key=key)))
            content = ft.Column(rows or [ft.Text(strings.t("tenant_setup_projects_empty"), size=11.5,
                                                  color=T.INK_3)], spacing=6)
        team_rows = []
        for team in teams:
            if not isinstance(team, dict):
                continue
            name = str(team.get("name") or "—")
            description = str(team.get("description") or "").strip()
            team_rows.append(ft.Container(ft.Row([
                ft.Icon(ft.Icons.GROUP_OUTLINED, size=15, color=T.VIOLET_INK),
                ft.Column([
                    ft.Text(name, size=12, weight=ft.FontWeight.W_700, color=T.INK),
                    *([ft.Text(description, size=10.5, color=T.INK_3, no_wrap=False)] if description else []),
                ], spacing=2, expand=True),
            ], spacing=8), padding=ft.Padding.symmetric(vertical=7, horizontal=9),
               bgcolor=T.CARD_2, border=ft.Border.all(1, T.BORDER), border_radius=T.R,
               tooltip=strings.t("tenant_setup_team_tip", name=name)))
        team_content = ft.Column(team_rows, spacing=6) if team_rows else ft.Text(
            strings.t("tenant_setup_teams_empty_admin" if is_admin else "tenant_setup_teams_empty"),
            size=11, color=T.INK_3)
        return card(ft.Column([
            sec_head("Pr", strings.t("tenant_setup_projects")),
            ft.Container(height=6),
            ft.Text(strings.t("tenant_setup_projects_help"), size=11.5, color=T.INK_3, no_wrap=False),
            ft.Container(height=9), content,
            ft.Container(height=13), ft.Container(height=1, bgcolor=T.BORDER), ft.Container(height=12),
            ft.Text(strings.t("tenant_setup_teams_admin" if is_admin else "tenant_setup_teams"),
                    size=11.5, weight=ft.FontWeight.W_700, color=T.INK),
            ft.Container(height=4),
            ft.Text(strings.t("tenant_setup_teams_help"), size=10.5, color=T.INK_3, no_wrap=False),
            ft.Container(height=8), team_content,
        ], spacing=0))

    def _nav_label(self, n):
        """Localized nav label for the current INTERFACE language.
        Falls back to the built-in English label for any nav id that has
        no translation key yet (links/users/ai_usage/performance)."""
        try:
            _k = "nav_" + n["id"]
            _v = strings.t(_k)
            return _v if _v != _k else n.get("label", n["id"])
        except Exception:
            return n.get("label", n["id"])

    def _screen_nav_cap(self, screen):
        """Capability needed to OPEN a screen (None = open to everyone)."""
        return {"setup": "nav.setup", "run": "nav.run", "report": "nav.report",
                "regression": "nav.regression", "testplan": "nav.sprint_plan",
                "titles": "nav.sprint_report", "automation": "nav.automation",
                "task_manager": "nav.task_manager",
                "links": "nav.links", "settings": "nav.settings",
                "users": "nav.users", "orgs": "nav.orgs", "audit": "nav.audit",
                "ai_usage": "nav.ai_usage",
                "remote_runs": "nav.run"}.get(screen)

    def can_open_screen(self, screen):
        """Return whether this user may open a screen.

        Capability chips can narrow an administrator's access, but they must
        never elevate a Member or Viewer into an administrative screen.  The
        Users and Organizations routes therefore require both their navigation
        capability and the matching trusted role boundary.
        """
        if not auth.configured():
            return True
        user = getattr(self, "user", None)
        cap = self._screen_nav_cap(screen)
        if screen == "users":
            return auth.can_manage_users(user) and (not cap or self.can(cap))
        if screen == "orgs":
            return auth.can_manage_org_settings(user) and (not cap or self.can(cap))
        if screen == "audit":
            return auth.can_manage_users(user) and (not cap or self.can(cap))
        return not cap or self.can(cap)

    def _screen_action_cap(self, screen):
        """Capability needed to ACT on a screen (None = the screen has no actions).
        Drives the per-screen read-only state.

        ai_usage is deliberately NOT mapped here (unlike users → act.manage_users):
        act.view_usage means "can see EVERYONE's usage", which is a narrower thing
        than "can use the AI Usage screen at all" — every signed-in user can view
        and export/email their OWN usage report, so this falls through to the
        generic no-action-cap rule below (read-only only for a true Viewer with
        zero act.* capabilities, same as Useful Links)."""
        return {"setup": "act.connect", "run": "act.run", "report": "act.export",
                "regression": "act.regression", "testplan": "act.sprint",
                "titles": "act.sprint_report", "automation": "act.automation",
                "task_manager": "act.task_manager",
                "settings": "act.settings", "users": "act.manage_users",
                "orgs": "act.manage_orgs"}.get(screen)

    def _first_allowed_screen(self):
        for n in T.NAV:
            if self.can_open_screen(n["id"]):
                return n["id"]
        return None   # nothing permitted (e.g. access revoked) → locked screen

    def _maybe_revalidate(self):
        """Throttled server re-check of the signed-in user's permissions so an admin
        revoke/role change takes effect within ~25s. The JWT caches app_metadata, so
        caps would otherwise stay stale until the user's next token refresh/re-login."""
        import time as _t
        now = _t.time()
        if now - getattr(self, "_last_revalidate", 0.0) < 25:
            return
        self._last_revalidate = now
        def _work():
            try:
                fresh = auth.revalidate()
                if fresh is None:
                    return
                before = auth.caps_for(getattr(self, "user", None))
                self.user = fresh
                if auth.caps_for(fresh) != before:
                    self.ui_safe(self.render)      # caps changed → re-gate
            except Exception:
                pass
        try:
            self._bg(_work)
        except Exception:
            threading.Thread(target=_work, daemon=True).start()

    def _on_secure_creds_ready(self):
        """Mobile only: fired by secure_store_mobile once its async OS-
        keychain read lands — the first launch, and again after every
        _switch_user_creds() (which calls store.set_user() → re-bootstraps
        for the new account). Deliberately narrow: refreshes ONLY the
        credential-derived state store.load() populates, not a full
        _switch_user_creds()-style reset — that would wipe out project/plan/
        run state the user may have already touched in the brief window
        before this callback fires.

        Also re-applies theme and re-checks onboarding — both __init__'s
        T.apply_theme(self.creds...) and the startup _maybe_show_onboarding()
        call run BEFORE this callback ever fires (they're synchronous, this
        is an async keychain read), so on mobile they always saw the empty/
        default dict store.load() falls back to pre-bootstrap: theme came up
        "light" regardless of the saved preference, and onboarding looked
        never-completed (onboarded missing) EVERY launch even for a user who
        finished it days ago. Redoing both here, once the real values exist,
        fixes both without touching desktop (this callback is only ever
        wired up when platform_caps.is_mobile())."""
        self.creds = store.load()
        self._migrate_key_slots()
        _sp = self.creds.get("provider")
        self._provider_choice = (_sp if _sp in E.AI_CONFIG
                                 else (E.active_providers()[:1] or ["anthropic"])[0])
        # See secure_store_mobile.py's _bootstrap()/consume_bio_revert():
        # a biometric-gated read that just failed auto-turns the Settings
        # toggle back off rather than leaving it silently, permanently
        # broken — surface that here so it isn't a silent mystery.
        try:
            import secure_store_mobile as _ssm
            if _ssm.consume_bio_revert():
                self._toast(strings.t("main_bio_unlock_failed"))
        except Exception:
            pass
        # Biometric-gated login: __init__/_restore_session_async both
        # deliberately skipped auto-restoring a cached Supabase session when
        # _biometric_login_gate_active() was true, so the login screen shows
        # FIRST instead of silently signing the user in. This is the moment
        # that gate gets resolved — secure_store_mobile's OS-keychain read
        # (built with enforce_biometrics=True whenever this setting is on,
        # see its init()) has now either succeeded, meaning the user just
        # passed a real fingerprint/PIN/Face check, or failed and already
        # auto-reverted the setting above. Only restore the session in the
        # SUCCESS case — a failed/canceled/reverted check must fall through
        # to the ordinary email+password sign-in screen, never silently in.
        # NEVER auto-restore after an explicit sign-out. _sign_out() calls
        # _switch_user_creds(), which re-bootstraps the vault and therefore
        # re-enters THIS callback moments later — so without this guard the
        # "biometrics not required" branch below signed the user straight back
        # in and Log out appeared to do nothing (reported live: "bio toggle off,
        # not able to logout, it forces keep me logged in"). The flag lives on
        # the instance, so relaunching the app clears it and normal session
        # restore resumes; login.py clears it on a successful sign-in.
        if self.user is None and not getattr(self, "_user_signed_out", False):
            try:
                import secure_store_mobile as _ssm
                # Restore when EITHER biometrics isn't required at all, OR it is
                # and the gate has been passed. The "not required" case matters
                # now that the session lives in the Keystore vault: it can only
                # be read once this bootstrap has landed, so __init__'s earlier
                # synchronous acquire_silent() found nothing. Without this
                # branch a user with biometrics OFF would be signed out on every
                # launch — the exact bug this whole area keeps producing.
                if (not _ssm.bio_required()) or _ssm.bio_gate_passed():
                    _u0 = auth.acquire_silent()
                    if _u0:
                        self.user = _u0
                        self._switch_user_creds()
                        # POST-LOGIN update check — fires for BOTH biometrics-on
                        # (gate just passed) and biometrics-off (auto-restore)
                        # session restores, so a signed-in user reliably sees a
                        # pending update instead of it popping behind the login
                        # gate at build time (self-guarded / once-per-session).
                        try:
                            self._check_mobile_update(force=True)
                        except Exception:
                            pass
            except Exception:
                pass
        # Clear the biometric-login hold once the outcome is settled: either the
        # session restored (→ app), or the gate has RESOLVED and the session
        # bootstrap has landed but there's nothing to restore / the user
        # cancelled (→ email+password). Until then keep the branded "Signing you
        # in…" frame up — this callback fires several times per launch (creds
        # bootstrap, session bootstrap, bio gate), and the early ones run before
        # the prompt is answered, so clearing on them would flash the login form
        # mid-restore, which is the whole bug.
        if getattr(self, "_awaiting_bio_login", False):
            try:
                import secure_store_mobile as _ssm
                if self.user is not None:
                    self._awaiting_bio_login = False
                elif _ssm.bio_gate_done() and _ssm.session_available():
                    self._awaiting_bio_login = False
            except Exception:
                self._awaiting_bio_login = False
        if getattr(self, "_theme_touched", False):
            # The user already toggled the theme locally (e.g. tapped it on
            # the login screen) since launch, possibly WHILE this bootstrap
            # was still in flight — see _toggle_theme()'s comment for the
            # GET/SET race this guards against. Their explicit choice wins;
            # keep self.creds in sync with what's actually on screen so a
            # later save() elsewhere doesn't silently revert it either.
            self.creds["theme"] = T.MODE
            # Prime the startup cache here TOO. It was only primed in the else
            # branch, so a session where the user had touched the theme never
            # wrote it — and the next launch had nothing to read and flashed
            # light again. Priming on BOTH paths is what makes the flash a
            # once-ever event rather than a recurring one.
            self._persist_theme(T.MODE)
        else:
            try:
                # NEVER apply or persist a DEFAULTED theme here. This callback
                # fires MULTIPLE times per launch — secure_store_mobile.init()
                # dispatches _bootstrap, _bootstrap_session and (optionally)
                # _bio_gate_check, and each one calls on_ready. The early ones
                # run while self.user is still None, so store.load() returns the
                # DEFAULT vault slot (qa_studio_creds), whereas the user's saved
                # theme lives in their PER-USER slot (qa_studio_creds_<uid>) —
                # every theme toggle is saved while signed in.
                #
                # So `self.creds.get("theme", "light")` used to hand back the
                # "light" DEFAULT on those early callbacks and repaint the whole
                # app light, overriding the correct value __init__ had already
                # read from mobile_prefs — THAT is the ~1s light flash on
                # relaunch (dark 74 → light 210 → dark 74 in the frame capture).
                # It also poisoned the mobile_prefs cache by persisting "light".
                # The real theme only arrives on the LAST callback, once
                # _switch_user_creds has re-bootstrapped the per-user slot.
                #
                # Acting only on a REAL saved value leaves __init__'s
                # mobile_prefs-derived theme in place until the per-user slot
                # confirms it. Found by a second-opinion review after three
                # failed attempts that all targeted __init__ instead of here.
                _saved = self.creds.get("theme")
                if _saved in ("dark", "light"):
                    if _saved != T.MODE:
                        T.apply_theme(_saved)
                        self.page.theme_mode = (ft.ThemeMode.DARK if _saved == "dark"
                                                else ft.ThemeMode.LIGHT)
                    self._persist_theme(_saved)
            except Exception:
                pass
        self.render()
        # One-shot: only the FIRST bootstrap of the app process drives the
        # auto-onboarding check (matches desktop's single at-launch check).
        # Later bootstraps (a mid-session account switch via
        # _switch_user_creds) refresh theme/creds above but deliberately
        # don't reopen onboarding on top of whatever the user is doing.
        if not getattr(self, "_onboarding_auto_checked", False):
            self._onboarding_auto_checked = True
            try:
                self._maybe_show_onboarding()
            except Exception:
                pass

    def _switch_user_creds(self):
        """Load the signed-in user's OWN credential store (per-user), so accounts on
        the same device don't share keys / PAT / prefs. Called after sign-in, silent
        session restore, and sign-out (reverts to the shared default file)."""
        try:
            uid = (getattr(self, "user", None) or {}).get("id")
        except Exception:
            uid = None
        try:
            store.set_user(uid)
            E.set_current_user(uid)   # per-user local AI-usage ledger, same split
            self.creds = store.load()
            self._migrate_key_slots()  # legacy per-provider keys → per-model slots
            _sp = self.creds.get("provider")
            self._provider_choice = (_sp if _sp in E.AI_CONFIG
                                     else (E.active_providers()[:1] or ["anthropic"])[0])
            # Theme is a device display preference, not a per-account
            # credential — don't let switching to this account's own creds
            # slot (which may have no "theme" key, or a stale one from a
            # much older session) silently diverge from what's already on
            # screen. Doesn't call T.apply_theme: this path never changes
            # the rendered theme, only keeps self.creds in sync so a later
            # unrelated store.save(self.creds) can't drift it either.
            self.creds["theme"] = T.MODE
        except Exception:
            return
        self.connected = False        # re-connect with THIS user's own creds
        # a different account starts with a clean selection (not the previous user's)
        self.project = None
        self.plan_id = None
        self.story_ids = []
        self._projects = []
        self._plans = []
        self._setup_stories = None
        self._cp_iterations = []       # clear per-project load caches + their keys so
        self._cp_iter_for = None       # a new account reloads fresh (not the prev user's)
        self._reg_plans_for = None
        self._st_iterations = []
        self._st_iter_for = None
        self._reg_plan_cache = {}      # per-plan / per-sprint story caches (PERF step 4)
        self._cp_sprint_story_cache = {}
        # Per-user isolation of the Run/Automation/Report screens: never surface one
        # user's activity to the next. Abort any in-flight generation from the prior
        # session, then wipe its transient logs, flags and report.
        try: E.request_stop()          # stop old run/automation workers promptly
        except Exception: pass
        self.stop_flag = True          # (re)set False when the next run starts
        self._auto_stop = True
        self._run_active = False
        self._auto_running = False
        self._log_lines = []
        self._auto_log = []
        self._reset_auto_retry_state()
        self.last_report = None
        self._idle_warning_active = False   # drop any pending idle countdown
        self._idle_warn_cancel = True
        self._last_activity = time.time()
        try:
            if hasattr(self, "_links"):
                del self._links       # links are per-user too — reload for this user
        except Exception:
            pass
        # Task Manager (_tm_*) is per-user too — never let one signed-in user's
        # sprint/date-range pick, "Assigned to" selection, chosen stories, or
        # last-run report leak to the next account signed into the same running
        # app instance (seen live: a Viewer signing in after an Admin saw the
        # Admin's own selections still sitting in Section 2). task_manager.py's
        # _init(app) only ever seeds each _tm_* attribute ONCE per attribute
        # (`if not hasattr(app, k)`) — by design, so normal in-session use (toggling
        # scope mode, picking dates, switching sprints) never gets wiped — so
        # deleting them here, at the one place a real account switch is known to
        # be happening, is what makes the NEXT screen(app) call reseed fresh
        # defaults. Deleting rather than re-listing task_manager._init's defaults
        # here avoids the two copies drifting out of sync over time.
        try:
            for _k in [k for k in vars(self) if k.startswith("_tm_")]:
                delattr(self, _k)
        except Exception:
            pass
        try:
            th = self.creds.get("theme")
            # Only a REAL saved value — same rule as _on_secure_creds_ready.
            # self.creds here can be the file/default fallback while the
            # per-user vault re-bootstrap is still in flight, and persisting
            # that guess is what poisoned the mobile_prefs startup cache.
            if th in ("dark", "light"):
                T.apply_theme(th)
                self._persist_theme(th)   # prime the startup cache (see _persist_theme)
                self.page.theme_mode = (ft.ThemeMode.DARK if th == "dark"
                                        else ft.ThemeMode.LIGHT)
                self.page.bgcolor = T.RAIL
        except Exception:
            pass
        try:
            if self.creds.get("lang"):
                _saved = str(self.creds.get("lang")).strip().lower()
                self.lang = _saved if _saved in E.LANGUAGES else "ar"
            self.run_lang = self.lang   # a fresh session resets the run override
            _uil = str(self.creds.get("ui_lang") or "").strip().lower()
            self.ui_lang = _uil if _uil in E.LANGUAGES else "en"
            strings.set_ui_lang(self.ui_lang)
            try:
                self.page.rtl = strings.ui_is_rtl()
            except Exception:
                pass
            _t = self.creds.get("tool")
            if _t in ("titles", "steps"):
                self.tool = _t
        except Exception:
            pass
        try:
            # perf logging is a runtime global — re-apply THIS user's saved value;
            # keep the generator's output language in sync with their pref too.
            regression.set_perf(bool(self.creds.get("perf", True)))
            E.set_output_lang(getattr(self, "run_lang", self.lang))
        except Exception:
            pass
        # Reset the per-account engine globals (org + PAT) to THIS account's own
        # saved values (or blank if it has none) — never leave them holding
        # whichever account last connected during this app session.
        # E.set_credentials(org=...) can't be used for this: it only assigns
        # when the value is truthy (`if org: AZURE_ORG = ...`), by design, so a
        # Connect click that leaves the org field untouched never accidentally
        # blanks a saved value. That's correct for Connect, but wrong here —
        # switching to an account with no saved org/PAT must actually go blank.
        # E.reset_session_credentials() is the dedicated function for exactly
        # this case (see its docstring in engine.py for the full history —
        # this used to be a direct `E.AZURE_ORG = ...` poke here only, which is
        # how the org half of this got fixed but PAT was never covered by the
        # same reset).
        try:
            E.reset_session_credentials(org=self.creds.get("org"),
                                        pat=self.creds.get("pat"))
        except Exception:
            pass
        # Re-sync the Automation screen's fields from the NOW-correct per-user
        # creds — same pattern as theme/lang/tool just above. These are otherwise
        # only ever seeded ONCE, in __init__, and __init__ runs BEFORE
        # store.set_user(uid) ever points at this account's own file (that switch
        # happens here, later — silent session-restore resolves the signed-in user
        # asynchronously in the background). So __init__ seeded them from the
        # SHARED DEFAULT file, not this account's. A PAT/URL typed and saved
        # earlier in a signed-in session DID reach the correct per-user file (by
        # the time you can type into the screen, this function has already run
        # once and self.creds/CRED_FILE are already correct) — but on the NEXT
        # launch, __init__ reads the default file again before this ever runs, so
        # the field looked empty/reverted until now, even though the save itself
        # had actually succeeded. Applied unconditionally (like theme/lang above),
        # since this only runs at sign-in / startup-restore / sign-out — never
        # while you're actively editing the screen — so there's nothing in
        # progress here that a stale value could clobber.
        try:
            _map = {"auto_site_url": "auto_site_url", "auto_login_url": "auto_login_url",
                    "auto_login_user": "auto_login_user", "auto_login_pass": "auto_login_pass",
                    "auto_git_url": "git_url", "auto_git_branch": "git_branch",
                    "auto_git_token": "git_token", "auto_local_path": "auto_local_path"}
            for _attr, _key in _map.items():
                setattr(self, _attr, self.creds.get(_key, "") or "")
            if not (self.auto_git_branch or "").strip():
                self.auto_git_branch = "main"
        except Exception:
            pass
        # Pull the shared, Admin-configured email settings (see _push_org_email
        # / _refresh_org_settings) so THIS user's install sends reports the same
        # way as everyone else's, not just whatever's in their own local creds.
        try:
            self._refresh_org_settings()
        except Exception:
            pass
        try:
            self._refresh_identity_visuals()
        except Exception:
            pass

    def _refresh_identity_visuals(self):
        """Fetch private, signed avatar/logo URLs for the active identity only."""
        user = getattr(self, "user", None) or {}
        uid = str(user.get("id") or "")
        if not auth.configured() or not uid:
            self._identity_visuals = {"avatar_url": "", "organization_logo_url": ""}
            return

        def work():
            try:
                ok, data = auth.get_identity_visuals()
            except Exception:
                return
            # Never apply a delayed response belonging to an account that has
            # since signed out or switched on this same device.
            current = str((getattr(self, "user", None) or {}).get("id") or "")
            if not ok or current != uid or not isinstance(data, dict):
                return
            self._identity_visuals = {
                "avatar_url": str(data.get("avatar_url") or ""),
                "organization_logo_url": str(data.get("organization_logo_url") or ""),
            }
            if getattr(self, "user", None):
                def _apply():
                    # Desktop navigation deliberately preserves its existing
                    # rail to retain scroll position. Patch its pinned logo
                    # independently before the normal content refresh.
                    self._refresh_rail_logo()
                    self.render(preserve_rail=True)
                self.ui_safe(_apply)

        self._bg(work)

    def _upload_profile_picture(self):
        """Choose and upload a square avatar for the signed-in user."""
        if not auth.configured() or not getattr(self, "user", None):
            self._err(strings.t("profile_upload_unavailable"))
            return
        import image_assets

        message_for = {
            "size": "profile_image_size", "dimensions": "profile_image_dimensions",
            "format": "profile_image_format", "empty": "profile_image_picker",
        }

        def rejected(reason):
            self._err(strings.t(message_for.get(reason, "profile_image_picker")))

        def ready(payload):
            self._toast(strings.t("profile_uploading"))

            def work():
                try:
                    ok, data = auth.upload_profile_picture(**payload)
                except Exception as ex:
                    ok, data = False, str(ex)
                if not ok:
                    self.ui_safe(lambda: self._err(strings.t("image_upload_failed", error=str(data)[:160])))
                    return
                self._identity_visuals["avatar_url"] = str((data or {}).get("avatar_url") or "")
                self.ui_safe(lambda: (self._toast(strings.t("profile_photo_saved")),
                                      self.render(preserve_rail=True)))

            self._bg(work)

        image_assets.choose_square_image(self, strings.t("profile_choose"), ready, rejected)

    def _remove_profile_picture(self):
        """Remove the current user's cloud avatar and restore initials."""
        if not auth.configured() or not getattr(self, "user", None):
            self._err(strings.t("profile_upload_unavailable"))
            return
        self._toast(strings.t("profile_removing"))

        def work():
            try:
                ok, data = auth.remove_profile_picture()
            except Exception as ex:
                ok, data = False, str(ex)
            if not ok:
                self.ui_safe(lambda: self._err(strings.t("image_remove_failed", error=str(data)[:160])))
                return
            self._identity_visuals["avatar_url"] = ""
            self.ui_safe(lambda: (self._toast(strings.t("profile_photo_removed")),
                                  self.render(preserve_rail=True)))

        self._bg(work)

    def _no_access_screen(self):
        return ft.Container(
            ft.Column([
                ft.Container(ft.Icon(ft.Icons.LOCK_OUTLINE, size=34, color=T.RED),
                             width=76, height=76,
                             bgcolor=ft.Colors.with_opacity(0.12, T.RED),
                             border_radius=20, alignment=ft.Alignment.CENTER),
                ft.Container(height=16),
                ft.Text(strings.t("main_access_revoked"), size=20, weight=ft.FontWeight.BOLD, color=T.INK),
                ft.Container(height=8),
                ft.Text(strings.t("main_access_removed"), size=13, color=T.INK_3,
                        text_align=ft.TextAlign.CENTER, no_wrap=False),
                ft.Container(height=20),
                ghost_btn(strings.t("main_sign_out"), icon=ft.Icons.LOGOUT, on_click=self._sign_out),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
               alignment=ft.MainAxisAlignment.CENTER, spacing=0, tight=True),
            expand=True, alignment=ft.Alignment.CENTER, padding=ft.Padding.all(40))

    IDLE_MINUTES_DEFAULT = 30              # default; admins can change it in Settings
    IDLE_MINUTES_CHOICES = (0, 5, 15, 30, 60)   # 0 = off (never auto-logout)
    IDLE_WARN_SECONDS = 60                 # final-minute "renew or logout" countdown

    def _idle_minutes(self):
        return idle_watch.idle_minutes(self)

    def _set_idle_minutes(self, minutes):
        return idle_watch.set_idle_minutes(self, minutes)

    def _start_idle_watch(self):
        # Desktop concept (mobile Phase 1 gating): on a phone the OS lifecycle
        # suspends the app itself; re-auth on resume replaces idle-logout.
        if platform_caps.is_mobile():
            return None
        return idle_watch.start_idle_watch(self)

    def _sign_out(self, e=None, full=False):
        # ROOT CAUSE this guards against: on mobile, _account_chip()'s
        # compact avatar opens an AlertDialog popup (avatar + name + role +
        # this same Sign out button) via self._show_dialog(). Flet 0.85's
        # dialog API is a real STACK (page.show_dialog()/pop_dialog()), and
        # this button's on_click used to call _sign_out directly with
        # nothing ever popping that dialog back off — so after user=None
        # and the login screen rendered underneath, the stale popup stayed
        # sitting on top of it, looking exactly like the login card had
        # somehow kept the previous account's avatar/name/role visible.
        # Reported live as "logging out keeps the avatar on the login
        # screen". Closing whatever dialog is open FIRST (a no-op if none
        # is) fixes every entry point into sign-out, not just this one.
        #
        # Must close ALL stacked dialogs, not just the top one: Flet 0.85's
        # pop_dialog() removes a single entry, so an auto-logout that shows the
        # idle-warning dialog ON TOP of an already-open modal (e.g. "Create test
        # plan") popped only the warning and left the modal over the login
        # screen — reported live as "the modal is still open after auto logout".
        try:
            self._close_all_dialogs()
        except Exception:
            pass
        # BIOMETRIC-PROTECTED REFRESH TOKEN: when biometric unlock is on, a
        # normal sign-out LOCKS the app instead of destroying the session —
        # auth.sign_out() would revoke the token server-side AND wipe the local
        # cache, leaving fingerprint with nothing to unlock on relaunch (the
        # reported "logged out + closed the app → fingerprint doesn't log me
        # in"). Keeping the refresh token means the launch gate can restore the
        # session after a successful fingerprint. The token is only ever usable
        # THROUGH that gate: every silent-restore path is guarded by
        # bio_required()/bio_gate_passed(). Pass full=True for a real,
        # complete sign-out (revoke + wipe) — and biometrics off behaves
        # exactly as before.
        _bio_lock = False
        if not full and platform_caps.is_mobile():
            try:
                import mobile_prefs as _mp
                _bio_lock = bool(_mp.get("require_biometric", False))
            except Exception:
                _bio_lock = False
        if not _bio_lock:
            try:
                auth.sign_out()
            except Exception:
                pass
        # Re-arm the biometric login gate (mobile): without this, THIS launch's
        # gate stayed "passed" from the initial unlock, so a cached-session
        # silent restore right after logout could sign the user back in with no
        # fresh biometric check — the reported "biometrics not working when
        # logging out". No-op on desktop / when biometrics isn't required.
        if platform_caps.is_mobile():
            try:
                import secure_store_mobile as _ssm
                _ssm.rearm_gate()
            except Exception:
                pass
        # Blocks _on_secure_creds_ready's auto-restore for the rest of this
        # process — _switch_user_creds() below re-bootstraps the vault and
        # re-enters that callback, which would otherwise sign the user straight
        # back in (see the guard there). Cleared by a successful sign-in.
        # Preserve the current account's Settings interface language as the
        # shared login-screen default. That keeps the sign-in gate visually in
        # step with Settings without copying credentials or other user data.
        _login_ui_lang = str(getattr(self, "ui_lang", "en") or "en").lower()
        _login_ui_lang = _login_ui_lang if _login_ui_lang in E.LANGUAGES else "en"
        self._user_signed_out = True
        self.user = None
        # Drop signed-in-user-scoped admin caches so the NEXT account never
        # sees the previous user's data (esp. the Users list — see
        # users_screen._load, which also re-fetches when the uid changes).
        self._users_list = None
        self._users_list_uid = None
        self._auth_msg = None           # clear login-card state (also the reset gate)
        self._gate_busy = False
        self._switch_user_creds()       # revert to the shared default cred file
        try:
            self.ui_lang = _login_ui_lang
            strings.set_ui_lang(_login_ui_lang)
            self.page.rtl = strings.ui_is_rtl()
            self.creds["ui_lang"] = _login_ui_lang
            store.save(self.creds)
        except Exception:
            pass
        # The login screen keeps its OWN theme flag (login.py's app._login_theme —
        # only ever set by its own toggle or on a successful sign-in), which never
        # syncs FROM the app's theme on the way back OUT. Left alone, it falls back
        # to its hardcoded dark default the first time this app instance ever
        # returns to the login gate, so the screen can visibly flip to dark even
        # though the app (and the account that just signed out) was in light mode.
        # T.MODE is already correct at this point — _switch_user_creds() above just
        # (re)applied the now-active creds file's saved theme — so hand it straight
        # to the login screen instead of leaving that gap.
        try:
            self._login_theme = T.MODE if T.MODE in ("dark", "light") else "dark"
        except Exception:
            pass
        self._auth_shown = False        # replay the entrance animation
        self.active = "setup"
        self.render()

    def _with_window_chrome(self, root):
        # Mobile has no window to chrome (mobile Phase 1 gating): no title bar
        # was hidden, so no custom min/max/close buttons or drag strip needed.
        if platform_caps.is_mobile():
            return root
        return window_chrome.with_window_chrome(self, root)

    def _entrance(self, child, dy=0.05, scale=0.98, dur=460):
        """Wrap a control so it fades + rises into place (a soft 'landing'). Flet
        animates a property change made AFTER the control is on the page, so we
        start offset/scaled/transparent and settle to normal from a bg tick."""
        c = ft.Container(child, offset=ft.Offset(0, dy), scale=scale, opacity=0.0,
                         animate_offset=dur, animate_scale=dur, animate_opacity=dur)

        def _go():
            import time
            time.sleep(0.05)
            try:
                c.offset = ft.Offset(0, 0)
                c.scale = 1.0
                c.opacity = 1.0
                c.update()
            except Exception:
                pass
        try:
            self._bg(_go)
        except Exception:
            threading.Thread(target=_go, daemon=True).start()
        return c

    def _login_parallax(self, e):
        return login.login_parallax(self, e)

    def _login_gate(self):
        return login.login_gate(self)

    def _organization_logo(self, size=48):
        """Render the current org logo while retaining the QA Studio fallback."""
        source = str((getattr(self, "_identity_visuals", {}) or {}).get("organization_logo_url") or "")
        if source:
            return ft.Container(
                ft.Image(src=source, width=size, height=size,
                         fit=ft.BoxFit.COVER, border_radius=12),
                width=size, height=size, border_radius=12,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                border=ft.Border.all(1, T.RAIL_LINE),
                tooltip=strings.t("org_logo"))
        return ft.Container(logo_img(size), width=size, height=size,
                            bgcolor=(None if _logo_b64() else T.VIOLET),
                            gradient=(None if _logo_b64() else grad(T.GRAD_LOGO)),
                            border_radius=12,
                            shadow=(None if _logo_b64() else _btn_shadow(T.STORY, 0.5)),
                            alignment=ft.Alignment.CENTER)

    def _refresh_rail_logo(self):
        """Refresh the pinned desktop brand without replacing the rail scroller."""
        holder = getattr(self, "_rail_brand_logo", None)
        if holder is None:
            return
        source = str((getattr(self, "_identity_visuals", {}) or {}).get("organization_logo_url") or "")
        if source:
            holder.content = ft.Image(src=source, width=48, height=48,
                                      fit=ft.BoxFit.COVER, border_radius=12)
            holder.bgcolor = None
            holder.gradient = None
            holder.shadow = None
            holder.border = ft.Border.all(1, T.RAIL_LINE)
            holder.clip_behavior = ft.ClipBehavior.HARD_EDGE
            holder.tooltip = strings.t("org_logo")
        else:
            holder.content = logo_img(48)
            holder.bgcolor = None if _logo_b64() else T.VIOLET
            holder.gradient = None if _logo_b64() else grad(T.GRAD_LOGO)
            holder.shadow = None if _logo_b64() else _btn_shadow(T.STORY, 0.5)
            holder.border = None
            holder.clip_behavior = None
            holder.tooltip = None
        try:
            holder.update()
        except Exception:
            pass

    def rail(self):
        # Show each tab only if the user is permitted to open it (per-user nav
        # capabilities). When auth is off, can() is True so every tab shows.
        nav_items = []
        for n in T.NAV:
            if not self.can_open_screen(n["id"]):
                continue
            # Platform gating (mobile Phase 0): same skip mechanism as the
            # per-user capability gate above, keyed on what the PLATFORM can
            # do — Automation needs a desktop filesystem/toolchain.
            if n["id"] == "automation" and not platform_caps.has_automation():
                continue
            st = self.nav_state.get(n["id"], "")
            is_active = (n["id"] == self.active)
            color = "#FFFFFF" if is_active else ("#B8B5C2" if st == "done" else T.RAIL_DIM)
            bg = ft.Colors.with_opacity(0.16, T.VIOLET) if is_active else None
            leading_icon = getattr(ft.Icons, n.get("icon", "CIRCLE"), ft.Icons.CIRCLE)
            icon_color = "#FFFFFF" if is_active else ("#B8B5C2" if st == "done" else T.RAIL_DIM)
            # trailing: ✓ when this stage is done; Report shows ✓ once a report
            # exists (run finished), and keeps it until connection lost / new run.
            _report_done = (n["id"] == "report"
                            and self.last_report is not None
                            and getattr(self, "_run_finished", False))
            _is_done = (st == "done") or _report_done
            ix = "✓" if _is_done else n["ix"]
            ixcolor = "#A99BFF" if is_active else (T.GREEN if _is_done else "#56535F")
            clickable = (st == "done" or is_active
                         or (n["id"] == "report" and self.last_report is not None)
                         or (n["id"] == "setup")
                         or (n["id"] == "automation")
                         or (n["id"] == "performance")
                         or (n["id"] == "regression")
                         or (n["id"] == "testplan")
                         or (n["id"] == "titles")
                         or (n["id"] == "task_manager")
                         or (n["id"] == "links")
                         or (n["id"] == "users")
                         or (n["id"] == "orgs")
                         or (n["id"] == "audit")
                         or (n["id"] == "ai_usage")
                         or (n["id"] == "run" and (getattr(self, "_run_active", False)
                                                   or st == "active"
                                                   or self.last_report is not None)))
            # active indicator bar on the far left — bright so it pops on the
            # active item's indigo gradient (plain VIOLET blended in before)
            indicator = ft.Container(width=4, height=22,
                                     bgcolor=("#FFFFFF" if is_active else ft.Colors.TRANSPARENT),
                                     border_radius=4, animate=200)
            def _nav_hover(e, base=bg):
                try:
                    hov = e.data in (True, "true", "True")
                    e.control.bgcolor = (ft.Colors.with_opacity(0.14, T.VIOLET)
                                         if hov else base)
                    e.control.offset = ft.Offset(0.02, 0) if hov else ft.Offset(0, 0)
                    e.control.update()
                except Exception:
                    pass
            nav_items.append(
                ft.Container(
                    ft.Row([
                        indicator,
                        ft.Icon(leading_icon, size=17, color=icon_color),
                        ft.Text(self._nav_label(n), size=13.5, weight=ft.FontWeight.BOLD, color=color),
                        ft.Container(expand=True),
                        ft.Text(ix, size=10.5, weight=ft.FontWeight.BOLD, color=ixcolor,
                                font_family=T.F_MONO),
                    ], spacing=9),
                    padding=ft.Padding.only(left=6, right=12, top=9, bottom=9),
                    bgcolor=(None if is_active else bg),
                    gradient=(grad(T.GRAD_NAV_ACT) if is_active else None),
                    border_radius=11,
                    shadow=(ft.BoxShadow(blur_radius=16, spread_radius=-6,
                                         offset=ft.Offset(0, 5),
                                         color=ft.Colors.with_opacity(0.45, T.VIOLET))
                            if is_active else None),
                    offset=ft.Offset(0, 0), animate=150, animate_offset=150,
                    on_hover=(_nav_hover if (clickable and not is_active) else None),
                    on_click=(lambda e, nid=n["id"]: self.goto(nid)) if clickable else None,
                ))
        # During a desktop navigation we keep the existing scrollable Column
        # mounted and replace only its children.  Recreating the Column is what
        # made Flet reset the sidebar to offset zero on every selection.
        if (getattr(self, "_preserve_desktop_rail", False)
                and getattr(self, "_desktop_rail", None) is not None
                and getattr(self, "_rail_scroll", None) is not None):
            self._rail_scroll.controls = nav_items
            return self._desktop_rail
        conn_color  = T.GREEN if self.connected else T.INK_3
        _prov = self.current_provider()
        conn_text   = (T.disp_name(_prov) + " · Claude") if (self.connected and _prov=="anthropic")                       else (T.disp_name(_prov) if self.connected else strings.t("conn_not_connected"))
        conn_sub    = strings.t("conn_connected") if self.connected else strings.t("conn_enter_creds")
        brand_logo = self._organization_logo(48)
        self._rail_brand_logo = brand_logo
        return ft.Container(
            width=244, bgcolor=T.RAIL, gradient=grad(T.GRAD_RAIL, diagonal=False),
            content=ft.Column([
                # The native window actions belong on the top-right. A compact
                # desktop identifier here replaces the macOS-style traffic
                # lights, which were misleading on Windows.
                ft.Container(
                    ft.Row([
                        ft.Container(ft.Icon(ft.Icons.COMPUTER, size=14, color=T.RAIL_DIM),
                                     width=24, height=24, border_radius=7,
                                     alignment=ft.Alignment.CENTER, bgcolor=T.CARD_2),
                        ft.Text("WINDOWS DESKTOP", size=9, weight=ft.FontWeight.BOLD,
                                color=T.RAIL_DIM, font_family="Consolas",
                                style=ft.TextStyle(letter_spacing=1.0)),
                    ], spacing=7, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.Padding.only(left=14, top=9, bottom=4)),
                # Brand (logo + Check updates) is PINNED — it never scrolls.
                ft.Container(
                    ft.Row([
                        brand_logo,
                        ft.Column([
                            ft.Text("QA Studio", size=15, weight=ft.FontWeight.BOLD, color=T.RAIL_INK),
                            ft.Row([
                                ft.Text(f"v{E.local_version()}", size=10, color=T.RAIL_DIM,
                                        weight=ft.FontWeight.BOLD),
                                self._check_updates_chip(),
                            ], spacing=7, tight=False,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        ], spacing=3, expand=True),
                    ], spacing=11, vertical_alignment=ft.CrossAxisAlignment.START),
                    padding=ft.Padding.symmetric(vertical=16, horizontal=6)),
                ft.Container(ft.Text(strings.t("pipeline"), size=10, weight=ft.FontWeight.BOLD,
                                     color="#615E6E"), padding=ft.Padding.only(left=18, top=14, bottom=6)),
                # Only the nav list scrolls; the brand stays pinned above it.
                ft.Container(self._rail_nav_column(nav_items),
                             padding=ft.Padding.symmetric(vertical=8, horizontal=12),
                             expand=True),
                # Help & guide — searchable briefing for every feature (everyone).
                ft.Container(
                    ft.Row([
                        ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=T.RAIL_INK),
                        ft.Text(strings.t("help_guide"), size=12, weight=ft.FontWeight.BOLD,
                                color=T.RAIL_INK),
                        ft.Container(expand=True),
                    ], spacing=9),
                    on_click=lambda e: self._open_help_guide(),
                    tooltip=strings.t("main_help_tip"),
                    ink=True, padding=ft.Padding.symmetric(vertical=10, horizontal=12),
                    margin=ft.Margin.only(left=10, right=10, bottom=4),
                    border_radius=10, bgcolor=ft.Colors.with_opacity(0.04, "#FFFFFF"),
                    border=ft.Border.all(1, T.RAIL_LINE),
                    offset=ft.Offset(0, 0), animate_offset=140,
                    on_hover=self._rail_btn_hover(ft.Colors.with_opacity(0.04, "#FFFFFF"))),
                # Settings entry — shown only to users permitted to open it.
                (ft.Container(
                    ft.Row([
                        ft.Icon(ft.Icons.SETTINGS_OUTLINED, size=16,
                                color=("#FFFFFF" if self.active == "settings" else T.RAIL_INK)),
                        ft.Text(strings.t("settings"), size=12, weight=ft.FontWeight.BOLD,
                                color=("#FFFFFF" if self.active == "settings" else T.RAIL_INK)),
                        ft.Container(expand=True),
                    ], spacing=9),
                    on_click=lambda e: self.goto("settings"),
                    tooltip=strings.t("main_settings_tip"),
                    ink=True, padding=ft.Padding.symmetric(vertical=10, horizontal=12),
                    margin=ft.Margin.only(left=10, right=10, bottom=4),
                    border_radius=10,
                    bgcolor=(ft.Colors.with_opacity(0.16, T.VIOLET) if self.active == "settings"
                             else ft.Colors.with_opacity(0.04, "#FFFFFF")),
                    border=ft.Border.all(1, T.RAIL_LINE),
                    offset=ft.Offset(0, 0), animate_offset=140,
                    on_hover=(self._rail_btn_hover(ft.Colors.with_opacity(0.04, "#FFFFFF"))
                              if self.active != "settings" else None))
                 if self.can("nav.settings") else ft.Container(height=0)),
                # Remote Runs — GitHub-executed runs live status/activity viewer
                # (REMOTE_RUNS.md). Shown only when Supabase sign-in is actually
                # configured (remote runs don't exist otherwise) and the user can
                # start runs at all (same cap "Run remotely" itself is gated on).
                (ft.Container(
                    ft.Row([
                        ft.Icon(ft.Icons.CLOUD_QUEUE_OUTLINED, size=16,
                                color=("#FFFFFF" if self.active == "remote_runs" else T.RAIL_INK)),
                        ft.Text(strings.t("remote_runs"), size=12, weight=ft.FontWeight.BOLD,
                                color=("#FFFFFF" if self.active == "remote_runs" else T.RAIL_INK)),
                        ft.Container(expand=True),
                    ], spacing=9),
                    on_click=lambda e: self.goto("remote_runs"),
                    tooltip=strings.t("main_remote_tip"),
                    ink=True, padding=ft.Padding.symmetric(vertical=10, horizontal=12),
                    margin=ft.Margin.only(left=10, right=10, bottom=4),
                    border_radius=10,
                    bgcolor=(ft.Colors.with_opacity(0.16, T.VIOLET) if self.active == "remote_runs"
                             else ft.Colors.with_opacity(0.04, "#FFFFFF")),
                    border=ft.Border.all(1, T.RAIL_LINE),
                    offset=ft.Offset(0, 0), animate_offset=140,
                    on_hover=(self._rail_btn_hover(ft.Colors.with_opacity(0.04, "#FFFFFF"))
                              if self.active != "remote_runs" else None))
                 if auth.configured() and self.can(auth.CAP_RUN) else ft.Container(height=0)),
                # theme toggle (light default · dark secondary)
                ft.Container(
                    ft.Row([
                        ft.Icon(ft.Icons.DARK_MODE_OUTLINED if T.MODE == "light"
                                else ft.Icons.LIGHT_MODE_OUTLINED,
                                size=16, color=T.RAIL_INK),
                        ft.Text(strings.t("dark_mode") if T.MODE == "light" else strings.t("light_mode"),
                                size=12, weight=ft.FontWeight.BOLD, color=T.RAIL_INK),
                        ft.Container(expand=True),
                        ft.Text(T.MODE.upper(), size=9.5, weight=ft.FontWeight.BOLD,
                                color=T.RAIL_DIM, font_family=T.F_MONO),
                    ], spacing=9),
                    on_click=lambda e: self._toggle_theme(),
                    tooltip=strings.t("main_theme_tip"),
                    ink=True, padding=ft.Padding.symmetric(vertical=10, horizontal=12),
                    margin=ft.Margin.only(left=10, right=10, bottom=4),
                    border_radius=10, bgcolor=ft.Colors.with_opacity(0.04, "#FFFFFF"),
                    border=ft.Border.all(1, T.RAIL_LINE),
                    offset=ft.Offset(0, 0), animate_offset=140,
                    on_hover=self._rail_btn_hover(ft.Colors.with_opacity(0.04, "#FFFFFF"))),
                ft.Container(
                    ft.Row([
                        ft.Container(
                            self._provider_logo(_prov, 30) if self.connected
                            else ft.Container(width=10, height=10, bgcolor=conn_color,
                                              border_radius=5),
                            width=30, height=30,
                            bgcolor=(None if self.connected else T.RAIL_2),
                            border_radius=8, alignment=ft.Alignment.CENTER),
                        ft.Column([
                            ft.Text(conn_text, size=12, weight=ft.FontWeight.BOLD, color=T.RAIL_INK),
                            ft.Row([
                                ft.Container(width=7, height=7, bgcolor=conn_color, border_radius=4),
                                ft.Text(conn_sub, size=10.5, color=T.RAIL_DIM, weight=ft.FontWeight.BOLD),
                            ], spacing=5, tight=True),
                        ], spacing=2, expand=True),
                    ], spacing=9),
                    padding=14, margin=ft.Margin.all(10), bgcolor=ft.Colors.with_opacity(0.04, "#FFFFFF"),
                    border_radius=10, border=ft.Border.all(1, T.RAIL_LINE)),
            ], spacing=0, expand=True),
        )

    def _account_chip(self, compact=None):
        """Signed-in user pill (avatar + name + role) with a sign-out button — lives
        in the top-right of the header so it's always visible, regardless of how many
        nav tabs there are. Returns None when auth is off / nobody is signed in.

        compact=True (mobile default): the full pill — avatar + name text +
        role badge + a separate logout icon, sized for a desktop header —
        has no give left once a hamburger button AND an expand=True title
        column are also fighting for the same ~390px width. Flutter's Row
        doesn't shrink non-Expanded children below their intrinsic size, so
        this fixed-width chip simply didn't fit and visually overlapped the
        title text underneath it (confirmed live on Users/Task
        Manager/Home). Compact mode drops to just the 34px avatar circle —
        tap it to see name/role/sign-out in a small popup instead of always
        inline — cutting the header's fixed-width footprint from ~200px to
        ~40px, which is what title_col's ellipsis fallback was actually
        sized to co-exist with."""
        u = getattr(self, "user", None)
        if not auth.configured() or not u:
            return None
        if compact is None:
            compact = platform_caps.is_mobile()
        _op = lambda c, o: ft.Colors.with_opacity(o, c)
        initial = (u.get("name") or u.get("email") or "?").strip()[:1].upper()
        name = u.get("name") or u.get("email") or "Signed in"
        role = u.get("role", "Viewer")
        role_col = {"Admin": T.VIOLET, "Member": getattr(T, "GREEN", "#1F9D57"),
                    "Viewer": T.INK_3}.get(role, T.INK_3)

        # Image uploads are center-cropped to a validated square before server
        # upload. The holder still clips the signed URL into the same circular
        # space used by the initials fallback, so it cannot disturb the header.
        initial_text = ft.Text(initial, size=14, weight=ft.FontWeight.W_800, color="#FFFFFF")
        avatar_url = str((getattr(self, "_identity_visuals", {}) or {}).get("avatar_url") or "")
        avatar_content = (ft.Image(src=avatar_url, width=34, height=34,
                                   fit=ft.BoxFit.COVER, border_radius=17)
                          if avatar_url else initial_text)
        avatar = ft.Container(
            avatar_content,
            width=34, height=34, border_radius=17, alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            gradient=(None if avatar_url else ft.LinearGradient(
                begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
                colors=[T.VIOLET, getattr(T, "VIOLET_H", T.VIOLET)])),
            shadow=ft.BoxShadow(blur_radius=10, spread_radius=-2, offset=ft.Offset(0, 2),
                                color=_op(T.VIOLET, 0.5)))
        avatar_wrap = ft.Stack([
            avatar,
            ft.Container(width=11, height=11, border_radius=6, bgcolor="#22C55E",
                         border=ft.Border.all(2, T.CARD), right=-1, bottom=-1),
        ], width=34, height=34, clip_behavior=ft.ClipBehavior.NONE)
        avatar_display = avatar_wrap
        if avatar_url:
            # Flet 0.85's native tooltip accepts text only and header parents
            # clip an out-of-bounds hover card. A click-to-expand dialog is
            # therefore the reliable full-resolution preview on Windows;
            # hover keeps the discovery hint without destabilizing the header.
            def _open_avatar_preview(e=None):
                dialog = ft.AlertDialog(
                    modal=False,
                    title=ft.Text(strings.t("avatar_preview_title"), size=15,
                                  weight=ft.FontWeight.W_800, color=T.INK),
                    content=ft.Container(
                        ft.Image(src=avatar_url, width=300, height=300,
                                 fit=ft.BoxFit.COVER, border_radius=150),
                        width=308, height=308, padding=4, border_radius=154,
                        bgcolor=T.CARD_2, clip_behavior=ft.ClipBehavior.HARD_EDGE),
                    actions=[ft.TextButton(strings.t("avatar_preview_close"),
                                           on_click=lambda e: self._close_all_dialogs())],
                    bgcolor=T.CARD)
                self._show_dialog(dialog)

            if not compact:
                # Stack itself does not reliably dispatch a pointer click on
                # Flet 0.85. Wrap it in an ink-enabled Container instead.
                avatar_display = ft.Container(
                    avatar_wrap, on_click=_open_avatar_preview, ink=True,
                    border_radius=20, padding=3,
                    tooltip=strings.t("avatar_tip_preview"))

        role_pill = ft.Container(
            ft.Text(strings.t("role_" + str(role).lower()).upper(), size=8.5, weight=ft.FontWeight.W_800, color=role_col,
                    style=ft.TextStyle(letter_spacing=0.7)),
            bgcolor=_op(role_col, 0.14), border_radius=6,
            padding=ft.Padding.symmetric(vertical=2, horizontal=6))

        logout = ft.Container(
            ft.Icon(ft.Icons.LOGOUT, size=16, color=T.INK_2),
            on_click=self._sign_out, ink=True, border_radius=10, padding=9,
            tooltip=strings.t("main_sign_out"), animate=120)

        def _lo_hover(e, _c=logout):
            try:
                on = e.data in (True, "true", "True")
                _c.bgcolor = _op(T.RED, 0.12) if on else None
                _c.content.color = T.RED if on else T.INK_2
                _c.update()
            except Exception:
                pass
        logout.on_hover = _lo_hover

        if compact:
            def _open_account_popup(e):
                dlg = ft.AlertDialog(
                    modal=False,
                    content=ft.Container(
                        ft.Row([
                            avatar_wrap,
                            ft.Column([
                                ft.Text(name, size=14, weight=ft.FontWeight.W_800,
                                        color=T.INK, max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Container(role_pill, margin=ft.Margin.only(top=4)),
                            ], spacing=0, tight=True, expand=True),
                            logout,
                        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        width=280, padding=ft.Padding.symmetric(vertical=4)))
                self._show_dialog(dlg)
            return ft.Container(
                avatar_wrap, on_click=_open_account_popup, ink=True,
                border_radius=20, padding=3, tooltip=name)

        name_text = ft.Text(name, size=12.5, weight=ft.FontWeight.W_800, color=T.INK,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        # Keep stable handles for the in-place profile refresh from Users.
        # Rebuilding the full page just to update this small header chip causes
        # the Windows scroll jump the user-management screen avoids elsewhere.
        self._account_initial_text = initial_text
        self._account_name_text = name_text
        chip = ft.Container(
            ft.Row([
                avatar_display,
                ft.Column([
                    name_text,
                    ft.Container(role_pill, margin=ft.Margin.only(top=3)),
                ], spacing=0, tight=True),
                ft.Container(width=2),
                logout,
            ], spacing=10, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.only(left=7, right=5, top=5, bottom=5),
            bgcolor=T.CARD, border_radius=999, border=ft.Border.all(1, T.BORDER),
            shadow=ft.BoxShadow(blur_radius=20, spread_radius=-8, offset=ft.Offset(0, 6),
                                color=_op("#000000", 0.22)),
            animate=140)

        def _chip_hover(e, _c=chip):
            try:
                on = e.data in (True, "true", "True")
                _c.border = ft.Border.all(1.4 if on else 1,
                                          _op(T.VIOLET, 0.55) if on else T.BORDER)
                _c.bgcolor = _op(T.VIOLET, 0.05) if on else T.CARD
                _c.update()
            except Exception:
                pass
        chip.on_hover = _chip_hover
        return chip

    def _refresh_account_identity(self):
        """Patch the desktop account chip after the signed-in user's name changes.

        This deliberately updates only the two affected controls; Users keeps
        its open panel and scroll position intact.
        """
        u = getattr(self, "user", None) or {}
        name = u.get("name") or u.get("email") or "Signed in"
        initial = name.strip()[:1].upper() or "?"
        for control, value in ((getattr(self, "_account_name_text", None), name),
                               (getattr(self, "_account_initial_text", None), initial)):
            if control is None:
                continue
            try:
                control.value = value
                control.update()
            except Exception:
                pass

    def _toggle_theme(self):
        new = "dark" if getattr(T, "MODE", "light") == "light" else "light"
        try:
            T.apply_theme(new)
            self._persist_theme(new)   # startup cache (see _persist_theme)
        except Exception:
            pass
        # Mobile: secure_store_mobile's OS-keychain read is async and can
        # still be in flight when this fires (e.g. a tap on the login
        # screen's theme toggle, seconds after launch). That in-flight GET
        # can resolve AFTER this toggle's SET lands and unconditionally
        # overwrites the in-memory cache with the pre-toggle value once it
        # completes (_on_secure_creds_ready() reruns T.apply_theme from
        # whatever store.load() returns then) — a classic concurrent
        # GET/SET race, and exactly what looked like "toggle to light, then
        # it silently reverts to dark". This flag tells that later callback
        # the user has since made an explicit local choice that must win.
        self._theme_touched = True
        try:
            self.creds["theme"] = new
            store.save(self.creds)
        except Exception:
            pass
        try:
            self.page.bgcolor = T.RAIL   # window frame; content wash is per-theme in shell()
            self.page.theme_mode = (ft.ThemeMode.DARK if T.MODE == "dark"
                                    else ft.ThemeMode.LIGHT)
        except Exception:
            pass
        self.render()

    def current_provider(self):
        return getattr(self, "_provider_choice", None) or (E.active_providers()[:1] or ["anthropic"])[0]

    # brand colour + monogram per provider (fallback when no logo file is present)
    PROVIDER_BRAND = {
        "anthropic": ("#D97757", "A"),
        "openai":    ("#10A37F", "O"),
        "gemini":    ("#1A73E8", "G"),
        "google":    ("#1A73E8", "G"),
        "nvidia":    ("#76B900", "N"),
        "mistral":   ("#FF7000", "M"),
        "groq":      ("#F55036", "G"),
        "deepseek":  ("#4D6BFE", "D"),
        "azure":     ("#0078D4", "Az"),
        "azure_openai": ("#0078D4", "Az"),
        "ollama":    ("#111111", "Ol"),
        "qwen":      ("#615CED", "Q"),
        "manus":     ("#5A4FE0", "Mn"),
        "cohere":    ("#39594D", "C"),
        "xai":       ("#111111", "X"),
        "cerebras":  ("#F15A22", "Cb"),
        "openrouter": ("#6467F2", "Or"),
    }

    # filename aliases: provider id -> logo basename(s) to look for
    PROVIDER_LOGO_ALIAS = {
        "azure": "azure_openai",
        "google": "gemini",
    }

    def _provider_logo(self, prov, size=30):
        key = (prov or "").lower()
        color, glyph = self.PROVIDER_BRAND.get(
            key, (T.VIOLET, (prov[:1].upper() if prov else "?")))
        # Use a real logo image if one is bundled. Files live in providers/<id>.png
        # (also checks assets/providers/ and the app root). .png and .webp both work.
        try:
            import os
            here = os.path.dirname(os.path.abspath(__file__))
            names = [key]
            alias = self.PROVIDER_LOGO_ALIAS.get(key)
            if alias:
                names.append(alias)
            dirs = [os.path.join(here, "providers"),
                    os.path.join(here, "assets", "providers"),
                    here]
            for nm in names:
                for d in dirs:
                    for ext in (".png", ".webp"):
                        cand = os.path.join(d, nm + ext)
                        if os.path.exists(cand):
                            return ft.Container(
                                ft.Image(src=cand, width=size, height=size),
                                width=size, height=size, bgcolor="#FFFFFF",
                                border_radius=int(size * 0.28),
                                padding=ft.Padding.all(max(2, int(size * 0.12))),
                                alignment=ft.Alignment.CENTER)
        except Exception:
            pass
        return ft.Container(
            ft.Text(glyph, size=int(size * 0.46), weight=ft.FontWeight.W_800,
                    color="#FFFFFF", font_family=T.F_UI),
            width=size, height=size, bgcolor=color, border_radius=int(size * 0.28),
            alignment=ft.Alignment.CENTER)

    def topbar(self, title, sub=None, right=None, badge=None, wrap=True):
        title_ctl = ft.Text(title, size=27, weight=ft.FontWeight.W_800, color=T.INK,
                            no_wrap=True)
        if badge:
            head = ft.Row([title_ctl,
                ft.Container(ft.Text(badge, size=11, weight=ft.FontWeight.BOLD,
                                     color=T.VIOLET, font_family=T.F_MONO),
                             padding=ft.Padding.symmetric(vertical=5, horizontal=10),
                             bgcolor=T.VIOLET_SOFT, border_radius=8,
                             border=ft.Border.all(1, "#E0E5FF"))],
                spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        else:
            head = title_ctl
        left = [head]
        sub_ctl = None
        if sub:
            sub_ctl = ft.Text(sub, size=14, color=T.INK_2, weight=ft.FontWeight.W_500)
            left.append(sub_ctl)
        title_col = ft.Column(left, spacing=3, tight=True)
        _mobile = platform_caps.is_mobile()
        if _mobile:
            # Mobile header-overflow fix: `tight=True` sizes this column to
            # its content's INTRINSIC width — for a Text with no wrap/width
            # constraint that's "as wide as the whole string on one line",
            # so any subtitle longer than a few words (e.g. "Manage who can
            # access QA Studio and grant/revoke individual tabs and
            # actions.") rendered straight past the physical screen edge
            # instead of wrapping or truncating (confirmed live: Users, AI
            # Usage, Regression Plan, Task Manager headers all cut off
            # mid-word). Fix: let this column take the Row's real remaining
            # width (expand=True) instead of a separate always-expand spacer
            # fighting it for space, and ellipsize both lines at that width.
            title_col.tight = False
            title_col.expand = True
            # WRAP (the default) lets the header run onto a second line instead
            # of ellipsizing. Ellipsis used to be the behaviour here — it kept
            # the header a fixed height, but it silently HID real information on
            # every screen with a descriptive subtitle ("Plan & estimate test
            # effort acros…", "GitHub-executed runs — live status & acti…").
            # Wrapping solves the original overflow problem just as well, since
            # it's capped at 2 lines and still ellipsizes past that, so a long
            # subtitle can never push the body off-screen. Pass wrap=False to
            # opt a screen back into single-line truncation.
            try:
                title_ctl.no_wrap = not wrap
                title_ctl.max_lines = 2 if wrap else None
                title_ctl.overflow = ft.TextOverflow.ELLIPSIS
            except Exception:
                pass
            if sub_ctl is not None:
                try:
                    sub_ctl.no_wrap = not wrap
                    sub_ctl.max_lines = 2 if wrap else None
                    sub_ctl.overflow = ft.TextOverflow.ELLIPSIS
                except Exception:
                    pass
            row = [title_col]
        else:
            row = [title_col, ft.Container(expand=True)]
        # The `right` element is a decorative per-screen status tag ("Credentials
        # saved on this device", "Completed", …). On a phone it competes with
        # the hamburger + title + avatar for a ~390px header and shoved the
        # rightmost item (the avatar) off the physical edge — reported live,
        # worst on Setup whose tag is the widest. Drop it on mobile; it's
        # supplementary and the info it conveys is available on-screen anyway.
        if right and not _mobile:
            row.append(right)
        _acct = self._account_chip()
        if _acct is not None:
            row.append(_acct)
        if _mobile:
            # Phone header (mobile Phase 2): hamburger opens the nav drawer —
            # shell() skips the permanent rail on mobile (it ate half the
            # width) — and the title shrinks so the row fits. Attach the drawer
            # shell NOW (idempotent) so it's synced to the client by the time
            # the user can tap — the first-tap reliability fix (see
            # _ensure_nav_drawer).
            try:
                self._ensure_nav_drawer()
            except Exception:
                pass
            row.insert(0, ft.IconButton(ft.Icons.MENU, icon_size=24,
                                        icon_color=T.INK,
                                        on_click=lambda e: self._open_nav_drawer()))
            try:
                title_ctl.size = 20
            except Exception:
                pass
        # Glass top bar: a translucent frosted gradient with a backdrop blur so
        # scrolled content shows softly behind it. Falls back to an opaque bar on
        # older Flet builds that lack ft.Blur.
        # Theme-aware surfaces (so the bar goes dark in dark mode).
        _has_blur = hasattr(ft, "Blur")
        if _has_blur:
            # More translucent so scrolled cards read through the frosted glass.
            _g = ft.LinearGradient(
                begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                colors=[ft.Colors.with_opacity(0.72, T.CARD),
                        ft.Colors.with_opacity(0.52, T.CARD_2)])
        else:
            _g = ft.LinearGradient(
                begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                colors=[T.CARD, T.CARD_2])
        bar = ft.Container(
            ft.Row(row, spacing=(8 if _mobile else 14),
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            height=94,
            # Tighter side padding on a phone so the avatar clears the edge.
            padding=ft.Padding.symmetric(vertical=0, horizontal=(12 if _mobile else 24)),
            alignment=ft.Alignment.CENTER_LEFT,
            border=ft.Border.only(
                bottom=ft.BorderSide(1, ft.Colors.with_opacity(0.65, T.BORDER))),
            gradient=_g,
            shadow=ft.BoxShadow(
                spread_radius=0, blur_radius=22, offset=ft.Offset(0, 7),
                color=ft.Colors.with_opacity(0.08, "#2A3566")))
        if _has_blur:
            try:
                bar.blur = ft.Blur(34, 34)
            except Exception:
                pass
        return bar

    def _install_top_gap(self, body, height):
        """Give every screen the SAME header→content gap AND let cards scroll up
        behind the translucent header. A top-spacer is placed at the very top of
        each primary column, so scrolling columns slide their cards behind the
        glass; static side cards are wrapped so the spacer sits above them.
        Idempotent within a render (controls are rebuilt fresh each render)."""
        def gap_into_column(col):
            try:
                if (isinstance(getattr(col, "controls", None), list)
                        and not getattr(col, "_qa_gap", False)):
                    col.controls.insert(0, ft.Container(height=height))
                    col._qa_gap = True
            except Exception:
                pass
        if isinstance(body, ft.Column):
            gap_into_column(body)
            return
        if isinstance(body, ft.Row):
            for child in (getattr(body, "controls", None) or []):
                try:
                    if isinstance(child, ft.Column):
                        gap_into_column(child)
                    elif isinstance(child, ft.Container):
                        inner = child.content
                        if isinstance(inner, ft.Column):
                            gap_into_column(inner)
                        elif inner is not None and not getattr(child, "_qa_gap", False):
                            # static side card → put the spacer above it
                            child.content = ft.Column([ft.Container(height=height), inner],
                                                      spacing=0, expand=True)
                            child._qa_gap = True
                except Exception:
                    pass

    def _nav_items_visible(self):
        """Nav entries the current user AND platform can see — the same two
        filters rail() applies (per-user capability + platform gating)."""
        out = []
        for n in T.NAV:
            if not self.can_open_screen(n["id"]):
                continue
            if n["id"] == "automation" and not platform_caps.has_automation():
                continue
            # "Run" is the LOCAL execution screen — on mobile it's a dead
            # end for anyone using this app the way the mobile flow was
            # actually built for (Setup's "Run remotely" toggle → GitHub
            # Actions worker → the Remote Runs viewer for progress), and
            # reported live as non-functioning even for a direct local run.
            # Rather than leave a nav destination that doesn't do anything
            # useful, drop it from the drawer on mobile; the drawer's own
            # "Remote Runs" item (added to the extra list below the primary
            # destinations — see _open_nav_drawer) is the working
            # equivalent entry point. Desktop's rail() is unaffected: it
            # iterates T.NAV directly, never through this method, and
            # local Run is fully functional there. A local run started
            # from Setup on mobile can still reach this screen via goto()
            # (that permission check doesn't depend on nav-list
            # membership) — this only removes it as a tappable drawer
            # destination.
            if n["id"] == "run" and platform_caps.is_mobile():
                continue
            # "Report" only ever gets populated by a LOCAL run finishing
            # (see _stop_run/_run's "transition to report" branch, which
            # sets self.active = "report" itself — it's not reached via
            # nav-list membership either). Since local Run is already
            # hidden on mobile as a dead end, Report is the same dead end
            # one step further down that same unreachable path: nothing on
            # mobile ever populates it, so a tappable "Report" destination
            # there just opens an empty/stale screen. Drop it from the
            # drawer for the same reason "run" is dropped above.
            if n["id"] == "report" and platform_caps.is_mobile():
                continue
            out.append(n)
        return out

    def _show_nav_drawer(self):
        """Actually trigger the drawer open animation.

        Verified against the installed Flet 0.85.3 package (the version this
        app's mobile build is pinned to, see requirements.txt/build-apk.yml):
        `Page.open()` does not exist on this version at all, and
        `NavigationDrawer` has no `open` field either (it's the newer
        dataclass-style control — `drawer.open = True` silently creates a
        throwaway Python attribute Flet never serializes). The real trigger is
        the ASYNC `Page.show_drawer()` (confirmed present on BasePage), so it
        must be scheduled via `page.run_task` — the same dispatch mechanism
        `_open_url()` already uses for `page.launch_url`. Previously this was
        a silent no-op: no exception, no drawer, hamburger looked dead."""
        try:
            self.page.run_task(self.page.show_drawer)
        except Exception:
            pass

    def _close_nav_drawer(self):
        try:
            self.page.run_task(self.page.close_drawer)
        except Exception:
            pass

    def _ensure_nav_drawer(self):
        """Create the mobile nav drawer ONCE and attach it to the page, so it's
        already synced to the Flutter client before the first hamburger tap.
        The previous approach created + attached + opened the drawer all inside
        one click handler — the client hadn't registered the brand-new control
        yet, so the first show_drawer() no-op'd (reported live twice: "first
        click does nothing, second opens it") even with a page.update() in
        between. A persistent, pre-attached drawer opens reliably on the very
        first tap; only its CONTENTS are rebuilt per open (see _open_nav_drawer).
        Attached during the first render via topbar() calling this, so it's
        long since synced by the time the user can tap."""
        d = getattr(self, "_nav_drawer", None)
        if d is None:
            d = self._nav_drawer = ft.NavigationDrawer(controls=[], bgcolor=T.RAIL)
            try:
                self.page.drawer = d   # render()'s own page.update() syncs it
            except Exception:
                pass
        return d

    def _nav_drawer_content(self):
        """Drawer body styled to match the desktop left rail (rail()): brand
        header, a PIPELINE nav list with the same active gradient + index
        badge, then Help / Settings / Remote Runs, the theme toggle and the
        connection card — instead of the plain Material ListTiles it used to
        show (which looked nothing like the rail)."""
        def _close_then(fn):
            def _run(e):
                self._close_nav_drawer()
                try:
                    fn()
                except Exception:
                    pass
            return _run

        brand = ft.Container(
            ft.Row([
                self._organization_logo(44),
                ft.Column([
                    ft.Text("QA Studio", size=15, weight=ft.FontWeight.BOLD, color=T.RAIL_INK),
                    ft.Text(f"v{E.local_version()}", size=10, color=T.RAIL_DIM,
                            weight=ft.FontWeight.BOLD),
                ], spacing=2),
            ], spacing=11, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.only(left=16, right=12, top=18, bottom=8))

        nav_tiles = []
        for n in self._nav_items_visible():
            active = (n["id"] == self.active)
            color = "#FFFFFF" if active else T.RAIL_INK
            icon_color = "#FFFFFF" if active else T.RAIL_DIM
            ixcolor = "#FFFFFF" if active else "#615E6E"
            leading = getattr(ft.Icons, n.get("icon", "CIRCLE"), ft.Icons.CIRCLE)
            nav_tiles.append(ft.Container(
                ft.Row([
                    ft.Container(width=4, height=22, border_radius=4,
                                 bgcolor=("#FFFFFF" if active else ft.Colors.TRANSPARENT)),
                    ft.Icon(leading, size=18, color=icon_color),
                    ft.Text(self._nav_label(n), size=14, weight=ft.FontWeight.BOLD, color=color),
                    ft.Container(expand=True),
                    ft.Text(n.get("ix", ""), size=10.5, weight=ft.FontWeight.BOLD,
                            color=ixcolor, font_family=T.F_MONO),
                ], spacing=9),
                padding=ft.Padding.only(left=6, right=12, top=12, bottom=12),
                margin=ft.Margin.symmetric(vertical=2, horizontal=10),
                border_radius=11,
                gradient=(grad(T.GRAD_NAV_ACT) if active else None),
                shadow=(ft.BoxShadow(blur_radius=16, spread_radius=-6, offset=ft.Offset(0, 5),
                                     color=ft.Colors.with_opacity(0.45, T.VIOLET)) if active else None),
                on_click=_close_then((lambda nid=n["id"]: self.goto(nid))),
            ))

        def _rail_btn(icon, label, active, on_tap):
            return ft.Container(
                ft.Row([ft.Icon(icon, size=17, color=("#FFFFFF" if active else T.RAIL_INK)),
                        ft.Text(label, size=13, weight=ft.FontWeight.BOLD,
                                color=("#FFFFFF" if active else T.RAIL_INK)),
                        ft.Container(expand=True)], spacing=10),
                on_click=_close_then(on_tap),
                padding=ft.Padding.symmetric(vertical=12, horizontal=12),
                margin=ft.Margin.only(left=10, right=10, bottom=6),
                border_radius=10,
                bgcolor=(ft.Colors.with_opacity(0.16, T.VIOLET) if active
                         else ft.Colors.with_opacity(0.04, "#FFFFFF")),
                border=ft.Border.all(1, T.RAIL_LINE))

        footer_btns = [_rail_btn(ft.Icons.HELP_OUTLINE, strings.t("help_guide"), False,
                                 self._open_help_guide)]
        if self.can("nav.settings"):
            footer_btns.append(_rail_btn(ft.Icons.SETTINGS_OUTLINED, strings.t("settings"),
                                         self.active == "settings",
                                         lambda: self.goto("settings")))
        if auth.configured() and self.can(auth.CAP_RUN):
            footer_btns.append(_rail_btn(ft.Icons.CLOUD_QUEUE_OUTLINED, strings.t("remote_runs"),
                                         self.active == "remote_runs",
                                         lambda: self.goto("remote_runs")))
        footer_btns.append(_rail_btn(
            ft.Icons.DARK_MODE_OUTLINED if T.MODE == "light" else ft.Icons.LIGHT_MODE_OUTLINED,
            strings.t("dark_mode") if T.MODE == "light" else strings.t("light_mode"), False, self._toggle_theme))

        conn_color = T.GREEN if self.connected else T.INK_3
        _prov = self.current_provider()
        conn_text = ((T.disp_name(_prov) + " · Claude") if (self.connected and _prov == "anthropic")
                     else (T.disp_name(_prov) if self.connected else strings.t("conn_not_connected")))
        conn_sub = strings.t("conn_connected") if self.connected else strings.t("conn_enter_creds")
        conn_card = ft.Container(
            ft.Row([
                ft.Container(self._provider_logo(_prov, 28) if self.connected
                             else ft.Container(width=10, height=10, bgcolor=conn_color, border_radius=5),
                             width=28, height=28, bgcolor=(None if self.connected else T.RAIL_2),
                             border_radius=8, alignment=ft.Alignment.CENTER),
                ft.Column([
                    ft.Text(conn_text, size=12, weight=ft.FontWeight.BOLD, color=T.RAIL_INK),
                    ft.Row([ft.Container(width=7, height=7, bgcolor=conn_color, border_radius=4),
                            ft.Text(conn_sub, size=10.5, color=T.RAIL_DIM, weight=ft.FontWeight.BOLD)],
                           spacing=5, tight=True),
                ], spacing=2, expand=True),
            ], spacing=9),
            padding=12, margin=ft.Margin.all(10),
            bgcolor=ft.Colors.with_opacity(0.04, "#FFFFFF"),
            border_radius=10, border=ft.Border.all(1, T.RAIL_LINE))

        return ft.Container(
            ft.Column([
                brand,
                ft.Container(ft.Text(strings.t("pipeline"), size=10, weight=ft.FontWeight.BOLD, color="#615E6E"),
                             padding=ft.Padding.only(left=18, top=8, bottom=6)),
                ft.Column(nav_tiles, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True),
                *footer_btns,
                conn_card,
            ], spacing=0, expand=True),
            expand=True, gradient=grad(T.GRAD_RAIL, diagonal=False), bgcolor=T.RAIL)

    def _open_nav_drawer(self):
        """Open the mobile nav drawer — rebuild its rail-styled content for the
        current screen/connection state, then show the persistent, already-
        attached drawer (see _ensure_nav_drawer for the first-tap fix)."""
        d = self._ensure_nav_drawer()
        d.controls = [self._nav_drawer_content()]
        try:
            d.update()
        except Exception:
            pass
        self._show_nav_drawer()

    def shell(self, title, sub, body, right=None, badge=None, wrap=True):
        # Glass-header pattern: the frosted, translucent header is pinned ON TOP of
        # the scroll area in a Stack. The body has NO top padding; instead each
        # primary column gets a scrolling top-spacer (see _install_top_gap), so
        # cards pass UP behind the header (visible, blurred) as you scroll, and the
        # header→content gap is identical on every screen.
        try:
            body.clip_behavior = ft.ClipBehavior.HARD_EDGE
        except Exception:
            pass
        # Read-only users (e.g. Viewers) can look but not touch: disabling the body
        # propagates to every button / dropdown / field / toggle inside it, while the
        # left nav + header stay interactive (so they can still browse and sign out).
        # Scrolling is unaffected, so all content is still viewable.
        try:
            body.disabled = bool(getattr(self, "readonly", False))
        except Exception:
            pass
        HEADER_H = 94
        GAP = 18
        self._install_top_gap(body, HEADER_H + GAP)
        # Track the body's PRIMARY scrollable column.  On a same-screen desktop
        # refresh we transplant the new controls into the mounted Column, which
        # keeps Flutter's native scroll offset intact instead of resetting first
        # and relying on a delayed scroll_to().
        scroller = (body if (isinstance(body, ft.Column)
                             and getattr(body, "scroll", None) is not None)
                    else self._find_scroller(body))
        self._body_scroll_reused = False
        if (scroller is not None and getattr(self, "_preserve_body_scroll", False)
                and not platform_caps.is_mobile()):
            body, scroller, self._body_scroll_reused = self._reuse_primary_scroller(
                body, scroller)
        if scroller is not None:
            try:
                scroller.on_scroll = self._track_scroll
                self._left_scroll = scroller
            except Exception:
                pass
        # Header badge mirrors the LEFT-NAV badge for this screen (S / Ru / Rp / Rg /
        # SP / SR / A / L / U), so the header pill and the rail always agree.
        if badge is None:
            badge = next((n.get("ix") for n in T.NAV
                          if n.get("id") == getattr(self, "active", None)), None)
        header = self.topbar(title, sub, right, badge, wrap=wrap)
        header.top = 0
        header.left = 0
        header.right = 0
        if platform_caps.is_mobile():
            # Phone shell (mobile Phase 2): NO permanent rail — nav lives in
            # the drawer behind the header's hamburger (see topbar /
            # _open_nav_drawer) — and content padding tightens for a ~390px
            # width. Desktop is untouched: this branch never runs there.
            # SafeArea (mobile Phase 2 fix): nothing previously inset this Stack
            # from the system status bar / notch, so the glass header rendered
            # flush against it. ft.SafeArea (present in the pinned Flet 0.85.3)
            # pads the whole Stack — header + scrolling content — by the actual
            # device insets instead of a guessed fixed value.
            return ft.Container(
                ft.SafeArea(
                    ft.Stack([
                        ft.Container(
                            ft.SelectionArea(content=ft.GestureDetector(
                                content=body, on_tap=self._close_dropdowns)),
                            expand=True,
                            padding=ft.Padding.only(left=10, right=10, bottom=12),
                            clip_behavior=ft.ClipBehavior.HARD_EDGE),
                        header,
                    ], expand=True),
                    expand=True),
                expand=True,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                    colors=list(T.GRAD_PAGE)))
        content_stack = ft.Stack([
            ft.Container(
                # Selection scoped to the content body only — the left nav
                # (self.rail()) and the header sit outside it, so they stay
                # non-selectable. Content text is drag-select + Ctrl+C.
                # Inner GestureDetector so an empty-space tap in the content
                # still closes open dropdowns: SelectionArea otherwise claims
                # taps in the arena, so the outer close-away detector never
                # fires. A child tap recognizer wins over the SelectionArea
                # ancestor for empty space, while checkboxes/buttons keep
                # their own taps and drag-to-select (pan) is unaffected.
                ft.SelectionArea(content=ft.GestureDetector(
                    content=body, on_tap=self._close_dropdowns)),
                expand=True,
                padding=ft.Padding.only(left=22, right=22, bottom=22),
                clip_behavior=ft.ClipBehavior.HARD_EDGE),
            header,
        ], expand=True)

        # A navigation hand-off reuses the mounted desktop row.  The rail's
        # scrollable Column stays the same Flet control; only its item controls
        # and the content stack change.
        if (getattr(self, "_preserve_desktop_rail", False)
                and getattr(self, "_desktop_shell", None) is not None
                and getattr(self, "_desktop_content_host", None) is not None):
            self.rail()  # refresh active nav styling without replacing its Column
            self._desktop_content_host.content = content_stack
            self._desktop_shell_reused = True
            return self._desktop_shell

        content_host = ft.Container(
            content_stack,
            expand=True,
            gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                colors=list(T.GRAD_PAGE)))   # theme-aware page wash (dark in dark mode)
        rail = self.rail()
        desktop_shell = ft.Row([rail, content_host], spacing=0, expand=True)
        self._desktop_rail = rail
        self._desktop_content_host = content_host
        self._desktop_shell = desktop_shell
        return desktop_shell

    # ---- Useful Links ----
    def _links_path(self):
        import os, re
        import platform_caps as _pc_dir
        d = _pc_dir.app_data_dir()   # writable on mobile too (see helper)
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        # Per-user file so accounts on the same device don't share links.
        try:
            uid = (getattr(self, "user", None) or {}).get("id")
        except Exception:
            uid = None
        if uid:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(uid))[:80]
            return os.path.join(d, f"links_{safe}.json")
        return os.path.join(d, "links.json")

    def _load_links(self):
        import os, json
        custom = []
        try:
            p = self._links_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    custom = [x for x in data if isinstance(x, dict) and x.get("url")
                              and not x.get("static")]
        except Exception:
            pass
        # Built-in links (e.g. the QA Studio site itself) are injected here rather
        # than stored in the per-user file above, so every signed-in user sees them
        # without having to add them manually. There's no shared backend for this
        # screen (links are per-user/per-device, see _links_path), so an admin
        # hiding/editing one of these only affects their own account — same scope
        # as everything else on this screen. Regular users just see them read-only.
        state = self._load_static_links_state()
        hidden = set(state.get("hidden") or [])
        edits = state.get("edits") or {}
        statics = []
        for base in useful_links.STATIC_LINKS:
            if base["id"] in hidden:
                continue
            item = dict(base)
            item.update(edits.get(base["id"], {}))
            item["static"] = True
            statics.append(item)
        return statics + custom

    def _save_links(self):
        import json
        try:
            data = [x for x in (self._links or []) if not x.get("static")]
            with open(self._links_path(), "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _static_links_state_path(self):
        import os, re
        import platform_caps as _pc_dir
        d = _pc_dir.app_data_dir()   # writable on mobile too (see helper)
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        try:
            uid = (getattr(self, "user", None) or {}).get("id")
        except Exception:
            uid = None
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(uid))[:80] if uid else "local"
        return os.path.join(d, f"static_links_{safe}.json")

    def _load_static_links_state(self):
        import os, json
        try:
            p = self._static_links_state_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return {"hidden": list(data.get("hidden") or []),
                            "edits": dict(data.get("edits") or {})}
        except Exception:
            pass
        return {"hidden": [], "edits": {}}

    def _save_static_links_state(self, state):
        import json
        try:
            with open(self._static_links_state_path(), "w", encoding="utf-8") as f:
                json.dump({"hidden": list(state.get("hidden") or []),
                           "edits": dict(state.get("edits") or {})}, f)
        except Exception:
            pass

    def _hide_static_link(self, link_id):
        """Admin-only: remove a built-in link from Useful Links (this account only —
        see _load_links's note on why there's no shared/global removal)."""
        state = self._load_static_links_state()
        hidden = set(state.get("hidden") or [])
        hidden.add(link_id)
        state["hidden"] = list(hidden)
        self._save_static_links_state(state)
        self._links = [x for x in self._links if x.get("id") != link_id]

    def _update_static_link(self, link_id, name, url):
        """Admin-only: rename/re-point a built-in link (this account only)."""
        state = self._load_static_links_state()
        edits = state.get("edits") or {}
        edits[link_id] = {"name": name, "url": url}
        state["edits"] = edits
        self._save_static_links_state(state)
        for x in self._links:
            if x.get("id") == link_id:
                x["name"] = name
                x["url"] = url

    def useful_links_screen(self):
        return useful_links.screen(self)

    def settings_screen(self):
        return settings.screen(self)

    def _set_perf(self, on):
        if getattr(self, "readonly", False):
            return
        try:
            regression.set_perf(on)
            self.creds["perf"] = bool(on)
            store.save(self.creds)
        except Exception:
            pass

    def _clear_caches(self):
        if getattr(self, "readonly", False):
            return self._toast(strings.t("main_readonly"))
        try:
            regression.clear_caches(self)
        except Exception:
            pass
        self._toast(strings.t("main_caches_cleared"))

    def _reset_prefs(self):
        if getattr(self, "readonly", False):
            return self._toast(strings.t("main_readonly"))
        try:
            T.apply_theme("light")
        except Exception:
            pass
        self.lang = "en"
        self.run_lang = "en"
        self.ui_lang = "en"
        try:
            strings.set_ui_lang("en")
            self.page.rtl = strings.ui_is_rtl()
        except Exception:
            pass
        try:
            regression.set_perf(True)
        except Exception:
            pass
        try:
            self.creds["theme"] = "light"
            self.creds["lang"] = "en"
            self.creds["ui_lang"] = "en"
            self.creds["perf"] = True
            store.save(self.creds)
        except Exception:
            pass
        try:
            self.page.bgcolor = T.RAIL
            self.page.theme_mode = ft.ThemeMode.LIGHT
        except Exception:
            pass
        self._toast(strings.t("main_prefs_reset"))
        self.render()

    # ---- command palette (Ctrl/⌘-K) ----
    def _on_key(self, e):
        try:
            key = (getattr(e, "key", "") or "")
            mod = bool(getattr(e, "ctrl", False) or getattr(e, "meta", False))
            if mod and key.lower() == "k":
                if getattr(self, "_palette_open", False):
                    self._close_palette()
                else:
                    self._open_palette()
                return
            if key == "Escape" and getattr(self, "_palette_open", False):
                self._close_palette()
        except Exception:
            pass

    def _palette_commands(self):
        def nav(s):
            return lambda: self.goto(s)
        return [
            ("Go to Setup", ft.Icons.TUNE, "setup connection credentials", nav("setup")),
            ("Go to Run", ft.Icons.MONITOR_HEART, "run live generate", nav("run")),
            ("Go to Report", ft.Icons.DESCRIPTION_OUTLINED, "report results summary", nav("report")),
            ("Go to Regression Plan", _ic("CHECKLIST","LAYERS_OUTLINED"), "regression plan effort", nav("regression")),
            ("Go to Sprint Plan", _ic("EVENT_NOTE_OUTLINED","DESCRIPTION_OUTLINED"), "sprint plan capacity", nav("testplan")),
            *((("Go to Automation", ft.Icons.CODE, "automation selenium tests",
                nav("automation")),) if platform_caps.has_automation() else ()),
            ("Go to Performance", getattr(ft.Icons, "SPEED", ft.Icons.CIRCLE),
             "performance load test jmeter", nav("performance")),
            ("Go to Useful Links", _ic("BOOKMARK_BORDER","BOOKMARKS"), "links bookmarks", nav("links")),
            *((("Go to Remote Runs", ft.Icons.CLOUD_QUEUE_OUTLINED,
                "remote run github actions status activity", nav("remote_runs")),)
              if auth.configured() and self.can(auth.CAP_RUN) else ()),
            ("Open Settings", ft.Icons.SETTINGS_OUTLINED, "settings preferences", nav("settings")),
            (("Switch to dark mode" if T.MODE == "light" else "Switch to light mode"),
             ft.Icons.DARK_MODE_OUTLINED, "theme dark light toggle",
             lambda: self._toggle_theme()),
            ("Clear regression & sprint caches", _ic("CLEANING_SERVICES_OUTLINED","DELETE_OUTLINE"),
             "clear cache refresh stale", lambda: self._clear_caches()),
            ("Check for updates", _ic("SYSTEM_UPDATE_ALT_OUTLINED","SYSTEM_UPDATE_ALT"), "update version",
             lambda: self._manual_update_check()),
        ]

    def _open_palette(self):
        self._palette_open = True
        cmds = self._palette_commands()
        results = ft.Column([], spacing=2, scroll=ft.ScrollMode.AUTO, height=326)

        def run_cmd(action):
            self._close_palette()
            try:
                action()
            except Exception:
                pass

        def make_row(label, icon, action):
            row = ft.Container(
                ft.Row([ft.Icon(icon, size=17, color=T.VIOLET_INK),
                        ft.Text(label, size=13.5, weight=ft.FontWeight.W_600, color=T.INK)],
                       spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=11, horizontal=12),
                border_radius=T.R, ink=True,
                on_click=lambda e, a=action: run_cmd(a))

            def _h(e, _c=row):
                try:
                    _c.bgcolor = (T.VIOLET_SOFT if e.data in (True, "true", "True")
                                  else None)
                    _c.update()
                except Exception:
                    pass
            row.on_hover = _h
            return row

        def render_list(q=""):
            q = (q or "").strip().lower()
            rows = [make_row(lbl, ico, act) for (lbl, ico, kw, act) in cmds
                    if (not q) or q in lbl.lower() or q in kw]
            results.controls = rows or [ft.Container(
                ft.Text(strings.t("main_no_commands"), size=12.5, color=T.INK_3,
                        weight=ft.FontWeight.W_500),
                padding=14, alignment=ft.Alignment.CENTER)]
            try:
                results.update()
            except Exception:
                pass

        search = ft.TextField(
            hint_text=strings.t("main_cmd_hint"), autofocus=True,
            border_color=T.BORDER,
            focused_border_color=T.VIOLET, border_radius=T.R, text_size=14,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
            on_change=lambda e: render_list(e.control.value))
        render_list("")

        dlg = ft.AlertDialog(
            content=ft.Container(width=540, padding=ft.Padding.all(6),
                                 content=ft.Column([
                                     hover_field(search),
                                     ft.Container(height=6),
                                     results,
                                 ], spacing=0, tight=True)),
            shape=ft.RoundedRectangleBorder(radius=T.R_LG),
            on_dismiss=lambda e: setattr(self, "_palette_open", False))
        self._show_dialog(dlg)

    def _close_palette(self):
        self._palette_open = False
        try:
            self._close_dialog()
        except Exception:
            pass

    # ---- first-run onboarding wizard ----
    def _maybe_show_onboarding(self):
        try:
            if platform_caps.is_mobile():
                # mobile_prefs is a synchronous, immediately-durable local
                # file (see mobile_prefs.py) — unlike self.creds["onboarded"]
                # on mobile, which lives behind the async OS-keychain write
                # in secure_store_mobile.py. That write is fire-and-forget
                # (page.run_task, never awaited) — closing/backgrounding the
                # app right after finishing onboarding could kill the process
                # before that write actually landed, so the NEXT launch's
                # real keychain read came back without "onboarded" and the
                # walkthrough played again — reported live as "closing the
                # app and reopening launching the walkthrough with the same
                # old issue". mobile_prefs.set() below returns only after
                # the file write completes, so it can't lose the race.
                import mobile_prefs
                if not mobile_prefs.get("onboarded", False):
                    self._open_onboarding()
                return
            if not self.creds.get("onboarded"):
                self._open_onboarding()
        except Exception:
            pass

    def _finish_onboarding(self, goto_setup=False):
        try:
            if platform_caps.is_mobile():
                import mobile_prefs
                mobile_prefs.set("onboarded", True)
            self.creds["onboarded"] = True
            store.save(self.creds)
        except Exception:
            pass
        self._onboarding_open = False
        try:
            self._close_dialog()
        except Exception:
            pass
        # Only navigate to Setup when actually SIGNED IN. First-run onboarding
        # shows OVER the login gate (no user yet) — calling goto("setup") then
        # hits the permission check and fires a "You don't have access to that
        # screen" toast (reported live), which is wrong for a brand-new user.
        # Signed out, just closing the walkthrough leaves the login screen up,
        # which is exactly where they should be.
        if goto_setup and getattr(self, "user", None) is not None:
            self.goto("setup")

    def _open_onboarding(self):
        # Match the walkthrough card to the theme actually on screen. At
        # first run it opens OVER the login gate, which renders in its own
        # theme (login.py's _login_theme, defaults dark) while global T —
        # what this dialog is built from — is still at its "light" default,
        # producing a light card over a dark login (reported live: "not
        # taking the current theme"). Before signing in, sync global T to
        # the login theme so the two agree.
        try:
            _lt = getattr(self, "_login_theme", None)
            if self.user is None and _lt in ("dark", "light") and T.MODE != _lt:
                T.apply_theme(_lt)
                self.page.theme_mode = (ft.ThemeMode.DARK if _lt == "dark"
                                        else ft.ThemeMode.LIGHT)
                self.creds["theme"] = _lt
                self.render()
        except Exception:
            pass
        # Flet 0.85's page.show_dialog() pushes onto a real dialog STACK
        # (not a single page.dialog slot), so a second dialog opened while
        # this one is still up renders layered on top of it instead of
        # replacing it — confirmed live on mobile: the update-available
        # notice (_check_mobile_update, a separate background/network-timed
        # path with no knowledge of onboarding) popped stacked over an
        # already-open onboarding walkthrough. This flag lets that path wait
        # its turn instead of racing onto the same stack.
        self._onboarding_open = True
        return modals.open_onboarding(self)

    def goto(self, screen):
        # Permission gate: block navigation to a screen the user can't open.
        if not self.can_open_screen(screen):
            try:
                self._toast(strings.t("main_no_screen_access"))
            except Exception:
                pass
            return
        # Mobile: surface any pending update on a nav switch too (self-guarded,
        # throttled, once-per-session — see _check_mobile_update). Gives a
        # signed-in user a reliable moment to see it beyond the login-time
        # check, without re-popping every switch.
        try:
            self._check_mobile_update()
        except Exception:
            pass
        # Persist automation inputs when leaving the Automation screen so they
        # are preserved until the user changes them.
        if self.active == "automation" and screen != "automation":
            try:
                self._save_git_creds()
            except Exception:
                pass
        # AI Usage previously only fetched once per app launch (screen() only
        # calls _load() when _usage_report is still None) — so re-opening the
        # nav item after usage had already been generated once just showed
        # the same stale numbers until the whole app was restarted. Clearing
        # the cached report on every fresh entry into this screen (not on
        # e.g. re-renders caused by the screen's own filters) makes it
        # refetch with the screen's still-current date range each time it's
        # opened from elsewhere, same as a user hitting Generate themselves.
        if screen == "ai_usage" and self.active != "ai_usage":
            self._usage_report = None
            self._usage_msg = None
        # Remote Runs' detail view polls in a background loop (~every 2.5s)
        # while a run is live. Leaving the screen via nav (Back button inside
        # it already does this itself) must stop that loop too, or it keeps
        # firing background re-renders of whatever screen the user moved to.
        if self.active == "remote_runs" and screen != "remote_runs":
            self._rr_poll_stop = True
            self._rr_view_id = None
        self.active = screen
        # On desktop the shell keeps its rail mounted and swaps only the body,
        # so choosing a navigation item cannot reset the user's rail scroll.
        self.render(preserve_rail=True)
        # A startup dialog cannot be opened until Flet has mounted its first
        # frame. Retry any unseen release summary on navigation as well, so an
        # initial-frame race can never make a completed update invisible.
        self._show_pending_update_notice()
        # Opportunistically check for a newer version when the user navigates.
        self._maybe_check_update_on_nav()

    def render(self, preserve_rail=False, preserve_setup_scroll=False):
        """Entry point for every screen (re)render.

        NOTE: an earlier version of this method auto-detected Regression Plan
        renders and transparently ran them through a "busy overlay, then real
        render" two-phase dance whenever a plan existed (_reg_selected_rows
        truthy). That was too broad: PLENTY of fast, everyday actions on that
        screen also call plain app.render() while a plan exists (opening the
        email recipient dropdown, closing a picker, etc. — anything that
        didn't get converted to an in-place dynamic-cell update), and routing
        ALL of them through the overlay's extra render + background-thread
        round trip made those instant actions feel delayed/broken instead of
        fixing anything. That auto-detection was removed. The overlay
        mechanism itself (_reg_render_with_overlay in regression.py) is still
        used, but now only at the ONE call site that's genuinely slow — the
        Generate button's completion callback — not for every render()."""
        self._render_body(preserve_rail=preserve_rail,
                          preserve_setup_scroll=preserve_setup_scroll)

    def _render_body(self, preserve_rail=False, preserve_setup_scroll=False):
        import time as _pt
        _r0 = _pt.perf_counter()
        _same_screen = getattr(self, "_last_active", None) == self.active
        # Background loaders (plans, iterations, members, automation state, ...)
        # historically called render() without preserve_rail=True.  Reuse the
        # mounted desktop shell whenever it exists so those callbacks cannot
        # rebuild the rail and reset its native scroll position.
        self._preserve_desktop_rail = bool(
            not platform_caps.is_mobile()
            and getattr(self, "_desktop_shell", None) is not None
            and getattr(self, "_desktop_content_host", None) is not None)
        # On a same-screen loading refresh, retain the real scrolling Column
        # rather than creating one at offset zero and trying to restore it later.
        self._preserve_body_scroll = bool(_same_screen and not platform_caps.is_mobile())
        # Setup has a long form.  A completed connection needs to replace its
        # cards, but it must not throw the person back to the beginning.  Keep
        # its actual scrolling Column alive for that one controlled refresh.
        self._preserve_setup_scroll = bool(
            (preserve_setup_scroll or _same_screen)
            and self.active == "setup" and not platform_caps.is_mobile())
        self._desktop_shell_reused = False
        self._last_activity = _pt.time()   # any re-render counts as user activity
        # THEME-FLASH GATE (mobile, first launch only). When the theme is
        # genuinely unknown — nothing cached in mobile_prefs, nothing in creds
        # because the Keystore read is still in flight — painting the app now
        # means guessing, and a wrong guess is the light flash. Hold the content
        # back for a moment and show only the page background instead: a brief
        # neutral frame is not a visual bug, a wrong-theme frame is.
        #
        # Shows the BRANDED splash (logo + version + spinner), not a blank
        # container — a plain empty frame reads as a broken/flashing app, while
        # a logo screen reads as the app starting up. Bounded by a deadline so
        # this can NEVER strand the UI if the bootstrap callback doesn't
        # arrive; _on_secure_creds_ready clears the flag the instant the real
        # theme is known, which is the normal path and usually sub-second.
        _boot_at = getattr(self, "_boot_at", None)
        _boot_splash_on = (_boot_at is not None
                           and (_pt.time() - _boot_at) < self._BOOT_SPLASH_SECONDS)
        # Biometric-login hold: keep the branded frame up through the post-prompt
        # session restore + token refresh (see __init__), deadline-bounded so a
        # stalled bootstrap can't strand the UI. Shows a "Signing you in…" line.
        _bio_hold = (getattr(self, "_awaiting_bio_login", False)
                     and _pt.time() < getattr(self, "_bio_login_deadline", 0)
                     and getattr(self, "user", None) is None)
        if not _bio_hold and getattr(self, "_awaiting_bio_login", False):
            self._awaiting_bio_login = False   # deadline passed or user restored
        if _boot_splash_on or _bio_hold:
            try:
                with self._render_lock:
                    self.page.controls.clear()
                    self.page.add(self._boot_splash(signing_in=_bio_hold))
                    self.page.update()
                return
            except Exception:
                self._boot_at = None       # never get stuck on the splash
                self._awaiting_bio_login = False
        try:
            # Reset dropdown closer registry so stale closers from the previous
            # render don't linger. Each _checkbox_multiselect re-registers itself.
            self._dd_closers = []
            self._dd_syncers = {}
            if getattr(self, "_last_active", None) != self.active:
                self._scroll_offset = 0
                self._last_active = self.active
            # Per-user permissions: if the active screen isn't permitted, bounce to
            # the first one that is. Then set per-screen read-only (all action
            # buttons disabled) when the user lacks THIS screen's action capability.
            if auth.configured() and getattr(self, "user", None):
                self._maybe_revalidate()      # pick up admin revoke/role changes
                if not self.can_open_screen(self.active):
                    self.active = self._first_allowed_screen() or "_locked"
            _av = self._screen_action_cap(self.active)
            if auth.configured() and getattr(self, "user", None):
                if _av:
                    self.readonly = not self.can(_av)
                else:
                    # Screens with no action cap (e.g. Useful Links): read-only for
                    # users who can't perform ANY action — i.e. Viewers (no act.* caps).
                    _caps = auth.caps_for(self.user)
                    self.readonly = not any(str(c).startswith("act.") for c in _caps)
            else:
                self.readonly = False
            global _READONLY
            _READONLY = self.readonly
            # Hard gate: when external auth is configured and nobody is signed in,
            # show the sign-in / sign-up screen instead of the app.
            if auth.configured() and not getattr(self, "user", None):
                # This gate replaces the app root, so its old desktop shell is
                # detached.  Drop the cache before the next sign-in.
                self._desktop_shell = None
                self._desktop_rail = None
                self._desktop_content_host = None
                view = self._login_gate()
            elif (auth.configured() and getattr(self, "user", None)
                  and self.user.get("must_reset")):
                # Invited with a temporary password → must set their own before
                # the app is usable. Rendered by the LOGIN screen in reset_mode
                # (same neon card UI); app_metadata.must_reset is cleared server-
                # side by auth.change_own_password.
                self._desktop_shell = None
                self._desktop_rail = None
                self._desktop_content_host = None
                view = self._login_gate()
            elif self.active == "_locked":
                view = self._no_access_screen()
            elif self.active == "setup":
                view = self.setup_screen()
            elif self.active == "run":
                view = self.run_screen()
            elif self.active == "automation":
                view = self.automation_screen()
            elif self.active == "performance":
                view = performance.screen(self)
            elif self.active == "regression":
                view = regression.screen(self)
            elif self.active == "testplan":
                view = regression.test_plan_screen(self)
            elif self.active == "titles":
                view = sprint_titles.screen(self)
            elif self.active == "task_manager":
                view = task_manager.screen(self)
            elif self.active == "users":
                view = users_screen.screen(self)
            elif self.active == "orgs":
                view = orgs_screen.screen(self)
            elif self.active == "audit":
                import audit_screen
                view = audit_screen.screen(self)
            elif self.active == "ai_usage":
                view = ai_usage_screen.screen(self)
            elif self.active == "remote_runs":
                view = remote_runs_screen.screen(self)
            elif self.active == "links":
                view = self.useful_links_screen()
            elif self.active == "settings":
                view = self.settings_screen()
            else:
                view = self.report_screen()
            # shell() has already patched the persistent desktop content host.
            # Do not wrap/clear/add the cached root again: doing so would still
            # detach the rail and lose its native scroll offset.
            if getattr(self, "_desktop_shell_reused", False):
                with self._render_lock:
                    self.page.update()
                # The normal path preserves the actual scrolling control.  A
                # screen without a reusable scroller still gets the previous
                # best-effort restore instead of being left at offset zero.
                if not getattr(self, "_body_scroll_reused", False):
                    self._restore_scroll()
                return
            # After a fresh sign-in, let the main app fade + rise into place slowly.
            if getattr(self, "_land_app", False) and getattr(self, "user", None):
                self._land_app = False
                try:
                    view = self._entrance(view, dy=0.025, scale=0.99, dur=560)
                except Exception:
                    pass
            try:
                view = ft.GestureDetector(content=view, on_tap=self._close_dropdowns,
                                          expand=True)
            except Exception:
                pass
            _build_ms = (_pt.perf_counter() - _r0) * 1000
            # Only the actual hand-off to Flet (clear/add/update) is
            # serialized — see ui_safe()'s docstring for the race this
            # guards against (interleaved patches silently corrupting the
            # client's widget tree, which is what made collapse/page-flip/the
            # email dropdown paint nothing afterward even though the Python
            # side "succeeded"). Everything expensive above this (building
            # `view`) already happened OUTSIDE the lock.
            with self._render_lock:
                self.page.controls.clear()
                banner = None
                try:
                    banner = self._update_banner()
                except Exception:
                    banner = None
                if banner is not None:
                    # Float the update card OVER the app (top-centre) as an overlay, so it
                    # never reserves a strip, leaves no gap, and clears the window controls.
                    _root = ft.Stack([
                        ft.Container(view, expand=True),
                        ft.Container(banner, top=14, left=0, right=0,
                                     alignment=ft.Alignment.TOP_CENTER),
                    ], expand=True)
                else:
                    _root = view
                try:
                    _root = self._with_window_chrome(_root)
                except Exception:
                    pass
                # REVERTED (2026-07-18): a mobile-only 2-view `page.views` stack
                # was mounted here so the Android BACK button would fire
                # page.on_view_pop. It shipped and the app launched to a BLANK
                # GREY SCREEN on device — appending a second View did not become
                # the displayed view (Flet 0.85's views/routing semantics), and
                # because nothing raised, the try/except fallback never engaged.
                # Back to the plain single-root mount, which is what actually
                # renders. Do NOT reintroduce a views stack here without
                # verifying it on a real device first.
                self.page.add(_root)
                _u0 = _pt.perf_counter()
                self.page.update()
                _upd_ms = (_pt.perf_counter() - _u0) * 1000
            regression._perf_log(
                f"render[{self.active}]: build {_build_ms:.0f} ms + "
                f"page.update {_upd_ms:.0f} ms = {_build_ms + _upd_ms:.0f} ms")
            self._restore_scroll()
        except Exception as ex:
            # Never leave the user on a blank "Working…" screen — show the error.
            import traceback
            tb = traceback.format_exc()
            # Bind the exception to a NON-except name before anything nested
            # closes over it. Python deletes the `except ... as ex` target when
            # the block exits, so the nested _draw_err_screen() below — which
            # renders str(ex) — saw a dead cell and raised "cannot access free
            # variable 'ex' where it is not associated with a value in enclosing
            # scope". The error screen crashed while reporting an error, which
            # REPLACED the real message with a confusing one and made the
            # original render failure invisible. `_err_obj` is an ordinary local,
            # so the closure keeps working.
            _err_obj = ex
            # Always keep the full trace on disk (never shown/sent anywhere but
            # the local diagnostics file) — previously the ONLY record of this
            # was whatever text happened to still be on screen. Showing the raw
            # traceback in the UI by default is also unfiltered internal detail
            # (file paths, code shape); it's now tucked behind a details toggle
            # instead of being on screen unconditionally.
            try:
                import diag_log
                diag_log.log(f"render[{getattr(self, 'active', '?')}]", ex)
            except Exception:
                pass
            try:
                self._render_err_expanded = getattr(self, "_render_err_expanded", False)

                def _draw_err_screen():
                    # Redraws just this error card in place — deliberately does
                    # NOT call self.render(), since a full re-render could
                    # succeed this time (nothing about the underlying state
                    # changed) and silently swap back to the normal screen
                    # instead of toggling the details the user just asked for.
                    details_row = [
                        ft.TextButton(
                            "Hide technical details" if self._render_err_expanded
                            else "Show technical details",
                            on_click=lambda e: (_flip(), _draw_err_screen())),
                    ]
                    if self._render_err_expanded:
                        details_row.append(ft.Container(
                            ft.Text(tb, size=10, selectable=True,
                                    font_family="monospace", color=T.INK_2),
                            bgcolor=T.CARD_2, padding=12, border_radius=8))
                    self.page.controls.clear()
                    self.page.add(ft.Container(
                        ft.Column([
                            ft.Text(strings.t("main_draw_error"),
                                    size=15, weight=ft.FontWeight.BOLD, color="#E0474D"),
                            ft.Text(str(_err_obj), size=12, color="#1B1A22"),
                            ft.Text(strings.t("main_draw_error_sub"),
                                    size=11, color=T.INK_2),
                            *details_row,
                        ], spacing=10, scroll=ft.ScrollMode.AUTO),
                        padding=24, expand=True, bgcolor=T.CARD))
                    self.page.update()

                def _flip():
                    self._render_err_expanded = not self._render_err_expanded

                _draw_err_screen()
            except Exception:
                pass

    def _build(self):
        self.page.title = "QA Studio"
        self.page.bgcolor = T.RAIL
        self.page.padding = 0
        # Command palette: Ctrl/⌘-K opens a quick-switcher; Esc closes it.
        try:
            self.page.on_keyboard_event = self._on_key
        except Exception:
            pass
        # MOBILE_PLAN.md Phase 3 Step 1: "warn on background attempt". A run
        # is worker threads calling Azure + AI for minutes — phones suspend
        # backgrounded apps (iOS kills the sockets outright), so there's
        # nothing to actually DO here except make sure the user finds out,
        # rather than silently losing progress with no explanation. See
        # _on_app_lifecycle_change's docstring for why the warning fires on
        # RETURN, not on the way out.
        if platform_caps.is_mobile():
            try:
                self.page.on_app_lifecycle_state_change = self._on_app_lifecycle_change
            except Exception:
                pass
            # Android hardware BACK button handler. CURRENTLY INERT: on_view_pop
            # only fires when the app renders through a `page.views` stack, and
            # that mount was REVERTED in render() after it launched to a blank
            # grey screen on device. Left wired (harmless no-op) so the handler
            # is ready if a views-based navigation is ever done properly — until
            # then Back keeps Android's default behavior (closes the app).
            # NOT WIRED: on_view_pop only fires when the app renders through a
            # page.views stack, and that mount was reverted (it launched to a
            # blank grey screen). Leaving the handler attached served no purpose
            # and kept a back-press path alive that used to hard-kill the
            # process. _on_view_pop is retained, unattached, for whenever
            # views-based navigation is done properly and verified on a device.
            #     self.page.on_view_pop = self._on_view_pop
        # Client-side (Flutter) render errors were previously INVISIBLE: a
        # widget subtree that fails to build on the Dart side just paints a
        # flat grey placeholder with nothing surfacing back to Python, so a
        # bug like that could only ever be diagnosed by guessing. Flet
        # forwards unhandled client-side exceptions via page.on_error — wire
        # it to the same local diagnostics log render() already uses, so the
        # NEXT time a screen renders as an unstyled grey box, the real
        # Flutter-side exception (not just "no Python exception") is on disk.
        try:
            def _on_page_error(e):
                try:
                    import diag_log
                    diag_log.log("flutter_client_error", Exception(str(getattr(e, "data", e))))
                except Exception:
                    pass
            self.page.on_error = _on_page_error
        except Exception:
            pass
        # Give Windows a distinct app identity BEFORE the window shows, so the
        # taskbar groups us as "QA Studio" and uses our icon instead of inheriting
        # the generic Flet/Python client icon.
        try:
            import ctypes  # Windows only; no-op elsewhere
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("QAStudio.Desktop.App")
        except Exception:
            pass
        # Window icon (taskbar + title bar) — look in the bundle dir (frozen exe),
        # the exe's folder, and the source folder so it works packaged or not.
        try:
            import os as _os, sys as _sys
            _cands = []
            if getattr(_sys, "frozen", False):
                _cands.append(getattr(_sys, "_MEIPASS", ""))
                _cands.append(_os.path.dirname(_os.path.abspath(_sys.executable)))
            _cands.append(_os.path.dirname(_os.path.abspath(__file__)))
            _icon = next((_os.path.join(d, "app.ico") for d in _cands
                          if d and _os.path.exists(_os.path.join(d, "app.ico"))), "")
            if _icon and not platform_caps.is_mobile():
                if hasattr(self.page, "window") and self.page.window is not None:
                    self.page.window.icon = _icon
                else:
                    self.page.window_icon = _icon
        except Exception:
            pass
        try:
            # Flet >= 0.23 uses page.window.* ; older uses page.window_*
            # (mobile Phase 1 gating: no sizing/frameless on a phone — the OS
            # owns the "window"; the elif chain keeps mobile out entirely.)
            if not platform_caps.is_mobile() and \
                    hasattr(self.page, "window") and self.page.window is not None:
                self.page.window.width = 1120
                self.page.window.height = 720
                self.page.window.min_width = 980
                self.page.window.min_height = 620
                # Frameless: hide the OS title bar so the background fills the ENTIRE
                # window (no dark title-bar band in any theme). Custom minimize /
                # maximize / close buttons + a drag strip are added in render().
                try:
                    self.page.window.title_bar_hidden = True
                    self.page.window.title_bar_buttons_hidden = True
                except Exception:
                    pass
            elif not platform_caps.is_mobile():
                self.page.window_width = 1120
                self.page.window_height = 720
                self.page.window_min_width = 980
                self.page.window_min_height = 620
            # centre the window on screen (else some Flet builds open it low/left)
            try:
                if platform_caps.is_mobile():
                    pass
                elif hasattr(self.page, "window") and self.page.window is not None:
                    _center = self.page.window.center
                    import asyncio as _aio
                    if _aio.iscoroutinefunction(_center):
                        # Flet 0.85.3: window.center() is a COROUTINE. Calling it
                        # directly never awaits it (the "coroutine 'Window.center'
                        # was never awaited" RuntimeWarning) and doesn't actually
                        # centre the window. Schedule it on Flet's own event loop
                        # instead — same dispatch as show_drawer()/launch_url().
                        self.page.run_task(_center)
                    else:
                        _center()          # older Flet: center() is synchronous
                elif hasattr(self.page, "window_center"):
                    self.page.window_center()
            except Exception:
                pass
        except Exception:
            pass
        # Window close handling (desktop only). We do NOT force prevent_close,
        # because that makes the X button unreliable across Flet versions. The X
        # closes the app normally. We only attach a listener to (best-effort)
        # confirm when a run is active; if the listener isn't supported, the
        # window still closes cleanly.
        import os
        _web = os.environ.get("WEB_MODE", "").strip() in ("1", "true", "yes")
        if not _web and not platform_caps.is_mobile():
            try:
                if hasattr(self.page, "window") and self.page.window is not None:
                    # Do NOT prevent_close while idle — let Flet's native close
                    # handle the X button (always reliable). We only attach the
                    # listener; prevent_close is turned on *only during a run* (see
                    # _set_run_active) so we can confirm-before-quit then. A
                    # background watchdog guarantees the process can't be orphaned.
                    self.page.window.prevent_close = False
                    self.page.window.on_event = self._on_window_event
            except Exception:
                pass
        else:
            # WEB MODE: closing the browser tab does NOT raise a window event, so
            # the Python server would keep running forever (this is what leaves
            # many orphaned python.exe processes in Task Manager). Exit the process
            # shortly after the browser client disconnects.
            try:
                self.page.on_disconnect = self._on_web_disconnect
            except Exception:
                pass
        self.render()
        # If an update completed after the previous window was closed, its UI
        # callback could not show the restart/release-notes dialog.  Replay the
        # one-time confirmation now that the updated process is alive.
        try:
            self._show_pending_update_notice()
        except Exception:
            pass
        # Mobile update NOTICE. Self-guards: no-op on desktop, and skips while
        # not signed in — so at build time (before login, especially with
        # biometrics on) it does nothing. The real triggers are post-login
        # (login._submit / _on_secure_creds_ready) and nav switches (goto). This
        # call still covers the case where auth is unconfigured (no login gate
        # at all), where the user is effectively "in" immediately.
        try:
            self._check_mobile_update()
        except Exception:
            pass
        # First-run onboarding (once, until "onboarded" is saved). Mobile
        # skips this synchronous check — at this point self.creds is still
        # the pre-bootstrap default store.load() falls back to before the
        # OS-keychain read lands (see secure_store_mobile.py / __init__),
        # so "onboarded" always reads as missing there even for a user who
        # finished onboarding days ago. _on_secure_creds_ready() runs this
        # same check once the real value is in, exactly once per process.
        if not platform_caps.is_mobile():
            self._onboarding_auto_checked = True
            try:
                self._maybe_show_onboarding()
            except Exception:
                pass
        # Check for a newer version in the background (never blocks startup)
        self._kickoff_update_check()

    def _on_web_disconnect(self, e=None):
        """Web client (browser tab) closed → terminate the server process after a
        short grace period (a refresh reconnects within that window)."""
        def _later():
            import os, time
            time.sleep(2.0)  # grace period: a page refresh reconnects quickly
            # If the client reconnected, a new session is active; still safe to
            # exit this orphaned one. Kill the whole process tree on Windows.
            try:
                if os.name == "nt":
                    import subprocess
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(os.getpid())],
                                   creationflags=0x08000000, check=False)
                    return
            except Exception:
                pass
            try:
                os._exit(0)
            except Exception:
                pass
        try:
            threading.Thread(target=_later, daemon=True).start()
        except Exception:
            pass

    def _set_run_active(self, active):
        """Track whether a run is in progress. prevent_close is turned ON only
        while a run/automation is active (so we can confirm-before-quit); when
        idle it's OFF so the X button closes natively and reliably."""
        self._run_active = bool(active)
        try:
            if hasattr(self.page, "window") and self.page.window is not None:
                self.page.window.prevent_close = bool(active)
                self.page.update()
        except Exception:
            pass
        # MOBILE_PLAN.md Phase 3 Step 1: keep the screen awake for the
        # duration of a LOCAL run (worker threads calling Azure + AI for
        # minutes) — phones suspend backgrounded apps and iOS kills sockets
        # mid-run, so "stay open and awake" is the whole v1 contract here.
        # Remote runs don't need this: they execute on GitHub, which is the
        # entire point of "you can close the app" for that path.
        if platform_caps.is_mobile():
            try:
                import mobile_wakelock
                (mobile_wakelock.enable if active else mobile_wakelock.disable)()
            except Exception:
                pass

    def _on_app_lifecycle_change(self, e):
        """Mobile-only (see _build()'s wiring): MOBILE_PLAN.md Phase 3 Step 1's
        "warn on background attempt". The warning deliberately fires on the
        way BACK (RESUME/RESTART/SHOW), not the way OUT (PAUSE/HIDE/INACTIVE)
        — a toast queued right as the OS is suspending the app has no
        reliable chance to actually render before the process freezes, so
        warning on return is the only version of this that a user can
        actually see. A best-effort log line is still appended immediately
        on the way out too (cheap, and occasionally does make it through),
        purely as a timestamped breadcrumb in the run's own activity log."""
        try:
            state = str(getattr(e, "state", "") or "")
        except Exception:
            return
        state = state.lower().rsplit(".", 1)[-1]   # enum str() vs .value, either shape
        running = bool(getattr(self, "_run_active", False)
                       or getattr(self, "_auto_running", False))
        if state in ("pause", "hide", "inactive"):
            if running:
                self._mobile_bg_during_run = True
                try:
                    self._log_lines.append({
                        "tone": "warn", "ico": "⏸",
                        "msg": "App backgrounded — Azure/AI calls in flight may be "
                               "interrupted if the OS suspends the process."})
                except Exception:
                    pass
            return
        if state in ("resume", "restart", "show"):
            # NO biometric re-lock here — REMOVED after it caused three live
            # bugs: Flutter fires `inactive` for ordinary in-app transients
            # (dropdowns, expanding rows, the system UI overlay), so the
            # re-lock fired the fingerprint prompt seemingly at random while
            # just browsing the app; and cancelling one of those stray prompts
            # ran its failure path, which SIGNED THE USER OUT. Biometrics is a
            # LAUNCH gate only (secure_store_mobile.init → _bio_gate_check),
            # which is what was actually asked for: "closed the app and
            # relaunch → fingerprint". Re-locking mid-session needs a real
            # lock-screen UI, not a lifecycle hook — don't reintroduce it here.
            if getattr(self, "_mobile_bg_during_run", False):
                self._mobile_bg_during_run = False
                try:
                    self._toast(strings.t("main_welcome_back"))
                except Exception:
                    pass

    def _on_view_pop(self, e=None):
        """Android hardware BACK (mobile only; wired in _build). render() mounts
        a 2-view stack so Back fires this instead of leaving the app. Behavior:
        an open onboarding walkthrough closes first; otherwise Back returns to
        the Setup screen; pressing Back again while already ON Setup within ~2s
        exits the app. Every branch re-renders, which re-asserts the content
        view on top of the base so the pop we just received doesn't actually
        exit."""
        try:
            import time as _t
            now = _t.time()
        except Exception:
            now = 0.0
        # Back dismisses the onboarding walkthrough if it's open.
        if getattr(self, "_onboarding_open", False):
            try:
                self._close_dialog()
            except Exception:
                pass
            self.ui_safe(self.render)
            return
        if getattr(self, "active", "setup") != "setup":
            self.active = "setup"
            self._back_exit_ts = 0.0
            self.ui_safe(self.render)
            return
        # Already on Setup: second Back within ~2s would exit.
        #
        # The os._exit(0) that used to live here has been REMOVED. It hard-kills
        # the process — no atexit, no thread shutdown, no Flet teardown — so
        # Android can be left holding a task record for a process that vanished,
        # which shows up as a DUPLICATE entry in the recents/background-apps list
        # (the next launch starts a fresh task alongside the ghost). This handler
        # is currently inert anyway (on_view_pop only fires with a page.views
        # stack, and that mount was reverted), but leaving a reachable hard-kill
        # in the tree is not worth the risk. If back-to-exit is ever revived, let
        # Android finish the activity normally — never _exit() the interpreter.
        if now and (now - getattr(self, "_back_exit_ts", 0.0) < 2.0):
            return
        self._back_exit_ts = now
        try:
            self._toast(strings.t("main_back_exit"))
        except Exception:
            pass
        self.ui_safe(self.render)   # re-assert the view stack so we don't exit

    # REMOVED: _maybe_bio_relock (resume re-lock). It re-challenged biometrics
    # whenever the app returned to the foreground, but Flutter's `inactive`
    # lifecycle state fires for ordinary in-app transients (dropdowns, expanding
    # rows, system UI), so it prompted at random during normal use — and
    # cancelling one of those stray prompts hit its failure path and SIGNED THE
    # USER OUT. Biometrics is now purely a LAUNCH gate (see
    # secure_store_mobile.init → _bio_gate_check), which is the requested
    # behavior. Doing this properly mid-session needs a real lock-screen view,
    # not an app-lifecycle hook.

    def _on_window_event(self, e):
        """Best-effort confirm-on-close while a run is active. If a run is NOT
        active, we never block the close, so the X button always works.

        Flet versions disagree on the close event's shape, so we detect it
        broadly: the event's data/type may be the string 'close', or an enum
        whose name/str contains 'close'. If we can't tell, we treat it as a close
        (fail-safe), because leaving prevent_close=True with an unrecognized event
        is what makes the X button do nothing."""
        raw = None
        for attr in ("data", "type"):
            v = getattr(e, attr, None)
            if v is not None:
                raw = v
                break
        token = str(getattr(raw, "name", raw) or "").lower()
        is_close = ("close" in token)
        # Some 0.90 builds deliver focus/blur/move/resize events here too; only
        # those are safe to ignore. Anything we don't recognize → treat as close.
        known_noise = any(k in token for k in
                          ("focus", "blur", "move", "resize", "restore",
                           "maximize", "minimize", "enterfullscreen", "leavefullscreen"))
        if not is_close and known_noise:
            return
        # close (or unknown) → proceed to close logic
        running = bool(getattr(self, "_run_active", False)
                       or getattr(self, "_auto_running", False))
        if not running:
            # let it close naturally
            self._force_close()
            return
        # A run is in progress — ask before quitting
        _is_auto = bool(getattr(self, "_auto_running", False))
        _what = strings.t("main_quit_what_auto") if _is_auto else strings.t("main_quit_what_run")
        def do_quit(_=None):
            self._close_dialog()
            self._force_close()
        def keep(_=None):
            self._close_dialog()
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Container(ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=18, color=T.AMBER),
                             width=34, height=34, bgcolor=T.AMBER_SOFT, border_radius=9,
                             alignment=ft.Alignment.CENTER),
                ft.Text(strings.t("main_quit_title", what=_what), weight=ft.FontWeight.W_800,
                        size=16, color=T.INK),
            ], spacing=10, tight=True),
            content=ft.Container(
                ft.Text(strings.t("main_quit_body", what=_what),
                        size=13, color=T.INK_2, weight=ft.FontWeight.W_500), width=380),
            actions=[ghost_btn(strings.t("main_keep_running"), on_click=keep),
                     danger_btn(strings.t("main_quit_btn"), icon=ft.Icons.CLOSE, on_click=do_quit)],
            actions_alignment=ft.MainAxisAlignment.END)
        self._show_dialog(dlg)

    def _force_close(self):
        return window_chrome.force_close(self)

    def _kickoff_update_check(self):
        """Check once at startup, then keep re-checking periodically while the
        app stays open, so users who never relaunch still get notified. Runs on a
        DAEMON thread that bails the moment we're closing, so it can never keep
        python.exe alive after the window is gone."""
        import time as _t
        def work():
            self._run_update_check()
            while not getattr(self, "_closing", False):
                for _ in range(60):                 # ~600s, but wake every 10s
                    if getattr(self, "_closing", False):
                        return
                    try:
                        _t.sleep(10)
                    except Exception:
                        return
                if (self._update_info or {}).get("update") and not self._update_dismissed:
                    continue
                self._run_update_check()
        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception:
            pass

    def _run_update_check(self):
        # Desktop-only: this feeds the floating top-centre banner whose "Update
        # now" button runs the zipball/.exe self-update (apply_update) — that
        # mechanism doesn't exist on Android (an installed APK is immutable,
        # which is why has_self_update() is False there; see platform_caps).
        # Without this gate, _kickoff_update_check()'s startup+periodic loop
        # and _maybe_check_update_on_nav()'s per-nav check both ran unconditionally
        # on mobile too, popping this same desktop banner (sometimes more than
        # once as nav-triggered re-renders repainted it) ON TOP OF the separate,
        # intentional mobile-only notice (_check_mobile_update's centered
        # dialog) — the "2 at the top + 1 in the middle" duplicate popups the
        # user hit. Mobile's sole update path is now _check_mobile_update().
        if not platform_caps.has_self_update():
            return
        # Startup and a quick navigation can overlap. Keep one network request
        # in flight, then render its result on whichever screen is current.
        if getattr(self, "_update_check_inflight", False):
            return
        self._update_check_inflight = True
        try:
            info = E.check_for_update()
            self._update_info = info
            # Repaint whenever an update is available and not dismissed, so the
            # banner appears on the next interaction even if a prior check missed
            # it or was still in flight. (render() rebuilds the banner from
            # _update_info, so a repaint is all that's needed.)
            if info.get("update") and not self._update_dismissed:
                self._safe_render(preserve_rail=True, preserve_setup_scroll=True)
        except Exception:
            pass
        finally:
            self._update_check_inflight = False

    def _manual_update_check(self):
        """User-triggered check. Always reports the outcome (up-to-date / newer /
        why it couldn't check), unlike the silent background check."""
        self._toast(strings.t("checking_updates"))
        def work():
            info = E.check_for_update()
            self._update_info = info
            local = info.get("local", "?")
            remote = info.get("remote")
            if info.get("update"):
                self._update_dismissed = False
                self._safe_render(preserve_rail=True, preserve_setup_scroll=True)
            elif info.get("error"):
                self.ui_safe(lambda: self._toast(strings.t("update_check_failed", err=info["error"])))
            elif remote:
                self.ui_safe(lambda: self._toast(strings.t("up_to_date_latest", local=local, remote=remote)))
            else:
                self.ui_safe(lambda: self._toast(strings.t("up_to_date", v=local)))
        try:
            self._bg(work)
        except Exception:
            pass

    def _maybe_check_update_on_nav(self):
        """Check after navigation, with a short network throttle.

        A relaunch starts an immediate check; every screen switch also gets a
        chance to notify if that check missed a transient network failure. Once
        an update is known, the persisted in-memory result is rendered on every
        screen without another request.
        """
        import time as _t
        now = _t.time()
        last = getattr(self, "_last_nav_update_check", 0)
        info = getattr(self, "_update_info", None) or {}
        # Do not wait through the normal throttle after startup/no-network
        # failures; a user changing screens is an intentional retry. Healthy
        # responses are checked at most once every 30 seconds.
        if info and not info.get("error") and now - last < 30:
            return
        self._last_nav_update_check = now
        try:
            self._bg(self._run_update_check)
        except Exception:
            pass

    def _update_banner(self):
        return updater_ui.banner(self)

    def _dismiss_update(self):
        return updater_ui.dismiss_update(self)

    def _do_update(self):
        return updater_ui.do_update(self)

    def _show_update_error(self, msg):
        return updater_ui.show_update_error(self, msg)

    def _show_restart_dialog(self, msg, version=""):
        return updater_ui.show_restart_dialog(self, msg, version)

    def _show_pending_update_notice(self):
        """Show the release summary after both automatic and manual updates.

        Older builds cannot write the pending marker introduced for the
        self-updater. Comparing the installed version to the locally
        acknowledged version closes that compatibility gap: an externally
        installed release still gets one clear summary on its first launch.
        """
        notice = E.get_pending_update_notice() or {}
        pending_version = str(notice.get("version") or "")
        installed_version = E.local_version()
        seen_version = E.get_seen_release_notice_version()
        advanced_since_seen = (not seen_version or
                               E._ver_newer(installed_version, seen_version))
        if not pending_version and not advanced_since_seen:
            return
        if getattr(self, "_release_notice_scheduled", False):
            return
        version = pending_version or installed_version

        def _present():
            self._release_notice_scheduled = False
            try:
                updater_ui.show_post_update_dialog(self, version)
            except Exception as ex:
                # Leave both records intact so a later navigation/relaunch can
                # retry. Log the real reason; a prior bare swallow made this
                # exact startup failure look like "nothing happened".
                try:
                    import diag_log
                    diag_log.log("release_notice", ex)
                except Exception:
                    pass
                return
            # Only acknowledge after Flet has accepted the dialog. This prevents
            # repeat popups while allowing a retry if startup fails beforehand.
            E.mark_release_notice_seen(version)
            if pending_version:
                E.clear_pending_update_notice()

        async def _after_first_frame():
            # page.show_dialog() during the same frame as page.add()/update()
            # can be dropped by Flet 0.85. Yield one short frame first.
            import asyncio
            await asyncio.sleep(0.25)
            _present()

        self._release_notice_scheduled = True
        try:
            self.page.run_task(_after_first_frame)
        except Exception:
            # Fallback for older Flet page implementations. It remains
            # deliberately non-blocking and retries on the next navigation.
            self._release_notice_scheduled = False
            self.ui_safe(_present)

    def _quit_after_update(self):
        return updater_ui.quit_after_update(self)

    def _restart_app(self):
        return updater_ui.restart_app(self)

    def _restart_close(self):
        return updater_ui.restart_close(self)

    def _confirm_close(self):
        return window_chrome.confirm_close(self)

    def setup_screen(self):
        return setup.screen(self)


    # ---- credential help content ----
    HELP = {
        "model": {
            "title": "Choosing a model",
            "steps": [
                "The list is fetched live from your provider using the saved key.",
                "Pick a chat/vision model (e.g. a Claude, GPT, Gemini or Qwen model).",
                "You can also type an exact model id if it isn't in the list.",
                "Each provider remembers its own model. Changing it disconnects so "
                "you reconnect against the new model.",
                "Use Refresh to reload the list after adding access to new models.",
            ],
            "url": None, "url_label": None,
        },
        "provider": {
            "title": "Activating an AI Provider",
            "steps": [
                "A provider becomes 'active' once you save a valid API key for it.",
                "Pick your provider from the dropdown (Anthropic, NVIDIA, OpenAI, …).",
                "Paste its API key in the API Key field below and click Save.",
                "The dropdown dot turns ● and shows '(active)'. Then click Connect.",
                "Each provider stores its own key — switching providers keeps them all.",
            ],
            "url": "https://console.anthropic.com/settings/keys",
            "url_label": "Open Anthropic Console",
        },
        "api_key": {
            "title": "AI Provider API Key",
            "steps": [
                "Anthropic: sign in at console.anthropic.com → API Keys → Create Key.",
                "NVIDIA: sign in at build.nvidia.com → your profile → API Keys → Generate.",
                "OpenAI: platform.openai.com → API Keys → Create new secret key.",
                "Copy the key and paste it here. It is stored only on this device.",
            ],
            "url": "https://console.anthropic.com/settings/keys",
            "url_label": "Open Anthropic Console",
        },
        "pat": {
            "title": "Azure DevOps Personal Access Token (PAT)",
            "steps": [
                "Open Azure DevOps → click your avatar (top right) → Personal access tokens.",
                "Click New Token. Give it a name and pick this organization.",
                "Set Scopes: Test Management (Read & write) and Work Items (Read).",
                "Set an expiry, click Create, then copy the token (shown once).",
            ],
            "url": "https://dev.azure.com/",  # placeholder — recomputed live in _show_help (org is empty at import)
            "url_label": "Open PAT settings",
        },
        "gmail": {
            "title": "Gmail App Password (optional)",
            "steps": [
                "Used only to email the run report. Needs 2-Step Verification enabled.",
                "Go to your Google Account → Security → 2-Step Verification → App passwords.",
                "Create an app password (16 characters) for 'Mail'.",
                "Paste it here. A normal Gmail password will not work for SMTP.",
            ],
            "url": "https://myaccount.google.com/apppasswords",
            "url_label": "Open Google App Passwords",
        },
        "org": {
            "title": "Azure DevOps Organization name",
            "steps": [
                "It's the first path segment after dev.azure.com in your Azure URL.",
                "Example: https://dev.azure.com/myCompany → the org is 'myCompany'.",
                "Open Azure DevOps in your browser and read it from the address bar.",
                "Type just the name here (not the full URL). It's used to build all API calls.",
            ],
            "url": "https://dev.azure.com",
            "url_label": "Open Azure DevOps",
        },
        **backend_setup.HELP,
        "git_pat": {
            "title": "Git access token (PAT) for pushing tests",
            "steps": [
                "This token lets QA Studio push the generated tests to your repo — and, the "
                "first time you push a brand-new output folder, CREATE the GitHub repo too "
                "if it doesn't exist yet. Later pushes to that same folder just push (no "
                "repo check/creation).",
                "GitHub, classic token (simplest): Settings → Developer settings → Personal "
                "access tokens → Tokens (classic) → Generate new token (classic). Scope: "
                "'repo' (needed for both push AND auto-creating a new repo; 'public_repo' "
                "is enough if you only ever use public repos). Copy the token (ghp_…, shown "
                "once).",
                "GitHub, fine-grained token: same menu → Fine-grained tokens → Generate. Set "
                "Repository access to 'All repositories' (a not-yet-created repo can't be "
                "picked individually). Under Repository permissions set 'Contents: Read and "
                "write' (push) AND 'Administration: Read and write' (create). Contents-only "
                "still pushes fine to a repo that already exists — Administration is only "
                "needed for the auto-create case.",
                "Org-owned repos: if your org restricts personal access tokens, approve the "
                "token from Org Settings → Personal access tokens after generating it, or "
                "the push/create calls will be rejected even with the right scopes.",
                "Azure DevOps Repos: User settings → Personal access tokens → New token → "
                "scope 'Code (Read & Write)'. (Auto-create-repo isn't supported for Azure "
                "DevOps — create the repo there yourself first.)",
                "Paste it here. It's stored locally like your other credentials and is "
                "scrubbed from logs. Keep the repo private.",
            ],
            "url": "https://github.com/settings/tokens",
            "url_label": "Open GitHub token settings",
        },
    }

    # per-provider key instructions + console link (drives the API-key info icon)
    PROVIDER_KEY_HELP = {
        "anthropic": ("console.anthropic.com → API Keys → Create Key.",
                      "https://console.anthropic.com/settings/keys", "Open Anthropic Console"),
        "openai":    ("platform.openai.com → API Keys → Create new secret key.",
                      "https://platform.openai.com/api-keys", "Open OpenAI Keys"),
        "gemini":    ("aistudio.google.com → Get API key → Create API key.",
                      "https://aistudio.google.com/app/apikey", "Open Google AI Studio"),
        "nvidia":    ("build.nvidia.com → your profile → API Keys → Generate.",
                      "https://build.nvidia.com/", "Open NVIDIA Build"),
        "deepseek":  ("platform.deepseek.com → API keys → Create new API key. "
                      "Ensure your balance is topped up.",
                      "https://platform.deepseek.com/api_keys", "Open DeepSeek Platform"),
        "qwen":      ("Alibaba Model Studio (International) → API-KEY → Create. "
                      "Use the Singapore endpoint key.",
                      "https://modelstudio.console.alibabacloud.com/", "Open Model Studio"),
        "azure_openai": ("Azure Portal → your Azure OpenAI resource → Keys and Endpoint.",
                         "https://portal.azure.com/", "Open Azure Portal"),
        "ollama":    ("Ollama runs locally — no API key needed. Just run `ollama serve`.",
                      "https://ollama.com/download", "Get Ollama"),
        "manus":     ("manus.im → Settings → API (Integrations) → Create API Key. "
                      "The key is shown once — copy it immediately.",
                      "https://manus.im/app?show_settings=integrations&app_name=api",
                      "Open Manus API Settings"),
        "groq":      ("console.groq.com → API Keys → Create API Key. Free tier, no card.",
                      "https://console.groq.com/keys", "Open Groq Console"),
        "cerebras":  ("cloud.cerebras.ai → API Keys → Generate key. Free tier, no card.",
                      "https://cloud.cerebras.ai/", "Open Cerebras Cloud"),
        "openrouter": ("openrouter.ai → Keys → Create Key. Use the ':free' models for "
                       "no-cost access.",
                       "https://openrouter.ai/keys", "Open OpenRouter Keys"),
        "mistral":   ("console.mistral.ai → API Keys → Create new key. The free "
                      "'Experiment' tier requires opting into data-training.",
                      "https://console.mistral.ai/api-keys", "Open Mistral Console"),
        "minimax":   ("platform.minimax.io → sign in → API Keys → Create. Uses the "
                      "international endpoint; the M2 free trial is time-limited.",
                      "https://platform.minimax.io/user-center/basic-information/interface-key",
                      "Open MiniMax Platform"),
        "glm":       ("z.ai → sign up (no card) → API Keys → Create. glm-4.5-flash's "
                      "free quota is a rate-limited trial that runs out — treat as paid.",
                      "https://z.ai/manage-apikey/apikey-list", "Open Z.AI Keys"),
    }

    def _show_help(self, key):
        # Both the AI-Provider and API-Key info icons are provider-aware: they show
        # the SELECTED provider's instructions + the correct console URL.
        if key in ("api_key", "provider"):
            name = getattr(self, "_provider_choice", None) or "anthropic"
            how, url, label = self.PROVIDER_KEY_HELP.get(
                name, self.PROVIDER_KEY_HELP["anthropic"])
            if key == "provider":
                h = {"title": strings.t("helpm_prov_title", prov=T.disp_name(name)),
                     "steps": [
                         strings.t("helpm_prov_s0", prov=T.disp_name(name)),
                         strings.t("helpm_prov_s1", how=how),
                         strings.t("helpm_prov_s2"),
                         strings.t("helpm_prov_s3"),
                         strings.t("helpm_prov_s4"),
                     ],
                     "url": url, "url_label": label}
            else:
                h = {"title": strings.t("helpm_apikey_title", prov=T.disp_name(name)),
                     "steps": [strings.t("helpm_apikey_s0", prov=T.disp_name(name), how=how),
                               strings.t("helpm_apikey_s1"),
                               strings.t("helpm_apikey_s2")],
                     "url": url, "url_label": label}
        else:
            # backend_setup owns the Jira/Zephyr/Xray topics and builds their
            # token/key URLs from the saved site, so the "Open …" button lands on
            # the REAL 'create token' page on this user's own site rather than a
            # generic docs/admin page (the reported "it sends me to admin.atlassian.com").
            try:
                h = backend_setup.help_topic(key, self.creds) or self.HELP.get(key)
            except Exception:
                h = self.HELP.get(key)
        if not h:
            return
        if key == "pat":
            # AZURE_ORG is set at RUNTIME (Setup), but HELP is a class-level dict
            # baked at import when the org is still empty — which left the PAT link
            # as https://dev.azure.com//_usersSettings/tokens (double slash, broken)
            # forever. Build it from the CURRENT org here; tolerate an empty org or
            # a pasted full URL in the org field.
            _org = (E.AZURE_ORG or "").strip().strip("/")
            _org = _org.replace("https://dev.azure.com/", "").replace(
                "http://dev.azure.com/", "").strip("/")
            h = dict(h)
            h["url"] = (f"https://dev.azure.com/{_org}/_usersSettings/tokens"
                        if _org else "https://dev.azure.com/")
        _htk = "helpm_" + str(key) + "_title"
        if strings.t(_htk) != _htk:
            h = dict(h)
            h["title"] = strings.t(_htk)
            h["steps"] = [strings.t("helpm_%s_s%d" % (key, _i)) for _i in range(len(h["steps"]))]
            _ulk = "helpm_%s_url" % key
            if h.get("url_label") and strings.t(_ulk) != _ulk:
                h["url_label"] = strings.t(_ulk)
        step_rows = []
        for i, s in enumerate(h["steps"], 1):
            step_rows.append(ft.Row([
                ft.Container(ft.Text(str(i), size=11, color=T.VIOLET_INK, weight=ft.FontWeight.BOLD),
                             width=20, height=20, bgcolor=T.VIOLET_SOFT, border_radius=6,
                             alignment=ft.Alignment.CENTER),
                ft.Text(s, size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500, expand=True),
            ], spacing=9, vertical_alignment=ft.CrossAxisAlignment.START))
        url = h.get("url")
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Container(ft.Icon(ft.Icons.HELP_OUTLINE, size=18, color=T.VIOLET_INK),
                             width=34, height=34, bgcolor=T.VIOLET_SOFT, border_radius=9,
                             alignment=ft.Alignment.CENTER),
                ft.Text(h["title"], size=16, weight=ft.FontWeight.W_800, color=T.INK, expand=True),
            ], spacing=10),
            content=ft.Container(width=460, content=ft.Column(
                step_rows + ([
                    ft.Container(height=6),
                    ft.Container(
                        ft.Row([ft.Icon(ft.Icons.OPEN_IN_NEW, size=14, color=T.VIOLET_INK),
                                ft.Text(h.get("url_label", strings.t("helpm_open_link")), size=12.5,
                                        color=T.VIOLET_INK, weight=ft.FontWeight.BOLD)],
                               spacing=6, tight=True),
                        on_click=lambda e, u=url: self._open_url(u),
                        padding=ft.Padding.symmetric(vertical=10, horizontal=12),
                        bgcolor=T.VIOLET_SOFT, border_radius=T.R,
                        border=ft.Border.all(1, "#E0DAFF")),
                ] if url else []),
                spacing=11, tight=True)),
            actions=[primary_btn(strings.t("main_got_it"), on_click=lambda e: self._close_dialog())],
            actions_alignment=ft.MainAxisAlignment.END)
        self._show_dialog(dlg)

    # ---- connection: editable (not connected) ----
    def _build_org_row(self):
        """(Re)build the Azure Organization field + Save/Update button. Separate
        from _connection_edit so Save/Update patches just this row in place.
        Deliberately does NOT fall back to E.AZURE_ORG when this account has none
        saved — that global can hold whichever account last connected, and
        pre-filling a different account's org here used to get it saved into the
        wrong creds file on a Save the user didn't scrutinize."""
        org_has = bool(self.creds.get("org"))
        org_editable = (not org_has) or self._org_unlocked
        self.org_field = ft.TextField(
            value=self.creds.get("org", ""), hint_text=strings.t("main_azure_org_name"),
            read_only=not org_editable,
            bgcolor=(T.CARD if org_editable else T.CARD_2),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
            text_size=13, expand=True)
        self.org_btn = (green_btn(strings.t("main_org_save"), on_click=self._save_org) if org_editable
                        else ghost_btn(strings.t("main_update_btn"), on_click=self._unlock_org))
        return ft.Row([hover_field(self.org_field), self.org_btn], spacing=8)

    def _sync_org_cell(self):
        try:
            self._org_cell.content = self._build_org_row()
            self._org_cell.update()
        except Exception:
            self.render()

    def _build_ai_key_row(self):
        """(Re)build the API-key field + its Save/Update button for the CURRENT
        provider, and return the row. Kept separate from _connection_edit so a
        provider/model change can patch just this row in place (via
        _sync_ai_key_cell) instead of a full render."""
        name = self._provider_choice
        active = self._provider_active(name)          # has a saved key for this slot
        key_editable = (not active) or self._key_unlocked
        self.api_key_field = ft.TextField(
            value=self._saved_key(name), password=True, can_reveal_password=True,
            hint_text=f"Paste key for {T.disp_name(name)}",
            read_only=not key_editable,
            bgcolor=(T.CARD if key_editable else T.CARD_2),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
            text_size=13, expand=True)
        self.api_btn = (green_btn(strings.t("main_org_save"), on_click=self._save_key) if key_editable
                        else ghost_btn(strings.t("main_update_btn"), on_click=self._unlock_key))
        return ft.Row([hover_field(self.api_key_field), self.api_btn], spacing=8)

    def _sync_ai_key_cell(self):
        """Rebuild the API-key row cell in place (new provider's key/state +
        Save-vs-Update button). Falls back to a full render if the cell isn't
        mounted (e.g. not on the connection-edit view)."""
        try:
            self._ai_key_cell.content = self._build_ai_key_row()
            self._ai_key_cell.update()
        except Exception:
            self.render()

    def _org_sender_scope_options(self):
        """Build the SuperAdmin sender-picker options from its cached directory."""
        target = self._org_email_target_id()
        options, option_ids = [], set()
        for org in getattr(self, "_org_email_targets", []) or []:
            if not isinstance(org, dict):
                continue
            org_id = str(org.get("id") or "").strip()
            if not org_id:
                continue
            name = str(org.get("name") or org_id).strip()
            options.append(ft.DropdownOption(key=org_id,
                           text=name if name == org_id else f"{name} ({org_id})"))
            option_ids.add(org_id)
        if target and target not in option_ids:
            options.insert(0, ft.DropdownOption(key=target, text=target))
        return options

    def _org_sender_scope_status(self):
        if getattr(self, "_org_email_targets_error", ""):
            return strings.t("org_sender_scope_error", err=self._org_email_targets_error)
        return (strings.t("org_sender_scope_loading")
                if getattr(self, "_org_email_targets_loading", False)
                else strings.t("org_sender_scope_hint"))

    def _build_org_sender_fields(self):
        """The sender-only portion of Setup, safe to replace in place."""
        super_scope = self._is_super_admin_org_email_manager()
        if not (self._can_manage_org_email()
                and (not super_scope or self._org_email_target_id())):
            return ft.Container()
        self._sender_cell = ft.Container(self._make_cred_field("sender"),
                                         padding=ft.Padding.only(top=4, bottom=12))
        self._sender_name_cell = ft.Container(self._make_cred_field("sender_name"),
                                              padding=ft.Padding.only(top=4, bottom=12))
        self._gmail_cell = ft.Container(self._make_cred_field("gmail"),
                                        padding=ft.Padding.only(top=4))
        self._org_email_audit_cell = ft.Container(self._org_email_audit_control(),
                                                  padding=ft.Padding.only(top=4))
        controls = [
            ft.Container(height=12),
            field_label(strings.t("au_email_sender"), hint="optional"), self._sender_cell,
            field_label(strings.t("au_sender_name"), hint=strings.t("au_sender_name_hint")), self._sender_name_cell,
            ft.Row([ft.Column([
                field_label(strings.t("au_gmail_pw"), hint="optional", req=False,
                            info=strings.t("au_gmail_pw_info"), on_info=lambda e: self._show_help("gmail")),
                self._gmail_cell,
            ], expand=True, spacing=0)]),
            ft.Container(height=8),
            ft.Text(strings.t("org_sender_audit"), size=11, color=T.INK_2, weight=ft.FontWeight.BOLD),
            self._org_email_audit_cell,
        ]
        if self._org_sender_test_recipient():
            controls += [
                ft.Container(height=4),
                ft.Row([ghost_btn(strings.t("org_sender_test"), icon=ft.Icons.SEND_OUTLINED,
                                  on_click=self._send_org_sender_test,
                                  disabled=(getattr(self, "_org_sender_test_running", False)
                                            or getattr(self, "_org_email_target_loading", False)),
                                  tooltip=strings.t("org_sender_test_hint"))], alignment=ft.MainAxisAlignment.END),
                ft.Text(strings.t("org_sender_test_hint"), size=11, color=T.INK_3),
            ]
        return ft.Column(controls, spacing=0)

    def _refresh_org_sender_fields(self):
        """Replace sender controls only; preserve Setup's outer scroll position."""
        cell = getattr(self, "_org_email_fields_cell", None)
        if cell is None:
            self.render()
            return
        try:
            cell.content = self._build_org_sender_fields()
            cell.update()
        except Exception:
            self.render()

    def _refresh_org_sender_scope(self):
        """Patch the selector/status after a target or directory response."""
        picker = getattr(self, "_org_email_scope_picker", None)
        status = getattr(self, "_org_email_scope_status", None)
        try:
            if picker is not None:
                picker.options = self._org_sender_scope_options()
                picker.value = self._org_email_target_id() or None
                picker.update()
            if status is not None:
                status.value = self._org_sender_scope_status()
                status.color = (T.RED if getattr(self, "_org_email_targets_error", "") else T.INK_3)
                status.update()
        except Exception:
            pass

    def _refresh_tenant_project_scope_cell(self):
        """Patch the selected organization's pre-connect project card in place."""
        cell = getattr(self, "_tenant_project_scope_cell", None)
        if cell is None:
            self.render()
            return
        try:
            content = self._tenant_project_scope_card()
            cell.content = content
            cell.visible = bool(content)
            cell.update()
        except Exception:
            self.render()

    def _connection_edit(self):
        name = self._provider_choice
        # Cap the menu height so the (now grouped) provider list scrolls instead of
        # running off-screen. menu_height is newer Flet — degrade gracefully.
        _prov_kwargs = dict(
            value=name, options=self._provider_options(), on_select=self._on_provider_change,
            border_color=T.BORDER, focused_border_color=T.VIOLET,
            border_radius=T.R, content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
            text_size=13, filled=True, bgcolor=T.CARD, expand=True)
        try:
            self.prov_dd = ft.Dropdown(menu_height=320, **_prov_kwargs)
        except TypeError:
            self.prov_dd = ft.Dropdown(**_prov_kwargs)

        # Key field + Save/Update button are built by _build_ai_key_row() and
        # wrapped in a stable cell (self._ai_key_cell) below, so a provider/model
        # change can rebuild JUST that row in place instead of a full render —
        # scroll_to() is a no-op coroutine on Flet 0.85, so a full render always
        # snaps the page to the top (the reason the other dropdowns went in-place).

        # Model dropdown — populated live from the provider (falls back to a
        # curated list). Editable so an exact model id can also be typed.
        cur_model = self._saved_model(name)
        # Disable the model picker once connected (it can't change mid-connection;
        # changing the model requires reconnecting anyway).
        _model_locked = bool(getattr(self, "connected", False))
        # Build with only the args this Flet version supports. on_select is the
        # event that works on this build (on_change raises TypeError here);
        # editable/enable_filter/menu_height are newer and added only if accepted.
        _dd_kwargs = dict(
            value=cur_model or None, options=self._model_options(name),
            on_select=self._on_model_change,
            hint_text=strings.t("main_select_model"),
            disabled=_model_locked,
            border_color=T.BORDER, focused_border_color=T.VIOLET,
            border_radius=T.R, content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
            text_size=13, filled=True,
            bgcolor=(T.CARD_2 if _model_locked else T.CARD), expand=True)
        # Try the richest control first, then degrade gracefully on older Flet.
        try:
            self.model_dd = ft.Dropdown(editable=True, enable_filter=True,
                                        menu_height=300, **_dd_kwargs)
        except TypeError:
            try:
                self.model_dd = ft.Dropdown(menu_height=300, **_dd_kwargs)
            except TypeError:
                self.model_dd = ft.Dropdown(**_dd_kwargs)

        # Stable cell for the API-key row (see _build_ai_key_row): lets provider/
        # model changes patch just this row in place.
        self._ai_key_cell = ft.Container(self._build_ai_key_row(),
                                         padding=ft.Padding.only(top=4, bottom=12))

        # PAT / Gmail / sender / sender-name fields are built by _make_cred_field
        # and wrapped in in-place cells (see below) so saving them rebuilds only
        # that one row, not the whole screen.
        self._pat_cell = ft.Container(self._make_cred_field("pat"),
                                      padding=ft.Padding.only(top=4))

        # Azure Organization field (one-time set, preserved, Update to change).
        # Deliberately does NOT fall back to E.AZURE_ORG when this account has
        # none saved — that global can hold whichever account last connected in
        # this app session (or the module's hardcoded dev-default org), and
        # falling back to it here used to pre-fill a new/different account's
        # field with someone else's organization, which then got saved into
        # THEIR OWN creds file the moment they clicked Save without noticing.
        # Built by _build_org_row() into a stable cell so Save/Update patches just
        # this row in place (no full render → no scroll jump).
        self._org_cell = ft.Container(self._build_org_row(),
                                      padding=ft.Padding.only(top=4, bottom=12))

        # ── Test Management (see backend_setup.py / TRACKER_BACKENDS_PLAN.md §0) ──
        # This section is DELIBERATELY FIRST on the Setup screen and rendered as a
        # distinct titled banner (backend_setup._section_header): choosing WHERE
        # tests go is the most consequential decision on this screen — it changes
        # every credential field below it — so it leads rather than sitting mid-
        # form after the AI fields. The Azure org/PAT rows are UNCHANGED, just
        # conditional; a legacy creds file (no "backend" key) resolves to "azure"
        # and renders exactly what it always did.
        _fields = backend_setup.picker_rows(self, field_label, hover_field)
        if backend_setup.is_azure(self.creds):
            _fields += [
                field_label(strings.t("au_azure_org"), req=True,
                            info=strings.t("au_azure_org_info"),
                            on_info=lambda e: self._show_help("org")),
                self._org_cell,
                ft.Row([
                    ft.Column([
                        field_label(strings.t("au_azure_pat"), req=True,
                                    info=strings.t("au_azure_pat_info"),
                                    on_info=lambda e: self._show_help("pat")),
                        self._pat_cell,
                    ], expand=True, spacing=0),
                ]),
            ]
        elif backend_setup.active(self.creds) == backend_setup.XRAY:
            _fields += backend_setup.xray_rows(self, field_label, hover_field)
        elif backend_setup.active(self.creds) == backend_setup.TESTRAIL:
            _fields += backend_setup.testrail_rows(self, field_label, hover_field)
        elif backend_setup.is_hybrid(self.creds):
            _fields += backend_setup.hybrid_rows(self, field_label, hover_field)
        else:
            _fields += backend_setup.jira_rows(self, field_label, hover_field)

        # AI provider section follows the test-management section.
        _fields += [
            ft.Container(height=6),
            field_label(strings.t("au_ai_provider"), req=True, info=strings.t("au_ai_provider_info"),
                        on_info=lambda e: self._show_help("provider")),
            ft.Container(hover_field(self.prov_dd), padding=ft.Padding.only(top=4, bottom=12)),
            field_label(strings.t("au_model"), req=False, hint=self._model_src_hint(),
                        info=strings.t("au_model_info"),
                        on_info=lambda e: self._show_help("model")),
            ft.Container(hover_field(self.model_dd), padding=ft.Padding.only(top=4, bottom=12)),
            field_label(strings.t("au_api_key"), req=True, info=strings.t("au_api_key_info"),
                        on_info=lambda e: self._show_help("api_key")),
            self._ai_key_cell,
        ]
        # Email setup is organization-owned. An Organization Manager sees only
        # their JWT-bound organization; a SuperAdmin first picks an existing
        # organization and the server verifies that choice before access.
        _super_sender_scope = self._is_super_admin_org_email_manager()
        if _super_sender_scope:
            if not hasattr(self, "_org_email_target"):
                self._org_email_target = auth.org_id_of(getattr(self, "user", None))
            self._load_org_email_targets()
            _target = self._org_email_target_id()
            _scope_kwargs = dict(
                value=_target or None, options=self._org_sender_scope_options(),
                hint_text=strings.t("org_sender_scope_select"),
                on_select=self._on_org_email_target_change,
                border_color=T.BORDER, focused_border_color=T.VIOLET,
                border_radius=T.R, content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
                text_size=13, filled=True, bgcolor=T.CARD, expand=True,
                tooltip=strings.t("org_sender_scope_hint"))
            try:
                _scope_dd = ft.Dropdown(menu_height=320, **_scope_kwargs)
            except TypeError:
                _scope_dd = ft.Dropdown(**_scope_kwargs)
            self._org_email_scope_picker = _scope_dd
            self._org_email_scope_status = ft.Text(
                self._org_sender_scope_status(), size=11,
                color=(T.RED if getattr(self, "_org_email_targets_error", "") else T.INK_3))
            _fields += [
                ft.Container(height=12),
                field_label(strings.t("org_sender_scope"), req=True),
                ft.Container(hover_field(_scope_dd), padding=ft.Padding.only(top=4, bottom=4)),
                self._org_email_scope_status,
            ]
        self._org_email_fields_cell = ft.Container(self._build_org_sender_fields())
        _fields.append(self._org_email_fields_cell)
        return ft.Column(_fields, spacing=0)

    # Credential fields whose save affects ONLY themselves (no "THIS RUN"
    # summary, no model catalogue, nothing cross-screen) — so a save just flips
    # this one field editable<->locked. These are rebuilt IN PLACE (see
    # _refresh_cred_field) instead of full-rendering the whole Setup screen.
    # AI key / provider / org are deliberately NOT here: saving them changes the
    # run summary and the model dropdown, so they still full-render.
    _INPLACE_CRED_FIELDS = {
        "pat":         dict(value_key="pat", hint=strings.t("au_ph_paste_pat"), password=True),
        "sender":      dict(value_key="gmail_sender", hint=strings.t("au_ph_sender_gmail"),
                            password=False, env="GMAIL_SENDER"),
        "sender_name": dict(value_key="gmail_sender_name", password=False,
                            hint=strings.t("au_ph_display_name"),
                            env="GMAIL_SENDER_NAME"),
        "gmail":       dict(value_key="gmail", password=True,
                            hint=strings.t("au_ph_gmail_pw")),
    }

    def _make_cred_field(self, kind):
        """Build (or REBUILD) one self-contained credential field + its
        Save/Update button, stash them on self.<kind>_field / self.<kind>_btn,
        and return the row. Shared by first render and in-place refresh, so both
        produce an identical control."""
        cfg = self._INPLACE_CRED_FIELDS[kind]
        _sender_tip_keys = {
            "sender": "org_sender_tip_address",
            "sender_name": "org_sender_tip_name",
            "gmail": "org_sender_tip_password",
        }
        _sender_tip = (strings.t(_sender_tip_keys[kind])
                       if kind in _sender_tip_keys else None)
        raw = self.creds.get(cfg["value_key"], "")
        val = raw or (getattr(E, cfg["env"], "") if cfg.get("env") else "")
        editable = (not bool(raw)) or getattr(self, f"_{kind}_unlocked", False)
        field = ft.TextField(
            value=val, hint_text=cfg["hint"], read_only=not editable,
            password=cfg["password"], can_reveal_password=cfg["password"],
            bgcolor=(T.CARD if editable else T.CARD_2),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
            text_size=13, expand=True, tooltip=_sender_tip)
        setattr(self, f"{kind}_field", field)
        btn = (green_btn(strings.t("main_org_save"), on_click=getattr(self, f"_save_{kind}"),
                         tooltip=(strings.t("org_sender_tip_save") if _sender_tip else None)) if editable
               else ghost_btn(strings.t("main_update_btn"), on_click=getattr(self, f"_unlock_{kind}"),
                              tooltip=(strings.t("org_sender_tip_update") if _sender_tip else None)))
        setattr(self, f"{kind}_btn", btn)
        return ft.Row([hover_field(field), btn], spacing=8)

    def _refresh_cred_field(self, *kinds):
        """In-place rebuild of one or more credential rows after a save/unlock —
        NO full screen render, so the rest of Setup doesn't rebuild or jump
        scroll. Falls back to a full render if a cell isn't mounted yet."""
        for kind in kinds:
            cell = getattr(self, f"_{kind}_cell", None)
            if cell is None:
                self.render()
                return
            try:
                cell.content = self._make_cred_field(kind)
                cell.update()
            except Exception:
                self.render()
                return

    # ---- connection: saved (connected) ----
    def _cred_saved_row(self, icon, k, v, badge_ctrl):
        return ft.Row([
            ft.Icon(icon, size=16, color=T.INK_2),
            ft.Column([
                ft.Text(k, size=11, color=T.INK_2, weight=ft.FontWeight.BOLD),
                ft.Text(v, size=12.5, color=T.INK, weight=ft.FontWeight.BOLD),
            ], spacing=1, expand=True),
            badge_ctrl,
            ghost_btn(strings.t("main_update_btn"), icon=ft.Icons.EDIT_OUTLINED,
                      on_click=lambda e: self._edit_connection()),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _connection_saved(self):
        name = self.current_provider()
        gm = self.creds.get("gmail", "")
        masked_gm = ("•••• •••• ••••" if gm else "—")
        _sender = self.creds.get("gmail_sender") or "—"
        _sender_name = self.creds.get("gmail_sender_name") or "—"
        div = ft.Container(height=1, bgcolor=T.BORDER_2, margin=ft.Margin.symmetric(vertical=8))
        _model = self._saved_model(name)
        prov_val = f"{T.disp_name(name)}  ·  {_model}" if _model else T.disp_name(name)

        def _mask(v):
            return ("••••••••••" + (v[-4:] if len(v) >= 4 else "")) if v else "—"

        _rows = [
            # Keep the Test Management backend visible after connecting (was only
            # shown in the pre-connect picker) — reuses the same banner so the
            # active backend (e.g. "Jira → TestRail") is always in view.
            backend_setup._section_header(
                ft, T, backend_setup.label_for(backend_setup.active(self.creds))),
            self._cred_saved_row(ft.Icons.AUTO_AWESOME, strings.t("au_ai_provider"), prov_val,
                                 badge("Active", "green", ft.Icons.CHECK)),
        ]
        # Backend-aware credential rows — was a single hardcoded "Azure DevOps PAT"
        # row, which showed (and claimed "Valid") on Jira/Xray/TestRail/hybrid
        # connections too. Now reflects the ACTIVE backend's real credential(s).
        for _label, _secret in backend_setup.saved_cred_fields(self.creds):
            _rows += [div, self._cred_saved_row(
                ft.Icons.KEY_OUTLINED, _label, _mask(_secret),
                badge("Valid", "green", ft.Icons.CHECK))]
        # Email setup is Admin-only (see _connection_edit) — a non-admin shouldn't
        # even see whether it's configured here either.
        if self._is_admin():
            _rows += [
                div,
                # Email SENDER address + display NAME — previously the saved view
                # jumped straight from PAT to the Gmail App Password and never
                # showed these, so once connected you couldn't see or reach the
                # sender name without editing blindly. Each row's "Update" opens
                # the full edit view (same as every other saved row).
                self._cred_saved_row(ft.Icons.ALTERNATE_EMAIL, strings.t("au_email_sender"), _sender,
                                     badge("optional", "grey")),
                div,
                self._cred_saved_row(ft.Icons.BADGE_OUTLINED, strings.t("au_sender_name"), _sender_name,
                                     badge("optional", "grey")),
                div,
                self._cred_saved_row(ft.Icons.MAIL_OUTLINED, strings.t("au_gmail_pw"), masked_gm,
                                     badge("optional", "grey")),
            ]
        return ft.Column(_rows, spacing=0)

    def _edit_connection(self):
        self.connected = False
        self.render()

    # ---- tool segment ----
    def _tool_segment(self, compact=False, persist=True):
        """persist=True (Settings' call site) writes the choice back into
        self.creds as the app-wide default for future sessions. persist=False
        (Setup's call site) only changes self.tool for the CURRENT session —
        Setup is meant to be a per-run override of Settings' default, not
        another way to silently rewrite it. See _set_tool's docstring."""
        # Settings' call site (persist=True) must show the PERSISTED default
        # (self.creds["tool"]), not the live self.tool — Setup's own toggle
        # (persist=False) changes self.tool for this session only (so
        # generation actually uses the override), but self.tool is one
        # shared attribute read everywhere. Without this split, overriding
        # the tool on Setup made Settings' toggle visually flip too, looking
        # like the saved default had silently changed when it hadn't.
        _cur_tool = (self.creds.get("tool", self.tool) if persist else self.tool)
        def seg(label, icon, key):
            sel = (_cur_tool == key)
            c = ft.Container(
                ft.Row([ft.Icon(icon, size=(15 if compact else 16),
                                color=(T.VIOLET_INK if sel else T.INK_2)),
                        ft.Text(label, size=(12 if compact else 12.5),
                                weight=ft.FontWeight.BOLD,
                                color=(T.VIOLET_INK if sel else T.INK_2))],
                       spacing=7, alignment=ft.MainAxisAlignment.CENTER, tight=True),
                expand=(not compact), height=(34 if compact else 40),
                alignment=ft.Alignment.CENTER,
                padding=ft.Padding.symmetric(vertical=0, horizontal=(15 if compact else 9)),
                bgcolor=(T.VIOLET_SOFT if sel else None), border_radius=T.R_SM,
                border=ft.Border.all(1, T.VIOLET if sel else ft.Colors.TRANSPARENT),
                animate=140,
                shadow=(ft.BoxShadow(blur_radius=10, spread_radius=-4,
                                     color=ft.Colors.with_opacity(0.35, T.VIOLET),
                                     offset=ft.Offset(0, 3)) if sel else None),
                on_click=lambda e, k=key: self._set_tool(k, persist=persist))
            if not sel:
                def _h(e, _c=c):
                    try:
                        _c.bgcolor = (ft.Colors.with_opacity(0.55, T.CARD)
                                      if e.data in (True, "true", "True") else None)
                        _c.update()
                    except Exception:
                        pass
                c.on_hover = _h
            return c
        _t = strings.t("main_gen_titles") if compact else strings.t("main_tool_titles")
        _s = strings.t("main_gen_steps") if compact else strings.t("main_tool_steps")
        return ft.Container(
            ft.Row([seg(_t, ft.Icons.DESCRIPTION_OUTLINED, "titles"),
                    seg(_s, ft.Icons.LAYERS_OUTLINED, "steps")],
                   spacing=4, tight=compact),
            padding=4, bgcolor=T.CARD_2, border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    def _set_tool(self, k, persist=True):
        """persist=False is Setup's per-run override: changes self.tool for
        THIS session only, without touching Settings' stored default — so a
        one-off "generate titles instead of steps just this once" pick on
        Setup doesn't silently redefine what every future session starts
        with. Settings' own toggle (persist=True, the default here for
        backward compatibility with any other caller) is the only thing that
        should actually rewrite self.creds['tool']. Setup still reads that
        default fresh at the start of every new session (see __init__ /
        _switch_user_creds) — it just doesn't write back to it anymore."""
        if getattr(self, "readonly", False):
            return
        _changed = (getattr(self, "tool", None) != k)
        self.tool = k
        if persist:
            try:
                self.creds["tool"] = k
                store.save(self.creds)
            except Exception:
                pass
        # The "THIS RUN" estimate is TOOL-SPECIFIC: Titles counts existing test
        # cases, Steps counts cases-with-steps (count_test_cases vs
        # count_existing_steps). Switching the toggle left the previous tool's
        # number on screen, so it read as a stale/wrong count for the newly
        # selected generator. Clear it and refetch (a no-op until connected +
        # project + plan + stories are all set).
        if _changed:
            self._estimated_tc = None
            try:
                self._fetch_estimate()
            except Exception:
                pass
        self.render()

    # ---- output-language segment ----
    def _lang_segment(self, persist=True):
        # Same split as _tool_segment above: Settings (persist=True) must
        # show the persisted default language, not Setup's session-only
        # override of self.lang.
        _cur_lang = ("en" if self.creds.get("lang") == "en" else "ar") if persist else self.lang
        def seg(label, key):
            sel = (_cur_lang == key)
            c = ft.Container(
                ft.Text(label, size=12, weight=ft.FontWeight.BOLD,
                        color=(T.VIOLET_INK if sel else T.INK_2)),
                height=32, alignment=ft.Alignment.CENTER,
                padding=ft.Padding.symmetric(vertical=0, horizontal=16),
                bgcolor=(T.VIOLET_SOFT if sel else None), border_radius=T.R_SM,
                border=ft.Border.all(1, T.VIOLET if sel else ft.Colors.TRANSPARENT),
                animate=140,
                shadow=(ft.BoxShadow(blur_radius=10, spread_radius=-4,
                                     color=ft.Colors.with_opacity(0.35, T.VIOLET),
                                     offset=ft.Offset(0, 3)) if sel else None),
                on_click=lambda e, k=key: self._set_lang(k, persist=persist))
            if not sel:
                def _h(e, _c=c):
                    try:
                        _c.bgcolor = (ft.Colors.with_opacity(0.55, T.CARD)
                                      if e.data in (True, "true", "True") else None)
                        _c.update()
                    except Exception:
                        pass
                c.on_hover = _h
            return c
        # Language dropdown built from engine.LANGUAGES (native names). Replaces
        # the old 2-button ar/en toggle so all supported languages are pickable.
        _lang_opts = [ft.DropdownOption(key=_code, text=_info["native"])
                      for _code, _info in E.LANGUAGES.items()]
        _lang_val = self.lang if persist else getattr(self, "run_lang", self.lang)
        _lang_kwargs = dict(
            value=(_lang_val if _lang_val in E.LANGUAGES else "ar"),
            options=_lang_opts,
            on_select=lambda e: self._set_lang(e.control.value or self.lang, persist=persist),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=10, horizontal=10),
            text_size=13, filled=True, bgcolor=T.CARD, width=200)
        try:
            _lang_dd = ft.Dropdown(menu_height=300, **_lang_kwargs)
        except TypeError:
            _lang_dd = ft.Dropdown(**_lang_kwargs)
        return ft.Container(
            _lang_dd, width=210,
            padding=4, bgcolor=T.CARD_2, border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    def _set_lang(self, k, persist=True):
        """See _set_tool's docstring — persist=False (Setup's call site) is a
        session-only override of Settings' saved default language."""
        if getattr(self, "readonly", False):
            return
        code = k if k in E.LANGUAGES else "ar"
        if persist:
            # Settings: set the saved DEFAULT (and the live session with it).
            self.lang = code
            self.run_lang = code
            try:
                self.creds["lang"] = code
                store.save(self.creds)
            except Exception:
                pass
            self.render()
            return
        # Setup: per-RUN override for THIS session only. Update IN PLACE
        # (like the provider/model pickers) so the page never jump-scrolls.
        self.run_lang = code
        updated = False
        try:
            _fn = getattr(self, "_setup_desc_build", None)
            _cell = getattr(self, "_setup_desc_cell", None)
            if _fn is not None and _cell is not None:
                _cell.content = _fn()
                _cell.update()
                updated = True
            _lt = getattr(self, "_sum_lang", None)
            if _lt is not None:
                _lt.value = E.LANGUAGES.get(code, E.LANGUAGES["en"])["native"]
                _lt.update()
                updated = True
        except Exception:
            updated = False
        if not updated:
            self.render()

    # ---- interface-language segment (SEPARATE from output language) ----
    def _ui_lang_segment(self):
        # The INTERFACE language picker. A distinct controller from
        # _lang_segment (which sets the OUTPUT/test-case language). This one
        # drives the nav, screen chrome and RTL — nothing about generated
        # content. Persisted default only (no per-run override for chrome).
        _opts = [ft.DropdownOption(key=_code, text=_info["native"])
                 for _code, _info in E.LANGUAGES.items()]
        _val = self.ui_lang if self.ui_lang in E.LANGUAGES else "en"
        _kwargs = dict(
            value=_val, options=_opts,
            on_select=lambda e: self._set_ui_lang(e.control.value or "en"),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=10, horizontal=10),
            text_size=13, filled=True, bgcolor=T.CARD, width=200)
        try:
            _dd = ft.Dropdown(menu_height=300, **_kwargs)
        except TypeError:
            _dd = ft.Dropdown(**_kwargs)
        return ft.Container(
            _dd, width=210,
            padding=4, bgcolor=T.CARD_2, border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    def _set_ui_lang(self, code):
        """Set the persisted INTERFACE language (nav/labels/RTL). Independent
        of the output/test-case language (see _set_lang)."""
        if getattr(self, "readonly", False):
            return
        code = code if code in E.LANGUAGES else "en"
        self.ui_lang = code
        try:
            strings.set_ui_lang(code)
        except Exception:
            pass
        try:
            self.creds["ui_lang"] = code
            store.save(self.creds)
        except Exception:
            pass
        try:
            self.page.rtl = strings.ui_is_rtl()
        except Exception:
            pass
        self.render()

    # ---- credential handlers ----
    def _on_provider_change(self, e):
        prev = getattr(self, "_provider_choice", None)
        sel = self.prov_dd.value
        # Group headers ("__grp_0__"/"__grp_1__") are not real providers — ignore a
        # stray selection (belt-and-suspenders for Flet builds that let a disabled
        # option be picked) and restore the previous choice.
        if not sel or str(sel).startswith("__grp_"):
            try:
                self.prov_dd.value = prev
                self.prov_dd.update()
            except Exception:
                pass
            return
        self._provider_choice = sel
        name = self._provider_choice
        # Persist the choice so it survives an app restart (not just sign-out/in).
        try:
            self.creds["provider"] = sel; store.save(self.creds)
        except Exception:
            pass
        # Cancel any in-flight Connect for the old provider: bump the generation
        # token (so its worker's results are ignored) and drop the spinner now.
        self._connect_gen = getattr(self, "_connect_gen", 0) + 1
        if getattr(self, "_connecting", False):
            self._connecting = False
            self._connect_status = ""
            self._err("")
        # changing provider while connected invalidates the connection — EXCEPT
        # while a Run/Automation is actively in progress. _disconnect() clears
        # self.project/self.plan_id/self.connected wholesale; the in-flight
        # run's own Azure calls are unaffected (project/plan_id were captured
        # as local args when it started, not re-read from self.* live), but
        # wiping the Setup screen's live selection out from under an ACTIVE
        # run corrupts state for whatever the user does next (e.g. Setup's
        # story loader / estimate fetch both bail out via `if not
        # (self.connected and self.project and self.plan_id)`), and was seen
        # live to also desync the Run screen's activity log (see run.py's
        # screen() for that half of the fix). The explicit reason this
        # provider switch exists at all — letting a PAUSED automation Resume
        # on a new provider without re-running Connect — never actually
        # needed self.connected to drop either; only a genuine, no-run-active
        # reconnect should force one.
        _did_disconnect = False
        if (prev and prev != name and getattr(self, "connected", False)
                and not getattr(self, "_run_active", False)
                and not getattr(self, "_auto_running", False)):
            self._disconnect(f"Provider changed to {T.disp_name(name)} — reconnect to continue.")
            _did_disconnect = True
        # reset the model list so it refetches for the newly selected provider
        self._models_for = None
        self._model_choices = None
        # switch the live engine provider (+ its saved key & model, if any) so a
        # PAUSED automation can Resume on this provider without re-running Connect
        try:
            E.set_credentials(provider=name, api_key=(self._saved_key(name) or None),
                              model=(self._saved_model(name) or None))
        except Exception:
            pass
        # A disconnect flips the whole connection view (summary → edit form), so a
        # full render is genuinely needed there. Otherwise patch only the changed
        # controls IN PLACE — a full render would snap the page to the top because
        # scroll_to() is a no-op coroutine on Flet 0.85 (restore can't fix it).
        if _did_disconnect:
            self.render()
            return
        try:
            self.model_dd.value = self._saved_model(name) or None
            self.model_dd.options = self._model_options(name)  # also kicks the live-catalogue refetch
            self.model_dd.update()
            self._sync_ai_key_cell()                           # key field + Save/Update button
        except Exception:
            self.render()

    def _save_key(self, e=None):
        name = self._provider_choice
        val = (self.api_key_field.value or "").strip()
        if not val:
            self._err(strings.t("main_api_key_required")); return
        self.creds["keys"][self._cred_slot(name)] = val; store.save(self.creds)
        # apply to the engine immediately so a PAUSED automation can Resume on the
        # newly chosen provider/key without re-running Connect
        try:
            E.set_credentials(provider=name, api_key=val,
                              model=(self._saved_model(name) or None))
        except Exception:
            pass
        self._key_unlocked = False
        # a new key may unlock a different model catalogue → refetch
        self._models_for = None
        self._model_choices = None
        self._toast(strings.t("main_apikey_saved_activated", name=T.disp_name(name)))
        # In-place: the row flips Save→Update and the model catalogue may change.
        # Patch just those instead of a full render (no scroll jump).
        try:
            self.model_dd.options = self._model_options(name)
            self.model_dd.update()
            self._sync_ai_key_cell()
        except Exception:
            self.render()

    def _model_src_hint(self):
        """Small label next to the Model field: 'live' or 'fallback list'."""
        src = getattr(self, "_model_src", None)
        if src == "live":
            return "live"
        if src == "static":
            return "fallback list"
        return None

    # ---- model selection ----
    def _model_has_key(self, name, model):
        """True if this specific model has a saved API key. PER_MODEL_KEY_PROVIDERS
        store a key per model ('<provider>::<model>'); other providers use one key
        for all their models."""
        if name in self.PER_MODEL_KEY_PROVIDERS:
            return bool((self.creds["keys"].get("%s::%s" % (name, model)) or "").strip())
        return bool((self.creds["keys"].get(name) or "").strip())

    def _model_option(self, name, m):
        """A model dropdown option. For per-model-key providers (e.g. NVIDIA) mark it
        ● active / ○ inactive by whether that model has a key — mirroring the
        provider list. Single-key providers show the plain model name (one key
        covers every model, so a per-model mark would be meaningless)."""
        if name not in self.PER_MODEL_KEY_PROVIDERS:
            return ft.DropdownOption(key=m, text=m)
        active = self._model_has_key(name, m)
        dot = "●" if active else "○"
        return ft.DropdownOption(key=m,
            text="%s  %s%s" % (dot, m, "  (active)" if active else ""))

    def _model_options(self, name):
        """Build dropdown options for the current provider. Shows the static list
        IMMEDIATELY (no blocking call), then fetches the live catalogue in the
        background and re-renders — so switching provider updates the models at
        once instead of freezing render() on a 15s network call."""
        if getattr(self, "_models_for", None) == name and getattr(self, "_model_choices", None) is not None:
            choices = self._model_choices
        else:
            static = list(E.STATIC_MODELS.get(name, []))
            cur = self._saved_model(name)
            if cur and cur not in static:
                static = [cur] + static
            self._model_choices = static
            self._model_src = "static"
            self._models_for = name
            choices = static
            self._fetch_models_async(name)   # upgrade to live in the background
        return [self._model_option(name, m) for m in choices]

    def _fetch_models_async(self, name):
        """Fetch the live model catalogue off the UI thread, then re-render."""
        if getattr(self, "readonly", False) or not self._saved_key(name):
            return  # read-only viewers / no saved key → keep the static list (no
                    # network call, so nothing to spin on)
        if getattr(self, "_model_fetching", None) == name:
            return  # a fetch for this provider is already in flight
        self._model_fetching = name
        def work():
            try:
                key = self._saved_key(name)
                models, src = E.list_models(provider=name, api_key=(key or None))
            except Exception:
                models, src = (E.STATIC_MODELS.get(name, []), "static")
            # discard if the user switched provider again while we were fetching
            if getattr(self, "_provider_choice", None) != name:
                self._model_fetching = None
                return
            cur = self._saved_model(name)
            if cur and cur not in models:
                models = [cur] + list(models)
            self._model_choices = models
            self._model_src = src
            self._models_for = name
            self._model_fetching = None
            self.ui_safe(lambda: self._apply_live_models(name))
        self._bg(work)

    def _apply_live_models(self, name):
        """Update ONLY the model dropdown's options when the background fetch
        returns. A full render() here would rebuild every Setup control and snap
        shut whichever dropdown (provider OR model) the user just opened — this
        targeted update leaves both dropdowns' open/closed state alone."""
        if getattr(self, "_provider_choice", None) != name:
            return  # user switched provider again; this result is stale
        dd = getattr(self, "model_dd", None)
        if dd is None:
            return
        new_opts = [self._model_option(name, m) for m in (self._model_choices or [])]
        # nothing changed (live == static) → don't touch the control at all
        try:
            cur = [getattr(o, "key", None) for o in (dd.options or [])]
            if cur == [getattr(o, "key", None) for o in new_opts]:
                return
        except Exception:
            pass
        try:
            dd.options = new_opts
            dd.update()
        except Exception:
            # dropdown isn't mounted (user navigated away) — next render shows it
            pass

    def _on_model_change(self, e):
        name = self._provider_choice
        val = (self.model_dd.value or "").strip()
        if not val:
            return
        prev = self._saved_model(name)
        if val == prev:
            return  # no real change (avoids churn from on_change while filtering)
        self.creds.setdefault("models", {})[name] = val
        store.save(self.creds)
        try:
            # Keys are stored per model, so the effective key can change with the
            # model — push the new model's saved key alongside it.
            E.set_credentials(provider=name, model=val,
                              api_key=(self._saved_key(name) or None))
        except Exception:
            pass
        # for per-model-key providers, a model switch swaps the key → refetch catalogue
        if name in self.PER_MODEL_KEY_PROVIDERS:
            self._models_for = None
            self._model_choices = None
        # changing the model while connected invalidates the connection → the view
        # flips (summary → edit), so a full render is genuinely needed there.
        if getattr(self, "connected", False):
            self._disconnect(f"Model changed to {val} — reconnect to continue.")
            self._toast(strings.t("main_model_set", model=val))
            self.render()
            return
        self._toast(strings.t("main_model_set", model=val))
        # Not connected: the model dropdown already shows the pick. The only
        # visible change is the key row on per-model-key providers (the key swaps
        # with the model) — patch just that in place, no full render (no scroll
        # jump; scroll_to is a no-op coroutine on Flet 0.85).
        if name in self.PER_MODEL_KEY_PROVIDERS:
            self._sync_ai_key_cell()

    def _unlock_key(self, e=None):
        # In-place: unlocking flips the key row from "Update" to an editable field
        # + "Save" — patch just that row, no full render (no scroll jump).
        self._key_unlocked = True; self._sync_ai_key_cell()

    def _save_pat(self, e=None):
        val = (self.pat_field.value or "").strip()
        if not val:
            self._err(strings.t("main_pat_required")); return
        self.creds["pat"] = val; store.save(self.creds)
        self._pat_unlocked = False
        self._toast(strings.t("main_pat_saved")); self._refresh_cred_field("pat")

    def _unlock_pat(self, e=None):
        self._pat_unlocked = True; self._refresh_cred_field("pat")

    def _save_gmail(self, e=None):
        val = (self.gmail_field.value or "").strip()
        self.creds["gmail"] = val; store.save(self.creds)
        self._gmail_unlocked = False
        self._toast(strings.t("main_gmail_saved")); self._refresh_cred_field("gmail")
        self._push_org_email()

    def _unlock_gmail(self, e=None):
        self._gmail_unlocked = True; self._refresh_cred_field("gmail")

    def _save_org(self, e=None):
        val = (self.org_field.value or "").strip()
        if not val:
            self._err(strings.t("main_org_required")); return
        self.creds["org"] = val; store.save(self.creds)
        try:
            E.set_credentials(org=val)
        except Exception:
            pass
        self._org_unlocked = False
        self._toast(strings.t("main_org_saved")); self._sync_org_cell()

    def _unlock_org(self, e=None):
        # In-place: flip the org row to editable + "Save", no full render.
        self._org_unlocked = True; self._sync_org_cell()

    def _save_sender(self, e=None):
        val = (self.sender_field.value or "").strip()
        self.creds["gmail_sender"] = val; store.save(self.creds)
        try:
            E.set_credentials(gmail_sender=val)
        except Exception:
            pass
        self._sender_unlocked = False
        self._toast(strings.t("main_sender_saved")); self._refresh_cred_field("sender")
        self._push_org_email()

    def _save_sender_name(self, e=None):
        """Persist the sender DISPLAY NAME (From: 'Name' <address>) and apply it
        immediately for the next email. Now driven by an explicit Save button
        (was on_change, i.e. saving on every keystroke) so it behaves like every
        other credential field in the card. Reads the field directly rather than
        e.control.value, since the event now comes from the BUTTON, not the
        TextField."""
        val = (self.sender_name_field.value or "").strip()
        self.creds["gmail_sender_name"] = val
        try:
            store.save(self.creds)
        except Exception:
            pass
        try:
            E.set_credentials(gmail_sender_name=val)
        except Exception:
            pass
        self._sender_name_unlocked = False
        self._toast(strings.t("main_sender_name_saved"))
        self._refresh_cred_field("sender_name")
        self._push_org_email()

    def _unlock_sender_name(self, e=None):
        self._sender_name_unlocked = True; self._refresh_cred_field("sender_name")

    def _unlock_sender(self, e=None):
        self._sender_unlocked = True; self._refresh_cred_field("sender")

    def _org_sender_test_recipient(self):
        """The only allowed recipient for a Setup sender test.

        A test is deliberately delivered to the authenticated administrator's
        own address rather than an arbitrary text field. This prevents Setup
        from becoming an unaudited bulk-email or address-probing surface.
        """
        try:
            return str((getattr(self, "user", None) or {}).get("email") or "").strip()
        except Exception:
            return ""

    def _send_org_sender_test(self, e=None):
        """Persist the displayed org sender settings, then send one SMTP test.

        The operation runs off the UI thread. It writes through the same
        authorization-protected org-settings endpoint used by the individual
        Save buttons, then sends only to the signed-in administrator.
        """
        if getattr(self, "_org_sender_test_running", False):
            return
        recipient = self._org_sender_test_recipient()
        if not recipient:
            self._toast(strings.t("org_sender_test_recipient_missing"))
            return
        target_org = self._org_email_target_id()
        if self._is_super_admin_org_email_manager() and not target_org:
            self._toast(strings.t("org_sender_scope_required"))
            return
        if getattr(self, "_org_email_target_loading", False):
            self._toast(strings.t("org_sender_test_wait"))
            return

        # Read the controls so the test can validate the values currently shown
        # in Setup. The button then persists that exact set atomically before
        # sending, rather than racing the three individual Save workers.
        sender = self._field_or_saved("sender_field", self.creds.get("gmail_sender", ""))
        sender_name = self._field_or_saved(
            "sender_name_field", self.creds.get("gmail_sender_name", ""))
        app_password = self._field_or_saved("gmail_field", self.creds.get("gmail", ""))
        if not sender or not app_password:
            self._toast(strings.t("org_sender_test_save_first"))
            return

        self._org_sender_test_running = True
        self.render()
        self._toast(strings.t("org_sender_test_sending", email=recipient))

        def work():
            try:
                if auth.configured():
                    ok, msg = auth.admin_set_org_email(
                        sender, sender_name, app_password,
                        org_id=(target_org if self._is_super_admin_org_email_manager() else None))
                    if not ok:
                        raise RuntimeError(msg)
                E.set_credentials(gmail=app_password, gmail_sender=sender,
                                  gmail_sender_name=sender_name)
                ok, msg = E.send_report(
                    [recipient],
                    "QA Studio — sender configuration test",
                    "<h2>QA Studio sender test</h2>"
                    "<p>Your organization’s report sender is configured and can send email.</p>",
                    refresh_sender=False)
                if not ok:
                    raise RuntimeError(msg or "Unknown SMTP error")
                outcome = (True, "")
            except Exception as ex:
                outcome = (False, str(ex))

            # The SMTP call runs on this device, so record its result explicitly
            # after it finishes. The server stores only success/failure and a
            # truncated error message — never the Gmail App Password.
            audit_payload = None
            if auth.configured():
                try:
                    auth.record_org_email_test(
                        target_org if self._is_super_admin_org_email_manager() else None,
                        outcome[0], outcome[1])
                    audit_ok, audit_payload = auth.get_org_settings(
                        org_id=(target_org if self._is_super_admin_org_email_manager() else None),
                        include_audit=True)
                    if not audit_ok:
                        audit_payload = None
                except Exception:
                    pass

            def apply():
                self._org_sender_test_running = False
                if isinstance(audit_payload, dict) and isinstance(audit_payload.get("audit"), list):
                    self._org_email_audit = audit_payload["audit"]
                if getattr(self, "active", "") == "setup":
                    self.render()
                if outcome[0]:
                    self._toast(strings.t("org_sender_test_sent", email=recipient))
                else:
                    self._toast(strings.t("org_sender_test_failed", err=outcome[1]))
            self.ui_safe(apply)

        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception as ex:
            self._org_sender_test_running = False
            self._toast(strings.t("org_sender_test_failed", err=str(ex)))

    def _push_org_email(self):
        """Publish the just-saved sender config to its organization.

        The Edge Function derives an Organization Manager's target from their
        signed JWT. A SuperAdmin supplies the selector's target id, but the
        function validates both their role and that the organization exists.
        Other organizations never receive this sender credential.
        In auth-disabled desktop use, fields remain local-only as before.

        Runs in the background and NEVER blocks or fails the local Save the caller
        already did — if publishing fails (offline, function not reachable, token
        expired, permission denied server-side despite the local scope check),
        the user sees a distinct toast explaining THAT part failed, while their
        own local credentials remain saved and usable regardless."""
        if not (auth.configured() and self._can_manage_org_email()):
            return
        target_org = self._org_email_target_id()
        if self._is_super_admin_org_email_manager() and not target_org:
            self._toast(strings.t("org_sender_scope_required"))
            return

        def work():
            try:
                ok, msg = auth.admin_set_org_email(
                    self.creds.get("gmail_sender", ""),
                    self.creds.get("gmail_sender_name", ""),
                    self.creds.get("gmail", ""),
                    org_id=(target_org if self._is_super_admin_org_email_manager() else None))
            except Exception as ex:
                ok, msg = False, str(ex)
            if not ok:
                self.ui_safe(lambda: self._toast(
                    strings.t("main_saved_not_shared", err=msg)))
        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception:
            # Thread creation itself failing is extremely unlikely, but this
            # method must never raise into a Save button's click handler.
            pass

    async def _apply_org_email_refresh_to_setup(self):
        """Apply an org-sender fetch on Flet 0.85's asyncio UI loop.

        ``page.run_task()`` is Flet's supported path for a background result
        that mutates controls. Calling ``Control.update()`` through this app's
        general ``ui_safe()`` helper uses ``page.run_thread()``, which is fine
        for heavy background rendering but can leave a small imperative patch
        unpainted on this Flet version. This narrow coroutine is intentionally
        used only for the three credential cells.
        """
        if getattr(self, "active", "") != "setup":
            return
        self._refresh_cred_field("sender", "sender_name", "gmail")
        audit_cell = getattr(self, "_org_email_audit_cell", None)
        if audit_cell is not None:
            try:
                audit_cell.content = self._org_email_audit_control()
                audit_cell.update()
            except Exception:
                pass

    def _refresh_org_settings(self):
        """Background-fetch the shared org-wide email settings (sender / sender
        name / Gmail App Password, set once by an Admin via _push_org_email
        above) and apply them via E.set_credentials — so THIS signed-in user's
        install sends reports with the same config as everyone else, regardless
        of what (if anything) is in their own local creds file. If Setup is
        already open when the background request completes, its sender fields
        are also rebuilt to reflect the fetched values. Called after every
        sign-in / session-restore / sign-out (see _switch_user_creds).

        Deliberately silent on every failure path (auth not configured, not
        signed in yet, offline, function/table not set up, malformed response):
        local creds — already applied earlier in __init__ / _switch_user_creds —
        remain the fallback in every one of those cases, so a user is never
        blocked from sending email just because the shared fetch didn't work.

        SECURITY: gated on holding at least one action capability — i.e. any
        user who is not a pure Viewer. Report / plan / usage emails can be sent
        from pickers spread across the app (the Run screen's end-of-run
        auto-email, the Report screen, Sprint Plan / Regression / Sprint Report
        exports+email, and the AI-Usage screen's 'Email report' — which every
        non-Viewer can use for their own usage), so the shared sender credential
        must reach anyone the app already lets send mail from ANYWHERE. That set
        is exactly 'has any act.* capability': the same rule used elsewhere
        (see the read-only handling for no-action-cap screens) to decide who can
        perform actions at all. This used to be gated on act.export alone, which
        was wrong — emailing is triggered from many screens, so e.g. a member who
        could run but lacked the Report-screen export cap would fire an email
        attempt that then found no password. We still withhold it from a pure
        zero-capability Viewer, for whom every send button is read-only anyway,
        preserving the secret-exposure guard: the org's Gmail App Password is
        never fetched into the process of a user who can't send from anywhere."""
        if not auth.configured():
            return
        _caps = auth.caps_for(getattr(self, "user", None))
        if not any(str(_c).startswith("act.") for _c in _caps):
            return

        def work():
            try:
                ok, data = auth.get_org_settings(include_audit=True)
            except Exception:
                return
            if not ok or not isinstance(data, dict):
                return
            settings = data.get("settings") or {}
            em = settings.get("email") if isinstance(settings, dict) else None
            if not isinstance(em, dict):
                return
            # Keep this in memory (not store.save): the Setup controls are built
            # from self.creds, while the encrypted per-user file remains the
            # user's own local configuration until they explicitly save.
            self.creds["gmail_sender"] = str(em.get("sender") or "")
            self.creds["gmail_sender_name"] = str(em.get("sender_name") or "")
            self.creds["gmail"] = str(em.get("app_password") or "")
            self._org_email_inherited_from = str(em.get("inherited_from_org_id") or "")
            if isinstance(data.get("audit"), list):
                self._org_email_audit = data["audit"]
            self._org_email_sync_status = strings.t("org_sender_synced")
            try:
                E.set_credentials(
                    gmail=self.creds["gmail"],
                    gmail_sender=self.creds["gmail_sender"],
                    gmail_sender_name=self.creds["gmail_sender_name"])
            except Exception:
                pass
            # The request often finishes AFTER Setup has rendered. Flet 0.85
            # needs the imperative cell updates queued on its own asyncio loop.
            try:
                self.page.run_task(self._apply_org_email_refresh_to_setup)
            except Exception:
                # A page can be closing while the request resolves. Fall back to
                # the app's general dispatcher rather than letting a refresh
                # failure affect reporting or sign-in.
                self.ui_safe(lambda: self._refresh_cred_field(
                    "sender", "sender_name", "gmail"))
        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception:
            pass

    def _ensure_org_email_fresh(self):
        """SYNCHRONOUS, best-effort refresh of the shared org-wide sender creds,
        run just-in-time before an email is sent (registered with engine via
        E.set_sender_refresher; engine calls it from ensure_sender_creds()).

        Same gate + apply as _refresh_org_settings, but blocking rather than
        threaded — the caller is already a send path about to do network I/O, so
        a short get_org_settings() call here is fine and guarantees the value is
        current at send time (fixing the 'admin updated it after I signed in, so
        my cached copy is stale' timing gap). Silent on EVERY failure path so it
        can never block or break a send: it only upgrades the cached credential
        to the latest server value when the server is reachable, and otherwise
        leaves whatever is already in memory untouched."""
        try:
            if not auth.configured():
                return
            _caps = auth.caps_for(getattr(self, "user", None))
            if not any(str(_c).startswith("act.") for _c in _caps):
                return
            target_org = (self._org_email_target_id()
                          if self._is_super_admin_org_email_manager() else None)
            ok, data = auth.get_org_settings(org_id=target_org or None)
            if not ok or not isinstance(data, dict):
                return
            em = data.get("email")
            if not isinstance(em, dict):
                return
            E.set_credentials(
                gmail=(em.get("app_password") or None),
                gmail_sender=(em.get("sender") or None),
                gmail_sender_name=(em.get("sender_name") or None))
        except Exception:
            pass

    def _snack(self, msg, color, icon):
        """Floating toast used for all errors & confirmations (never inline now)."""
        if not msg:
            return
        try:
            sb = ft.SnackBar(
                content=ft.Row([
                    ft.Icon(icon, color="#FFFFFF", size=18),
                    ft.Text(msg, color="#FFFFFF", size=13,
                            weight=ft.FontWeight.W_600, expand=True),
                ], spacing=10, tight=False),
                bgcolor=color, duration=6000,
                behavior=ft.SnackBarBehavior.FLOATING,
                shape=ft.RoundedRectangleBorder(radius=12),
                margin=ft.Margin.all(16),
                padding=ft.Padding.symmetric(vertical=12, horizontal=16))
        except Exception:
            sb = ft.SnackBar(content=ft.Text(msg, color="#FFFFFF"),
                             bgcolor=color, duration=6000)
        try:
            # dismiss any showing snackbar (open=False) THEN drop it, so Flet — which
            # shows one snackbar at a time — reliably shows this one. Just removing it
            # from the overlay left its state "open" and could block the next.
            for c in list(self.page.overlay):
                if isinstance(c, ft.SnackBar):
                    try:
                        c.open = False
                    except Exception:
                        pass
            self.page.overlay[:] = [c for c in self.page.overlay
                                    if not isinstance(c, ft.SnackBar)]
        except Exception:
            pass
        try:
            self.page.overlay.append(sb)
            sb.open = True
            self.page.update()
        except Exception:
            try:
                if hasattr(self.page, "open"):
                    self.page.open(sb)
            except Exception:
                pass

    def _err(self, msg):
        self._err_msg = msg
        # keep the (now-unmounted) label in sync so other code paths don't break
        try:
            self.err_text.value = msg
        except Exception:
            pass
        # Errors surface as a floating toast, not an inline line at the page bottom.
        self._snack(msg, T.RED, ft.Icons.ERROR_OUTLINE)

    def _toast(self, msg):
        self._snack(msg, T.GREEN, ft.Icons.CHECK_CIRCLE)

    # ---- task card (connected) ----
    def _task_card(self):
        # Lazily load the selected plan's sprint stories for the searchable picker.
        if self.plan_id and self._setup_stories is None and not self._setup_stories_loading:
            self._setup_stories_loading = True

            def _load_ss():
                try:
                    # Primary: stories actually in this test plan (its requirement
                    # suites) — works even when the plan has no iteration, which is
                    # why the picker used to stay empty/disabled. Both that and the
                    # iteration fallback now live in backend_setup.fetch_stories_for_plan
                    # (they were duplicated inline here and at _reload_setup_stories,
                    # including a raw dev.azure.com fetch in the UI layer).
                    ss = backend_setup.fetch_stories_for_plan(self, self.plan_id)
                except Exception:
                    ss = []
                self._setup_stories = ss
                self._setup_stories_loading = False
                # Patches just the story picker in place — see
                # _sync_setup_story_cell (defined later this same
                # _task_card() call, so it's already set by the time this
                # background fetch can possibly complete). Falls back to a
                # full render on the off chance it isn't set yet.
                self.ui_safe(getattr(self, "_sync_setup_story_cell", self.render))
            threading.Thread(target=_load_ss, daemon=True).start()

        _inv0 = getattr(self, "_invalid", set())
        self.project_dd = ft.Dropdown(
            value=self.project, hint_text=strings.t("main_select_project"),
            options=[ft.DropdownOption(p) for p in self._projects],
            on_select=self._on_project_change,
            tooltip=(self.project or None),
            border_color=(T.RED if "project" in _inv0 else T.BORDER),
            focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=8), text_size=13, filled=True,
            bgcolor=T.CARD, expand=True)

        # Matches Task Manager's searchable pickers (report_user_dd/ct_user_dd
        # in task_manager.py) exactly — same searchable_dropdown() helper,
        # same UN-filled styling. Those two are the proven-working type-to-
        # filter instances in this app; this one previously also passed
        # filled=True + bgcolor=T.CARD, which neither of them do. Filled mode
        # pairs a Material "filled" background with the internal editable
        # filter field in a way that's inconsistent with how the other two
        # actually render/behave, so it's dropped here to match rather than
        # to guess at a middle ground.
        _plan_tip = next((f"[{p['id']}] {p['name']}" for p in self._plans if p["id"] == self.plan_id), None)
        self.plan_dd = searchable_dropdown(
            value=(str(self.plan_id) if self.plan_id else None),
            hint_text=strings.t("main_search_plan"),
            options=[ft.DropdownOption(key=str(p["id"]), text=f"[{p['id']}] {p['name']}") for p in self._plans],
            on_select=self._on_plan_change,
            tooltip=_plan_tip,
            border_color=(T.RED if "plan" in _inv0 else T.BORDER),
            focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
            text_size=13, expand=True)

        self.plan_id_field = ft.TextField(
            value=(str(self.plan_id) if self.plan_id else ""), read_only=True,
            hint_text=strings.t("main_none"), bgcolor=T.CARD_2, color=T.VIOLET_INK,
            tooltip=(f"Test Plan ID: {self.plan_id}" if self.plan_id else None),
            text_size=13, border_color=T.BORDER, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=10), expand=True)

        # Story IDs: editable comma field + in-place chip preview (no full re-render)
        self._chip_row = ft.Row([], wrap=True, spacing=6, run_spacing=6)
        self._chip_wrap = ft.Container(self._chip_row, padding=ft.Padding.only(top=8),
                                       visible=bool(self.story_ids))

        def _build_chips():
            chips = []
            for sid in self.story_ids:
                chips.append(ft.Container(
                    ft.Row([
                        ft.Text(str(sid), size=12, weight=ft.FontWeight.BOLD,
                                color=T.VIOLET_INK, font_family=T.F_MONO),
                        ft.GestureDetector(
                            content=ft.Icon(ft.Icons.CLOSE, size=12, color=T.VIOLET_INK),
                            on_tap=lambda e, s=sid: _remove_story(s),
                            mouse_cursor=ft.MouseCursor.CLICK),
                    ], spacing=5, tight=True),
                    padding=ft.Padding.only(left=10, right=7, top=5, bottom=5),
                    bgcolor=T.VIOLET_SOFT, border_radius=T.R_SM,
                    border=ft.Border.all(1, "#D9D2FF"),
                    on_hover=regression._chip_hover, animate_scale=120))
            if len(self.story_ids) > 1:
                chips.append(regression._clear_chip(_clear_all_stories))
            self._chip_row.controls = chips
            self._chip_wrap.visible = bool(self.story_ids)
            # Keep the story checkbox-multiselect in sync with the chips: removing a
            # chip unticks its checkbox; adding via search/manual entry ticks it.
            # (No-op at first build — the picker registers its syncer just below.)
            _sync = (getattr(self, "_dd_syncers", {}) or {}).get("setup_stories")
            if _sync:
                try:
                    _sync([str(s) for s in self.story_ids])
                except Exception:
                    pass

        def _update_summary_inplace():
            # update the THIS RUN stats + estimate labels without a full render
            try:
                if hasattr(self, "_sum_stories"):
                    self._sum_stories.value = strings.t("run_n_selected", n=len(self.story_ids))
                    self._sum_stories.update()
            except Exception:
                pass
            try:
                if hasattr(self, "_est_sub"):
                    self._est_sub.value = strings.t("run_est_sub", n=len(self.story_ids))
                    self._est_sub.update()
            except Exception:
                pass

        def _remove_story(sid):
            self.story_ids = [s for s in self.story_ids if s != sid]
            self.story_field.value = ", ".join(str(s) for s in self.story_ids)
            _build_chips()
            self._estimated_tc = None
            try:
                self.story_field.update(); self._chip_row.update(); self._chip_wrap.update()
            except Exception:
                pass
            _update_summary_inplace()
            self._fetch_estimate()

        def _clear_all_stories(e=None):
            self.story_ids = []
            self.story_field.value = ""
            _build_chips()
            self._estimated_tc = None
            try:
                self.story_field.update(); self._chip_row.update(); self._chip_wrap.update()
            except Exception:
                pass
            _update_summary_inplace()
            self._fetch_estimate()

        def _parse_ids(raw):
            ids = []
            for x in (raw or "").replace(" ", ",").replace("\n", ",").split(","):
                x = x.strip().strip("()[]")
                if x.isdigit() and int(x) not in ids and int(x) not in self.story_ids:
                    ids.append(int(x))
            return ids

        def _commit_stories(e=None):
            """Full commit (Enter): turn everything in the box into chips."""
            new_ids = _parse_ids(self.story_field.value)
            for i in new_ids:
                if i not in self.story_ids:
                    self.story_ids.append(i)
            if self.story_ids:
                self._err_msg = ""
            self.story_field.value = ""   # cleared; committed IDs now live as chips
            _build_chips()
            self._estimated_tc = None
            try:
                self.story_field.update(); self._chip_row.update(); self._chip_wrap.update()
            except Exception:
                pass
            _update_summary_inplace()
            self._fetch_estimate()
            _clear_story_invalid()

        def _on_story_change(e=None):
            """As the user types/pastes, auto-chip any COMPLETED id (one followed
            by a comma or space), but leave a trailing in-progress number in the
            box so clicking away to copy another id won't prematurely commit it."""
            val = self.story_field.value or ""
            # Only act when the text ends with a separator (id is 'finished')
            if val and val[-1] in (",", " ", "\n"):
                new_ids = _parse_ids(val)
                if new_ids:
                    for i in new_ids:
                        if i not in self.story_ids:
                            self.story_ids.append(i)
                    self._err_msg = ""
                    self.story_field.value = ""
                    _build_chips()
                    self._estimated_tc = None
                    try:
                        self.story_field.update(); self._chip_row.update(); self._chip_wrap.update()
                    except Exception:
                        pass
                    _update_summary_inplace()
                    self._fetch_estimate()
                    _clear_story_invalid()

        _inv = getattr(self, "_invalid", set())
        self.story_field = ft.TextField(
            value="",
            hint_text=strings.t("main_paste_id"),
            border_color=(T.RED if "stories" in _inv else T.BORDER),
            focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
            text_size=13, expand=True, on_submit=_commit_stories,
            on_change=_on_story_change)
        self._story_err = ft.Container(
            ft.Row([ft.Icon(ft.Icons.ERROR_OUTLINE, size=13, color=T.RED),
                    ft.Text(strings.t("main_need_story"),
                            size=11.5, color=T.RED, weight=ft.FontWeight.W_600)],
                   spacing=5, tight=True),
            visible=("stories" in _inv),
            padding=ft.Padding.only(top=2, bottom=8))

        def _clear_story_invalid():
            inv = getattr(self, "_invalid", None)
            if inv is not None:
                inv.discard("stories")
            try:
                if self.story_ids:
                    self.story_field.border_color = T.BORDER
                    self.story_field.update()
                    self._story_err.visible = False
                    self._story_err.update()
            except Exception:
                pass

        _build_chips()

        # Searchable MULTISELECT of the plan's stories (same component as the
        # Regression / Sprint screens — type-to-filter + checkboxes). Selecting
        # syncs self.story_ids; picked stories also show as removable chips below.
        def _toggle_setup_story(key, checked):
            # `key` is str(s["id"]). Recover the story's REAL id so its type is
            # preserved: an int work-item id on Azure, a string issue KEY
            # (e.g. "SCRUM-3") on Jira/Xray. int(key) crashed on Jira keys, and
            # is what left the picker unusable on those backends. Matches
            # _all_setup_stories, which already appends s["id"] verbatim.
            sid = next((s["id"] for s in (self._setup_stories or [])
                        if str(s["id"]) == key), key)
            if checked and sid not in self.story_ids:
                self.story_ids.append(sid)
            elif not checked:
                self.story_ids = [s for s in self.story_ids if str(s) != str(sid)]
            self._err_msg = ""
            self._estimated_tc = None
            _build_chips()   # in-place chip refresh — no full render, scroll stays put
            try:
                self._chip_row.update(); self._chip_wrap.update()
            except Exception:
                pass
            _update_summary_inplace()
            self._fetch_estimate()
            _clear_story_invalid()

        def _all_setup_stories(checked):
            # Reads self._setup_stories fresh (not the outer `_ss` snapshot)
            # since this closure now outlives a single _task_card() build —
            # _sync_setup_story_cell() rebuilds the picker in place once new
            # stories load, without re-running _task_card() (and therefore
            # without re-defining this closure with a fresh `_ss`). Reading
            # live state here is what keeps "select all" correct once that
            # no longer happens automatically.
            if checked:
                have = set(self.story_ids)
                for s in (self._setup_stories or []):
                    if s["id"] not in have:
                        self.story_ids.append(s["id"])
            else:
                self.story_ids = []
            self._err_msg = ""
            self._estimated_tc = None
            _build_chips()
            try:
                self._chip_row.update(); self._chip_wrap.update()
            except Exception:
                pass
            _update_summary_inplace()
            self._fetch_estimate()
            _clear_story_invalid()

        def _open_setup_stories():
            self._setup_story_open = not self._setup_story_open

        # Dynamic cell for the story multiselect (same pattern as Task
        # Manager's _report_dynamic_cell/_ct_dynamic_cell): a STABLE Container
        # whose .content gets swapped + .update()-ed directly, instead of the
        # picker being rebuilt only as a side effect of a full self.render().
        # This is what _load_setup_stories_inplace() now calls into once a
        # plan's stories finish loading, rather than falling back to
        # self.render() — which used to tear down and rebuild the ENTIRE
        # scroll column just to populate this one control, forcing a second,
        # timing-dependent scroll-restore race on top of the editable
        # dropdown's own focus-scroll (see _on_plan_change's _scroll_keep
        # comment). Removing that full render doesn't touch the dropdown's
        # own focus-scroll behavior, but it removes the SECOND, compounding
        # jump this one screen — uniquely among this app's pickers — used to
        # add on top of it.
        def _build_story_picker():
            _ss = self._setup_stories or []
            _ph = ("Select stories" if _ss
                   else ("Loading stories…" if (self.plan_id and self._setup_stories_loading)
                         else ("No stories in this plan" if self.plan_id
                               else strings.t("main_select_plan_first"))))
            return regression._checkbox_multiselect(
                [(str(s["id"]), f"[{s['id']}] {(s['title'] or '')[:60]}") for s in _ss],
                [str(s) for s in self.story_ids],
                _toggle_setup_story, _all_setup_stories,
                is_open=self._setup_story_open, on_open=_open_setup_stories,
                placeholder=_ph, height=260, empty="No stories found in this plan.",
                page=self.page, app=self, sync_key="setup_stories",
                disabled=not _ss)

        self._setup_story_cell = ft.Container(_build_story_picker())

        def _sync_setup_story_cell():
            # Falls back to a full render only if this cell was somehow
            # unmounted (e.g. the user navigated off Setup before the story
            # fetch finished) — same defensive shape as this session's
            # Task Manager epoch-guard fix for stale/orphaned cell refs.
            try:
                self._setup_story_cell.content = _build_story_picker()
                self._setup_story_cell.update()
            except Exception:
                self.render()
        self._sync_setup_story_cell = _sync_setup_story_cell

        # The manual "paste IDs" input was removed; self.story_field is kept alive
        # (unrendered) so the legacy add/remove/commit handlers stay valid.
        story_box = ft.Column([
            self._setup_story_cell,
            self._chip_wrap], spacing=0, tight=True)

        self.email_picker = regression.email_recipient_picker(
            self, "emails", is_open_key="_email_open", sync_key="setup_emails",
            on_change=self._refresh_email_summary)

        # Sprint summary button — green (like Create) when a plan is selected,
        # grey/disabled when no plan is chosen yet.
        _sum_enabled = bool(self.plan_id) and self.can("act.sprint_summary")
        self._summary_btn = ft.FilledButton(
            strings.t("main_sprint_summary_btn"),
            icon=ft.Icons.SUMMARIZE_OUTLINED, height=42,
            disabled=not _sum_enabled,
            on_click=lambda e: self._open_sprint_summary(),
            style=ft.ButtonStyle(
                bgcolor={"": (T.GREEN if _sum_enabled else T.CARD_2)},
                color={"": ("#FFFFFF" if _sum_enabled else T.INK_3)},
                elevation=0,
                shape=ft.RoundedRectangleBorder(radius=T.R),
                side=(None if _sum_enabled else ft.BorderSide(1, T.BORDER)),
                padding=ft.Padding.symmetric(horizontal=16, vertical=0)))
        self._summary_btn.expand = True
        # Match Create Plan: carry a green drop shadow when enabled; none when disabled.
        self._summary_shadow = ft.Container(
            ft.Row([self._summary_btn], spacing=0),
            border_radius=T.R, expand=True,
            shadow=(_btn_shadow(T.GREEN, 0.5) if _sum_enabled else None))
        _summary_row = self._summary_shadow

        # Open-in-Azure button beside the Test Plan ID (refreshed in _on_plan_change)
        _can_open_plan = self.can("act.open_plan")
        self._open_plan_btn = ft.IconButton(
            ft.Icons.OPEN_IN_NEW, icon_size=17,
            icon_color=(T.VIOLET_INK if (self.plan_id and _can_open_plan) else T.INK_3),
            tooltip=((backend_setup.plan_link_label(self) if self.plan_id
                      else strings.t("main_select_plan_first")) if _can_open_plan
                     else "You don’t have permission to open the plan"),
            disabled=not (bool(self.plan_id) and _can_open_plan),
            on_click=lambda e: self._open_azure(),
            style=ft.ButtonStyle(
                bgcolor={"": T.VIOLET_SOFT} if self.plan_id else {"": T.CARD_2},
                shape=ft.RoundedRectangleBorder(radius=T.R)),
            width=46, height=46)

        rows = [
            sec_head("3", strings.t("main_task_head"),
                     ft.Row([ft.Icon(ft.Icons.ARROW_FORWARD, size=13, color=T.INK_3),
                             ft.Text(strings.t("main_from_connection"), size=11, color=T.INK_3, weight=ft.FontWeight.BOLD)],
                            spacing=4, tight=True)),
            ft.Container(height=12),
            # Row 1 — Project (full width)
            field_label(strings.t("au_project"), req=True),
            ft.Container(hover_field(self.project_dd), padding=ft.Padding.only(top=4, bottom=12)),
            # Row 2 — Test Plan (50%) · Test Plan ID (50%)
            ft.Row([
                ft.Column([field_label(strings.t("au_test_plan"), req=True),
                           ft.Container(hover_field(self.plan_dd), padding=ft.Padding.only(top=4))],
                          expand=1, spacing=0),
                ft.Column([field_label(strings.t("au_test_plan_id"), hint="auto"),
                           ft.Container(
                               ft.Row([self.plan_id_field, self._open_plan_btn],
                                      spacing=8,
                                      vertical_alignment=ft.CrossAxisAlignment.CENTER),
                               padding=ft.Padding.only(top=4))],
                          expand=1, spacing=0),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Container(height=12),
            # Row 3 — Create Plan (50%) · Sprint Summary (50%)
            ft.Row([
                ft.Container(
                    green_btn(strings.t("main_create_plan_btn"), icon=ft.Icons.ADD, expand=True,
                              on_click=lambda e: self._open_create_plan(),
                              disabled=not self.can("act.create_plan"), ignore_ro=True),
                    expand=1),
                ft.Container(_summary_row, expand=1),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=14),
            field_label(strings.t("au_user_story_ids"), req=True, hint=strings.t("au_comma_separated")),
            ft.Container(story_box, padding=ft.Padding.only(top=4, bottom=2)),
            self._story_err,
        ]

        rows += [
            field_label(strings.t("au_report_emails"), hint="optional", req=False),
            ft.Container(self.email_picker, padding=ft.Padding.only(top=4)),
        ]
        return card(ft.Column(rows, spacing=0), expand=False)

    def _existing_segment(self):
        def seg(label, key, icon=None):
            sel = (self.existing_mode == key)
            row = []
            if icon: row.append(ft.Icon(icon, size=13, color=(T.INK if sel else T.INK_2)))
            row.append(ft.Text(label, size=12.5, weight=ft.FontWeight.BOLD, color=(T.INK if sel else T.INK_2)))
            return ft.Container(ft.Row(row, spacing=5, alignment=ft.MainAxisAlignment.CENTER, tight=True),
                                expand=True, padding=ft.Padding.symmetric(vertical=0, horizontal=8),
                                bgcolor=(T.CARD if sel else None), border_radius=T.R_SM,
                                border=ft.Border.all(1, T.BORDER) if sel else None,
                                on_click=lambda e, k=key: self._set_existing(k))
        return ft.Container(
            ft.Row([seg(strings.t("au_skip"), "skip"), seg(strings.t("au_evaluate"), "evaluate", ft.Icons.AUTO_AWESOME)], spacing=4),
            padding=4, bgcolor=T.CARD_2, border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    def _set_existing(self, k):
        self.existing_mode = k; self.render()

    def _on_project_change(self, e):
        self.project = self.project_dd.value
        self.plan_id = None; self.plan_name = None
        # Project changed → drop project-scoped caches so EVERY dependent picker/
        # dropdown (member & email pickers across Regression / Sprint / Task
        # Manager / Performance, plans, stories) refetches for the new project
        # instead of showing the previous project's data.
        self._members_cache = None
        self._members_cache_project = None
        self._load_plans()
        self.render()

    def _on_plan_change(self, e):
        # Remember where the user was BEFORE selecting — Flet's editable dropdown
        # focus-scrolls the page on selection; we snap back to this afterwards.
        _scroll_keep = getattr(self, "_scroll_offset", 0) or 0
        # ROOT CAUSE (two bugs, one line). `int(self.plan_dd.value)` used to
        # raise "int() argument must be ... not 'NoneType'" on Flet's top-level
        # red banner. Reading the value off the STORED control ref is the
        # problem: `self.plan_dd` is rebuilt on every render (see its
        # construction) with `value=str(self.plan_id) if self.plan_id else None`,
        # so the FIRST selection fires on_select while that ref still holds
        # None — hence int(None). Guarding it without fixing the read then
        # traded the crash for a silent no-op: plan_id stayed None, the Test
        # Plan ID showed "— none —" and the story dropdown stayed disabled
        # ("Select a test plan first") even though a plan was visibly picked.
        # So resolve from the EVENT first, and only fall back to the stored ref.
        _v = None
        for _src in (getattr(e, "data", None),
                     getattr(getattr(e, "control", None), "value", None),
                     getattr(self.plan_dd, "value", None)):
            if _src is not None and str(_src).strip():
                _v = str(_src).strip()
                break
        if _v is None:
            return
        # An editable Dropdown can hand back the option TEXT ("[103151] Name")
        # rather than its key, so recover the id when that happens.
        # NB: plain string parsing on purpose — main.py does not import `re`.
        _ids = {str(p["id"]) for p in (self._plans or [])}
        if _v not in _ids and _v.startswith("[") and "]" in _v:
            _inner = _v[1:_v.index("]")].strip()
            if _inner in _ids:
                _v = _inner
        # Only Azure plan ids are integers; a Zephyr folder, an Xray "Test Plan"
        # issue key ("PROJ-123") or a TestRail suite id is not — keep those as
        # the string every backend adapter already accepts.
        self.plan_id = int(_v) if _v.lstrip("-").isdigit() else _v
        for p in self._plans:
            if p["id"] == self.plan_id:
                self.plan_name = p["name"]
        # Load this plan's stories right now and patch the picker in place, so the
        # dropdown fills immediately on selection (it used to wait for an unrelated
        # re-render because this handler patches controls in place, never rendering).
        self._load_setup_stories_inplace()
        # Update only the affected controls in place so the scroll position
        # doesn't jump to the top on every selection.
        updated_any = False
        try:
            if hasattr(self, "plan_id_field"):
                self.plan_id_field.value = str(self.plan_id)
                self.plan_id_field.update(); updated_any = True
        except Exception:
            pass
        # Enable/recolor the Open-in-Azure button now that a plan is chosen — but
        # only if the user actually has permission to open the plan.
        try:
            if hasattr(self, "_open_plan_btn") and self.can("act.open_plan"):
                self._open_plan_btn.disabled = False
                self._open_plan_btn.icon_color = T.VIOLET_INK
                self._open_plan_btn.tooltip = backend_setup.plan_link_label(self)
                self._open_plan_btn.style = ft.ButtonStyle(
                    bgcolor={"": T.VIOLET_SOFT},
                    shape=ft.RoundedRectangleBorder(radius=T.R))
                self._open_plan_btn.update(); updated_any = True
        except Exception:
            pass
        # Enable/recolor the Sprint Summary button now that a plan is chosen —
        # only if the user has permission to generate it.
        try:
            if hasattr(self, "_summary_btn") and self.can("act.sprint_summary"):
                self._summary_btn.disabled = False
                self._summary_btn.style = ft.ButtonStyle(
                    bgcolor={"": T.GREEN}, color={"": "#FFFFFF"}, elevation=0,
                    shape=ft.RoundedRectangleBorder(radius=T.R),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=0))
                if hasattr(self, "_summary_shadow"):
                    self._summary_shadow.shadow = _btn_shadow(T.GREEN, 0.5)
                    try:
                        self._summary_shadow.update()
                    except Exception:
                        pass
                self._summary_btn.update(); updated_any = True
        except Exception:
            pass
        # Update the THIS RUN summary panel's "Test plan" line
        try:
            if hasattr(self, "_sum_plan"):
                self._sum_plan.value = f"#{self.plan_id}"
                self._sum_plan.update(); updated_any = True
        except Exception:
            pass
        self._fetch_estimate()
        # Fall back to a full render only if we couldn't patch in place
        if not updated_any:
            self.render()
        # Snap the scroll back to where it was (counteracts the dropdown's focus
        # auto-scroll-to-top). Fire now + a few delayed shots so the last word wins.
        if _scroll_keep:
            def _keep():
                col = getattr(self, "_left_scroll", None)
                if col is not None:
                    try:
                        col.scroll_to(offset=_scroll_keep, duration=0)
                    except Exception:
                        pass
            # Serialized on the session loop — see ui_safe's docstring.
            try:
                self.ui_safe(_keep)
            except Exception:
                pass
            for _d in (0.15, 0.35, 0.6):
                try:
                    threading.Timer(_d, lambda: self.ui_safe(_keep)).start()
                except Exception:
                    pass

    def _load_setup_stories_inplace(self):
        """Load the selected plan's stories (from its requirement suites), then
        patch just the story multiselect in place via _sync_setup_story_cell
        — NOT a full self.render(). This used to call self.render() here
        ("plan-change is a single deliberate event, so one render is fine"),
        but that was the actual cause of the persistent scroll jump on plan
        selection, not Flet's editable-dropdown focus-scroll itself: a full
        render tears down and rebuilds the ENTIRE scroll column from scratch
        (see main.py's render()/_restore_scroll comments), which then raced
        against _on_plan_change's own manual scroll-restore timers rather
        than cooperating with them. Every OTHER control this same plan-change
        flow touches (plan_id_field, the Open/Summary buttons, the THIS RUN
        panel) already patches in place for exactly this reason — this was
        the one piece still falling back to a full render, and it was the
        slow, async-timing-dependent one, which is exactly why it was the
        visible source of the jump."""
        if not (self.connected and self.project and self.plan_id):
            return
        self._setup_stories = None
        self._setup_stories_loading = True
        pid = self.plan_id

        def work():
            try:
                # Same two-step as the Setup task-card loader — shared via
                # backend_setup so the Azure-specific plan/iteration fallback
                # exists in exactly one place.
                ss = backend_setup.fetch_stories_for_plan(self, pid)
            except Exception:
                ss = []
            if pid != self.plan_id:   # plan changed again mid-load — drop stale result
                return
            try:
                E.sort_stories_by_board(getattr(self, "project", ""), ss)
            except Exception:
                pass
            self._setup_stories = ss
            self._setup_stories_loading = False
            self.ui_safe(getattr(self, "_sync_setup_story_cell", self.render))
        self._bg(work)

    def _fetch_estimate(self):
        """Fetch the real number of test cases across selected stories (steps mode).
        Updates only the estimate labels in place — no full render, so scroll stays put."""
        if not self.story_ids:
            # No stories selected (e.g. just cleared) -> reset the estimate in place
            # instead of leaving the previous count stale on the THIS RUN panel.
            self._estimated_tc = 0
            try:
                if hasattr(self, "_est_num"):
                    self._est_num.value = "~0"
                    self._est_sub.value = strings.t("run_est_sub", n=0)
                    self._est_num.update(); self._est_sub.update()
            except Exception:
                pass
            return
        if not (self.connected and self.project and self.plan_id):
            return
        def work():
            try:
                if self.tool == "steps":
                    have, total = backend_setup.count_existing_steps(self, self.plan_id, self.story_ids)
                    self._estimated_tc = total
                else:
                    # Real count of existing test cases across the selected stories
                    # (was a fabricated stories×6 guess).
                    self._estimated_tc = backend_setup.count_test_cases(
                        self, self.plan_id, self.story_ids)
            except Exception:
                return
            # Update just the two labels, not the whole page
            try:
                if hasattr(self, "_est_num"):
                    self._est_num.value = f"~{self._estimated_tc}"
                    self._est_sub.value = strings.t("run_est_sub", n=len(self.story_ids))
                    self._est_num.update(); self._est_sub.update()
            except Exception:
                pass
        self._bg(work)

    def _on_stories_change(self, e):
        raw = (self.story_field.value or "").strip().strip("()[]")
        ids = []
        for x in raw.replace(" ", ",").split(","):
            x = x.strip()
            if x.isdigit(): ids.append(int(x))
        self.story_ids = ids

    # ---- task locked ----
    def _task_locked(self):
        return card(ft.Stack([
            ft.Column([
                sec_head("3", strings.t("main_task_head"),
                         ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, size=13, color=T.INK_3),
                                 ft.Text(strings.t("main_locked"), size=11, color=T.INK_3, weight=ft.FontWeight.BOLD)],
                                spacing=4, tight=True)),
                ft.Container(height=14),
                ft.Row([ft.Container(ft.Container(height=40, bgcolor=T.CARD_2,
                                                  border=ft.Border.all(1, T.BORDER), border_radius=T.R), expand=True),
                        ft.Container(ft.Container(height=40, bgcolor=T.CARD_2,
                                                  border=ft.Border.all(1, T.BORDER), border_radius=T.R), expand=True)],
                       spacing=13),
                ft.Container(height=12),
                ft.Container(height=44, bgcolor=T.CARD_2, border=ft.Border.all(1, T.BORDER), border_radius=T.R),
            ], spacing=0),
            ft.Container(
                ft.Container(
                    ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, size=14, color=T.INK_2),
                            ft.Text(strings.t("main_connect_hint"), size=12,
                                    color=T.INK_2, weight=ft.FontWeight.BOLD)], spacing=6, tight=True),
                    padding=ft.Padding.symmetric(vertical=14, horizontal=9), bgcolor=T.CARD, border_radius=20,
                    border=ft.Border.all(1, T.BORDER)),
                alignment=ft.Alignment.CENTER, expand=True),
        ]), expand=True)

    def _email_summary_text(self):
        """'N recipients' (or '—') for the THIS RUN → Email summary line."""
        n = len([e for e in (self.emails or "").split(",") if e.strip()])
        return f"{n} recipient{'s' if n != 1 else ''}" if n else "—"

    def _refresh_email_summary(self):
        """Update the THIS RUN → Email line the moment recipients change, without a
        full re-render (the email picker updates in place)."""
        lbl = getattr(self, "_sum_email", None)
        if lbl is None:
            return
        try:
            lbl.value = self._email_summary_text()
            lbl.update()
        except Exception:
            pass

    # ---- right rail ----
    def _setup_right(self):
        if self.connected:
            est = getattr(self, "_estimated_tc", None)
            # Show "…" while the real count is being fetched, instead of a fake guess.
            _est_disp = "…" if (est is None and self.story_ids) else f"{est or 0}"
            self._est_num = ft.Text(f"~{_est_disp}", size=32, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK)
            self._est_sub = ft.Text(strings.t("run_est_sub", n=len(self.story_ids)), size=12,
                                    color=T.INK_2, weight=ft.FontWeight.BOLD)
            rows = [("Generator", strings.t("main_gen_steps") if self.tool == "steps" else strings.t("main_gen_titles")),
                    ("Language", E.LANGUAGES.get(getattr(self, "run_lang", self.lang), E.LANGUAGES["en"])["native"]),
                    ("Project", (self.project or "—")[:16]),
                    ("Test plan", f"#{self.plan_id}" if self.plan_id else "—"),
                    ("Stories", strings.t("run_n_selected", n=len(self.story_ids))),
                    ("Email", self._email_summary_text())]
            if self.tool == "steps":
                rows.insert(5, ("Existing", self.existing_mode.title()))
            full_vals = {"Project": (self.project or "—"),
                         "Test plan": (f"#{self.plan_id}" if self.plan_id else "—")}
            detail_rows = []
            _sum_label = {"Generator": strings.t("main_sum_generator"),
                          "Language": strings.t("main_sum_language"),
                          "Project": strings.t("main_sum_project"),
                          "Test plan": strings.t("main_sum_testplan"),
                          "Stories": strings.t("main_sum_stories"),
                          "Email": strings.t("main_sum_email"),
                          "Existing": strings.t("main_sum_existing")}
            for i, (k, v) in enumerate(rows):
                val_text = ft.Text(v, size=12, color=T.INK, weight=ft.FontWeight.BOLD,
                                   tooltip=full_vals.get(k))
                if k == "Stories":
                    self._sum_stories = val_text
                if k == "Test plan":
                    self._sum_plan = val_text
                if k == "Email":
                    self._sum_email = val_text
                if k == "Language":
                    self._sum_lang = val_text
                detail_rows.append(ft.Container(
                    ft.Row([ft.Text(_sum_label.get(k, k), size=12, color=T.INK_2, weight=ft.FontWeight.BOLD),
                            ft.Container(expand=True),
                            val_text]),
                    padding=ft.Padding.symmetric(vertical=0, horizontal=8),
                    border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER_2)) if i < len(rows)-1 else None))
            ready = bool(self.project and self.plan_id and self.story_ids)
            # A run (or Automation) already in progress → block starting a second
            # one from here. self._run_active is the same flag the window-close
            # confirm dialog already trusts (_set_run_active), so this stays in
            # sync with the real run lifecycle for free instead of needing its
            # own separate tracking. Two concurrent runs would fight over the
            # same activity log/stats state and Azure calls, so this isn't just
            # a UX nicety — starting a second run while one is live isn't safe.
            _run_busy = bool(getattr(self, "_run_active", False)
                             or getattr(self, "_auto_running", False)
                             or getattr(self, "_remote_run_active", False))
            return card(ft.Column([
                ft.Text(strings.t("main_this_run"), size=11, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK),
                ft.Container(height=13),
                *detail_rows,
                ft.Container(expand=True),
                ft.Container(
                    ft.Row([self._est_num, self._est_sub],
                           spacing=8, vertical_alignment=ft.CrossAxisAlignment.END),
                    padding=ft.Padding.only(bottom=14)),
                self._run_start_btn(_run_busy),
            ], spacing=0, expand=True), expand=True)
        else:
            # Stored as an instance ref (same idiom as self._est_num/_est_sub
            # above, and self._sum_stories/_sum_plan) so _connect()'s
            # background worker can update JUST this status line while an
            # attempt is in flight — via self._connect_status_text.value = …
            # + .update() — instead of calling self.render(), which tears
            # down and rebuilds this entire screen from scratch just to swap
            # one line of text. Only meaningful while _connecting is True,
            # since that's the only time this control is actually mounted.
            # expand=True so a long status ("Validating Azure → TestRail & loading
            # projects…") WRAPS inside the narrow right panel instead of running
            # off the edge — the hybrid labels are longer than "Azure DevOps".
            self._connect_status_text = ft.Text(self._connect_status, size=12,
                                                color=T.INK_2, weight=ft.FontWeight.BOLD,
                                                expand=True)
            self._connect_status_row = ft.Container(
                ft.Row([
                    ft.ProgressRing(width=16, height=16, stroke_width=2, color=T.VIOLET),
                    self._connect_status_text,
                ], spacing=10),
                padding=ft.Padding.symmetric(vertical=10, horizontal=0),
                visible=bool(self._connecting),
            )
            self._connect_btn = primary_btn(
                strings.t("main_connect_btn"), icon=ft.Icons.POWER, expand=True, wrap=True,
                disabled=self._connecting, on_click=lambda e: self._connect())
            return card(ft.Column([
                ft.Text(strings.t("main_step1"), size=11, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK),
                ft.Container(height=13),
                ft.Row([ft.Container(width=8, height=8, bgcolor=T.RED, border_radius=10),
                        ft.Text(strings.t("main_not_connected_yet"), size=12, color=T.RED, weight=ft.FontWeight.BOLD)], spacing=6),
                ft.Container(height=14),
                ft.Text(strings.t("main_setup_desc"),
                        size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500),
                ft.Container(height=14),
                *[ft.Container(ft.Row([
                    ft.Container(ft.Text(str(i+1), size=11, color=T.INK_3, weight=ft.FontWeight.BOLD),
                                 width=20, height=20, bgcolor=T.CARD_2, border_radius=6,
                                 border=ft.Border.all(1, T.BORDER), alignment=ft.Alignment.CENTER),
                    ft.Text(t, size=12, color=T.INK_2, weight=ft.FontWeight.W_500)], spacing=8),
                    padding=ft.Padding.only(bottom=8))
                  for i, t in enumerate([strings.t("au_step_validates_pat"),
                                         strings.t("au_step_loads_projects"),
                                         strings.t("au_step_fetches_plans")])],
                ft.Container(expand=True),
                self._connect_status_row,
                self._connect_btn,
                ft.Container(
                    ft.Row([
                        ft.Icon(ft.Icons.LOCK_OUTLINE, size=12, color=T.INK_3),
                        ft.Text(strings.t("main_unlock_hint"), size=11,
                                color=T.INK_3, weight=ft.FontWeight.BOLD,
                                text_align=ft.TextAlign.CENTER, max_lines=2, expand=True),
                    ], spacing=5, tight=False,
                       alignment=ft.MainAxisAlignment.CENTER),
                    padding=ft.Padding.only(top=9), alignment=ft.Alignment.CENTER),
            ], spacing=0, expand=True), expand=True)

    # ═══════════════════════════════════════════════════════════════════════════
    #  CONNECT + DATA LOADING
    # ═══════════════════════════════════════════════════════════════════════════
    def _field_or_saved(self, field_attr, saved_value):
        """Read a credential field; if it is read-only/empty, fall back to the saved value."""
        f = getattr(self, field_attr, None)
        if f is not None:
            v = (f.value or "").strip()
            if v:
                return v
        return (saved_value or "").strip()

    def _connect(self, e=None):
        # Gather credentials — prefer typed value, fall back to saved store
        name = self._provider_choice
        key = self._field_or_saved("api_key_field", self._saved_key(name))
        if not key:
            self._err(strings.t("main_api_key_required_provider")); return
        self.creds["keys"][self._cred_slot(name)] = key
        pat = self._field_or_saved("pat_field", self.creds.get("pat", ""))
        if not pat:
            self._err(strings.t("main_pat_required")); return
        self.creds["pat"] = pat
        gmail = self._field_or_saved("gmail_field", self.creds.get("gmail", ""))
        self.creds["gmail"] = gmail
        # No fallback to E.AZURE_ORG here — that global can be holding whichever
        # account last connected in this app session (or the module's hardcoded
        # dev-default org), so falling back to it let Connect silently push a
        # DIFFERENT account's organization into this one's own saved creds. If
        # neither the field nor this account's own creds has an org, that's a
        # real missing-required-field case now, same as the PAT/API key above.
        # Azure org is required only when Azure is the ACTIVE backend. On
        # Jira+Zephyr there is no org concept at all, and demanding one would
        # make that backend impossible to connect. backend_setup validates the
        # Jira/Zephyr credential set instead — including the SSRF check on the
        # user-supplied site URL, which must happen BEFORE any request carries
        # the API token to that host.
        if backend_setup.is_azure(self.creds):
            org = self._field_or_saved("org_field", self.creds.get("org", ""))
            if not org:
                self._err(strings.t("main_org_required")); return
            self.creds["org"] = org
        else:
            org = self.creds.get("org", "")
            _ok, _err_msg = backend_setup.validate_for_connect(self)
            if not _ok:
                self._err(_err_msg); return
        sender = self._field_or_saved("sender_field", self.creds.get("gmail_sender", "")) or E.GMAIL_SENDER
        self.creds["gmail_sender"] = sender
        store.save(self.creds)

        E.set_credentials(provider=name, api_key=key, pat=pat, gmail=gmail,
                          org=org, gmail_sender=sender,
                          model=(self._saved_model(name) or None))
        self._err("")
        # Bump the connect-generation token: this attempt supersedes any earlier
        # one, and switching the provider (or starting another connect) bumps it
        # again so this worker's slow/stale results get ignored.
        self._connect_gen = getattr(self, "_connect_gen", 0) + 1
        my_gen = self._connect_gen
        self._connecting = True
        self._connect_status = strings.t("main_validating_pat")
        # The right panel's spinner/button are mounted before the click.  Patch
        # those controls in place instead of rebuilding Setup and snapping both
        # Setup and the navigation rail back to their starts.
        self._set_connect_loading(True)

        def _friendly(msg):
            m = (msg or "").lower()
            if "401" in m or "unauthor" in m or "expecting value" in m or "char 4" in m:
                return "PAT rejected — check the token is correct, complete, and not expired."
            if "403" in m or "denied" in m or "permission" in m:
                return "Access denied — the PAT lacks the required scopes (Test Management, Work Items)."
            if "404" in m:
                return "Organisation/project not found — check the org name."
            if "timed out" in m or "timeout" in m or "10060" in m:
                return "Connection timed out — your network may be blocking dev.azure.com."
            if "ssl" in m or "certificate" in m or "cannot reach" in m or "unreachable" in m:
                return "Cannot reach Azure DevOps — check your network/firewall."
            return f"Connection failed: {(msg or '')[:90]}"

        def work():
            def alive():
                # False once a newer connect started or the provider was switched.
                return my_gen == self._connect_gen
            try:
                # 1) Validate the AI provider key first (cheap ping)
                if not alive():
                    return
                self._set_connect_status(strings.t("main_checking_key"))
                kok, kmsg = E.validate_api_key()
                if not alive():
                    return            # provider switched / re-connected mid-ping
                if not kok:
                    prov = E.T_disp(E.AI_PROVIDER)
                    if kmsg == "auth":
                        self._err(strings.t("main_apikey_rejected", prov=prov))
                    elif kmsg == "network":
                        # Ollama is LOCAL (localhost:11434), so "check your
                        # network/firewall" sends the user to the wrong place —
                        # the fix is starting the local server. Reported live.
                        if name == "ollama":
                            _base = ((E.AI_CONFIG.get("ollama") or {}).get("base_url")
                                     or "localhost:11434")
                            self._err(strings.t("main_ollama_unreachable", base=_base))
                        else:
                            self._err(strings.t("main_provider_unreachable", prov=prov))
                    elif kmsg == "timeout":
                        self._err(strings.t("main_provider_timeout", prov=prov))
                    elif kmsg in ("server", "overloaded"):
                        self._err(strings.t("main_provider_unavailable", prov=prov))
                    elif kmsg == "content_filter":
                        self._err(strings.t("main_provider_blocked", prov=prov))
                    elif kmsg.startswith("missing-package:"):
                        pkg = kmsg.split(":", 1)[1]
                        self._err(strings.t("main_pkg_not_installed", prov=prov, pkg=pkg))
                    elif kmsg.startswith("error:"):
                        # already a friendly classified message (e.g. bad model)
                        self._err(kmsg.split(":", 1)[1].strip())
                    else:
                        self._err(strings.t("main_key_check_failed", prov=prov, err=kmsg))
                    return
                # key is VALID but soft-limited — connect, yet warn so the green
                # status isn't misleading (generation would otherwise fail later).
                if kmsg in ("credit", "ratelimited"):
                    prov = E.T_disp(E.AI_PROVIDER)
                    warn = (f"{prov} key is valid, but the account is out of credit/quota "
                            f"— AI generation will fail until you top up or switch provider."
                            if kmsg == "credit" else
                            f"{prov} key is valid, but it's rate-limited right now — "
                            f"generation may pause and retry.")
                    self.ui_safe(lambda: self._toast(warn))
                # 2) Validate the tracker connection & load projects.
                # The Azure branch is unchanged. Non-Azure backends go through
                # backend_setup, which validates BOTH credentials (a Jira token
                # that works while the Zephyr one doesn't would otherwise pass
                # Setup and fail later mid-generation, reading as a broken run
                # rather than a bad token) and returns the same list-of-strings
                # shape `self._projects` has always held.
                if not alive():
                    return
                if backend_setup.is_azure(self.creds):
                    self._set_connect_status(strings.t("main_validating_pat"))
                    ok, msg = E.validate_pat(pat)
                    if not alive():
                        return
                    if not ok:
                        self._err(_friendly(msg))
                        return
                    self._projects = E.fetch_projects(pat)
                else:
                    label = backend_setup.label_for(backend_setup.active(self.creds))
                    self._set_connect_status(f"Validating {label} & loading projects…")
                    try:
                        self._projects = backend_setup.connect_and_list_projects(self)
                    except Exception as ex:
                        if alive():
                            self._err(str(ex)[:160])
                        return
                    if not alive():
                        return
                self.connected = True
                self._run_finished = False
                self.last_report = None
                self.nav_state = {"setup": "active"}
                if self._projects:
                    self.project = self._projects[0]
                    self._load_plans()
            except Exception as ex:
                if alive():
                    self._err(_friendly(str(ex)))
            finally:
                # Clear the loading state only if we're still the current attempt;
                # otherwise a superseded worker would wipe the newer one's spinner.
                if alive():
                    self._connecting = False
                    self._connect_status = ""
                    self._safe_render(preserve_rail=True, preserve_setup_scroll=True)
        self._bg(work)

    def _bg(self, fn):
        """Run fn in a background thread using Flet's loop-aware runner when available.
        This fixes the 0.85 bug where thread updates don't repaint until refocus.

        fn is wrapped so an exception in the background work is LOGGED rather
        than vanishing — nearly every save/fetch/export/generation runs through
        here, and a bare unhandled error in one was previously invisible (on
        mobile there's no console to catch it). Still fail-soft: the thread
        dies quietly as before, but now with a diagnostics.log breadcrumb."""
        def _guarded():
            try:
                fn()
            except Exception as ex:
                try:
                    import diag_log
                    diag_log.log("bg_task", ex)
                except Exception:
                    pass
        runner = getattr(self.page, "run_thread", None)
        if callable(runner):
            runner(_guarded)
        else:
            threading.Thread(target=_guarded, daemon=True).start()

    def _track_scroll(self, e):
        try:
            self._scroll_offset = e.pixels
        except Exception:
            pass

    def _flush_deferred_render(self):
        """Render if a repaint was deferred while a dropdown was open (so loaded
        stories/counts appear once the picker closes — without rebuilding the open
        panel mid-select and snapping its list to the top)."""
        if getattr(self, "_deferred_render", False):
            self._deferred_render = False
            self.ui_safe(self.render)
            return True
        return False

    def _rail_nav_column(self, nav_items):
        # Scrollable nav list; the same instance is stored each render so
        # _restore_scroll can reapply the remembered offset (no jump on select).
        self._rail_scroll = ft.Column(
            nav_items, spacing=2, expand=True, scroll=ft.ScrollMode.AUTO,
            key="rail_scroll", on_scroll=self._track_rail_scroll)
        return self._rail_scroll

    def _track_rail_scroll(self, e):
        try:
            self._rail_scroll_offset = e.pixels
        except Exception:
            pass

    def _find_scroller(self, ctrl, depth=0):
        """Depth-first search for the FIRST scrollable Column inside a control tree.
        Lets the shell track the real scroller even when it's nested under a Row /
        Container, so scroll (incl. an open multiselect panel) survives a re-render."""
        if ctrl is None or depth > 7:
            return None
        try:
            if isinstance(ctrl, ft.Column) and getattr(ctrl, "scroll", None) is not None:
                return ctrl
        except Exception:
            return None
        for attr in ("content", "controls"):
            child = getattr(ctrl, attr, None)
            if child is None:
                continue
            items = child if isinstance(child, (list, tuple)) else [child]
            for it in items:
                found = self._find_scroller(it, depth + 1)
                if found is not None:
                    return found
        return None

    def _replace_control_ref(self, root, target, replacement, depth=0):
        """Replace one control reference in a newly-built screen tree.

        Flet controls are stateful on the client.  Retaining the already-mounted
        scrollable Column is the reliable way to keep its offset through a data
        refresh; this helper swaps that one new reference for the mounted one
        before the new shell content is installed.
        """
        if root is target:
            return replacement, True
        if root is None or depth > 10:
            return root, False
        for attr in ("content", "controls"):
            try:
                child = getattr(root, attr, None)
            except Exception:
                continue
            if isinstance(child, list):
                for i, item in enumerate(child):
                    if item is target:
                        child[i] = replacement
                        return root, True
                    _, changed = self._replace_control_ref(item, target, replacement, depth + 1)
                    if changed:
                        return root, True
            elif child is target:
                try:
                    setattr(root, attr, replacement)
                    return root, True
                except Exception:
                    continue
            elif child is not None:
                _, changed = self._replace_control_ref(child, target, replacement, depth + 1)
                if changed:
                    return root, True
        return root, False

    def _reuse_primary_scroller(self, body, fresh):
        """Keep a same-screen desktop scroller mounted while refreshing its rows.

        Returns (body, scroller, reused).  It intentionally falls back silently:
        a non-standard screen layout should still render normally rather than
        risking a stale control tree.
        """
        mounted = getattr(self, "_left_scroll", None)
        if mounted is None:
            return body, fresh, False
        if mounted is fresh:
            return body, fresh, True
        try:
            mounted.controls = list(getattr(fresh, "controls", []) or [])
            for name in ("spacing", "run_spacing", "horizontal_alignment",
                         "alignment", "tight", "expand"):
                try:
                    setattr(mounted, name, getattr(fresh, name))
                except Exception:
                    pass
            body, replaced = self._replace_control_ref(body, fresh, mounted)
            if not replaced:
                return body, fresh, False
            return body, mounted, True
        except Exception:
            return body, fresh, False

    def _restore_scroll(self):
        # Restore scroll after a full render so opening a dropdown / ticking a
        # checkbox / pressing Generate doesn't snap to the top.
        #
        # Why deferred: render() rebuilds the whole page, so the scroller is a
        # brand-new ft.Column. scroll_to on a control that hasn't been laid out
        # yet clamps to 0. We retry across increasing delays until the layout
        # settles. The closure re-reads _left_scroll each time so it always acts
        # on the freshest reference (in case another render fires mid-flight).
        off = getattr(self, "_scroll_offset", 0) or 0
        rail_off = getattr(self, "_rail_scroll_offset", 0) or 0
        if not off and not rail_off:
            return

        def _do():
            if off:
                col = getattr(self, "_left_scroll", None)
                if col is not None:
                    try:
                        col.scroll_to(offset=off, duration=0)
                    except Exception:
                        pass
            if rail_off:
                rc = getattr(self, "_rail_scroll", None)
                if rc is not None:
                    try:
                        rc.scroll_to(offset=rail_off, duration=0)
                    except Exception:
                        pass
            try:
                self.page.update()
            except Exception:
                pass

        def _run():
            # Serialize on the session loop (see ui_safe): this fires a whole-
            # page update right after every render — off-loop it raced the
            # user's first clicks (collapse/page-flip) and could desync the
            # client tree, making those clicks paint nothing.
            self.ui_safe(_do)

        # Fire immediately, then one late shot to catch slow layouts. (Was 5
        # shots firing a full page.update() each — that thrashed the event loop
        # right after every render, including the post-Generate render, which
        # made the first interactions feel sluggish. Two shots restore the
        # scroll just as reliably with a fraction of the update traffic.)
        _run()
        try:
            threading.Timer(0.35, _run).start()
        except Exception:
            pass

    def _scroll_to_key(self, key, delays=(0, 0.35)):
        """Restore scroll by KEY instead of raw pixel offset.

        NOTE: no active caller right now (its previous caller — the
        Generate-button anchor in regression.py — was removed in favor of
        never shrinking the page on click; see that file's comment).

        Calls scroll_to(key=...) bare/unawaited, same as every other
        scroll_to() call site in this file — deliberately NOT "fixed" to
        actually execute. A session this same day tried making scroll_to()
        actually run (via page.run_task): it fixed nothing (Flet 0.85.3's
        real param is scroll_key, not key, so this call raises TypeError
        either way) and separately caused real visual corruption — black
        screen flashes and a chrome-less native placeholder — on ANY screen
        with back-to-back renders (confirmed live: AI Usage's load-then-
        loaded pair, disconnect+navigate), because a scheduled scroll_to RPC
        could race a newer render's page.controls.clear()/add() and target
        an already-replaced control. Reverted app-wide. Leave this bare."""
        def _do():
            col = getattr(self, "_left_scroll", None)
            if col is None:
                regression._perf_log(f"_scroll_to_key({key}): no _left_scroll set")
                return
            try:
                col.scroll_to(key=key, duration=0)
                regression._perf_log(f"_scroll_to_key({key}): scroll_to() called, "
                                     f"col={type(col).__name__} id={id(col)}")
            except Exception as ex:
                regression._perf_log(f"_scroll_to_key({key}): scroll_to() RAISED: {ex}")
                return
            try:
                self.page.update()
            except Exception:
                pass

        def _run():
            # Serialized on the session loop — see ui_safe's docstring.
            self.ui_safe(_do)

        for d in delays:
            if d <= 0:
                _run()
            else:
                try:
                    threading.Timer(d, _run).start()
                except Exception:
                    _run()

    def _close_dropdowns(self, e=None):
        # Click-away: close every open dropdown in-place via the registry.
        # Each _checkbox_multiselect that is currently mounted registers a
        # _close() callable here; we call them all and page.update() once.
        closers = list(getattr(self, "_dd_closers", []))
        changed = False
        for fn in closers:
            try:
                if fn():          # fn() returns True when it actually closed something
                    changed = True
            except Exception:
                pass
        # also clear the open flags so _close_dropdowns stays in sync
        for attr in ("_setup_story_open", "_reg_plan_open",
                     "_reg_story_open", "_cp_sprint_open"):
            if getattr(self, attr, False):
                setattr(self, attr, False)
                changed = True
        if changed:
            try:
                self.page.update()
            except Exception:
                pass
        # Panels are closed now → flush any repaint deferred while one was open
        # (so freshly-loaded sprint/plan stories + counts appear). Safe: with the
        # panels closed the render can't jump an open list.
        if changed:
            self._flush_deferred_render()

    def _set_connect_status(self, msg):
        """Update the Connect flow's one-line status ("Checking AI provider
        key…", "Validating PAT…") WITHOUT a full self.render() — see the
        comment on self._connect_status_text in _setup_right(). A full
        render() tears down and rebuilds the whole Setup screen just to swap
        one line of text, which is disproportionate for something this
        session's Task Manager work already established a lighter pattern
        for (targeted control updates instead of full re-renders). Falls
        back to _safe_render() only if the control ref is somehow missing —
        shouldn't happen in practice, since render() always runs once with
        _connecting=True (which is what creates this ref) before _connect()'s
        background worker ever calls this."""
        self._connect_status = msg
        txt = getattr(self, "_connect_status_text", None)
        if txt is None:
            self._safe_render(preserve_rail=True, preserve_setup_scroll=True)
            return
        def _upd():
            try:
                txt.value = msg
                txt.update()
            except Exception:
                self._safe_render(preserve_rail=True, preserve_setup_scroll=True)
        self.ui_safe(_upd)

    def _set_connect_loading(self, loading):
        """Toggle Setup's Connect progress affordance without re-rendering it."""
        def _upd():
            status = getattr(self, "_connect_status_row", None)
            button = getattr(self, "_connect_btn", None)
            text = getattr(self, "_connect_status_text", None)
            if status is None or button is None or text is None:
                self._safe_render(preserve_rail=True, preserve_setup_scroll=True)
                return
            try:
                status.visible = bool(loading)
                text.value = self._connect_status or ""
                button.disabled = bool(loading)
                button.opacity = 0.60 if loading else 1.0
                status.update()
                button.update()
            except Exception:
                self._safe_render(preserve_rail=True, preserve_setup_scroll=True)
        self.ui_safe(_upd)

    def _safe_render(self, preserve_rail=False, preserve_setup_scroll=False):
        """Render from a background worker, serialized on Flet's event loop.

        Used to render inline on the CALLING thread and then push an extra
        page.update() through run_thread to work around the focus-repaint
        bug. That inline render was itself an unserialized patch source
        (see ui_safe's docstring — concurrent diffs desync the client
        tree), so route the whole render through ui_safe instead; running
        on the session loop also makes the extra repaint kick unnecessary."""
        self.ui_safe(lambda: self.render(
            preserve_rail=preserve_rail,
            preserve_setup_scroll=preserve_setup_scroll))

    def _load_plans(self):
        if not self.project:
            self._plans = []; return
        try:
            # Azure still calls engine directly inside fetch_plans, so its
            # behaviour is unchanged; only a non-Azure backend routes through
            # the tracker adapter, which flattens its Plan DTOs into the same
            # [{"id","name"}] shape this dropdown and regression.py consume.
            self._plans = backend_setup.fetch_plans(self, self.project)
        except Exception as ex:
            self._plans = []
            self._err(strings.t("main_could_not_load_plans", err=ex))

    def _busy(self, msg):
        # Show a lightweight loading bar in the snackbar area (page.splash was
        # removed/changed in newer Flet, so we avoid depending on it).
        try:
            self.page.splash = ft.ProgressBar(color=T.VIOLET)
        except Exception:
            pass
        try:
            self.page.snack_bar = ft.SnackBar(ft.Text(msg), bgcolor=T.INK, duration=8000)
            self.page.snack_bar.open = True
        except Exception:
            pass
        self.page.update()

    def _unbusy(self):
        try:
            self.page.splash = None
        except Exception:
            pass
        try:
            if self.page.snack_bar:
                self.page.snack_bar.open = False
        except Exception:
            pass
        self.page.update()

    # ═══════════════════════════════════════════════════════════════════════════
    #  CREATE TEST PLAN MODAL
    # ═══════════════════════════════════════════════════════════════════════════
    def _open_create_plan(self):
        return modals.open_create_plan(self)

    def _open_sprint_summary(self):
        return modals.open_sprint_summary(self)

    def ui_safe(self, fn):
        """Run a UI mutation on a background thread (page.run_thread), not
        on Flet's own asyncio event loop.

        This went through two iterations. Originally: page.run_thread(),
        which runs each callback on its own executor thread — concurrently
        and in NO guaranteed order. Flet 0.85's patch engine
        (Session.patch_control → ObjectPatch.from_diff) has no locking, so
        two overlapping full-page renders could interleave their diffs and
        leave the client's widget tree corrupted — after which every SMALL
        in-place update (collapse/page-flip's _refresh_table, opening the
        email recipient picker, …) would patch against ids/paths the client
        no longer had and silently paint nothing.

        That was then "fixed" by routing everything through page.run_task()
        instead — a coroutine on Flet's own asyncio loop — which does
        serialize things, but at a real cost: fn() here is often
        self.render(), and Regression Plan's render can take 0.5–3s+ of pure
        synchronous Python (building a 400+-story table). Run THAT on the
        event loop itself and it blocks the loop entirely for that whole
        stretch — Flet can't flush ANY pending message during that window,
        including an already-queued "loading" overlay update, which is why
        the overlay stopped visibly appearing at all once this ran.

        The actual fix for the original corruption bug is the App-level
        `self._render_lock` now held around ONLY the real page-mutation step
        in _render_body() (controls.clear/add/update) — see its comment
        there. That serializes the part that actually needs it without
        forcing heavy control-tree construction onto the event loop, so
        ui_safe goes back to a real background thread here."""
        try:
            ru = getattr(self.page, "run_thread", None)
            if callable(ru):
                ru(fn); return
        except Exception:
            pass
        try:
            fn()
        except Exception as ex:
            # LOG the swallowed UI exception instead of a bare pass. ui_safe
            # wraps almost every UI mutation and render, so a bare swallow here
            # made a whole class of bugs invisible — e.g. an AttributeError
            # while BUILDING a dialog (the ft.alignment.center typo) silently
            # produced "the popup just doesn't appear", diagnosable only by
            # guesswork. Still fail-soft (a broken render must never crash the
            # app), but now every occurrence lands in diagnostics.log.
            try:
                import diag_log
                diag_log.log("ui_safe", ex)
            except Exception:
                pass

    def _open_help_guide(self, initial=None):
        """Open the searchable feature guide (Help & guide)."""
        try:
            import help_guide
            help_guide.show(self, initial)
        except Exception as e:
            self._toast(strings.t("main_could_not_open_guide", err=str(e)[:80]))

    def _check_updates_chip(self):
        """The 'Check updates' pill under the QA Studio logo — accent-tinted and
        visible, with a hover lift (brighter fill + border + a slight scale).
        Hidden entirely where self-update isn't a thing (mobile builds — store
        distribution replaces it; see platform_caps.has_self_update)."""
        if not platform_caps.has_self_update():
            return ft.Container()
        chip = ft.Container(
            ft.Row([
                ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, size=12, color=T.RAIL_INK),
                ft.Text(strings.t("check_updates"), size=10, weight=ft.FontWeight.BOLD, color=T.RAIL_INK,
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
            ], spacing=4, tight=False),
            expand=True,
            on_click=lambda e: self._manual_update_check(),
            tooltip=strings.t("check_updates_tip"),
            padding=ft.Padding.symmetric(vertical=3, horizontal=8),
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.10, T.VIOLET),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.28, T.VIOLET)),
            ink=True, scale=1.0, animate_scale=140, animate=140)

        def _hover(e, _c=chip):
            try:
                on = e.data in (True, "true", "True")
                _c.bgcolor = ft.Colors.with_opacity(0.22 if on else 0.10, T.VIOLET)
                _c.border = ft.Border.all(1, ft.Colors.with_opacity(0.5 if on else 0.28, T.VIOLET))
                _c.scale = 1.04 if on else 1.0
                _c.update()
            except Exception:
                pass
        chip.on_hover = _hover
        return chip

    def _rail_btn_hover(self, rest_bg):
        """Hover for the pinned rail buttons (Help / Settings / theme) — matches the
        nav-item hover: a violet tint + a tiny slide-right."""
        def _h(e, base=rest_bg):
            try:
                on = e.data in (True, "true", "True")
                e.control.bgcolor = ft.Colors.with_opacity(0.14, T.VIOLET) if on else base
                e.control.offset = ft.Offset(0.02, 0) if on else ft.Offset(0, 0)
                e.control.update()
            except Exception:
                pass
        return _h

    def _show_dialog(self, dlg):
        return dialogs.show_dialog(self, dlg)

    def _close_dialog(self):
        return dialogs.close_dialog(self)

    def _close_all_dialogs(self):
        """Close EVERY stacked dialog — used on session teardown, where leaving
        a lower one open leaves it floating over the login screen."""
        return dialogs.close_all_dialogs(self)

    def _confirm(self, title, message, on_yes, yes_label=strings.t("main_remove"), danger=True,
                 icon=ft.Icons.HELP_OUTLINE):
        return dialogs.confirm(self, title, message, on_yes, yes_label, danger, icon)


    # ═══════════════════════════════════════════════════════════════════════════
    #  EXISTING STEPS MODAL
    # ═══════════════════════════════════════════════════════════════════════════
    def _open_existing_steps_modal(self, have, total, on_choice):
        return modals.open_existing_steps_modal(self, have, total, on_choice)

    def _run_start_btn(self, run_busy):
        """The Setup screen's "Start run" CTA, swapped to a disabled "Run in
        progress…" state while self._run_active/_auto_running is true — see
        the call site's comment for why this matters (two concurrent runs
        would fight over the same activity-log/stats state and Azure calls)."""
        btn = primary_btn(
            strings.t("main_run_in_progress_cta") if run_busy else strings.t("main_start_run"),
            icon=ft.Icons.HOURGLASS_TOP if run_busy else ft.Icons.PLAY_ARROW,
            expand=True, disabled=run_busy,
            on_click=lambda e: self._start_run())
        try:
            btn.tooltip = ("A run is already in progress — wait for it to "
                          "finish or stop it first." if run_busy else None)
        except Exception:
            pass
        # 'Run remotely' toggle (REMOTE_RUNS.md): same Start button, but the
        # run enqueues for the GitHub Actions worker (executing with THIS
        # user's synced vault credentials) instead of running locally.
        def _flip(e):
            self.run_remote = bool(e.control.value)
        sw = ft.Switch(value=bool(getattr(self, "run_remote", False)),
                       active_color=T.VIOLET, scale=0.8,
                       disabled=run_busy, on_change=_flip)
        remote_row = ft.Container(
            ft.Row([sw, ft.Column([
                ft.Text(strings.t("main_run_remotely"), size=12.5, weight=ft.FontWeight.BOLD,
                        color=T.INK),
                ft.Text(strings.t("main_run_remotely_desc"), size=11, color=T.INK_3),
            ], spacing=1, expand=True)],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.only(bottom=10))
        return ft.Column([remote_row, btn], spacing=0)

    def _start_run(self):
        # Belt-and-suspenders: the button above is already disabled while a
        # run is active, but _start_run can in principle still be invoked
        # directly (e.g. a stray double-click event queued before the button
        # re-rendered disabled) — refuse here too rather than trusting the
        # UI alone to prevent a second run from starting.
        if bool(getattr(self, "_run_active", False) or getattr(self, "_auto_running", False)
               or getattr(self, "_remote_run_active", False)):
            self._err(strings.t("main_run_in_progress"))
            return
        # DOUBLE-START GUARD. The checks above only cover a run that has already
        # LAUNCHED — they say nothing about the window between clicking Start and
        # the run beginning. On the Steps tool that window includes a background
        # precheck AND (when cases already have steps) the Skip-vs-Evaluate
        # dialog waiting on the user. Clicking Start again in there ran a second
        # precheck and opened a SECOND dialog on top of the first — reported live
        # as "I can open the evaluate popup twice by keep clicking start run"
        # (Flet stacks dialogs, so the duplicate really is a second layer).
        # Two conditions, because they cover different halves of that window:
        #   _start_pending  → a precheck is in flight
        #   _dialog_depth>0 → a dialog (e.g. that very prompt) is already open
        if getattr(self, "_start_pending", False):
            return
        if int(getattr(self, "_dialog_depth", 0) or 0) > 0:
            return
        # RBAC: Viewers (or any role without RUN) can't start a run.
        if not self.can(auth.CAP_RUN):
            self._err(strings.t("main_no_run_perm"))
            return
        # Commit any ID still sitting in the input box
        inp = getattr(self, "_story_input", None)
        if inp is not None and (inp.value or "").strip():
            for x in (inp.value or "").strip().strip("()[],").replace(",", " ").split():
                if x.isdigit() and int(x) not in self.story_ids:
                    self.story_ids.append(int(x))
            inp.value = ""
        # Mark which required fields are missing → red border + inline helper
        # (visual), AND keep the toast (per request).
        self._invalid = set()
        if not self.project:
            self._invalid.add("project")
        if not self.plan_id:
            self._invalid.add("plan")
        if not self.story_ids:
            self._invalid.add("stories")
        if self._invalid:
            self._err(strings.t("main_select_project_first") if "project" in self._invalid
                      else strings.t("main_select_or_create_plan") if "plan" in self._invalid
                      else strings.t("main_add_story_id"))
            self.render()   # repaint so the invalid fields turn red with a helper
            return
        self._invalid = set()
        self._err_msg = ""  # all good — clear any prior validation error
        # 'Run remotely': enqueue for the GitHub Actions worker instead of
        # executing locally — the validations above apply to both paths.
        if bool(getattr(self, "run_remote", False)):
            self._start_remote_run()
            return
        # MOBILE: there is no local run path. A local run needs the Azure SDK
        # writing under the app's home dir and a long-lived foreground process —
        # neither of which a phone provides (MOBILE_PLAN Phase 3: mobile is a
        # viewer/controller, execution happens on the remote worker). Starting
        # one anyway got partway, failed on the SDK's own cache dir, and then
        # opened the local Report screen — which is desktop-only and rendered
        # empty (reported live). Stop here with an actionable message instead.
        if platform_caps.is_mobile():
            self._err_msg = ""
            self._toast(strings.t("main_mobile_remote_only"))
            self.render()
            return
        # Steps tool: check existing steps first
        if self.tool == "steps":
            self._start_pending = True      # see the double-start guard above
            self._busy(strings.t("main_checking_steps"))
            def precheck():
                # First: confirm every story actually belongs to the selected plan
                # (Azure only — routed through the seam; non-Azure backends don't
                # tie stories to a plan, so this is a no-op there instead of a 404).
                try:
                    found, missing = backend_setup.validate_stories_in_plan(
                        self, self.plan_id, self.story_ids)
                except Exception as ex:
                    self._unbusy()
                    self._err(strings.t("main_could_not_verify_stories", err=str(ex)[:90]))
                    return
                if missing:
                    self._unbusy()
                    ids = ", ".join(str(m) for m in missing)
                    if not found:
                        self._err(strings.t("main_story_not_in_plan", ids=ids, plan=self.plan_id))
                    else:
                        self._err(strings.t("main_stories_not_in_plan", plan=self.plan_id, ids=ids))
                    return
                # BUG FIX: this used to swallow ANY count_existing_steps failure into
                # a bare `have, total = 0, 0`, which then hit the `total == 0` branch
                # below — the SAME code path used for "no test cases yet, no prompt
                # needed". That made a genuine failure (Azure hiccup, a bug in
                # discover_suites_for_stories, etc.) look IDENTICAL to the normal
                # no-op case: the run just launched straight in "skip" mode with no
                # popup, no toast, nothing — exactly what would be reported as "the
                # evaluation modal didn't pop up", with no clue it was actually an
                # error being eaten silently. Now a real failure here gets its own
                # visible warning before falling back to the same safe "skip" default
                # (still fail-open — a working run beats a hard stop over a
                # best-effort precheck — but the user now knows why they didn't see
                # the Skip/Evaluate choice).
                try:
                    have, total = backend_setup.count_existing_steps(self, self.plan_id, self.story_ids)
                except Exception as ex:
                    # Log it, don't just toast. This failure was diagnosable
                    # only from a screenshot before — it turned out to be the
                    # Azure SDK's own ~/.azure-devops cache dir being
                    # unwritable on Android, which ALSO silently zeroed the
                    # existing-test-case count. A toast disappears; the log
                    # doesn't.
                    try:
                        import diag_log
                        diag_log.log("run.count_existing_steps", ex)
                    except Exception:
                        pass
                    self._unbusy()
                    self._snack(
                        strings.t("main_check_steps_failed", err=str(ex)[:100]), T.AMBER, ft.Icons.WARNING_AMBER_ROUNDED)
                    self.existing_mode = "skip"
                    self._launch_run("skip")
                    return
                self._unbusy()
                if total == 0:
                    # No test cases yet — the Steps run will generate titles first,
                    # create the test cases, then add steps. No Skip/Evaluate needed.
                    self.existing_mode = "skip"
                    self._launch_run("skip")
                    return
                if have > 0:
                    # some cases already have steps → ask Skip vs Evaluate. Wrapped in
                    # ui_safe (this whole precheck runs on a background thread via
                    # self._bg above) to match the same dialog-from-a-worker-thread
                    # pattern used everywhere else in the app (e.g. _ask_reeval).
                    self.ui_safe(lambda: self._open_existing_steps_modal(
                        have, total, on_choice=lambda mode: self._launch_run(mode)))
                else:
                    # cases exist but none have steps → just generate, no prompt
                    self.existing_mode = "skip"
                    self._launch_run("skip")
            def _precheck_guarded():
                # Clear the in-flight flag on EVERY exit path (there are seven,
                # including three early returns and an exception). Once precheck
                # is done the other half of the guard takes over: either the
                # Skip/Evaluate dialog is open (_dialog_depth > 0) or the run has
                # launched (_run_active), so Start stays blocked either way.
                try:
                    precheck()
                finally:
                    self._start_pending = False
            self._bg(_precheck_guarded)
        else:
            # Titles tool: still verify stories belong to the plan first
            self._start_pending = True
            self._busy(strings.t("main_verifying_stories"))
            def precheck_titles():
                try:
                    found, missing = backend_setup.validate_stories_in_plan(
                        self, self.plan_id, self.story_ids)
                except Exception as ex:
                    self._unbusy(); self._err(strings.t("main_could_not_verify_stories", err=str(ex)[:90])); return
                self._unbusy()
                if missing:
                    ids = ", ".join(str(m) for m in missing)
                    self._err(strings.t("main_story_not_in_plan", ids=ids, plan=self.plan_id))
                    return
                self._launch_run(None)
            def _precheck_titles_guarded():
                try:
                    precheck_titles()
                finally:
                    self._start_pending = False
            self._bg(_precheck_titles_guarded)

    def _launch_run(self, existing_mode):
        if existing_mode:
            self.existing_mode = existing_mode
        # apply the chosen output language for this run
        try:
            E.set_output_lang(getattr(self, "run_lang", self.lang))
        except Exception:
            pass
        self.stop_flag = False
        self._stopping = False
        self._run_paused = False   # a new run always starts un-paused
        try: E.clear_stop()
        except Exception: pass
        self.active = "run"
        self.nav_state = {"setup": "done", "run": "active"}
        # reset run state
        self._stats = {"total": 0, "stories_done": 0, "total_stories": 0,
                       "done": 0, "skipped": 0, "errors": 0, "created": 0}
        self._progress = {"pct": 0, "label": "Starting…"}
        self._log_lines = []
        self._rendered_count = 0
        self._run_log_ui_lock = threading.Lock()
        self._current_wip = None
        self._story_prog = {}
        self._emailed_to = None
        self._run_finished = False
        self._run_started = False
        import time as _t
        self._run_start_ts = _t.time()
        self._run_end_ts = None
        self._set_run_active(True)
        self._start_meta_ticker()      # smooth per-second elapsed/ETA
        self.render()

        def cb(ev, payload):
            if ev == "stat":
                self._stats.update(payload)
            elif ev == "progress":
                self._progress.update(payload)
                self._run_started = True
            elif ev == "story_progress":
                self._story_prog[payload["id"]] = payload
                self._refresh_story_cards()
            elif ev == "story":
                self._log_lines.append({"tone": "story", "ico": "▸",
                                        "msg": f"Story {payload['id']} · {payload['title']}",
                                        "ar": True})
            elif ev == "log":
                # If this result replaces a "generating…" spinner line, remove that
                # line — and any lingering "Still generating/describing…" heartbeat
                # line for the same id (hb_id reuses wip_id/tc_id on purpose), so a
                # slow call's last ticking line doesn't outlive the call itself.
                rw = payload.get("replace_wip")
                if rw is not None:
                    self._log_lines = [l for l in self._log_lines
                                       if l.get("wip_id") != rw and l.get("hb_id") != rw]
                    # force a full re-render of the log since we removed a line
                    self._rendered_count = -1
                hb = payload.get("hb_id")
                if hb is not None:
                    # Heartbeat ping for a call still in flight: update the ONE
                    # existing line for this hb_id in place (new elapsed-seconds
                    # text) instead of appending a fresh line every ~15s — that
                    # used to leave a wall of near-identical "Still generating…"
                    # lines behind a single slow call.
                    for l in self._log_lines:
                        if l.get("hb_id") == hb:
                            l.clear(); l.update(payload)
                            self._rendered_count = -1   # in-place edit → full re-render
                            break
                    else:
                        self._log_lines.append(payload)
                    self._refresh_run()
                    return
                self._log_lines.append(payload)
                if payload.get("detail"):
                    self._log_lines.append({"tone": "dim", "indent": True, "msg": payload["detail"]})
            elif ev == "done":
                self.last_report = payload
                # The run has ended on ANY 'done' (success, stop, or credit) — clear
                # the active flag here so the "Preparing stories…" spinner can't
                # linger if the after-run block is skipped.
                self._run_finished = True
                self._run_end_ts = _t.time()
                try:
                    self._set_run_active(False)
                except Exception:
                    self._run_active = False
                reason = payload.get("reason")
                if reason == "credit":
                    self._log_lines.append({"tone": "err", "ico": "✕",
                        "msg": "Out of AI credits — run stopped. Add credits to your provider and retry."})
                elif payload.get("errors", 0) and "failed" in str(payload.get("summary","")).lower():
                    pass  # individual errors already logged
            self._refresh_run()

        def work():
            # ops routes the generators' tracker calls at the active backend.
            # Returns None for Azure, which makes engine use its own defaults —
            # i.e. the Azure path below is byte-for-byte what it always was.
            _ops = backend_setup.generation_ops(self)
            try:
                if self.tool == "steps":
                    E.run_steps(self.project, self.plan_id, self.story_ids, cb,
                                should_stop=lambda: self.stop_flag,
                                existing_mode=self.existing_mode,
                                on_ai_error=self._run_on_ai_error,
                                gate=self._run_gate, ops=_ops)
                else:
                    E.run_titles(self.project, self.plan_id, self.story_ids, cb,
                                 should_stop=lambda: self.stop_flag,
                                 on_ai_error=self._run_on_ai_error,
                                 gate=self._run_gate, ops=_ops)
            except E.StopRequested:
                # Defensive backstop: run_steps/run_titles are supposed to
                # catch StopRequested internally at every AI call site and
                # resolve to a normal cb("done", ...) instead of ever letting
                # it escape — but one call site (evaluate_existing_steps,
                # reached via run_steps' UI-description fetch) was missing
                # that catch, so clicking Stop while it was in flight let
                # StopRequested propagate all the way out here uncaught.
                # Since str(StopRequested()) == "" (no message text), the
                # generic branch below rendered it as a bare "Run failed: "
                # with nothing after the colon — a real bug seen live, now
                # fixed at its actual source too. This branch stays as a
                # safety net in case any OTHER call site has the same gap:
                # an honest "stopped" outcome instead of a fake failure.
                self._log_lines.append({"tone": "dim", "ico": "⏹",
                    "msg": "Stopped."})
                self.last_report = {"summary": "Stopped", "reason": "stopped", "errors": 0}
                self._refresh_run()
            except Exception as ex:
                emsg = str(ex)
                if "credit" in emsg.lower() or "balance" in emsg.lower():
                    self._log_lines.append({"tone":"err","ico":"✕",
                        "msg":"Out of AI credits — add credits and retry."})
                    self.last_report = {"summary":"Stopped — out of AI credits","reason":"credit","errors":0}
                elif "401" in emsg or "403" in emsg:
                    self._log_lines.append({"tone":"err","ico":"✕",
                        "msg":"Azure auth failed — your PAT may have expired."})
                    self.last_report = {"summary":"Run failed — Azure auth","errors":1}
                else:
                    self._log_lines.append({"tone":"err","ico":"✕","msg":f"Run failed: {emsg[:120]}"})
                    self.last_report = {"summary": f"Run failed: {emsg[:80]}", "errors": 1}
                self._refresh_run()
            # Send email report if configured
            rpt = self.last_report or {}
            if not self.emails.strip():
                self._log_lines.append({"tone": "dim",
                    "msg": "No report email sent — Report Emails field is empty."})
                self._refresh_run()
            elif not E.ensure_sender_creds():
                self._log_lines.append({"tone": "warn",
                    "msg": "No email sent — no Gmail App Password is configured. "
                           "An admin can set it in Setup → Connection (it's shared "
                           "with the whole org)."})
                self._refresh_run()
            if self.emails.strip() and rpt:
                tool_name = "Test Case Steps" if self.tool == "steps" else "Test Case Titles"
                _secs = rpt.get("total_secs")
                if self.tool == "steps":
                    stats = {
                        "Created": rpt.get("created", 0),
                        "Updated": rpt.get("updated", 0),
                        "Skipped": rpt.get("skipped", 0),
                        "Failed":  rpt.get("errors", 0),
                        "Stories": f"{rpt.get('stories_done',0)}/{rpt.get('total_stories',0)}",
                    }
                else:
                    stats = {
                        "Created": rpt.get("created", 0),
                        "Skipped": rpt.get("skipped", 0),
                        "Failed":  rpt.get("errors", 0),
                        "Stories": f"{rpt.get('stories_done',0)}/{rpt.get('total_stories',0)}",
                    }
                if _secs not in (None, "", 0):
                    stats["Time"] = E._fmt_secs(_secs)
                # Test Plan deep link (if we have project + plan)
                plan_url = None
                if self.project and self.plan_id:
                    plan_url = backend_setup.plan_url(self, self.plan_id)
                to = [e.strip() for e in self.emails.split(",") if e.strip()]
                # Build a STRUCTURED log for the email so it renders like the
                # in-app Run activity log (icon · id · title · detail), not raw text.
                email_log = []
                for ln in getattr(self, "_log_lines", []):
                    msg = ln.get("msg", "")
                    if not msg:
                        continue
                    email_log.append({
                        "msg": msg,
                        "id": ln.get("id", ""),
                        "ico": ln.get("ico", ""),
                        "detail": ln.get("detail", ""),
                        "tone": ln.get("tone", "dim"),
                        "indent": bool(ln.get("indent")),
                        "ar": bool(ln.get("ar")),
                    })
                # Backend-aware item links for the email: resolve each item id
                # through the tracker seam (Jira/Xray/Zephyr/TestRail/Azure) so
                # the links match the connected backend instead of always
                # pointing at dev.azure.com.
                _url_map = {}
                try:
                    for _it in (list(rpt.get("action_items", []))
                                + list(rpt.get("skipped_items", []))
                                + list(rpt.get("per_story", []))):
                        _id = _it.get("id") if isinstance(_it, dict) else None
                        if _id and str(_id) not in _url_map:
                            _u = backend_setup.item_url(self, _id) or ""
                            if _u:
                                _url_map[str(_id)] = _u
                except Exception:
                    _url_map = {}
                html = E.build_report_email(tool_name, rpt.get("summary",""), stats,
                                            rpt.get("action_items",[]),
                                            rpt.get("skipped_items",[]),
                                            per_story=rpt.get("per_story", []),
                                            plan_url=plan_url,
                                            total_secs=_secs,
                                            log_lines=email_log,
                                            org=E.AZURE_ORG, project=self.project,
                                            url_map=_url_map)
                ok, err = E.send_report(to, f"QA Studio — {tool_name} report", html)
                if not ok:
                    self._log_lines.append({"tone":"warn","ico":"✉",
                        "msg":f"Report not emailed — {err}"})
                else:
                    self._emailed_to = self.emails
                    self._log_lines.append({"tone":"ok","msg":f"Report emailed to {self.emails}"})
                self._refresh_run()
            # transition to report
            import time as _t
            self._report_time = _t.time()
            self._stopping = False
            self._run_finished = True
            self._set_run_active(False)
            self.nav_state = {"setup": "done", "run": "done", "report": "active"}
            self.active = "report"
            self.render()
        self._bg(work)

    def _stop_run(self):
        self.stop_flag = True
        self._stopping = True
        try: E.request_stop()   # interrupt any in-flight retry backoff
        except Exception: pass
        # update the stop button label in place if present
        try:
            if hasattr(self, "_stop_btn_text"):
                self._stop_btn_text.value = "Stopping…"
                self._stop_btn_text.update()
        except Exception:
            pass
        self._toast(strings.t("main_will_stop"))
        self._log_lines.append({"tone": "warn", "msg": "Stop requested — finishing current test case…"})
        self._refresh_run()

    def _story_card(self, sp):
        """One per-story card: ring + Arabic title + id + status chip + n/total."""
        total = sp.get("total", 0)
        done = sp.get("done", 0)
        ok = sp.get("ok", 0); skipped = sp.get("skipped", 0); err = sp.get("err", 0)
        pct = int(done / total * 100) if total else 0
        finished = getattr(self, "_run_finished", False)
        if err:
            status = badge(f"{err} error" + ("s" if err > 1 else ""), "amber",
                           ft.Icons.WARNING_AMBER_ROUNDED); ring_c = T.AMBER
        elif done >= total and total:
            status = badge("Done", "green", ft.Icons.CHECK); ring_c = T.GREEN
        elif finished:
            # run ended but this story wasn't fully processed
            status = badge("Stopped", "grey"); ring_c = T.INK_3
        elif done > 0 or sp.get("_active"):
            status = badge("Running", "violet"); ring_c = T.VIOLET
        else:
            status = badge("Queued", "grey"); ring_c = T.INK_3
        return ft.Container(
            ft.Row([
                progress_ring(pct, ring_c, size=46, label=pct),
                ft.Column([
                    ft.Text(sp.get("title", ""), size=13, weight=ft.FontWeight.BOLD,
                            color=T.INK, font_family=T.F_AR, text_align=ft.TextAlign.RIGHT,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(f"#{sp.get('id','')}" + (f" · suite {sp.get('suite')}" if sp.get('suite') else ""),
                            size=11, color=T.INK_3, weight=ft.FontWeight.BOLD, font_family=T.F_MONO),
                ], spacing=2, expand=True),
                ft.Column([status,
                           ft.Text(f"{done}/{total}", size=11, color=T.INK_2,
                                   weight=ft.FontWeight.BOLD)],
                          spacing=5, horizontal_alignment=ft.CrossAxisAlignment.END),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=14, bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER),
            border_radius=T.R, expand=True)

    def _build_story_cards(self):
        cards = [self._story_card(sp) for sp in self._story_prog.values()]
        if not cards:
            # Only spin while the run is actually active — once it stops/finishes,
            # don't leave a perpetual "Preparing stories…" spinner.
            if not getattr(self, "_run_active", False):
                return []
            return [ft.Container(
                ft.Row([ft.ProgressRing(width=14, height=14, stroke_width=2, color=T.VIOLET),
                        ft.Text(((self._progress.get("label") or "Preparing stories…")
                                 if getattr(self, "_run_started", False)
                                 else "Preparing stories…"),
                                size=12.5, color=T.INK_3,
                                weight=ft.FontWeight.BOLD)], spacing=10),
                padding=14)]
        # MOBILE_PLAN.md Phase 2 explicitly called this out: "the Run
        # screen's story grid + stats row need to collapse to a vertical
        # list" — the stats row already wraps (see run.py), but this grid
        # never got the same treatment. A card packs a 46px progress ring +
        # an expanding title/id column + a status/count column; halving its
        # width in a 2-column grid on a ~390px phone squeezes all three into
        # the same "1-char-wide" pattern fixed elsewhere this session
        # (Regression/Sprint Plan/AI Usage). One card per row on mobile.
        if platform_caps.is_mobile():
            return [ft.Container(c, padding=ft.Padding.only(bottom=12)) for c in cards]
        # 2-column grid (desktop)
        rows = []
        for i in range(0, len(cards), 2):
            pair = cards[i:i+2]
            if len(pair) == 1:
                pair.append(ft.Container(expand=True))
            rows.append(ft.Row(pair, spacing=12))
        return rows

    def _refresh_story_cards(self):
        def _apply():
            try:
                if hasattr(self, "_story_grid"):
                    self._story_grid.controls = self._build_story_cards()
                    self.page.update()
            except Exception:
                pass
        # Single serialized dispatch (see ui_safe): the old inline call +
        # run_thread duplicate ran the same whole-page update on two threads.
        self.ui_safe(_apply)

    @staticmethod
    def _fmt_dur(secs):
        secs = int(max(0, secs))
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def _tick_run_meta(self):
        """Refresh only the 'THIS RUN' meta label in place (no full render)."""
        try:
            if hasattr(self, "_tr_meta"):
                self._tr_meta.value = self._run_meta_line()
                self._tr_meta.update()
        except Exception:
            pass

    def _start_meta_ticker(self):
        """Tick the elapsed/ETA line every second while a run is active, so the
        clock advances smoothly instead of only jumping on log events."""
        if getattr(self, "_meta_tick_on", False):
            return
        self._meta_tick_on = True

        def _loop():
            try:
                while getattr(self, "_run_active", False):
                    time.sleep(1)
                    self.ui_safe(self._tick_run_meta)
            finally:
                self._meta_tick_on = False
        try:
            threading.Thread(target=_loop, daemon=True).start()
        except Exception:
            self._meta_tick_on = False

    def _run_meta_line(self):
        """One-line 'THIS RUN' meta: elapsed · ETA · story x of y.
        ETA is projected from elapsed time and % complete; it freezes when the
        run finishes (showing the total time taken)."""
        import time as _t
        p = getattr(self, "_progress", {}) or {}
        s = getattr(self, "_stats", {}) or {}
        pct = p.get("pct", 0) or 0
        # Fallback progress for runs that don't stream a live pct (Titles): derive it
        # from stories completed so multi-story runs still get a counting-down ETA.
        # (Steps runs already report a per-test-case pct, so this never overrides them.)
        if pct < 2:
            ts = s.get("total_stories") or 0
            sd = s.get("stories_done") or 0
            if ts > 1 and sd > 0:
                pct = (sd / ts) * 100.0
        start = getattr(self, "_run_start_ts", None)
        finished = getattr(self, "_run_finished", False)
        ended = not getattr(self, "_run_active", False)
        end = getattr(self, "_run_end_ts", None)
        bits = []
        if start:
            elapsed = (end or _t.time()) - start
            bits.append(("Total " if (finished or ended) else "Elapsed ")
                        + self._fmt_dur(elapsed))
            if not (finished or ended):
                if pct >= 2:
                    remaining = elapsed / (pct / 100.0) - elapsed
                    bits.append("ETA ~" + self._fmt_dur(remaining))
                else:
                    bits.append("ETA estimating…")
        if s.get("total_stories"):
            bits.append(f"story {s.get('stories_done', 0)} of {s['total_stories']}")
        return "   ·   ".join(bits)

    def run_screen(self):
        return run.screen(self)

    def _log_icon(self, ln, tone, color):
        ico = ln.get("ico")
        if ln.get("wip") and tone == "info":
            return ft.ProgressRing(width=11, height=11, stroke_width=2, color=T.VIOLET)
        if ico:
            return ft.Text(ico, size=12, color=color, weight=ft.FontWeight.BOLD)
        if tone == "ok":
            return ft.Icon(ft.Icons.CHECK, size=12, color=T.GREEN)
        if tone == "err":
            return ft.Icon(ft.Icons.CLOSE, size=12, color=T.RED)
        if tone == "warn":
            return ft.Text("⏭", size=12, color=T.AMBER, weight=ft.FontWeight.BOLD)
        return None

    def _render_one_log(self, ln):
        # design log colors: ok=green, err=red, warn=amber, dim=ink-3,
        # story=story-violet (bold), info=violet-ink
        tone_color = {"ok": T.GREEN, "err": T.RED, "warn": T.AMBER, "info": T.VIOLET_INK,
                      "skip": T.INK_3, "review": T.AMBER,
                      "dim": T.INK_3, "story": T.STORY}
        tone = ln.get("tone", "dim")
        color = tone_color.get(tone, T.INK_2)
        icon = self._log_icon(ln, tone, color)
        idtxt = (ft.Text(f"[{ln['id']}]", size=11, color=T.INK_3,
                         weight=ft.FontWeight.BOLD, font_family=T.F_MONO)
                 if ln.get("id") else None)
        # "#k/N" run-progress chip (same convention as the Automation log's
        # "Sequencing case k/N"). Its own LTR mono Text control — like the
        # [id] chip — so it can't bidi-scramble against an Arabic title.
        seqtxt = (ft.Text(f"#{ln['seq']}", size=11, color=T.STORY,
                          weight=ft.FontWeight.BOLD, font_family=T.F_MONO)
                  if ln.get("seq") else None)
        # A trailing "\n" was tried here to fix Ctrl+C copy losing line
        # breaks between entries (each line is its own Row, not one shared
        # multi-line Text, so Flutter's SelectionArea doesn't reliably insert
        # a break between separate Row siblings on copy). REVERTED — it
        # visibly added extra vertical gaps between every log line in the
        # actual app (confirmed live via screenshot), contrary to the
        # assumption that Flutter drops a lone trailing newline from layout
        # height. Use the "Copy entire log" toolbar button instead
        # (_copy_log_text, unaffected either way — it joins app._log_lines
        # with "\n" directly, not via SelectionArea's copy behavior) until a
        # copy-friendly fix is found that doesn't touch visible layout.
        _msg = ln.get("msg", "")
        _s = _msg.lstrip()
        # Direction per line: Arabic content (flag or first strong char) → RTL,
        # right-aligned; everything else → LTR. Prefixing a Left-to-Right Mark
        # (U+200E) forces the paragraph base direction to LTR so trailing
        # punctuation / IDs on an English line don't get bidi-flipped to the
        # front when the whole app is in RTL (Arabic UI).
        _ar_line = bool(ln.get("ar")) or (bool(_s) and "\u0600" <= _s[0] <= "\u06ff")
        txt = ft.Text(_msg if _ar_line else ("\u200e" + _msg), size=12,
                      color=color,
                      weight=ft.FontWeight.BOLD if tone in ("story", "ok") else ft.FontWeight.W_500,
                      font_family=(T.F_AR if _ar_line else T.F_UI),
                      expand=True,
                      text_align=ft.TextAlign.RIGHT if _ar_line else ft.TextAlign.LEFT)
        # Left cluster = icon + id + seq (always on the left margin for consistency)
        left = [c for c in (icon, idtxt, seqtxt) if c is not None]
        row_children = left + [txt]
        indent = ln.get("indent")
        row = ft.Row(row_children, spacing=7, vertical_alignment=ft.CrossAxisAlignment.START)
        return ft.Container(row, padding=ft.Padding.only(
            left=22 if indent else 0, top=2, bottom=2))

    def _render_log_lines(self):
        return [self._render_one_log(ln) for ln in getattr(self, "_log_lines", [])]

    def _refresh_run(self):
        def _apply():
            # Only manipulate run-screen controls while the Run screen is shown.
            # Otherwise those controls are detached and updating them can leave a
            # ghost (e.g. the progress spinner) painted on the current screen.
            if self.active != "run":
                return
            try:
                if hasattr(self, "_stats_row"):
                    s = self._stats
                    if self.tool == "steps":
                        self._stats_row.controls = [
                            stat_tile(strings.t("au_stat_test_cases"), s["total"]),
                            stat_tile(strings.t("au_stat_created"), s.get("created", 0), tone="violet"),
                            stat_tile(strings.t("au_stat_updated"), s["done"], tone="green"),
                            stat_tile(strings.t("au_stat_skipped"), s["skipped"], tone="amber"),
                            stat_tile(strings.t("au_stat_errors"), s["errors"], tone="red"),
                        ]
                    else:
                        self._stats_row.controls = [
                            stat_tile(strings.t("au_stat_test_cases"), s["total"]),
                            stat_tile(strings.t("au_stat_stories"), f"{s['stories_done']}", tone="violet", sub=f"/{s['total_stories']}"),
                            stat_tile(strings.t("au_stat_created"), s["done"], tone="green"),
                            stat_tile(strings.t("au_stat_skipped"), s["skipped"], tone="amber"),
                            stat_tile(strings.t("au_stat_errors"), s["errors"], tone="red"),
                        ]
                if hasattr(self, "_bar"):
                    self._bar.value = (self._progress["pct"]/100) if self._progress["pct"] > 0 else None
                # _prow children: [spinner, label, spacer, pct]
                if hasattr(self, "_prow"):
                    try:
                        _stopping = getattr(self, "_stopping", False)
                        _done = self._progress["pct"] >= 100
                        _ended = not getattr(self, "_run_active", False)
                        # swap spinner → static container when stopping / done / ended
                        # (ended covers a credit-stop, which isn't 100% and isn't a
                        # user-initiated stop — the spinner used to keep spinning).
                        if _stopping or _done or _ended:
                            self._prow.controls[0] = ft.Container(width=14, height=14)
                        self._prow.controls[1].value = (
                            "Stopping after current test case…" if _stopping
                            else "Completed" if _done
                            else "Stopped" if _ended
                            else self._progress["label"])
                        self._prow.controls[-1].value = (
                            "Stopped" if (_ended and not _done)
                            else "Done" if _done
                            else f"{self._progress['pct']}%" if self._progress["pct"] > 0
                            else "Starting…")
                        # live "THIS RUN" meta (elapsed · ETA · story x/y)
                        if hasattr(self, "_tr_meta"):
                            self._tr_meta.value = self._run_meta_line()
                    except Exception:
                        pass
                if hasattr(self, "_log_col"):
                    # Locked: _apply() is invoked BOTH synchronously (inline,
                    # from whatever thread called _refresh_run) AND again via
                    # page.run_thread() just below ("to defeat the focus-repaint
                    # bug") — two invocations of this exact block, on two
                    # different threads, reading/writing the SAME
                    # _rendered_count/_log_col.controls with no coordination.
                    # With run_steps now able to fire cb() from a couple of
                    # worker threads in quick succession (bounded concurrency —
                    # see engine.py's run_steps), these overlapping applies
                    # started actually colliding: both read the same stale
                    # `rendered`, both append the same slice of new lines
                    # (visible duplicates), or one clobbers _rendered_count
                    # past what the other actually rendered (skipped lines,
                    # out-of-order appends). Wrapping the read-diff-render-
                    # write sequence in a lock makes it atomic across however
                    # many concurrent _apply() calls are in flight — same fix
                    # shape as automation.py's _auto_log_ui_lock for the
                    # identical class of bug there.
                    with self._run_log_ui_lock:
                        rendered = getattr(self, "_rendered_count", 0)
                        all_lines = getattr(self, "_log_lines", [])
                        if rendered < 0 or (rendered == 0 and all_lines):
                            # full rebuild (placeholder swap or a wip line was removed)
                            self._log_col.controls = self._render_log_lines()
                        elif len(all_lines) > rendered:
                            new_ctrls = [self._render_one_log(ln) for ln in all_lines[rendered:]]
                            self._log_col.controls.extend(new_ctrls)
                        self._rendered_count = len(all_lines)
                self.page.update()
            except Exception:
                pass
        # Single serialized dispatch (see ui_safe): the old inline call +
        # run_thread duplicate ("to defeat the focus-repaint bug") ran this
        # same whole-page update on two threads at once — the lock above kept
        # the log list consistent, but the two page.update() diffs could still
        # interleave with each other and with event-handler updates. On the
        # session loop, updates repaint normally and run one at a time.
        self.ui_safe(_apply)

    # ═══════════════════════════════════════════════════════════════════════════
    #  REPORT SCREEN
    # ═══════════════════════════════════════════════════════════════════════════
    def _relative_time(self):
        """Human 'just now / 5 mins ago / 1 hr ago' from the run-finish time."""
        import time as _t
        ts = getattr(self, "_report_time", None)
        if not ts:
            return "just now"
        secs = int(_t.time() - ts)
        if secs < 45:
            return "just now"
        if secs < 90:
            return "1 min ago"
        mins = secs // 60
        if mins < 60:
            return f"{mins} mins ago"
        hrs = mins // 60
        if hrs < 24:
            return f"{hrs} hr ago" if hrs == 1 else f"{hrs} hrs ago"
        days = hrs // 24
        return "1 day ago" if days == 1 else f"{days} days ago"

    def report_screen(self):
        return report.screen(self)

    def _new_run(self):
        self.active = "setup"
        self.nav_state = {"setup": "active"}
        # clear the finished-report markers so the Report nav goes back to "03"
        self._run_finished = False
        self.last_report = None
        self.render()

    def _open_url(self, url):
        """Open a URL in the default browser, brought to the FRONT (over the app).

        Mobile (every "link" in the app funnels through here — useful_links.py's
        Links screen, work-item/story links in regression.py and report.py,
        "Open plan in Azure"): os.startfile is Windows-only and already
        correctly skipped, but webbrowser.open() is ALSO desktop-oriented —
        it shells out looking for a system browser controller, which doesn't
        exist in Flet's embedded Android/iOS runtime, and can return True
        there without having opened anything (no exception, no real failure
        signal) — reported live as "links not working" (fix #1).

        The fallback THAT fix relied on — Flet's deprecated `page.launch_url()`
        convenience wrapper — turned out to be broken too, and the same bug
        reappeared live ("links not working" AGAIN after fix #1 shipped).
        `page.launch_url()` just does `await UrlLauncher().launch_url(url)`
        — a brand-new, throwaway UrlLauncher on every call, never attached to
        `page.services`. Same class of bug already found twice this session
        in mobile_wakelock.py/secure_store_mobile.py: an unattached Service
        control has no matching control on the Flutter client, so its RPC
        silently goes nowhere — and because this all happens inside a
        page.run_task()-scheduled coroutine, the failure never reaches this
        function's try/except either. Fixed (fix #2) by routing through
        mobile_url_launcher.py's ONE persistent, properly-attached
        `ft.UrlLauncher()` instance (see main.py's QAStudio.__init__ for the
        page.services.append()+page.update() wiring) instead."""
        try:
            import os as _os
            if _os.name == "nt":
                # Windows: ShellExecute 'open' (os.startfile) foregrounds the
                # browser window over the app, instead of opening it behind
                # us like webbrowser.open can.
                _os.startfile(url)   # noqa: S606 — trusted, user-initiated links
                return
        except Exception:
            pass
        if not platform_caps.is_mobile():
            opened = False
            try:
                import webbrowser
                opened = webbrowser.open(url)
            except Exception:
                opened = False
            if opened:
                return
        # Mobile, or the desktop webbrowser path above didn't pan out: use
        # the persistent, properly-attached UrlLauncher service.
        try:
            import mobile_url_launcher
            if mobile_url_launcher.open(url):
                return
        except Exception:
            pass
        # Last-resort fallback (kept in case mobile_url_launcher failed to
        # attach for some reason) — same caveat as before: may silently no-op.
        try:
            rt = getattr(self.page, "run_task", None)
            if callable(rt):
                rt(self.page.launch_url, url)
                return
        except Exception:
            pass
        try:
            self.page.launch_url(url)
        except Exception:
            pass

    def _open_azure(self):
        if not self.can("act.open_plan"):
            return self._toast(strings.t("main_no_plan_perm"))
        if self.project and self.plan_id:
            url = backend_setup.plan_url(self, self.plan_id)
            self._open_url(url)
        else:
            self._toast(strings.t("main_no_plan"))

    # ═══════════════════════════════════════════════════════════════════════════
    #  AUTOMATION SCREEN — Selenium DOM scrape → TestNG/POM project → Git push
    # ═══════════════════════════════════════════════════════════════════════════
    def _auto_field(self, label, attr, hint, password=False, req=False,
                    info=None, on_info=None):
        invalid = attr in getattr(self, "_auto_invalid", set())
        err = ft.Text(
            getattr(self, "_auto_invalid_msgs", {}).get(attr, "Required."),
            size=11, color=T.RED, weight=ft.FontWeight.W_500, visible=invalid)
        tf = ft.TextField(
            value=getattr(self, attr, "") or "", hint_text=hint, password=password,
            can_reveal_password=password,
            border_color=(T.RED if invalid else T.BORDER),
            focused_border_color=(T.RED if invalid else T.VIOLET), border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=11, horizontal=12),
            text_size=13, expand=True,
            on_change=lambda e, a=attr, et=err: self._auto_field_change(a, e.control, et))
        return ft.Column([field_label(label, req=req, info=info, on_info=on_info),
                          ft.Container(hover_field(tf), padding=ft.Padding.only(top=4)),
                          ft.Container(err, padding=ft.Padding.only(top=4, left=2))],
                         spacing=0)

    def _auto_field_change(self, attr, ctrl, err):
        """Live-update the bound value and clear the red invalid state as soon as
        the user types into a previously-empty required field."""
        setattr(self, attr, ctrl.value)
        # Persist automation inputs IMMEDIATELY (like the PAT/org fields) so a
        # changed folder/URL survives an app restart or re-login. Previously these
        # only saved on leave/generate, so a new path could silently revert to the
        # old one and the next run would write to the wrong folder.
        try:
            self._save_git_creds()
        except Exception:
            pass
        inv = getattr(self, "_auto_invalid", None)
        if inv and attr in inv and (ctrl.value or "").strip():
            inv.discard(attr)
            ctrl.border_color = T.BORDER
            ctrl.focused_border_color = T.VIOLET
            err.visible = False
            try:
                ctrl.update(); err.update()
            except Exception:
                pass

    def automation_screen(self):
        return automation.screen(self)

    def _auto_count(self):
        """TODO tally = locators the generated tests resolve at RUNTIME (no stable
        seed found). Updated live during sequencing via hidden `TODO_LIVE: N` control
        lines, and reconciled to the emitted total when the project is written.
        'skipped' = duplicate test cases left out before sequencing, updated live
        via hidden `SKIPPED_LIVE: N` control lines the same way."""
        return {"todo": getattr(self, "_auto_todo", 0),
                "skipped": getattr(self, "_auto_skipped", 0)}

    def _auto_counts_header(self):
        # Restyled to match Run screen's stat_tile (ui.py) cards exactly —
        # neutral card background + border, a small label row with a tone
        # dot, and a big brand-gradient number underneath — instead of the
        # solid-tinted colored pills this used before. Not calling stat_tile()
        # directly: it builds its own number Text internally with no way to
        # get a handle back, and _upd_todo/_upd_skipped (above) need to keep
        # updating that Text in place on every TODO_LIVE/SKIPPED_LIVE line
        # without a full app.render() (which would reset scroll position
        # mid-run). This mirrors stat_tile's exact visual recipe by hand so
        # that handle can be kept, same as the previous chip() closure did.
        c = self._auto_count()
        self._auto_count_ctl = {}
        def tile(key, label, dot_color):
            num = ft.Text(str(c[key]), size=22, weight=ft.FontWeight.BOLD, color="#FFFFFF")
            self._auto_count_ctl[key] = num
            try:
                num_display = ft.ShaderMask(
                    content=num, blend_mode=ft.BlendMode.SRC_IN,
                    shader=ft.LinearGradient(begin=ft.Alignment.TOP_LEFT,
                                             end=ft.Alignment.BOTTOM_RIGHT,
                                             colors=list(T.GRAD_LOGO)))
            except Exception:
                num.color = T.VIOLET_INK
                num_display = num
            return ft.Container(
                ft.Column([
                    ft.Row([ft.Text(label, size=10.5, color=T.INK_2, weight=ft.FontWeight.BOLD,
                                    expand=True, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Container(width=8, height=8, bgcolor=dot_color, border_radius=5)],
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    num_display,
                ], spacing=3),
                padding=ft.Padding.symmetric(vertical=14, horizontal=12),
                bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER),
                border_radius=T.R, expand=True)
        return ft.Row([
            tile("todo", strings.t("main_stat_selfhealed"), T.VIOLET_INK),
            tile("skipped", strings.t("main_stat_dupskipped"), T.AMBER),
        ], spacing=7, vertical_alignment=ft.CrossAxisAlignment.STRETCH)

    def _auto_log_line(self, msg, tone):
        # "case" (Sequencing/title/⏱ lines from engine.py's _compile_one) gets
        # its own color — T.STORY, a brighter cyan than "story"/"info"'s
        # VIOLET_INK — so a case's own group of lines visually stands apart
        # from connection/retry/status noise around it, instead of blending
        # into "dim" gray like everything else did before.
        cmap = {"ok": T.GREEN, "err": T.RED, "warn": T.AMBER, "story": T.VIOLET_INK,
                "info": T.VIOLET_INK, "case": T.STORY, "dim": T.INK_3, "review": T.AMBER}
        color = cmap.get(tone, T.INK_2)
        stripped = (msg or "").lstrip(" ")
        indent = len(msg or "") - len(stripped)
        pad = min(indent, 8) * 3
        # "\x1f" (invisible control char, never rendered) splits an LTR
        # meta bit ("\u23f1 0:20 \u00b7 #3/36") from an RTL/LTR title body \u2014 see
        # engine.py's _compile_one. Concatenating both into ONE Text control
        # hit Unicode's bidi reordering: Flutter resolves a Text's paragraph
        # direction from the first strong-directional character in its WHOLE
        # string, so an LTR tag glued onto an Arabic title visibly scrambled
        # ("#3/36" rendered back as "3/36#", confirmed live). Rendering meta
        # and body as two independent Text controls means each resolves its
        # own direction from its own content only \u2014 neither can reorder
        # relative to the other.
        _sep = ""
        if _sep in stripped:
            meta_part, body_part = stripped.split(_sep, 1)
        else:
            meta_part, body_part = None, stripped
        # RTL only when the BODY starts in Arabic (a raw story/test-case
        # title). Checking for ANY Arabic character anywhere used to flip
        # lines like "skipping duplicate: <Arabic title> (same as #103921)"
        # \u2014 English scaffolding with an embedded Arabic title \u2014 into full
        # right-aligned RTL too, which garbled the English prefix/suffix
        # around the title.
        is_ar = bool(body_part) and ("\u0600" <= body_part[0] <= "\u06ff")
        weight = ft.FontWeight.BOLD if tone in ("story", "ok") else ft.FontWeight.W_500
        # A heartbeat line ("\u23f1 0:25") already carries its own leading glyph
        # (in its meta half, or \u2014 for older/unsplit lines \u2014 at the very start
        # of the string) \u2014 giving it a second symbol in front doubled up
        # visually ("\u2022 \u23f1 0:25"). Skip the symbol slot for those.
        _has_own_symbol = (meta_part or stripped).startswith("\u23f1")
        meta_txt = (ft.Text(meta_part, size=12, color=color, weight=ft.FontWeight.W_500,
                            font_family=T.F_MONO, text_align=ft.TextAlign.LEFT)
                    if meta_part is not None else None)
        # tone symbol, matching the Run activity log (icon + colour). "dim"
        # used to be a plain gray dot; replaced with a small chevron so the
        # (most common) plain progress/status lines read as a real marker
        # instead of a bullet-list look.
        if tone == "ok":
            sym = ft.Icon(ft.Icons.CHECK, size=13, color=T.GREEN)
        elif tone == "err":
            sym = ft.Icon(ft.Icons.CLOSE, size=13, color=T.RED)
        elif tone == "warn":
            sym = ft.Text("\u26a0", size=12, color=T.AMBER, weight=ft.FontWeight.BOLD)
        elif tone == "review":
            sym = ft.Text("\u2691", size=12, color=T.AMBER, weight=ft.FontWeight.BOLD)
        elif tone == "story":
            sym = ft.Text("\u25b8", size=13, color=T.VIOLET_INK, weight=ft.FontWeight.BOLD)
        elif tone == "info":
            sym = ft.Icon(ft.Icons.PLAY_ARROW, size=13, color=T.VIOLET_INK)
        elif _has_own_symbol:
            sym = None
        elif tone == "case":
            sym = ft.Text("\u203a", size=14, color=T.STORY, weight=ft.FontWeight.BOLD)
        else:  # dim (and unknown): a small chevron instead of a plain bullet dot
            sym = ft.Text("\u203a", size=14, color=T.INK_3, weight=ft.FontWeight.BOLD)
        # Hugged to the RIGHT of its slot (toward the text) instead of
        # centered — centering a narrow glyph like "›" in a 16px box left
        # visible dead space on both sides, reading as a gap between the
        # symbol and the line instead of one connected entry.
        sym_wrap = (ft.Container(sym, width=13, alignment=ft.Alignment.CENTER_RIGHT,
                                 margin=ft.Margin.only(top=2))
                    if sym is not None else ft.Container(width=13))
        # Trailing "\n" copy-separator fix REVERTED — see _render_one_log's
        # comment (Run/Report log): it visibly added extra vertical gaps
        # between every log line in the actual app, confirmed live. Use the
        # "Copy entire log" toolbar button instead until a copy-friendly fix
        # is found that doesn't touch visible layout.
        # RIGHT-aligning Arabic lines was reverted: in this narrow (384px)
        # rail, it left the tone icon pinned at the far left and the text
        # pushed to the far right, reading as a broken/disconnected line
        # instead of one entry. run.py's _render_one_log never right-aligns
        # Arabic text either (only swaps the font) — matching that exactly.
        txt = ft.Text(body_part, size=12, color=color, weight=weight,
                      font_family=(T.F_AR if is_ar else
                                   (T.F_MONO if tone in ("dim", "info", "case") else None)),
                      text_align=ft.TextAlign.LEFT,
                      expand=True)
        row_children = [sym_wrap, ft.Container(width=6)]
        if meta_txt is not None:
            row_children += [meta_txt, ft.Container(width=6)]
        row_children.append(txt)
        return ft.Container(
            ft.Row(row_children, spacing=0,
                   vertical_alignment=ft.CrossAxisAlignment.START),
            padding=ft.Padding.only(left=pad, top=1, bottom=1))

    def _auto_log_scroll_end(self, col):
        # ft.ListView had auto_scroll=True built in; the plain Column it was
        # reverted to (see automation.screen()'s comment on why) doesn't, so
        # this restores "follow the tail during a live run" manually. A large
        # finite offset rather than an unbounded one — Flet's IPC payload is
        # JSON, which has no real Infinity value. Best-effort: any failure
        # here (e.g. the control isn't laid out yet on the very first call)
        # just means the log doesn't auto-follow that one time, not a crash.
        def _go():
            # Both calls run bare/unawaited and are silent no-ops by design
            # — see _scroll_to_key's docstring for why (making scroll_to()
            # actually execute was tried and reverted: it raced newer
            # renders and caused visible client-side corruption). The real
            # reason this log auto-follows in practice is automation.py
            # building app._auto_log_col as an ft.ListView(auto_scroll=True)
            # — a declarative attribute handled inside the widget itself,
            # not an RPC call — which doesn't depend on scroll_to() at all
            # (see automation.py's numbered comment: offset/auto_scroll-on-
            # Column/key-based scroll_to were all tried and failed before
            # that). These two calls stay as harmless (inert) belt-and-
            # suspenders; key-based first (the tail row is tagged
            # "autolog-tail" by _retag_log_tail below), offset as a fallback
            # for when the tag isn't present (e.g. mid-rebuild).
            try:
                col.scroll_to(key="autolog-tail", duration=100)
            except Exception:
                pass
            try:
                col.scroll_to(offset=10_000_000, duration=100)
            except Exception:
                pass
        _go()
        # Fire once more shortly after: the immediate scroll_to runs before
        # Flutter has laid out the just-appended row(s) (especially tall
        # wrapped Arabic lines), so it scrolls to the PREVIOUS content extent
        # and the rail sits one entry behind. The delayed pass runs after
        # layout and lands on the true bottom. (auto_scroll=True on the
        # column — automation.py — is the primary mechanism now; this is the
        # belt-and-suspenders backup for renders that replace the column.)
        try:
            import threading as _t
            _t.Timer(0.15, lambda: self.ui_safe(_go)).start()
        except Exception:
            pass

    def _retag_log_tail(self, col):
        """Keep the scroll anchor on the LAST log row: clears the
        "autolog-tail" key from whichever control held it and stamps it onto
        the current last row, so _auto_log_scroll_end's key-based scroll_to
        always lands on the true bottom. Keys must be unique, hence the
        move-not-add. Called just before col.update() on every mutation."""
        try:
            prev = getattr(self, "_auto_tail_ctl", None)
            last = col.controls[-1] if col.controls else None
            if prev is not None and prev is not last:
                prev.key = None
            if last is not None:
                last.key = "autolog-tail"
                self._auto_tail_ctl = last
        except Exception:
            pass

    def _auto_logmsg(self, msg, tone="dim"):
        import re as _re
        # 'TODO_LIVE: N' is a HIDDEN control line — update the TODO counter in place
        # (live, as cases are sequenced) without adding a visible log entry.
        if (msg or "").startswith("TODO_LIVE:"):
            try:
                self._auto_todo = int((msg.split(":", 1)[1] or "0").strip().split()[0])
            except Exception:
                pass
            def _upd_todo():
                ctl = getattr(self, "_auto_count_ctl", None)
                if ctl and "todo" in ctl:
                    try:
                        ctl["todo"].value = str(getattr(self, "_auto_todo", 0))
                        ctl["todo"].update()
                    except Exception:
                        pass
            self.ui_safe(_upd_todo)
            return
        # 'SKIPPED_LIVE: N' — same hidden-control-line pattern as TODO_LIVE
        # above, for the running duplicate-skip tally.
        if (msg or "").startswith("SKIPPED_LIVE:"):
            try:
                self._auto_skipped = int((msg.split(":", 1)[1] or "0").strip().split()[0])
            except Exception:
                pass
            def _upd_skipped():
                ctl = getattr(self, "_auto_count_ctl", None)
                if ctl and "skipped" in ctl:
                    try:
                        ctl["skipped"].value = str(getattr(self, "_auto_skipped", 0))
                        ctl["skipped"].update()
                    except Exception:
                        pass
            self.ui_safe(_upd_skipped)
            return
        # drop consecutive duplicate lines (e.g. repeated "Paused…" notices).
        # Guarded by the same lock upd() uses below for the UI-sync read —
        # now that Automation's compile/dedupe loops call this from multiple
        # worker threads, the dup-check read + append must be atomic together
        # or two concurrent calls can both pass the check and double-append,
        # or interleave an append between another call's check and append.
        #
        # Provider-retry lines ("NVIDIA: provider error (503). Retrying… —
        # waiting 2s then retry (1/5)…") aren't exact duplicates — only the
        # delay/attempt count changes each retry — so the check above never
        # caught them; a flaky provider could stack 4-5 near-identical lines
        # in a row (confirmed live). Collapsing same-cause retries into ONE
        # line that updates in place, same idea as Run's "still generating…"
        # heartbeat (see _refresh_run's hb_id handling), without needing the
        # full dict-based hb_id machinery Run uses — this just compares the
        # message text with the "waiting Ns then retry (k/N)…" tail stripped
        # off, and if that base matches the previous line's, replaces it
        # in place instead of appending a new one.
        # KEYED in-place collapse — ONE line per retrying call, updated with the
        # latest state plus a running "×N waits" tally. The previous version
        # only collapsed when the retry line was the LAST log line, which
        # stopped working the moment two concurrent workers interleaved their
        # retries (confirmed live: #27/66 and #28/66 alternating appended a
        # dozen+ lines in a minute). Keyed by the "#k/N" case label when the
        # line carries one — so BOTH shapes a rate-limited case emits
        # ("… rate limited (429) … — waiting 25s then retry (4/8)…" from
        # ai_complete's backoff AND "… rate-limited — waiting 14s (shared
        # cooldown)…" from the shared cooldown gate) fold into the SAME line —
        # otherwise keyed by the message base with the wait/attempt tail
        # stripped (the old behavior's identity, minus the last-line-only
        # restriction). The mutated line's INDEX is queued in
        # _auto_log_inplace_idxs for upd() below to re-render in place.
        # Three shapes fold into one keyed, in-place line: ai_complete's backoff
        # ("… — waiting 25s then retry (4/8)…"), the shared-cooldown wait
        # ("… — waiting 14s (shared cooldown)…"), and the slow-call heartbeat
        # ("… — 120s so far…"). Only the two WAIT shapes (named group 'w')
        # increment the ×N tally — heartbeats just refresh the line's text.
        _retry_m = _re.match(
            r"^(.*?)\s*—\s*(?:(?P<w>waiting\s+\d+s\s*(?:then retry \(\d+/\d+\)|\(shared cooldown\)))|\d+s so far)…?\s*$",
            msg or "")
        with self._auto_log_ui_lock:
            if self._auto_log and self._auto_log[-1].get("msg") == msg:
                return
            _pend = self._auto_log_inplace_idxs = getattr(
                self, "_auto_log_inplace_idxs", set())
            _inplace = False
            if _retry_m:
                _base = _retry_m.group(1)
                _lab = _re.match(r"^\s*(#\d+/\d+)\s*·", _base)
                _key = "retry:" + (_lab.group(1) if _lab else _base)
                _idx_map = self._auto_retry_idx = getattr(self, "_auto_retry_idx", {})
                _n_map = self._auto_retry_n = getattr(self, "_auto_retry_n", {})
                if _retry_m.group("w"):
                    _n_map[_key] = _n_map.get(_key, 0) + 1
                if _n_map.get(_key, 0) > 1:
                    msg = f"{msg}  ·  ×{_n_map[_key]} waits so far"
                _i = _idx_map.get(_key)
                if _i is not None and 0 <= _i < len(self._auto_log):
                    self._auto_log[_i] = {"msg": msg, "tone": tone}
                    _pend.add(_i)
                    _inplace = True
                else:
                    _idx_map[_key] = len(self._auto_log)   # appended just below
            if not _inplace:
                self._auto_log.append({"msg": msg, "tone": tone})
            # Coalesce: ui_safe() hands upd() to page.run_thread, which spins up
            # a BRAND-NEW thread per call. Compile/dedupe worker threads can each
            # call this several times a second, especially right as Stop makes
            # multiple in-flight calls all wrap up within the same instant — that
            # was producing a visible "wall of lines appears all at once" effect
            # (reported live), because dozens of independently-scheduled update
            # threads were queuing/racing instead of the log growing smoothly as
            # each line was actually generated. If an update is already in
            # flight, skip scheduling another one — the in-flight upd() already
            # reads self._auto_log[have:real] fresh each time (see below), so it
            # will pick up THIS line too; scheduling a second one would just be
            # redundant thread churn, not a faster or more correct render.
            if getattr(self, "_auto_log_upd_pending", False):
                return
            self._auto_log_upd_pending = True
        def upd():
            try:
                lock = getattr(self, "_auto_log_ui_lock", None)
                if lock is not None:
                    lock.acquire()
                try:
                    col = getattr(self, "_auto_log_col", None)
                    if col is not None:
                        real = len(self._auto_log)
                        # The empty-state placeholder is ONE control but ZERO log
                        # lines — it's flagged explicitly at both build sites
                        # (automation.screen / _clear_auto_log below) so it can't
                        # be mistaken for a rendered line. The old heuristic
                        # (real == 1 and have <= 1) missed the case where TWO
                        # lines land before the first upd() runs: it took the
                        # have<real append path, which left the placeholder on
                        # screen as row 0 and dropped line 1 forever.
                        if getattr(col, "_qa_placeholder", False):
                            if real > 0:
                                col.controls = [self._auto_log_line(e["msg"], e["tone"])
                                                for e in self._auto_log]
                                col._qa_placeholder = False
                                self._retag_log_tail(col)
                                col.update()
                                self._auto_log_scroll_end(col)
                                # full rebuild already reflects every in-place edit
                                getattr(self, "_auto_log_inplace_idxs", set()).clear()
                        else:
                            have = len(col.controls)
                            # render() rebuilds the column from self._auto_log, and
                            # this incremental append can race it at run-end —
                            # appending a line render already added (the duplicate
                            # "Stopped." etc.). Only touch the column when it's
                            # actually behind self._auto_log.
                            _changed = False
                            if have < real:
                                # Catch the column up to the FULL current log, in
                                # source order — NOT just this call's own (msg,
                                # tone). ui_safe() hands each call's UI update to
                                # Flet's page.run_thread, which starts a brand-new
                                # thread per call with no ordering guarantee, so
                                # several updates can be in flight and finish in
                                # any order. Reading fresh from
                                # self._auto_log[have:real] (rather than the value
                                # this particular closure captured) means whichever
                                # update happens to run — first, last, or anywhere
                                # between — always renders every missing line in
                                # the right order, so the screen can't end up
                                # scrambled relative to the (always correctly-
                                # ordered) underlying log list.
                                for entry in self._auto_log[have:real]:
                                    col.controls.append(
                                        self._auto_log_line(entry["msg"], entry["tone"]))
                                _changed = True
                            # Re-render any line _auto_logmsg mutated IN PLACE
                            # (the keyed retry collapse). Generalizes the old
                            # "last line only" flag, which broke the moment two
                            # concurrent workers' retry lines interleaved —
                            # col.controls maps 1:1 onto self._auto_log here
                            # (append-only + in-place edits), so the queued
                            # indexes address the right rows directly.
                            _pend = getattr(self, "_auto_log_inplace_idxs", None)
                            if _pend:
                                for _i in sorted(_pend):
                                    if 0 <= _i < min(len(col.controls), real):
                                        _e = self._auto_log[_i]
                                        col.controls[_i] = self._auto_log_line(
                                            _e["msg"], _e["tone"])
                                        _changed = True
                                _pend.clear()
                            if _changed:
                                self._retag_log_tail(col)
                                col.update()
                                self._auto_log_scroll_end(col)
                            # else: render already has every line; skip
                    ctl = getattr(self, "_auto_count_ctl", None)
                    if ctl:
                        c = self._auto_count()
                        for k, t in ctl.items():
                            try:
                                t.value = str(c.get(k, 0)); t.update()
                            except Exception:
                                pass
                    # Cleared under the SAME lock the coalescing check above
                    # uses, so the flip from "one is in flight" back to "none
                    # scheduled" can't race a concurrent append.
                    self._auto_log_upd_pending = False
                finally:
                    if lock is not None:
                        lock.release()
            except Exception:
                pass
        self.ui_safe(upd)

    @staticmethod
    def _win_clipboard_set(text):
        """Write `text` straight to the Windows clipboard via ctypes — no window
        needs to stay alive afterward, and nothing async is involved.

        This replaces an EARLIER fallback that used a separate native clipboard API
        clipboard_append(), which caused a real, reproduced bug: Windows'
        clipboard normally uses DELAY-RENDER — clipboard_append() doesn't hand
        the actual text to the OS immediately, it just registers the calling
        window as able to produce it LATER, when some other app actually pastes.
        That earlier code destroyed the (hidden) Tk window right after calling
        it, so by the time the user pasted into Notepad, Windows tried to ask
        the now-dead window for the data and got no answer — hanging Notepad's
        paste indefinitely.

        SetClipboardData with a real global-memory handle has no such
        requirement: the text is committed to the system clipboard
        synchronously inside this call, and ownership of the memory transfers
        to Windows itself — so there is nothing left alive (or dead) for a
        later paste to wait on."""
        import ctypes
        import ctypes.wintypes as wintypes
        GMEM_MOVEABLE = 0x0002
        CF_UNICODETEXT = 13
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        # Explicit argtypes/restype on every call — an unconfigured ctypes
        # function defaults to 32-bit int, which truncates real pointers/handles
        # on 64-bit Windows (the exact class of bug already caught once in this
        # codebase's DPAPI code in store.py).
        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        if not user32.OpenClipboard(None):
            raise OSError("OpenClipboard failed")
        try:
            user32.EmptyClipboard()
            data = (text or "").encode("utf-16-le") + b"\x00\x00"
            h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h_mem:
                raise OSError("GlobalAlloc failed")
            p_mem = kernel32.GlobalLock(h_mem)
            if not p_mem:
                raise OSError("GlobalLock failed")
            try:
                ctypes.memmove(p_mem, data, len(data))
            finally:
                kernel32.GlobalUnlock(h_mem)
            if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
                raise OSError("SetClipboardData failed")
            # h_mem now belongs to the system on success — must NOT be freed here.
        finally:
            user32.CloseClipboard()

    @staticmethod
    def _clip_fail_reason(ex1, ex2):
        """Turn the two clipboard-copy exceptions (Flet's page.set_clipboard,
        then the direct-to-Windows ctypes fallback) into ONE short, plain-English
        clause a non-technical user can actually understand — instead of a raw
        exception dump. Falls back to the exception's own text (truncated) only
        when nothing more specific is recognized."""
        combined = f"{ex1} {ex2}".lower()
        if "windll" in combined:
            return "clipboard access isn't supported on this operating system"
        if "display" in combined or "no display" in combined:
            return "no display is available for clipboard access"
        if "timed out" in combined or "timeout" in combined:
            return "the app window didn't respond in time"
        if "access" in combined and ("denied" in combined or "permission" in combined):
            return "another app has the clipboard locked — close it and try again"
        detail = (str(ex1) or str(ex2) or "").strip()
        return detail[:100] if detail else "an unknown error"

    def _copy_text_to_clipboard(self, text, ok_msg="Copied to clipboard."):
        """Shared clipboard-copy logic — the actual copy-with-fallback
        mechanism behind _copy_log_text below, pulled out on its own so ANY
        screen can reuse the same robust behavior on an already-formatted
        string (not just newline-joined log lines, which get blank-line
        stripping that would ruin deliberate spacing elsewhere — see
        sprint_titles.py's report Copy button). Tries Flet's own
        page.set_clipboard() first, falls back to a direct Windows clipboard
        write (_win_clipboard_set) if that fails, and only shows a red error
        toast — with a readable, classified reason, not a raw exception dump —
        if BOTH paths fail."""
        if not (text or "").strip():
            self._toast(strings.t("main_nothing_copy"))
            return
        try:
            self.page.set_clipboard(text)
            self._toast(ok_msg)
            return
        except Exception as ex1:
            # Fall back to setting the OS clipboard directly (see
            # _win_clipboard_set) if Flet's own page.set_clipboard() fails —
            # page.set_clipboard() goes over Flet's IPC channel to the Flutter
            # client, and a long log can be several thousand characters; if that
            # channel has an undocumented payload limit on this build, this
            # bypasses it entirely. An EARLIER version of this fallback used
            # a native clipboard append call, which caused a real reported bug —
            # Notepad hanging on the next paste — because of how Windows'
            # delay-render clipboard interacts with a destroyed window; see
            # _win_clipboard_set's docstring for the full explanation. This
            # direct ctypes write has no such issue.
            try:
                if not platform_caps.is_windows():
                    # ctypes/user32 clipboard is Windows-only (mobile Phase 0):
                    # route non-Windows straight to the readable error path
                    # instead of an AttributeError from a missing windll.
                    raise RuntimeError("direct clipboard write is Windows-only")
                self._win_clipboard_set(text)
                self._toast(ok_msg)
            except Exception as ex2:
                # A readable, one-sentence reason instead of a raw exception dump —
                # and via _err (red), not _toast (always green): a failure toast
                # rendering as a green success checkmark was its own bug.
                reason = self._clip_fail_reason(ex1, ex2)
                self._err(strings.t("main_copy_failed", reason=reason))

    def _copy_log_text(self, lines):
        """Shared clipboard-copy logic for the Run and Automation activity logs
        (kept in one place so both stay in sync) — builds the joined text and
        delegates the actual copy-with-fallback to _copy_text_to_clipboard."""
        # "\x1f" is the invisible meta/body separator in Automation "case"
        # lines (see engine.py's _compile_one) — it renders as two separate
        # Text controls on screen, but copied raw it disappears entirely,
        # jamming the halves together ("#2/36التحقق…"). Swap it for a real
        # space in the copied text. Run-log lines never contain it — no-op.
        lines = [(l or "").replace("\x1f", " ") for l in lines if (l or "").strip()]
        if not lines:
            self._toast(strings.t("main_nothing_copy"))
            return
        n = len(lines)
        self._copy_text_to_clipboard(
            "\n".join(lines), f"Log copied to clipboard ({n} line{'s' if n != 1 else ''}).")

    def _copy_auto_log(self, e=None):
        self._copy_log_text([ln.get("msg", "") for ln in getattr(self, "_auto_log", [])])

    def _copy_run_log(self, e=None):
        self._copy_log_text([ln.get("msg", "") for ln in getattr(self, "_log_lines", [])])

    def _clear_run_log(self, e=None):
        self._log_lines = []
        self._rendered_count = 0   # so the next appended line renders, not skipped
        # Scoped update — see _clear_auto_log below for the full reasoning
        # (same pattern: swap the log column's own content in place instead of
        # a full app.render(), which would rebuild the whole Run screen).
        col = getattr(self, "_log_col", None)
        if col is not None:
            try:
                col.controls = [ft.Row([
                    ft.Icon(ft.Icons.TERMINAL, size=14, color=T.INK_3),
                    ft.Text(strings.t("main_no_activity"), size=12.5, color=T.INK_3,
                           weight=ft.FontWeight.BOLD),
                ], spacing=10)]
                col.update()
                return
            except Exception:
                pass
        self.render()

    def _reset_auto_retry_state(self):
        """Reset the keyed retry-collapse bookkeeping (see _auto_logmsg) —
        MUST be called wherever self._auto_log is cleared/replaced: the maps
        hold INDEXES into _auto_log, and a stale index surviving a clear
        would silently overwrite an unrelated line in the next run's log."""
        self._auto_retry_idx = {}
        self._auto_retry_n = {}
        getattr(self, "_auto_log_inplace_idxs", set()).clear()

    def _clear_auto_log(self, e=None):
        self._auto_log = []
        self._reset_auto_retry_state()
        # Scoped update — same reasoning as the push-flow fix earlier (see
        # _push_automation / automation._refresh_auto_state): a full render()
        # rebuilds the ENTIRE Automation screen, not just the log, which is
        # unnecessary here and would reset scroll position / any in-progress
        # edits on the left-hand form for no reason. Just swap the log column's
        # own content back to the empty-state placeholder in place.
        col = getattr(self, "_auto_log_col", None)
        if col is not None:
            try:
                # Fixed-height wrapper: see automation.py's screen() comment on
                # the same construct — empty_state()'s own expand=True can't
                # be a direct child of this scroll=AUTO column (unbounded
                # scroll-axis constraint), which was blanking the whole
                # Activity card, not just the log, right after Clear.
                col.controls = [ft.Container(empty_state(
                    ft.Icons.TERMINAL, "No activity yet",
                    "Fill in the site and Git details, then Generate — "
                    "each step shows up here live."), height=320)]
                # Back to placeholder state — see _auto_logmsg's upd(): the next
                # real line must REPLACE this control, not append after it.
                col._qa_placeholder = True
                col.update()
                return
            except Exception:
                pass
        # No live column reference yet (e.g. Clear was somehow reachable before
        # the Automation screen ever rendered) — fall back to a full render.
        self.render()

    def _save_git_creds(self):
        try:
            self.creds["git_url"] = self.auto_git_url
            self.creds["git_branch"] = self.auto_git_branch
            self.creds["git_token"] = self.auto_git_token
            # Persist the Target site + login fields too, so the Automation
            # screen keeps everything until the user changes it.
            self.creds["auto_site_url"] = self.auto_site_url
            self.creds["auto_login_url"] = self.auto_login_url
            self.creds["auto_login_user"] = self.auto_login_user
            self.creds["auto_login_pass"] = self.auto_login_pass
            self.creds["auto_local_path"] = self.auto_local_path
            store.save(self.creds)
        except Exception:
            pass

    def _stop_automation(self):
        """Request the running automation to stop after the current step."""
        try: E.request_stop()   # interrupt any in-flight retry backoff
        except Exception: pass
        with self._auto_cond:
            self._auto_stop = True
            self._auto_paused = False
            self._auto_cond.notify_all()
        self._auto_logmsg("Stopping after the current step…", "warn")

    def _pause_automation(self):
        """Pause the run at the next safe point (between test cases)."""
        with self._auto_cond:
            self._auto_paused = True
        self._auto_logmsg("Paused. Switch the AI provider in Setup if needed, "
                          "then Resume — or Stop to abort.", "warn")
        try:
            self.render()
        except Exception:
            pass

    def _resume_automation(self):
        """Resume a paused run (e.g. after switching provider)."""
        with self._auto_cond:
            self._auto_paused = False
            self._auto_cond.notify_all()
        self._auto_logmsg(strings.t("main_resuming"), "info")
        try:
            self.render()
        except Exception:
            pass

    def _auto_gate(self):
        """Block while paused; return False if we're stopping (so the engine
        aborts cleanly). Called by the engine between test cases."""
        with self._auto_cond:
            while self._auto_paused and not self._auto_stop:
                self._auto_cond.wait()
        return not self._auto_stop

    def _auto_on_ai_error(self, msg):
        """Engine calls this on a recoverable AI error (e.g. low credit). Auto-pause
        and wait: Resume (after switching provider) → 'retry'; Stop → 'stop'."""
        # The engine already passes a friendly, provider-prefixed message — log it
        # as-is (re-running friendly_ai_error here double-prefixed it: "Gemini: Gemini:…").
        self._auto_logmsg(str(msg)[:300], "red")
        self._auto_logmsg("Paused on error. Switch the AI provider in Setup, then "
                          "Resume — or Stop to abort.", "warn")
        with self._auto_cond:
            self._auto_paused = True
        try:
            self.render()
        except Exception:
            pass
        with self._auto_cond:
            while self._auto_paused and not self._auto_stop:
                self._auto_cond.wait()
        if self._auto_stop:
            return "stop"
        # _resume_automation already logged "Resuming…"; no second "Retrying…" line.
        return "retry"

    def _run_pause_condition(self):
        """Lazily-created Condition shared by the Run screens' pause/resume
        machinery: the Pause/Resume header button, the between-items gate,
        and the fatal-provider-error auto-pause all wait/notify on it."""
        cond = getattr(self, "_run_pause_cond", None)
        if cond is None:
            import threading as _t
            cond = self._run_pause_cond = _t.Condition()
        return cond

    def _ai_config_signature(self):
        """What "the AI setup changed" actually means, for the Resume guard.

        Comparing the PROVIDER alone was wrong: switching to a different MODEL
        on the same provider (an OpenRouter ':free' model, a smaller Gemini,
        a different NVIDIA model — which on NVIDIA even carries its own API
        key) is a completely legitimate fix for the failure, and a
        provider-only check nagged the user for it. The api_key is included
        because pasting a topped-up / corrected key is also a real change.

        Returns an opaque tuple used ONLY for equality — never logged, never
        displayed, so the key value can't leak into the run log."""
        prov = getattr(E, "AI_PROVIDER", "") or ""
        cfg = (getattr(E, "AI_CONFIG", {}) or {}).get(prov) or {}
        model = (cfg.get("deployment") if prov == "azure_openai"
                 else cfg.get("model")) or ""
        return (prov, str(model).strip(), (cfg.get("api_key") or "").strip())

    def _ai_label(self):
        """'Provider · model' for user-facing messages (never includes the key)."""
        prov, model, _ = self._ai_config_signature()
        disp = E.T_disp(prov) if prov else ""
        return f"{disp} · {model}" if (disp and model) else (disp or "this provider")

    def _run_logmsg(self, msg, tone="dim", ico=None):
        """Append a line to the RUN activity log from the UI side.

        The run log is normally fed only by engine's `cb("log", …)`, so
        UI-initiated events (the manual Pause/Resume button) left NO trace in
        it — the log jumped straight from one case to the next with no record
        that the user had paused, which made reading a run back impossible.
        Automation already had this via _auto_logmsg; this is the Run twin."""
        try:
            payload = {"msg": msg, "tone": tone}
            if ico:
                payload["ico"] = ico
            self._log_lines.append(payload)
            self._rendered_count = -1
            self._refresh_run()
        except Exception:
            pass

    def _toggle_run_pause(self):
        """Run-screen Pause/Resume header button (twin of Automation's).
        Pause lets in-flight cases finish, then the run holds before the next
        item; the user can switch the AI provider in Setup meanwhile. Resume
        wakes both the between-items gate and any workers auto-paused on a
        fatal provider error (_run_on_ai_error).

        RESUME GUARD: when the run was auto-paused by a provider error, the
        instruction is "switch the AI provider in Setup, then Resume" — but
        nothing stopped Resume being clicked with the SAME provider still
        selected, which just burns another attempt on the thing that had
        already failed (seen live: NVIDIA 404 → Resume → 'retrying with
        NVIDIA' → out-of-credits). The first Resume in that state now warns
        instead of resuming; a second click proceeds anyway, so the user is
        never actually blocked."""
        resuming = bool(getattr(self, "_run_paused", False))
        if resuming and getattr(self, "_run_pause_reason", "") == "provider_error":
            # Compare the FULL AI signature (provider + model + key), not just
            # the provider: switching model on the same provider is a valid fix.
            _same = (self._ai_config_signature() ==
                     getattr(self, "_run_pause_ai_sig", None))
            if _same and not getattr(self, "_run_resume_override", False):
                self._run_resume_override = True
                _prov = self._ai_label()
                _warn = (f"Still on {_prov}, which just failed. Change the AI "
                         f"provider or model in Setup — or click Resume again "
                         f"to retry anyway.")
                self._toast(_warn)
                self._run_logmsg(_warn, tone="warn", ico="⚠")
                try:
                    self.render()
                except Exception:
                    pass
                return
        cond = self._run_pause_condition()
        with cond:
            self._run_paused = not getattr(self, "_run_paused", False)
            paused = self._run_paused
            cond.notify_all()
        if paused:
            # Manual pause: record it in the run log too (see _run_logmsg).
            self._run_pause_reason = "manual"
            self._run_pause_ai_sig = self._ai_config_signature()
            self._run_logmsg("Paused by you — change the AI provider or model "
                             "in Setup if needed, then Resume; or Stop to end "
                             "the run.", tone="warn", ico="⏸")
        else:
            self._run_logmsg(f"Resumed on {self._ai_label()}.", tone="ok", ico="▶")
            self._run_pause_reason = ""
            self._run_resume_override = False
        self._toast(strings.t("main_run_paused_resume") if paused else strings.t("main_resuming"))
        try:
            self.render()
        except Exception:
            pass

    def _run_gate(self):
        """Between-items gate for run_steps/run_titles (engine calls it before
        dispatching each case/story): blocks while paused; returns False when
        Stop was clicked so the engine unwinds cleanly."""
        cond = self._run_pause_condition()
        with cond:
            while getattr(self, "_run_paused", False) and not self.stop_flag:
                cond.wait(timeout=0.5)
        return not self.stop_flag

    def _run_on_ai_error(self, msg):
        """Called by run_steps/run_titles (engine worker threads) on a FATAL
        provider error (out of credits / auth / bad model / network) that
        previously STOPPED the whole run. Auto-pauses instead: the engine has
        already logged the error + the 'Paused on provider error…' line; this
        flips the header button to Resume and blocks until the user resumes
        (→ 'retry', after switching provider in Setup) or stops (→ 'stop').
        Several workers erroring at once all wait on the same Condition."""
        cond = self._run_pause_condition()
        with cond:
            if self.stop_flag:
                return "stop"
            if not getattr(self, "_run_paused", False):
                self._run_paused = True
                # Remember WHY we paused and WHICH provider failed, so the
                # Resume guard in _toggle_run_pause can tell "user switched
                # provider" from "user just clicked Resume again".
                self._run_pause_reason = "provider_error"
                self._run_pause_ai_sig = self._ai_config_signature()
                self._run_resume_override = False
                self.ui_safe(self.render)   # flip the header button to Resume
        with cond:
            while getattr(self, "_run_paused", False) and not self.stop_flag:
                cond.wait(timeout=0.5)
        return "stop" if self.stop_flag else "retry"

    def _start_remote_run(self):
        """'Run remotely' path (REMOTE_RUNS.md): INSERT a remote_runs row as
        the signed-in user — the DB trigger auto-dispatches the GitHub
        Actions worker, which executes with THIS user's vault credentials.
        The app can be closed afterwards; progress is visible in the repo's
        Actions tab (in-app live viewer is the next iteration)."""
        def work():
            try:
                st = auth.remote_credentials_status()
                if not (st and st.get("has_pat") and st.get("has_key")):
                    self._unbusy()
                    self._err(strings.t("main_sync_creds_first"))
                    return
                ok, res = auth.enqueue_remote_run(
                    self.tool, self.project, self.plan_id, self.story_ids,
                    existing_mode=getattr(self, "existing_mode", "skip"),
                    output_lang=E.OUTPUT_LANG,
                    email_recipients=[e.strip() for e in
                                      (getattr(self, "emails", "") or "").split(",")
                                      if e.strip()])
                self._unbusy()
                if ok:
                    self._toast(strings.t("main_remote_queued", id=str(res)[:8]))
                    # BUG FIX: enqueue is just a fast INSERT (the DB trigger
                    # dispatches the workflow async) — _busy()/_unbusy() only
                    # covered that brief round-trip, so the Start Run button
                    # went right back to enabled while the actual remote job
                    # was still queued/running for minutes on GitHub. Nothing
                    # stopped tapping Start Run again for the identical
                    # selection — confirmed live: two remote_runs rows for
                    # the same project/plan/story 35 seconds apart, both
                    # dispatched. _run_active/_auto_running (the local-run
                    # busy flags _run_start_btn already checks) never applied
                    # to the remote path at all. _remote_run_active now gates
                    # the same button until this run reaches a terminal
                    # status, polled via get_remote_run_status (no live
                    # viewer yet — this is just enough to stop duplicates).
                    self._remote_run_active = True
                    self.ui_safe(self.render)
                    self._poll_remote_run_done(res)
                else:
                    self._err(strings.t("main_remote_queue_failed", err=res))
            except Exception as ex:
                self._unbusy()
                self._err(strings.t("main_remote_queue_failed", err=str(ex)[:120]))
        self._busy(strings.t("main_queuing"))
        self._bg(work)

    def _poll_remote_run_done(self, run_id):
        """Background poll (every 5s) until `run_id` reaches a terminal
        status (done/stopped/error) or a hard cap is hit, then clears
        _remote_run_active so Start Run is usable again. Capped at 30 min of
        polling (a stuck/lost row must never wedge the button shut forever —
        fails OPEN); a status check that errors out just retries next tick
        rather than counting toward the cap, since get_remote_run_status()
        already returns None on transient failures."""
        def work():
            import time as _t
            max_ticks = 360   # 360 * 5s = 30 min wall clock, regardless of
                              # how many individual lookups come back None —
                              # a network blip must count against the same
                              # 30-minute cap, not reset it, or a sustained
                              # outage would poll forever.
            for _ in range(max_ticks):
                _t.sleep(5)
                status = auth.get_remote_run_status(run_id)
                if status in ("done", "stopped", "error"):
                    break
            self._remote_run_active = False
            self.ui_safe(self.render)
        try:
            self._bg(work)
        except Exception:
            self._remote_run_active = False

    def _check_mobile_update(self, force=False):
        """Mobile-only UPDATE NOTICE (option 1 of the mobile update plan):
        Android can't self-update (an installed APK is immutable — which is
        why has_self_update() is gated off there), so instead of the desktop's
        zipball updater we compare the bundled VERSION against the repo's
        (E.check_for_update — the same source the desktop trusts) and offer
        the latest GitHub Release's APK for download; opening the downloaded
        APK updates in place (same package id). Fail-silent end to end: no
        network / no release / API hiccups must never disturb startup.

        Called at three moments now (was only once, at build): app build,
        straight after a successful sign-in (force=True), and on every nav
        switch. The guards below make that safe — it never pops over the login
        screen and never shows more than once per session."""
        if not platform_caps.is_mobile():
            return
        # (1) Never surface an update over the LOGIN screen. With biometrics ON,
        # __init__/_build run BEFORE sign-in, so the old single build-time call
        # fired while the fingerprint gate was still up and the user never saw
        # the popup — the whole reason this is now also triggered post-login.
        if auth.configured() and getattr(self, "user", None) is None:
            return
        # (2) Show at most once per session, so switching navs can drive the
        # check without re-popping the dialog every time.
        if getattr(self, "_mobile_update_shown", False):
            return
        # (3) Throttle the network hit — nav switches call this frequently.
        import time as _upd_t
        if not force and (_upd_t.time() - getattr(self, "_mobile_update_ts", 0)) < 60:
            return
        self._mobile_update_ts = _upd_t.time()

        def work():
            try:
                # MOBILE uses check_MOBILE_update (the published-APK version
                # marker), NOT check_for_update (the repo VERSION on main).
                # release.bat bumps VERSION ~10 min before the APK is built, so
                # keying off the repo version told users an update existed while
                # its APK didn't exist yet. The marker only advances once the
                # APK is actually attached — see engine.check_mobile_update.
                res = E.check_mobile_update() or {}
                if not res.get("update"):
                    return
                # Download the APK DIRECTLY. Reported live: tapping Download
                # only landed on the GitHub release page and the user still had
                # to hunt for the asset — they want the file to just download.
                # The rolling "android-apk" release now ALWAYS carries a stable,
                # world-downloadable asset named exactly qa-studio.apk
                # (build-apk.yml's "Publish to rolling Android release" step
                # re-uploads it with --clobber on every build — same URL every
                # time, same one index.html's Android button uses), so the old
                # 404/asset-drift worry that justified opening the page instead
                # is gone. Handing this direct URL to the OS launcher opens it
                # in the real browser (Chrome), whose download manager fetches
                # the .apk straight to Downloads for the user to tap-install.
                url = ("https://github.com/AhmedSayedRepo/QA-Studio/releases/"
                       "download/android-apk/qa-studio.apk")

                def _show():
                    remote_v = str(res.get("remote") or "")
                    local_v = str(res.get("local") or "")
                    # Version transition pill: your current build → the new one,
                    # so the update reads at a glance (muted "from" → green "to").
                    def _pill(text, ink, bg):
                        return ft.Container(
                            content=ft.Text(text, size=12.5,
                                            weight=ft.FontWeight.W_700, color=ink),
                            bgcolor=bg, border_radius=8,
                            padding=ft.Padding.symmetric(horizontal=10, vertical=4))
                    version_row = ft.Row(
                        [_pill(f"v{local_v}", T.INK_2, T.CARD_2),
                         ft.Icon(ft.Icons.ARROW_FORWARD_ROUNDED, size=16,
                                 color=T.INK_3),
                         _pill(f"v{remote_v}", T.GREEN, T.GREEN_SOFT)],
                        spacing=8, tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER)
                    # Header: brand-green badge + title + subtitle, matching the
                    # app's other themed dialog headers (show_dialog() supplies
                    # the card surface/border/scrim around this).
                    header = ft.Row([
                        ft.Container(
                            content=ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT_ROUNDED,
                                            size=22, color=T.GREEN),
                            width=42, height=42, bgcolor=T.GREEN_SOFT,
                            border_radius=12, alignment=ft.Alignment.CENTER),
                        # expand=True + no_wrap=False so the subtitle WRAPS
                        # inside the dialog instead of running off its right
                        # edge — on a phone "…is ready" was clipped mid-word.
                        # The Row must not be tight either, or it sizes to the
                        # text's unwrapped width and the Column never gets a
                        # bound to wrap against.
                        ft.Column([
                            ft.Text(strings.t("main_update_available"), size=16,
                                    weight=ft.FontWeight.BOLD, color=T.INK,
                                    no_wrap=False),
                            ft.Text(strings.t("main_update_ready"),
                                    size=11.5, color=T.INK_2, no_wrap=False),
                        ], spacing=1, tight=True, expand=True),
                    ], spacing=13,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER)
                    # In-app download progress — hidden until Download is tapped.
                    # NO browser, NO GitHub page: the .apk is streamed straight
                    # into the app and handed to Android's own installer via the
                    # OS open sheet, so the user never leaves QA Studio for a web
                    # page (reported live: "it shouldn't redirect to github").
                    dl_bar = ft.ProgressBar(value=0, visible=False, color=T.GREEN,
                                            bgcolor=T.CARD_2, border_radius=6)
                    dl_status = ft.Text("", size=11.5, color=T.INK_2, visible=False)
                    body = ft.Container(
                        content=ft.Column([
                            version_row,
                            ft.Text(
                                strings.t("au_msg_update_downloaded_desc"),
                                size=12.5, color=T.INK_2),
                            dl_bar, dl_status,
                        ], spacing=14, tight=True),
                        # Cap width so the card reads as a tidy panel on phones
                        # without overflowing the narrowest screens.
                        width=min(340, (self.page.width or 400) - 64))

                    def _start_download(e):
                        # Reveal the progress UI, disable the button, and stream
                        # the APK in the background — the dialog stays open so
                        # the user sees progress rather than being bounced out.
                        try:
                            e.control.disabled = True
                            e.control.update()
                        except Exception:
                            pass
                        dl_bar.visible = True
                        dl_status.visible = True
                        dl_status.value = "Starting download…"
                        try:
                            dl_bar.update(); dl_status.update()
                        except Exception:
                            pass
                        self._download_mobile_update(url, dl_status, dl_bar)

                    dlg = ft.AlertDialog(
                        modal=False, title=header, content=body,
                        actions=[
                            ghost_btn(strings.t("main_later"),
                                      on_click=lambda e: self._close_dialog()),
                            green_btn(strings.t("main_download_btn"), icon=ft.Icons.DOWNLOAD_ROUNDED,
                                      on_click=_start_download),
                        ])
                    self._show_dialog(dlg)

                # Flet 0.85's dialog API is a real stack (page.show_dialog),
                # not a single page.dialog slot — showing this while
                # onboarding is still up would layer on top of it instead of
                # replacing it (confirmed live: the update notice appeared
                # stacked over the "Welcome to QA Studio" card, its own
                # actions unreachable behind the modal onboarding barrier).
                # Wait for onboarding to close first; give up quietly after
                # 2 minutes (fail-soft, same posture as everything else in
                # this notice — a missed update popup once isn't fatal, the
                # next launch checks again).
                import time as _t
                waited = 0
                while getattr(self, "_onboarding_open", False) and waited < 120:
                    _t.sleep(1)
                    waited += 1
                if getattr(self, "_onboarding_open", False):
                    return
                # Re-check the session guards now that we're about to show —
                # the user may have signed out, or another trigger may have
                # already shown it, during the network round-trip.
                if getattr(self, "_mobile_update_shown", False):
                    return
                if auth.configured() and getattr(self, "user", None) is None:
                    return
                self._mobile_update_shown = True
                self.ui_safe(_show)
            except Exception:
                pass
        self._bg(work)

    def _download_mobile_update(self, url, status_text=None, bar=None):
        """Fetch the update APK straight into the app — NO browser, NO GitHub
        page — and hand it to Android's installer via the OS open sheet.
        A Flet app can't silently self-install (that needs the system package
        installer), but this keeps the whole flow in-app up to that final
        system prompt: stream the .apk to app storage with live progress, then
        reveal_export() (ft.Share) opens the OS sheet whose 'Package installer'
        updates in place. Fail-soft: any network/IO error just toasts."""
        import os

        def _set(msg, val=None):
            def _apply():
                try:
                    if status_text is not None:
                        status_text.value = msg
                        status_text.update()
                    if bar is not None:
                        bar.value = val
                        bar.update()
                except Exception:
                    pass
            self.ui_safe(_apply)

        def work():
            path = None
            try:
                import requests
                dest = os.environ.get("FLET_APP_STORAGE_DATA") or "/tmp"
                try:
                    os.makedirs(dest, exist_ok=True)
                except Exception:
                    pass
                path = os.path.join(dest, "qa-studio.apk")
                # stream=True + iter_content so a ~30-40 MB APK reports live
                # progress instead of blocking on one big read.
                with requests.get(url, stream=True, timeout=90, headers={
                        "Accept": "application/octet-stream"}) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("content-length") or 0)
                    done = 0
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=131072):
                            if not chunk:
                                continue
                            f.write(chunk)
                            done += len(chunk)
                            if total:
                                _set(f"Downloading… {int(done * 100 / total)}%",
                                     done / total)
                            else:
                                _set(f"Downloading… {done // 1024} KB", None)
                _set("Downloaded — opening installer…", 1.0)
                # Open the .apk DIRECTLY so Android resolves it to the package
                # installer. ft.Share (reveal_export) opens the SHARE SHEET
                # instead — an app chooser the user had to hunt through for
                # "Package installer" (reported as having to select the
                # download place). flet_open_file wraps open_filex, which
                # handles the FileProvider + intent; REQUEST_INSTALL_PACKAGES is
                # granted in build-apk.yml. Falls back to the share sheet if the
                # extension isn't in this build, so it degrades, never breaks.
                # NOTE: the system install confirmation and the one-time "Allow
                # from this source" grant remain — no app can bypass those.
                # SELF-DIAGNOSING: every failure path below is logged, because
                # the fallback (share sheet) looks IDENTICAL to the old
                # behaviour — so "still shows the chooser" could mean the
                # extension is missing from the bundle, or that open() was
                # refused. Silently falling back is what made this
                # indistinguishable; diagnostics.log now says which.
                def _diag(msg, exc=None):
                    try:
                        import diag_log
                        diag_log.log(f"update_install.{msg}",
                                     exc or Exception(msg))
                    except Exception:
                        pass

                def _share_fallback():
                    try:
                        import platform_caps as _pc
                        _pc.reveal_export(self.page, path)
                    except Exception:
                        pass

                _opened = False
                try:
                    import flet_open_file as _fof

                    async def _open_apk():
                        try:
                            svc = _fof.OpenFile()
                            self.page.services.append(svc)
                            self.page.update()
                            res = str(await svc.open(path) or "")
                            # open_filex returns "done" on success; anything
                            # else (noAppToOpen / permissionDenied / error)
                            # means the install intent didn't take, so fall
                            # back to the sheet rather than doing nothing.
                            if res != "done":
                                _diag(f"open_returned_{res or 'empty'}")
                                self.ui_safe(_share_fallback)
                        except Exception as ex:
                            _diag("open_raised", ex)
                            self.ui_safe(_share_fallback)
                    self.page.run_task(_open_apk)
                    _opened = True
                except Exception as ex:
                    # The extension isn't importable in this bundle — THE most
                    # likely reason the chooser still appears.
                    _diag("extension_import_failed", ex)
                    _opened = False
                if not _opened:
                    _share_fallback()
                self.ui_safe(lambda: (
                    self._close_dialog(),
                    self._toast(strings.t("main_update_downloaded"))))
            except Exception as ex:
                self.ui_safe(lambda m=str(ex)[:120]:
                             self._err(strings.t("main_update_dl_failed", err=m)))
        self._bg(work)

    def _mobile_download_popup(self, path, label=None):
        """Mobile export DELIVERY. Desktop exports 'reveal in Explorer'; a phone
        has no user-visible app folder, so a file the app just wrote is
        invisible until it's handed out. This shows a themed popup naming the
        generated file with a Download button that opens the OS save/share
        sheet (platform_caps.reveal_export → ft.Share) so the user can save it
        to Downloads or send it on — reported live: exports 'should open a
        popup to accept + start download the exported file, not like desktop'."""
        import os
        try:
            name = os.path.basename(path) or "export"
        except Exception:
            name = "export"
        try:
            sz = os.path.getsize(path)
            size_str = (f"{sz / 1048576:.1f} MB" if sz >= 1048576
                        else f"{max(1, sz // 1024)} KB")
        except Exception:
            size_str = ""
        ext = (os.path.splitext(name)[1].lstrip(".").upper() or "FILE")

        def _start(e):
            try:
                import platform_caps
                platform_caps.reveal_export(self.page, path)
            except Exception:
                pass
            self._close_dialog()
            self._toast(strings.t("main_opening_save"))

        header = ft.Row([
            ft.Container(
                content=ft.Icon(ft.Icons.FILE_DOWNLOAD_OUTLINED, size=22,
                                color=T.GREEN),
                width=42, height=42, bgcolor=T.GREEN_SOFT,
                border_radius=12, alignment=ft.Alignment.CENTER),
            # Same wrap fix as the update dialog's header: expand + no_wrap so a
            # long label can't run off the dialog's right edge on a phone.
            ft.Column([
                ft.Text(strings.t("main_export_ready"), size=16, weight=ft.FontWeight.BOLD,
                        color=T.INK, no_wrap=False),
                ft.Text(label or "Save or send your file", size=11.5,
                        color=T.INK_2, no_wrap=False),
            ], spacing=1, tight=True, expand=True),
        ], spacing=13,
            vertical_alignment=ft.CrossAxisAlignment.CENTER)
        # File chip: format badge + name + size, on the themed card surface.
        file_chip = ft.Container(
            content=ft.Row([
                ft.Container(
                    content=ft.Text(ext, size=10, weight=ft.FontWeight.W_800,
                                    color=T.VIOLET_INK),
                    bgcolor=T.VIOLET_SOFT, border_radius=6,
                    padding=ft.Padding.symmetric(horizontal=7, vertical=3)),
                ft.Column([
                    ft.Text(name, size=12.5, weight=ft.FontWeight.W_700,
                            color=T.INK, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(size_str, size=10.5, color=T.INK_3),
                ], spacing=0, tight=True, expand=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=T.CARD_2, border_radius=10,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10))
        body = ft.Container(
            content=ft.Column([file_chip], spacing=12, tight=True),
            width=min(340, (self.page.width or 400) - 64))
        dlg = ft.AlertDialog(
            modal=False, title=header, content=body,
            actions=[
                ghost_btn(strings.t("main_close"), on_click=lambda e: self._close_dialog()),
                green_btn(strings.t("main_download_btn"), icon=ft.Icons.DOWNLOAD_ROUNDED,
                          on_click=_start),
            ])
        self._show_dialog(dlg)

    def _sync_remote_creds(self, status_ctl=None):
        """Settings → Remote runs: push the credentials THIS app is currently
        using (Azure org/PAT + the active AI provider's key/model + Gmail app
        password/sender) to the per-user Supabase vault (rpc
        set_my_credentials), so remote runs — GitHub Actions worker, later
        the mobile app — execute AS this user, AND can email the same report
        the desktop's local runs already send. No new form: the values were
        already entered in Setup; this is a one-click sync of exactly what
        the app is connected with. Gmail is OPTIONAL here (unlike PAT/AI key)
        — remote runs still work without it, they just won't be able to
        email a report, same as a local run with no Gmail App Password set."""
        def work():
            try:
                prov = E.AI_PROVIDER
                cfg = E.AI_CONFIG.get(prov) or {}
                key = (cfg.get("api_key") or "").strip()
                model = ((cfg.get("deployment") if prov == "azure_openai"
                          else cfg.get("model")) or "").strip()
                pat = (E.AZURE_PAT or "").strip()
                org = (E.AZURE_ORG or "").strip()
                if not pat or not key:
                    self.ui_safe(lambda: self._err(
                        strings.t("au_msg_connect_first_sync")))
                    return
                ok, msg = auth.sync_remote_credentials(
                    org, pat, prov, key, model,
                    gmail_sender=(E.GMAIL_SENDER or "").strip(),
                    gmail_sender_name=(E.GMAIL_SENDER_NAME or "").strip(),
                    gmail_app_pass=(E.GMAIL_APP_PASS or "").strip())
                st = auth.remote_credentials_status() if ok else None
                def _apply():
                    (self._toast if ok else self._err)(msg)
                    if status_ctl is not None and st:
                        status_ctl.value = (
                            f"Synced ✓ · {E.T_disp(st.get('ai_provider') or '')}"
                            + (f" · {st.get('ai_model')}" if st.get("ai_model") else "")
                            + f" · PAT {'✓' if st.get('has_pat') else '—'}"
                            + f" · AI key {'✓' if st.get('has_key') else '—'}"
                            + f" · Gmail {'✓' if st.get('has_gmail') else '—'}")
                        status_ctl.color = T.GREEN
                        try:
                            status_ctl.update()
                        except Exception:
                            pass
                self.ui_safe(_apply)
            except Exception as ex:
                self.ui_safe(lambda m=str(ex)[:120]: self._err(strings.t("main_sync_failed", err=m)))
        self._bg(work)

    def _auto_project_dir(self):
        """The chosen folder IS the project home and the git repo we push from.
        Use it exactly as given (created if missing) — NO nesting, so the Maven
        project lands right next to .git and 'Push to Git' pushes this folder."""
        import os as _os
        lp = (self.auto_local_path or "").strip()
        if not lp:
            return None
        return _os.path.normpath(lp)

    def _ask_reeval(self, new_ids, grew_ids, done_ids, on_choice):
        """Confirmation shown when some selected stories were already generated.
        Default-safe: keep existing methods; re-eval only on explicit choice."""
        already = list(grew_ids or []) + list(done_ids or [])
        bits = [f"{len(already)} selected story(ies) already have generated tests"]
        if grew_ids:
            bits.append(f"{len(grew_ids)} of them have new test cases to add")
        if new_ids:
            bits.append(f"{len(new_ids)} brand-new story(ies) will be generated regardless")
        msg = (". ".join(bits) + ".\n\nKeep the existing methods and only add the new "
               "test cases, or re-evaluate the already-generated stories from scratch "
               "with AI (this REPLACES their current methods)?")
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Container(ft.Icon(ft.Icons.HISTORY, size=18, color=T.VIOLET),
                             width=34, height=34, bgcolor=T.VIOLET_SOFT, border_radius=9,
                             alignment=ft.Alignment.CENTER),
                ft.Text(strings.t("main_existing_found"), size=16,
                        weight=ft.FontWeight.W_800, color=T.INK)], spacing=10),
            content=ft.Container(width=430, content=ft.Text(
                msg, size=13, color=T.INK_2, weight=ft.FontWeight.W_500)),
            actions=[
                ghost_btn(strings.t("main_cancel"), on_click=lambda e: (on_choice("cancel"), self._close_dialog())),
                green_btn(strings.t("main_keep_add_new"), on_click=lambda e: (on_choice("keep"), self._close_dialog())),
                danger_btn(strings.t("main_reeval_ai"), on_click=lambda e: (on_choice("reeval"), self._close_dialog())),
            ],
            actions_alignment=ft.MainAxisAlignment.END)
        self._show_dialog(dlg)

    def _start_automation(self):
        # Guard like the Regression/Sprint plans: don't run two heavy generators at
        # once (GIL contention freezes everything), and don't double-start.
        if self._auto_running:
            self._toast(strings.t("main_auto_running"))
            return
        if (getattr(self, "_reg_busy", False) or getattr(self, "_cp_busy", False)
                or getattr(self, "_cp_stories_loading", False)):
            self._toast(strings.t("main_plan_generating"))
            return
        if not (self._auto_selected and self.project):
            self._toast(strings.t("au_msg_pick_plan_stories"))
            return
        # Field-level validation: red borders + inline helpers on the required
        # fields, while keeping the toast (per the rest of the app's pattern).
        self._auto_invalid = set()
        self._auto_invalid_msgs = {}
        def _mark(attr, msg):
            self._auto_invalid.add(attr)
            self._auto_invalid_msgs[attr] = msg
        if not self.auto_site_url.strip():
            _mark("auto_site_url", "Enter the target site URL.")
        if not (self.auto_git_url or "").strip():
            _mark("auto_git_url", "Enter the Git repository URL.")
        if not (self.auto_git_token or "").strip():
            _mark("auto_git_token", "A Git access token (PAT) is required to push.")
        if not self._auto_project_dir():
            _mark("auto_local_path",
                  "Set the project folder — it's the home we push from.")
        if self._auto_invalid:
            first = next(iter(self._auto_invalid))
            self._toast(self._auto_invalid_msgs[first])
            self.render()
            return
        self._save_git_creds()
        self._auto_running = True
        self._auto_stop = False
        self._auto_paused = False
        try: E.clear_stop()
        except Exception: pass
        try:
            if hasattr(self.page, "window") and self.page.window is not None:
                self.page.window.prevent_close = True
                self.page.update()
        except Exception:
            pass
        self._auto_built = False
        self._auto_log = []
        self._reset_auto_retry_state()
        self._auto_todo = 0          # live TODO tally, reset for this run
        self._auto_skipped = 0       # live duplicate-skip tally, reset for this run
        self._auto_run_start = time.time()
        self.render()

        def cb(msg, tone="dim"):
            self._auto_logmsg(msg, tone)

        def work():
            # Pre-declared so the report email (sent on both the success path
            # and the except/failure path below) always has something to read,
            # even if an exception hits before these are computed for real.
            stories_payload = []
            total_tc = 0
            total_steps = 0
            walk_payload = []

            def _send_auto_report(failed=False, stopped=False):
                to_raw = (getattr(self, "_auto_email_to", "") or "").strip()
                if not to_raw:
                    return
                if not E.ensure_sender_creds():
                    cb("No report sent — no Gmail App Password is configured. An "
                       "admin can set it in Setup → Connection (shared org-wide).",
                       "warn")
                    return
                import re as _re
                to = [a.strip() for a in _re.split(r"[,\s;]+", to_raw) if a.strip()]
                if not to:
                    return
                _tgt = getattr(self, "_auto_target", "selenium")
                _secs = time.time() - getattr(self, "_auto_run_start", time.time())
                stats = {
                    "Stories": len(stories_payload),
                    "Test cases": total_tc,
                    "Skipped": getattr(self, "_auto_skipped", 0),
                    "Self-healed": getattr(self, "_auto_todo", 0),
                    "Time": E._fmt_secs(_secs),
                }
                if failed:
                    summary = "Automation failed"
                elif stopped:
                    summary = "Stopped early"
                elif walk_payload:
                    _n = len(walk_payload)
                    summary = f"{_n} stor{'y' if _n == 1 else 'ies'} generated"
                else:
                    summary = "Nothing new — existing tests kept as-is"
                email_log = [{"msg": ln.get("msg", ""), "tone": ln.get("tone", "dim")}
                            for ln in getattr(self, "_auto_log", []) if ln.get("msg")]
                try:
                    html = E.build_automation_report_email(
                        _tgt, summary, stats,
                        project_dir=getattr(self, "_auto_out_dir", None) or self._auto_project_dir(),
                        git_url=(self.auto_git_url or "").strip() or None,
                        git_branch=(self.auto_git_branch or "").strip() or None,
                        log_lines=email_log, org=E.AZURE_ORG, project=self.project,
                        failed=failed, stopped=stopped)
                    ok, err = E.send_report(to, "QA Studio — Automation report", html)
                except Exception as _ex:
                    ok, err = False, str(_ex)[:150]
                if ok:
                    cb(f"Report emailed to {to_raw}", "ok")
                else:
                    cb(f"Report not emailed — {err}", "warn")

            try:
                # 1) connect + fetch stories with their test cases/steps.
                # Compute the seam FIRST: `generation_ops` returns None on Azure
                # (meaning "use engine's own Azure-SDK functions"), an injected
                # tracker ops object otherwise. ONLY the Azure path needs the
                # Azure SDK connect — connect_azure_sdk() raises on a Jira/Xray
                # connection (no AZURE_ORG via _require_org), which used to crash
                # the whole automation run at step 1. The Azure path stays
                # byte-for-byte what it was.
                _ops = backend_setup.generation_ops(self)
                if _ops is None:
                    cb("Connecting to Azure DevOps...", "dim")
                    E.connect_azure_sdk(self.project)
                else:
                    cb(f"Connecting to {backend_setup.story_store_label(self)}…", "dim")
                # Automation's stories come from its own multi-plan "Source &
                # stories" picker (app._auto_selected), each tagged with the
                # plan_id it was picked from — unlike the old single self.plan_id
                # flow, suites must be discovered PER PLAN and the maps merged.
                _auto_sel = list(self._auto_selected)
                _story_ids = [s["id"] for s in _auto_sel]
                _story_plan = {s["id"]: s.get("plan_id") for s in _auto_sel}
                _by_plan = {}
                for s in _auto_sel:
                    _by_plan.setdefault(s.get("plan_id"), set()).add(s["id"])
                smap = {}
                for _pid, _sids in _by_plan.items():
                    if not _pid:
                        continue
                    try:
                        if _ops is None:
                            smap.update(E.discover_suites_for_stories(
                                self.project, _pid, _sids, create_missing=False))
                        else:
                            smap.update(_ops.discover_suites(
                                self.project, _pid, _sids, create_missing=False))
                    except Exception as _ex:
                        cb(f"Couldn't read suites for plan {_pid}: {str(_ex)[:120]}", "warn")
                stories = (E.fetch_stories(_story_ids) if _ops is None
                           else _ops.fetch_stories(_story_ids))
                # Fetching every story's suite test cases + every case's steps
                # (Phases A/B below) makes NO cb() calls while it runs — on a
                # large selection that can take a while with nothing on screen
                # to show for it. This line is the only thing standing between
                # "Connecting..." and the first real progress line, so the
                # screen doesn't look stuck.
                cb(f"Fetching test cases and steps for {len(stories)} "
                   f"stor{'y' if len(stories) == 1 else 'ies'}...", "dim")

                import concurrent.futures as _cf
                stories_payload = []
                total_tc = 0
                total_steps = 0

                # Phase A: each story's suite test cases, fetched CONCURRENTLY.
                def _story_tcs(s):
                    suite_id = smap.get(s.id)
                    out = []
                    if suite_id:
                        try:
                            # Seam-routed: Azure uses the engine fn; a tracker
                            # backend uses ops.cases_for_suite. Both return the
                            # same [{"workItem":{"id","name"}}] shape.
                            _cases = (E.fetch_test_cases_for_suite(
                                          self.project, _story_plan.get(s.id), suite_id)
                                      if _ops is None else
                                      _ops.cases_for_suite(
                                          self.project, _story_plan.get(s.id), suite_id))
                            for tc in _cases:
                                wi = tc.get("workItem", {})
                                tcid = wi.get("id")
                                if tcid:
                                    out.append({"id": tcid, "title": wi.get("name", "")})
                        except Exception:
                            pass
                    return s.id, out
                story_tcs = {}
                if stories:
                    with _cf.ThreadPoolExecutor(max_workers=min(8, len(stories))) as _ex:
                        for _sid, _out in _ex.map(_story_tcs, stories):
                            story_tcs[_sid] = _out
                if self._auto_stop:
                    cb("Stopped before scraping.", "warn"); return

                # Phase B: steps for every test case, fetched CONCURRENTLY (was a
                # serial fetch_test_case_steps per test case — slow on big sets).
                _all_tcids = [tc["id"] for lst in story_tcs.values() for tc in lst]
                steps_map = {}
                title_map = {}
                def _detail(tcid):
                    # title + steps for one case. Seam-routed: Azure fetches both
                    # in one work-item call; a tracker backend reads steps via
                    # ops.case_detail (title comes from Phase A's cases_for_suite,
                    # so ops.case_detail returns "" for it and the caller falls
                    # back to title_map). Same (title, [{"index","action",
                    # "expected"}]) shape either way.
                    try:
                        t, st = (E.fetch_test_case_detail(tcid) if _ops is None
                                 else _ops.case_detail(tcid))
                        return tcid, t, st
                    except Exception:
                        return tcid, "", []
                if _all_tcids:
                    with _cf.ThreadPoolExecutor(max_workers=min(16, len(_all_tcids))) as _ex:
                        for _tcid, _title, _st in _ex.map(_detail, _all_tcids):
                            steps_map[_tcid] = _st
                            title_map[_tcid] = _title

                # Announce the dedup pass BEFORE the assembly loop below, since
                # that loop is where dedupe_case_list actually runs (per story)
                # — without this, the first thing the user sees after the long
                # silent fetch above is a "skipping duplicate" line with no
                # lead-in explaining what's happening.
                cb("Checking for duplicate test cases...", "dim")

                # Assemble each story's test-case list first (fast, no AI) in
                # original story order, THEN run the (slow) AI dedup pass with
                # up to _AUTO_DEDUP_WORKERS concurrent workers — same
                # dedup-check parallelization already applied to run_steps/
                # run_titles this session. Results are re-applied in ORIGINAL
                # story order below (not completion order) so stories_payload
                # and the "Loaded N stories…" summary stay deterministic.
                _story_prep = []
                for s in stories:
                    if self._auto_stop:
                        cb("Stopped before scraping.", "warn"); return
                    sid = s.id
                    title = s.fields.get("System.Title", "")
                    criteria = s.fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")
                    # Announce which story is being checked/prepped BEFORE its
                    # dedup pass runs — with multiple stories selected this is
                    # the only way to tell which one is currently in progress,
                    # same "▸ Story N → suite M · Title" format as the Run log.
                    _suite_id = smap.get(sid)
                    cb(f"Story {sid}" + (f" → suite {_suite_id}" if _suite_id else "")
                       + f" · {title}", "story")
                    tcs = []
                    for tc in story_tcs.get(sid, []):
                        steps = steps_map.get(tc["id"], [])
                        total_steps += len(steps)
                        tcs.append({"id": tc["id"],
                                    "title": (tc.get("title") or title_map.get(tc["id"], "")),
                                    "steps": steps})
                    _story_prep.append((sid, title, criteria, tcs))

                # Duplicate test cases already sitting in the suite (same
                # scenario, different wording) would otherwise each get
                # sequenced and get their own generated script. Skip-only —
                # nothing is deleted from Azure DevOps here, the most
                # complete case of each duplicate set is just the only one
                # carried forward into automation.
                total_skipped = 0
                _AUTO_DEDUP_WORKERS = 2
                _dedup_results = {}   # sid -> deduped tcs (only set when len(tcs) > 1)
                with _cf.ThreadPoolExecutor(max_workers=_AUTO_DEDUP_WORKERS) as _ex:
                    _futs = {}
                    for sid, _t, _c, tcs in _story_prep:
                        if self._auto_stop:
                            break
                        if len(tcs) > 1:
                            fut = _ex.submit(E.dedupe_case_list, tcs, log=cb,
                                             should_stop=lambda: self._auto_stop,
                                             story_title=_t)
                            _futs[fut] = sid
                    for fut in _cf.as_completed(_futs):
                        sid = _futs[fut]
                        try:
                            _dedup_results[sid] = fut.result()
                        except Exception as e:
                            cb(f"Dedup check failed for story {sid}: {e} "
                               f"— keeping all its test case(s).", "warn")

                if self._auto_stop:
                    cb("Stopped before scraping.", "warn"); return

                for sid, title, criteria, tcs in _story_prep:
                    _before = len(tcs)
                    final_tcs = _dedup_results.get(sid, tcs)
                    if _before > 1:
                        total_skipped += _before - len(final_tcs)
                        cb(f"SKIPPED_LIVE: {total_skipped}", "dim")
                    total_tc += len(final_tcs)
                    stories_payload.append({
                        "story": {"id": sid, "title": title, "criteria": criteria},
                        "test_cases": final_tcs,
                    })
                cb(f"Loaded {len(stories_payload)} story/stories - {total_tc} test case(s) - "
                   f"{total_steps} step(s) from {backend_setup.story_store_label(self)}.", "ok")

                if self._auto_stop:
                    cb("Stopped before scraping.", "warn"); return

                # 2) decide what needs (re)generating vs what we keep
                project_dir = self._auto_project_dir()
                # Actually create the output folder if it doesn't exist yet (the
                # docstring long claimed "created if missing" but nothing did —
                # classify_selection then read a non-existent dir). makedirs is
                # recursive, so a brand-new nested path is created in full.
                try:
                    if project_dir:
                        os.makedirs(project_dir, exist_ok=True)
                except Exception as _mk_ex:
                    cb(f"Could not create output folder {project_dir}: {_mk_ex}", "err")
                    return
                # Show exactly where we're writing — the 'Save project to folder'
                # value. Makes it obvious if it's not the folder you expected
                # (e.g. an old path left in the field).
                cb(f"Output folder: {project_dir}", "info")
                new_ids, grew_ids, done_ids, new_tcs = E.classify_selection(
                    project_dir, stories_payload)
                reeval = set()
                if grew_ids or done_ids:
                    ev = threading.Event()
                    decision = {"reeval": set(), "cancel": False}
                    def _choose(kind):
                        if kind == "reeval":
                            decision["reeval"] = set(grew_ids) | set(done_ids)
                        elif kind == "cancel":
                            decision["cancel"] = True
                        ev.set()
                    self.ui_safe(lambda: self._ask_reeval(new_ids, grew_ids, done_ids, _choose))
                    ev.wait()
                    if decision["cancel"]:
                        cb("Cancelled - existing tests untouched.", "warn"); return
                    reeval = decision["reeval"]

                # Which stories to (re)generate. The deterministic emitter REWRITES
                # the whole spec/class from the cases it's given, so a 'grew' story
                # must pass ALL its cases (old + new) — otherwise only the new ones
                # survive and the existing cases are dropped. Kept (done) stories are
                # skipped entirely. Difference between the two choices: 'Keep & add
                # new' leaves DONE stories untouched; 'Re-evaluate with AI' also
                # regenerates them.
                walk_payload = []
                for sp in stories_payload:
                    sid = str(sp["story"]["id"])
                    if sid in new_ids or sid in reeval or sid in grew_ids:
                        walk_payload.append(sp)

                # 2) Generate a SELF-HEALING project from the stories — no browser.
                #    Locators are seeded (stable where known, // TODO otherwise) and
                #    resolved at RUNTIME by the generated framework via the Anthropic
                #    API when a seed fails. Cases are validated + ordered into a
                #    logical sequence (logged-out negatives/validation/login-page →
                #    successful login → app cases) so we never log out to re-test.
                # Login URL seeds the generated project's config so its login step
                # targets the right page. Credentials are NOT collected here — the
                # generated tests read APP_USER / APP_PASS from their env at run time.
                _login_url = self.auto_login_url.strip() or self.auto_site_url.strip()
                login = {"url": _login_url} if _login_url else None
                # Generate ONLY what the keep/re-evaluate choice said to (walk_payload):
                # brand-new stories, the new test cases of "grew" stories, and any the
                # user chose to re-evaluate. "Done" stories are kept untouched. (Was
                # passing the full stories_payload, so the choice had no effect.)
                if walk_payload:
                    cb(f"Generating self-healing automation for {len(walk_payload)} "
                       f"story(ies) (no browser)…", "info")
                    E.generate_and_push_selfhealing(
                        project_dir, walk_payload, self.auto_site_url.strip(),
                        login=login, cb=cb, should_stop=lambda: self._auto_stop,
                        on_error=self._auto_on_ai_error, gate=self._auto_gate,
                        target=getattr(self, "_auto_target", "selenium"))
                else:
                    cb("Nothing new to generate — existing tests are kept as-is.", "ok")

                self._auto_out_dir = project_dir
                self.creds["auto_local_path"] = (self.auto_local_path or "").strip()
                self.creds["auto_target"] = getattr(self, "_auto_target", "selenium")
                try:
                    store.save(self.creds)
                except Exception:
                    pass
                if self._auto_stop:
                    cb("Stopped.", "warn"); return
                self._auto_built = True
                _hprov = T.disp_name(getattr(self, "_provider_choice", "") or "")
                _tgt = getattr(self, "_auto_target", "selenium")
                if _tgt == "selenium":
                    cb(f"Done — review the activity, then Push to Git. In IntelliJ set "
                       f"QA_AI_API_KEY (your {_hprov} key), APP_USER and APP_PASS, then run "
                       f"`mvn test`. The report lands in target/surefire-reports; self-healing "
                       f"resolves the TODO locators at runtime.", "ok")
                else:
                    _setup = ("npm install && npx playwright install"
                              if _tgt == "playwright" else "npm install")
                    _rep = ("npx playwright show-report" if _tgt == "playwright"
                            else "open cypress/reports/index.html")
                    cb(f"Done — review the activity, then Push to Git. Run `{_setup}`, put "
                       f"QA_AI_API_KEY (your {_hprov} key), APP_USER and APP_PASS in .env, then "
                       f"`npm test`. Self-healing resolves the TODO locators at runtime; open the "
                       f"report with `{_rep}`.", "ok")
                _send_auto_report(stopped=bool(self._auto_stop))
            except Exception as ex:
                cb(f"Automation failed: {str(ex)[:200]}", "err")
                _send_auto_report(failed=True)
            finally:
                self._auto_running = False
                self._auto_stop = False
                try:
                    if hasattr(self.page, "window") and self.page.window is not None:
                        self.page.window.prevent_close = False
                except Exception:
                    pass
                self._auto_paused = False
                self.ui_safe(self.render)

        self._bg(work)

    def _set_auto_local_path(self, p):
        if not p:
            return
        self.auto_local_path = p
        try:
            self._save_git_creds()
        except Exception:
            pass
        self.ui_safe(self.render)

    def _browse_auto_folder(self, e=None):
        """Choose the automation project folder through Flet's shared picker."""
        import image_assets
        image_assets.choose_directory(
            self, "Select the automation project folder", self._set_auto_local_path,
            lambda: self._toast(strings.t("au_msg_no_folder_picker")))

    def _push_automation(self):
        import os as _os
        proj = self._auto_project_dir()
        if not proj or not _os.path.isdir(proj):
            self._toast(strings.t("main_gen_scripts_first"))
            return
        if not self.auto_git_url.strip() or not self.auto_git_token.strip():
            self._toast(strings.t("main_enter_git"))
            return
        _ok_url, _url_msg = E._validate_remote_url(self.auto_git_url.strip())
        if not _ok_url:
            self._toast(_url_msg)
            self._auto_logmsg(_url_msg, "err")
            return
        self._save_git_creds()
        self._auto_running = True
        # Scoped refresh (buttons + spinner) instead of a full render() — render()
        # rebuilds app._auto_log_col from scratch every time, which resets the
        # Activity log's scroll position. This used to fire at the start AND end
        # of every push, plus again on a Force-push retry, so the log rail
        # visibly jumped/scrolled-to-top several times over one push. Falls back
        # to a full render() only if the scoped refs aren't there yet (shouldn't
        # happen — Push is only clickable once the screen has rendered once).
        if not automation._refresh_auto_state(self):
            self.render()

        def cb(msg, tone="dim"):
            self._auto_logmsg(msg, tone)

        def _sync_ui():
            if not automation._refresh_auto_state(self):
                self.render()

        def work(force=False):
            try:
                cb(f"Pushing from {proj}", "dim")
                ok, msg = E.push_to_git(proj, self.auto_git_url.strip(),
                                        self.auto_git_token.strip(),
                                        branch=(self.auto_git_branch.strip() or "main"),
                                        cb=cb, force=force)
                if ok:
                    cb("Pushed. Open/refresh the repo in IntelliJ to sync.", "ok")
                elif msg.startswith("rejected:"):
                    # Non-fast-forward: don't just dump the raw git hint text —
                    # log one clear sentence and offer a Force-push retry.
                    cb(msg[len("rejected:"):].strip(), "err")
                    def _retry_forced():
                        self._auto_running = True
                        self.ui_safe(_sync_ui)
                        self._bg(lambda: work(force=True))
                    self.ui_safe(lambda: self._confirm(
                        strings.t("au_push_rejected"),
                        strings.t("au_push_rejected_body"),
                        on_yes=_retry_forced,
                        yes_label=strings.t("main_force_push"), danger=True,
                        icon=ft.Icons.WARNING_AMBER_ROUNDED))
                else:
                    cb(f"Push failed - {msg}", "err")
            except Exception as ex:
                cb(f"Push error: {str(ex)[:200]}", "err")
            finally:
                self._auto_running = False
                self.ui_safe(_sync_ui)

        self._bg(work)



# ═══════════════════════════════════════════════════════════════════════════════
def main(page: ft.Page):
    # Record the runtime platform once (mobile Phase 0) — everything that is
    # desktop-/Windows-only asks platform_caps instead of assuming.
    try:
        platform_caps.set_flet_platform(getattr(page, "platform", ""))
    except Exception:
        pass
    # Flet needs direct font-FILE urls (.ttf), not Google's CSS endpoint — the old
    # css2?family=… links silently failed and the UI fell back to Roboto. These are
    # the variable .ttf files from the google/fonts repo (jsDelivr mirror).
    page.fonts = {
        T.F_UI: "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/plusjakartasans/PlusJakartaSans%5Bwght%5D.ttf",
        T.F_MONO: "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
        T.F_AR: "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/ibmplexsansarabic/IBMPlexSansArabic-Regular.ttf",
        # Space Grotesk — the display font used by the (former WebView2) login.
        "Space Grotesk": "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/spacegrotesk/SpaceGrotesk%5Bwght%5D.ttf",
    }
    try:
        page.theme = ft.Theme(font_family=T.F_UI, color_scheme_seed=T.VIOLET)
        page.dark_theme = ft.Theme(font_family=T.F_UI, color_scheme_seed=T.VIOLET)
    except Exception:
        page.theme = ft.Theme(font_family=T.F_UI)
    QAStudio(page)


def _launch(view=None):
    if hasattr(ft, "run"):
        return ft.run(main, view=view) if view is not None else ft.run(main)
    return ft.app(target=main, view=view) if view is not None else ft.app(target=main)


if __name__ == "__main__":
    import os
    # Force web mode explicitly with:  set WEB_MODE=1
    _web = os.environ.get("WEB_MODE", "").strip() in ("1", "true", "yes")
    if _web:
        _launch(view=ft.AppView.WEB_BROWSER)
    else:
        # Native desktop window. Login is handled entirely inside the Flet app by
        # the sign-in gate (_login_gate) — there is no WebView2/pywebview anymore,
        # so no embedded browser window can freeze.
        try:
            _launch()
        except Exception as _e:
            print("\n" + "="*64)
            print("Desktop launch failed. Details below:")
            traceback.print_exc()
            print("="*64)
