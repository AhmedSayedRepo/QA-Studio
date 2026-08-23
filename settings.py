"""settings.py — Settings screen.

Extracted from main.py (Step-3 modular refactor). screen(app) builds the
Appearance / Data & Diagnostics / Help & Reset cards; the segment builders
(app._lang_segment, app._tool_segment) and action handlers stay on the app.
"""
import flet as ft
import theme as T
import regression
import platform_caps
import strings
from ui import _ic, card, ghost_btn, danger_btn


def _share_diag_log(app):
    """Hand the diagnostics log to the user.

    This exists because mobile failures were previously undiagnosable: the log
    was written under expanduser("~"), which isn't writable on Android, so
    every diag_log call silently vanished (see diag_log._data_dir). Now that it
    actually lands in the app-private dir, there still has to be a way to GET
    it off the phone — the app sandbox isn't browsable. Mobile hands it to the
    OS share sheet (same ft.Share path the exports use); desktop just reveals
    it in Explorer, matching every other export on that platform."""
    try:
        import os
        import diag_log
        path = diag_log.LOG_FILE
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            app._toast(strings.t("set_diag_none"))
            return
        if platform_caps.is_mobile():
            # Reuse the proven export delivery popup so this behaves exactly
            # like every other "here's your file" flow on mobile.
            try:
                app._mobile_download_popup(path, strings.t("set_diag_log"))
            except Exception:
                platform_caps.reveal_export(app.page, path)
        else:
            platform_caps.open_folder(os.path.dirname(path))
            app._toast(strings.t("set_diag_path", path=path))
    except Exception as ex:
        try:
            app._err(strings.t("set_diag_open_err", err=str(ex)[:120]))
        except Exception:
            pass


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
                ft.Row([seg(strings.t("set_theme_light"), ft.Icons.LIGHT_MODE_OUTLINED, "light"),
                        seg(strings.t("set_theme_dark"), ft.Icons.DARK_MODE_OUTLINED, "dark")],
                       spacing=4, tight=True),
                padding=4, bgcolor=T.CARD_2, border_radius=T.R,
                border=ft.Border.all(1, T.BORDER))

        # Profile images belong to the signed-in user, not to this device or
        # the shared organization credential set. The app center-crops the
        # source image to a validated square; this preview mirrors the avatar.
        current_user = getattr(app, "user", None) or {}
        avatar_url = str((getattr(app, "_identity_visuals", {}) or {}).get("avatar_url") or "")
        initial = str(current_user.get("name") or current_user.get("email") or "?").strip()[:1].upper()
        avatar_preview = (ft.Image(src=avatar_url, width=64, height=64,
                                   fit=ft.BoxFit.COVER, border_radius=32)
                          if avatar_url else ft.Container(
                              ft.Text(initial, size=17, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                              width=64, height=64, alignment=ft.Alignment.CENTER,
                              border_radius=32, gradient=ft.LinearGradient(
                                  begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
                                  colors=[T.VIOLET, getattr(T, "VIOLET_H", T.VIOLET)])))
        settings_avatar_holder = ft.Container(avatar_preview, width=64, height=64,
                                              border_radius=32,
                                              clip_behavior=ft.ClipBehavior.HARD_EDGE)
        app._settings_avatar_holder = settings_avatar_holder
        profile = card(ft.Column([
            ft.Text(strings.t("profile_title"), size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow(strings.t("profile_photo"), strings.t("profile_photo_desc"),
                 ft.Row([settings_avatar_holder], spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER)),
        ], spacing=0))

        appearance = card(ft.Column([
            ft.Text(strings.t("set_appearance"), size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow(strings.t("set_theme"), strings.t("set_theme_desc"),
                 _theme_seg()),
            divider(),
            srow(strings.t("au_output_language"), strings.t("au_output_language_desc"),
                 app._lang_segment()),
            divider(),
            srow(strings.t("ui_language"),
                 strings.t("au_interface_language_desc"),
                 app._ui_lang_segment()),
            divider(),
            srow(strings.t("set_generate"),
                 strings.t("set_generate_desc"),
                 app._tool_segment(compact=True)),
        ], spacing=0))

        perf_switch = ft.Switch(value=regression.perf_on(), active_color=T.VIOLET,
                                disabled=ro,
                                on_change=(None if ro else
                                           (lambda e: app._set_perf(e.control.value))))
        data = card(ft.Column([
            ft.Text(strings.t("set_data_diag"), size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow(strings.t("set_caches"),
                 strings.t("set_caches_desc"),
                 ghost_btn(strings.t("set_clear_caches"), icon=_ic("CLEANING_SERVICES_OUTLINED","DELETE_OUTLINE"),
                           on_click=lambda e: app._clear_caches())),
            divider(),
            srow(strings.t("set_perf_log"),
                 strings.t("set_perf_log_desc"),
                 perf_switch),
            divider(),
            srow(strings.t("set_diag_log"),
                 strings.t("set_diag_log_desc"),
                 ghost_btn(strings.t("set_share_log"), icon=_ic("BUG_REPORT_OUTLINED", "DESCRIPTION"),
                           on_click=lambda e: _share_diag_log(app))),
        ], spacing=0))

        # ── Security — every account controls its own idle-logout preference ──
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
            labels = [(strings.t("set_idle_off"), 0), ("5m", 5), ("15m", 15), ("30m", 30), ("60m", 60)]
            return ft.Container(
                ft.Row([opt(l, m) for l, m in labels], spacing=4, tight=True),
                padding=4, bgcolor=T.CARD_2, border_radius=T.R,
                border=ft.Border.all(1, T.BORDER))

        security = card(ft.Column([
            ft.Text(strings.t("set_security"), size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow(strings.t("set_idle_logout"),
                 strings.t("set_idle_logout_desc"),
                 _idle_seg()),
        ], spacing=0))

        # ── Remote runs — sync THIS user's credentials to the per-user vault ──
        # Deliberately no fetch at build time (no load-on-render — see
        # PERF_ARCH_PLAN.md): the status line updates in place after a Sync.
        _remote_status = ft.Text(
            strings.t("set_remote_not_synced"),
            size=12, color=T.INK_3, weight=ft.FontWeight.W_500)
        remote = card(ft.Column([
            ft.Text(strings.t("set_remote_runs"), size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow(strings.t("set_remote_creds"),
                 strings.t("set_remote_creds_desc"),
                 ghost_btn(strings.t("set_sync_now"), icon=_ic("CLOUD_SYNC_OUTLINED", "SYNC"),
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
                strings.t("set_bio_note"),
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
                            app._toast(strings.t("set_bio_on")
                                       if want else
                                       strings.t("set_bio_off"))
                        else:
                            app._err(strings.t("set_bio_err_prefix")
                                     + (err or strings.t("set_bio_err_fallback")))
                        app.render()   # reflect the real (possibly reverted) state
                    try:
                        app.ui_safe(_apply)
                    except Exception:
                        _apply()

                app._toast(strings.t("set_applying"))
                _ss.apply_biometric_setting(want, on_done=_done)

            device_security = card(ft.Column([
                ft.Text(strings.t("set_device_security"), size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
                ft.Container(height=4),
                srow(strings.t("set_bio_title"),
                     strings.t("set_bio_desc"),
                     ft.Switch(value=_bio_on, active_color=T.VIOLET, disabled=ro,
                               on_change=(None if ro else _bio_change))),
                ft.Container(_bio_note, padding=ft.Padding.only(bottom=6)),
            ], spacing=0))

        reset = card(ft.Column([
            ft.Text(strings.t("set_help_reset"), size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=4),
            srow(strings.t("set_walkthrough"),
                 strings.t("set_walkthrough_desc"),
                 ghost_btn(strings.t("set_replay"), icon=_ic("SLIDESHOW_OUTLINED","PLAY_ARROW"),
                           on_click=lambda e: app._open_onboarding())),
            divider(),
            srow(strings.t("set_restore_prefs"),
                 strings.t("set_restore_prefs_desc"),
                 danger_btn(strings.t("set_reset"), icon=ft.Icons.RESTART_ALT,
                            on_click=lambda e: app._reset_prefs())),
        ], spacing=0))

        cards = ([profile, appearance, data, remote] + ([security] if security else [])
                 + ([device_security] if device_security else []) + [reset])
        body = ft.Column(cards, spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)
        return app.shell(strings.t("settings"), strings.t("set_subtitle"), body)

