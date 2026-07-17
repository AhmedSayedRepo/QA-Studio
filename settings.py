"""settings.py — Settings screen.

Extracted from main.py (Step-3 modular refactor). screen(app) builds the
Appearance / Data & Diagnostics / Help & Reset cards; the segment builders
(app._lang_segment, app._tool_segment) and action handlers stay on the app.
"""
import flet as ft
import theme as T
import regression
import platform_caps
from ui import _ic, card, ghost_btn, danger_btn


def screen(app):
        ro = bool(getattr(app, "readonly", False))   # no 'act.settings' → read-only

        def srow(title, desc, control):
            title_col = ft.Column([
                ft.Text(title, size=13.5, weight=ft.FontWeight.BOLD, color=T.INK),
                ft.Text(desc, size=12, color=T.INK_3, weight=ft.FontWeight.W_500),
            ], spacing=2, expand=True)
            if platform_caps.is_mobile():
                # A wide, non-shrinking control (e.g. the idle-auto-logout
                # 5-option segmented row: Off/5m/15m/30m/60m) left title_col's
                # expand=True share so close to zero on a ~390px phone that
                # its Text wrapped one CHARACTER per line ("I d l e a u t o -
                # l o g o u t…", confirmed live) rather than a normal word
                # wrap — Flutter still tries to lay text out in whatever
                # sliver of width is left, and a sliver narrower than one
                # glyph forces that. Stack instead of sitting side by side on
                # mobile, same as every other "too much crammed in one Row"
                # fix this session — title/desc get the full row width, the
                # control gets its own line below with room to wrap/scroll.
                title_col.expand = False
                return ft.Container(
                    ft.Column([title_col, ft.Container(control, padding=ft.Padding.only(top=8))],
                              spacing=0),
                    padding=ft.Padding.symmetric(vertical=13))
            return ft.Container(
                ft.Row([
                    title_col,
                    control,
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=16),
                padding=ft.Padding.symmetric(vertical=13))

        def divider():
            return ft.Container(height=1, bgcolor=T.BORDER_2)

        def _theme_seg():
            def seg(label, icon, mode):
                sel = (T.MODE == mode)
                return ft.Container(
                    ft.Row([ft.Icon(icon, size=15,
                                    color=(T.VIOLET_INK if sel else T.INK_2)),
                            ft.Text(label, size=12, weight=ft.FontWeight.BOLD,
                                    color=(T.VIOLET_INK if sel else T.INK_2))],
                           spacing=7, tight=True),
                    height=34, alignment=ft.Alignment.CENTER,
                    padding=ft.Padding.symmetric(vertical=0, horizontal=16),
                    bgcolor=(T.VIOLET_SOFT if sel else None), border_radius=T.R_SM,
                    border=ft.Border.all(1, T.VIOLET if sel else ft.Colors.TRANSPARENT),
                    on_click=(None if ro else (lambda e, m=mode: (None if T.MODE == m
                                                                  else app._toggle_theme()))))
            return ft.Container(
                ft.Row([seg("Light", ft.Icons.LIGHT_MODE_OUTLINED, "light"),
                        seg("Dark", ft.Icons.DARK_MODE_OUTLINED, "dark")],
                       spacing=4, tight=True),
                padding=4, bgcolor=T.CARD_2, border_radius=T.R,
                border=ft.Border.all(1, T.BORDER))

        appearance = card(ft.Column([
            ft.Text("APPEARANCE", size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow("Theme", "Light is the default; dark is easier on the eyes at night.",
                 _theme_seg()),
            divider(),
            srow("Output language", "Default language for newly generated test cases.",
                 app._lang_segment()),
            divider(),
            srow("What to generate",
                 "Default generator for new runs — test-case titles or full steps.",
                 app._tool_segment(compact=True)),
        ], spacing=0))

        perf_switch = ft.Switch(value=regression.perf_on(), active_color=T.VIOLET,
                                disabled=ro,
                                on_change=(None if ro else
                                           (lambda e: app._set_perf(e.control.value))))
        data = card(ft.Column([
            ft.Text("DATA & DIAGNOSTICS", size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow("Regression & sprint caches",
                 "Cached Azure data speeds up re-generating plans. Clear it if "
                 "stories or test cases look out of date.",
                 ghost_btn("Clear caches", icon=_ic("CLEANING_SERVICES_OUTLINED","DELETE_OUTLINE"),
                           on_click=lambda e: app._clear_caches())),
            divider(),
            srow("Performance logging",
                 "Append timing diagnostics to qa_perf.log. Leave on if I'm helping "
                 "you troubleshoot speed.",
                 perf_switch),
        ], spacing=0))

        # ── Security (Admins only) — idle auto-logout policy ──
        security = None
        if app._is_admin():
            cur = app._idle_minutes()

            def _idle_seg():
                def opt(label, mins):
                    sel = (cur == mins)
                    return ft.Container(
                        ft.Text(label, size=12, weight=ft.FontWeight.BOLD,
                                color=(T.VIOLET_INK if sel else T.INK_2)),
                        height=34, alignment=ft.Alignment.CENTER,
                        padding=ft.Padding.symmetric(vertical=0, horizontal=14),
                        bgcolor=(T.VIOLET_SOFT if sel else None), border_radius=T.R_SM,
                        border=ft.Border.all(1, T.VIOLET if sel else ft.Colors.TRANSPARENT),
                        on_click=(None if ro else
                                  (lambda e, m=mins: (None if cur == m
                                                      else app._set_idle_minutes(m)))))
                labels = [("Off", 0), ("5m", 5), ("15m", 15), ("30m", 30), ("60m", 60)]
                return ft.Container(
                    ft.Row([opt(l, m) for l, m in labels], spacing=4, tight=True),
                    padding=4, bgcolor=T.CARD_2, border_radius=T.R,
                    border=ft.Border.all(1, T.BORDER))

            security = card(ft.Column([
                ft.Text("SECURITY", size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
                ft.Container(height=4),
                srow("Idle auto-logout",
                     "Sign inactive users out automatically. A 60-second warning lets "
                     "them stay signed in. Admins only.",
                     _idle_seg()),
            ], spacing=0))

        # ── Remote runs — sync THIS user's credentials to the per-user vault ──
        # Deliberately no fetch at build time (no load-on-render — see
        # PERF_ARCH_PLAN.md): the status line updates in place after a Sync.
        _remote_status = ft.Text(
            "Not synced from this device yet — Sync sends the credentials "
            "the app is currently connected with.",
            size=12, color=T.INK_3, weight=ft.FontWeight.W_500)
        remote = card(ft.Column([
            ft.Text("REMOTE RUNS", size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow("Credentials for remote & mobile runs",
                 "Stores your Azure PAT, AI key, and Gmail App Password in "
                 "your private encrypted vault (Supabase) so runs can "
                 "execute server-side as you and email you the report on "
                 "completion, same as a local run. Re-sync after changing "
                 "provider, rotating a key, or updating your App Password.",
                 ghost_btn("Sync now", icon=_ic("CLOUD_SYNC_OUTLINED", "SYNC"),
                           on_click=(None if ro else
                                     (lambda e: app._sync_remote_creds(_remote_status))))),
            ft.Container(_remote_status, padding=ft.Padding.only(bottom=10)),
        ], spacing=0))

        # ── Device security (mobile only) — biometric/PIN unlock for the
        # OS-keychain credential store. Opt-in and off by default: forcing
        # it on would throw on any device with no biometric/PIN enrolled
        # (flet_secure_storage's own documented behavior), so this is a
        # deliberate choice, not a default. Takes effect on next launch —
        # the SecureStorage service for THIS session was already constructed
        # with whatever the setting was at startup (see secure_store_mobile.
        # init()), so flipping it live can't retroactively change that.
        device_security = None
        if platform_caps.is_mobile():
            import mobile_prefs
            _bio_on = bool(mobile_prefs.get("require_biometric", False))
            _bio_note = ft.Text(
                "Applies next time you open the app.",
                size=11, color=T.INK_3, weight=ft.FontWeight.W_500)

            def _bio_change(e):
                # Migrate the vault under the new key policy RIGHT NOW (see
                # secure_store_mobile.apply_biometric_setting) instead of just
                # flipping a next-launch pref — the old approach lost the
                # saved credentials because they'd been written under the
                # non-biometric key. The pref is persisted only on success.
                want = bool(e.control.value)
                import secure_store_mobile as _ss

                def _done(ok, err):
                    def _apply():
                        if ok:
                            app._toast("Fingerprint/PIN unlock is on — you'll be "
                                       "asked next time you open the app."
                                       if want else
                                       "Biometric unlock turned off.")
                        else:
                            app._err("Couldn't enable biometric unlock: "
                                     + (err or "check that a fingerprint or PIN "
                                        "is set up on this device."))
                        app.render()   # reflect the real (possibly reverted) state
                    try:
                        app.ui_safe(_apply)
                    except Exception:
                        _apply()

                app._toast("Applying…")
                _ss.apply_biometric_setting(want, on_done=_done)

            device_security = card(ft.Column([
                ft.Text("DEVICE SECURITY", size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
                ft.Container(height=4),
                srow("Require biometric/PIN unlock",
                     "Adds a fingerprint/face/PIN prompt to open the app: "
                     "with this on, a saved sign-in no longer skips straight "
                     "past the login screen — you'll unlock with biometrics/"
                     "PIN first, or fall back to your email and password. "
                     "Also gates this device's stored credentials. Off by "
                     "default so reopening the app never needs re-entering "
                     "them; needs a biometric or PIN already set up on this "
                     "device.",
                     ft.Switch(value=_bio_on, active_color=T.VIOLET, disabled=ro,
                               on_change=(None if ro else _bio_change))),
                ft.Container(_bio_note, padding=ft.Padding.only(bottom=6)),
            ], spacing=0))

        reset = card(ft.Column([
            ft.Text("HELP & RESET", size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow("Welcome walkthrough",
                 "Replay the first-run guided tour of the app.",
                 ghost_btn("Replay", icon=_ic("SLIDESHOW_OUTLINED","PLAY_ARROW"),
                           on_click=lambda e: app._open_onboarding())),
            divider(),
            srow("Restore default preferences",
                 "Resets theme, language, and logging to defaults. Your saved "
                 "credentials and links are kept.",
                 danger_btn("Reset", icon=ft.Icons.RESTART_ALT,
                            on_click=lambda e: app._reset_prefs())),
        ], spacing=0))

        cards = ([appearance, data, remote] + ([security] if security else [])
                 + ([device_security] if device_security else []) + [reset])
        body = ft.Column(cards, spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)
        return app.shell("Settings", "Preferences for this device", body)

