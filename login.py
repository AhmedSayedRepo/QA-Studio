"""login.py — native sign-in / sign-up gate + parallax backdrop.

Extracted from main.py (Step-9 modular refactor). login_gate(app) is the
hard auth gate; login_parallax(app, e) is the mouse-move backdrop handler
(uses HoverEvent.local_position -> Offset .x/.y). _entrance stays on the app
(shared with render); called here as app._entrance.
"""
import threading
import flet as ft
import theme as T
import store
import auth_supabase as auth
import platform_caps
import strings
import engine as E
from ui import card, grad, logo_img


def _saved_logins(app):
    v = app.creds.get("saved_logins") if isinstance(getattr(app, "creds", None), dict) else None
    return [l for l in v if isinstance(l, dict) and (l.get("email") or "").strip()] if isinstance(v, list) else []


_MAX_SAVED_LOGINS = 24


def _save_login(app, email, pw):
    """Remember a login in the SHARED creds file (dedup by email, most-recent
    first, capped) so it can be offered on the next sign-in. Passwords are stored
    via the same encrypted store the app already uses for auto-login."""
    email = (email or "").strip()
    pw = pw or ""
    if not email or not pw or not isinstance(getattr(app, "creds", None), dict):
        return
    logins = [l for l in _saved_logins(app)
              if (l.get("email") or "").lower() != email.lower()]
    logins.insert(0, {"email": email, "password": pw})
    # The selector is searchable, so retaining a practical account history no
    # longer makes the login card taller or forces users through a tiny chip
    # scroller. Keep the most recently used accounts first.
    app.creds["saved_logins"] = logins[:_MAX_SAVED_LOGINS]
    try:
        store.save(app.creds)
    except Exception:
        pass


def _remove_login(app, email):
    if not isinstance(getattr(app, "creds", None), dict):
        return
    app.creds["saved_logins"] = [l for l in _saved_logins(app)
                                 if (l.get("email") or "").lower() != (email or "").lower()]
    try:
        store.save(app.creds)
    except Exception:
        pass


def login_parallax(app, e):
    """Shift the login backdrop with the cursor (like the WebView2 parallax).

    Flet 0.85's HoverEvent exposes the cursor via `local_position` (an Offset),
    NOT `local_x`/`local_y` — reading the old names returned None, so the
    backdrop never tracked. We read local_position (with legacy fallbacks)."""
    lay = getattr(app, "_login_bg_layer", None)
    if lay is None:
        return
    try:
        lx = ly = None
        pos = getattr(e, "local_position", None)
        if pos is not None:
            lx = getattr(pos, "x", None)
            ly = getattr(pos, "y", None)
        if lx is None:
            lx = getattr(e, "local_x", None)
            ly = getattr(e, "local_y", None)
        if lx is None:
            return
        w = (app.page.width or 1440) or 1440
        h = (app.page.height or 900) or 900
        mx = (lx or 0) / w - 0.5
        my = (ly or 0) / h - 0.5
        # Parallax strength (was 0.06 — too subtle to notice). 0.13 is a clearly
        # stronger shift, still safely within the 1.3x layer's headroom (max
        # offset 0.15) so an edge is never exposed.
        lay.offset = ft.Offset(-mx * 0.13, -my * 0.13)
        lay.update()
    except Exception:
        pass

def login_gate(app):
    """Native Flet sign-in — the app's only login (replaces the old WebView2
    window that kept freezing). Full-bleed neon backdrop + frosted-glass card,
    email/password with sign-up + forgot-password, matching the previous look
    but 100% Flet, so there is no embedded browser that can freeze."""
    import os as _os
    # The pre-sign-in credential store keeps the last interface-language
    # default, while a signed-in account keeps its own Settings preference.
    # Make the login gate honour the active value before any localized controls
    # are created (including when it is reached immediately after sign-out).
    _login_ui_lang = str(getattr(app, "ui_lang", strings.UI_LANG) or "en").lower()
    if _login_ui_lang not in E.LANGUAGES:
        _login_ui_lang = "en"
    app.ui_lang = _login_ui_lang
    try:
        strings.set_ui_lang(_login_ui_lang)
        # Keep the desktop composition stable: Flutter's page-level RTL reverses
        # the entire hero/card Row, not just Arabic text, producing a mirrored
        # two-tone login screen. Arabic text itself is Unicode bidi-aware; its
        # fields below are explicitly right aligned.
        app.page.rtl = False
    except Exception:
        pass
    # The login keeps its OWN theme (defaults to dark, like the old WebView2
    # login) with a working toggle; the choice is applied to the whole app on
    # successful sign-in.
    # Forced-password-reset mode: a signed-in invitee whose TEMPORARY password
    # must be replaced before the app unlocks. Reuses this entire login card UI
    # (same neon card / fields / button) with a "Set your password" form. Mirror
    # the CURRENT app theme so showing/leaving this gate never flips the theme.
    reset_mode = bool(getattr(app, "user", None) and isinstance(app.user, dict)
                      and app.user.get("must_reset"))
    if reset_mode:
        app._login_theme = "dark" if getattr(T, "MODE", "dark") == "dark" else "light"
    if getattr(app, "_login_theme", None) not in ("dark", "light"):
        # Default the login to DARK (the neon look). Safe now that the window is
        # frameless — there's no OS title bar to mismatch the theme.
        app._login_theme = "dark"
    dark = (app._login_theme == "dark")
    signup = (not reset_mode) and (getattr(app, "_auth_mode", "signin") == "signup")
    busy = bool(getattr(app, "_gate_busy", False))
    msg = getattr(app, "_auth_msg", None)
    DISP = "Space Grotesk"     # display font (headings) — matches the old login
    MONO = T.F_MONO            # JetBrains Mono (captions / button)

    def _op(c, o):
        return ft.Colors.with_opacity(o, c)

    # Exact palette lifted from the former WebView2 login (dark neon / light).
    if dark:
        accent = "#00dbe7"
        HEAD = "#e1fdff"; INK = "#e2dffd"; INK2 = "#b9cacb"; CAP = "#8fa6a8"
        LEFT_HEAD = "#eef7ff"
        FIELD_BG = _op("#0c0c21", 0.55); FIELD_BD = "#333349"
        FIELD_IC = "#849495"; FIELD_INK = "#e1fdff"
        CARD_GRAD = [_op("#160A33", 0.66), _op("#062A3A", 0.34), _op("#25123A", 0.42)]
        CARD_BD = _op("#00dbe7", 0.38); CARD_GLOW = _op("#00dbe7", 0.22)
        BTN = ["#006a71", "#00dbe7"]; BTN_INK = "#00363a"
        SCRIM = [_op("#05060F", 0.74), _op("#05060F", 0.0)]
    else:
        # Exact values from the WebView2 login's `html.light` theme.
        accent = "#2563eb"                                     # --accent / --link
        HEAD = "#0f1830"; INK = "#101a30"; INK2 = "#5a6273"; CAP = "#8a93a8"
        LEFT_HEAD = "#0f1830"                                  # --headline
        FIELD_BG = _op("#ffffff", 0.70); FIELD_BD = "#dbe2ee"  # --input-bg / --input-bd
        FIELD_IC = "#8a93a8"; FIELD_INK = "#101a30"            # --input-ic / --input-ink
        # --panel-grad: 135deg rgba(255,255,255,.78) / (37,99,235,.05) / (34,211,238,.05)
        CARD_GRAD = [_op("#ffffff", 0.78), _op("#2563eb", 0.05), _op("#22d3ee", 0.05)]
        CARD_BD = _op("#2563eb", 0.45); CARD_GLOW = _op("#2563eb", 0.16)  # --panel-bd/glow
        BTN = ["#2563eb", "#22d3ee"]; BTN_INK = "#ffffff"      # --btn / --btn-ink
        SCRIM = [_op("#ffffff", 0.66), _op("#ffffff", 0.0)]

    # Match the window shell AND native title bar to the login theme the moment
    # the login opens (no dark title bar / navy band in light mode).
    try:
        app.page.bgcolor = ("#05060F" if dark else "#eef3fb")
        app.page.theme_mode = (ft.ThemeMode.DARK if dark else ft.ThemeMode.LIGHT)
    except Exception:
        pass

    # Theme toggle lives in the card header (so it can't overlap the card).
    def _toggle_login_theme(_e=None):
        app._login_theme = "light" if dark else "dark"
        # Explicit choice — make it authoritative for this session so an async
        # creds bootstrap landing mid-login can't revert it (see _submit).
        app._theme_touched = True
        try:
            # keep the GLOBAL palette in sync so global-token UI (the update banner)
            # follows the theme on the login screen too — not just the login's colors
            T.apply_theme(app._login_theme)
            app.page.theme_mode = (ft.ThemeMode.DARK if app._login_theme == "dark"
                                   else ft.ThemeMode.LIGHT)
        except Exception:
            pass
        app.ui_safe(app.render)
    theme_btn = ft.Container(
        ft.Icon(ft.Icons.LIGHT_MODE if dark else ft.Icons.DARK_MODE,
                size=18, color=(HEAD if dark else INK)),
        width=40, height=40, border_radius=11, alignment=ft.Alignment.CENTER,
        bgcolor=_op("#FFFFFF" if dark else "#0f1830", 0.12),
        border=ft.Border.all(1, _op(accent, 0.35)), ink=True,
        on_click=_toggle_login_theme, tooltip=strings.t("login_toggle_theme"),
        scale=1.0, animate_scale=140, animate=140, rotate=0)

    def _theme_hover(e, _c=theme_btn):
        try:
            on = e.data in (True, "true", "True")
            _c.scale = 1.1 if on else 1.0
            _c.bgcolor = _op(accent, 0.22 if on else (0.12))
            _c.update()
        except Exception:
            pass
    theme_btn.on_hover = _theme_hover

    # Same native-name interface-language picker used in Settings. It belongs
    # in the compact card header so the login form remains short and no extra
    # scrollable area is introduced.
    _login_rtl = (_login_ui_lang == "ar")

    def _set_login_ui_lang(e):
        code = str(getattr(e.control, "value", "") or "en").lower()
        if code not in E.LANGUAGES:
            code = "en"
        app.ui_lang = code
        app._login_ui_lang_touched = True
        # Keep this choice separate from the account preference that is loaded
        # as part of a successful sign-in.
        app._login_ui_lang_selected = code
        try:
            strings.set_ui_lang(code)
            app.page.rtl = False
            # Before sign-in this is the shared device store, so the next
            # login screen starts in the same language. On successful sign-in
            # the submit flow below copies an explicit choice into that user's
            # Settings preference as well.
            if isinstance(getattr(app, "creds", None), dict):
                app.creds["ui_lang"] = code
                store.save(app.creds)
        except Exception:
            pass
        app.ui_safe(app.render)

    _login_lang_kwargs = dict(
        value=_login_ui_lang,
        options=[ft.DropdownOption(key=code, text=info["native"])
                 for code, info in E.LANGUAGES.items()],
        on_select=_set_login_ui_lang,
        border_color=_op(accent, 0.35), focused_border_color=accent,
        border_radius=10, text_size=11.5, dense=True,
        content_padding=ft.Padding.symmetric(vertical=5, horizontal=7),
        color=INK, bgcolor=_op("#FFFFFF" if dark else "#0f1830", 0.10),
        tooltip=strings.t("ui_language"))
    try:
        _login_lang_dd = ft.Dropdown(menu_height=300, **_login_lang_kwargs)
    except TypeError:
        _login_lang_dd = ft.Dropdown(**_login_lang_kwargs)
    login_lang_picker = ft.Container(_login_lang_dd, width=106,
                                     tooltip=strings.t("ui_language"))

    # background image (decode embedded jpeg once to a cached temp file)
    def _bg():
        key = "_login_bg_d" if dark else "_login_bg_l"
        p = getattr(app, key, None)
        if not p or not _os.path.exists(p):
            try:
                import login_bg_assets as _LBA, base64 as _b64, tempfile as _tf
                data = _b64.b64decode(_LBA.LOGIN_BG_DARK_B64 if dark
                                      else _LBA.LOGIN_BG_LIGHT_B64)
                _f = _tf.NamedTemporaryFile(suffix=".jpg", delete=False)
                _f.write(data); _f.close()
                p = _f.name
                setattr(app, key, p)
            except Exception:
                p = None
        # BUG FIX (root cause of the persistent black gutters, found via a
        # fresh second-opinion review): this Flet build (0.85.3, the 1.0
        # architecture line) renamed `ft.ImageFit` to `ft.BoxFit` — `ft.ImageFit`
        # doesn't exist at all here (getattr silently returns None). So `_cover`
        # was ALWAYS None, every fit=COVER attempt below was skipped, and the
        # bare no-fit DecorationImage fallback was used instead — which Flutter
        # defaults to BoxFit.scaleDown (contain, never upscale) when fit is
        # None. That shrank the square 1080x1080 photos to match the window's
        # HEIGHT only, letterboxing the sides — exactly the gutters reported.
        # (The prior width/height and Stack-pinning fixes were both correct
        # changes but attacked the wrong layer: the box was always full-size —
        # provable because the gutters were the Container's own bgcolor
        # showing through — it was only the image's OWN paint-fit that was
        # silently falling back to scaleDown.)
        _fit_enum = getattr(ft, "BoxFit", None) or getattr(ft, "ImageFit", None)
        _cover = getattr(_fit_enum, "COVER", None)
        _contain = getattr(_fit_enum, "CONTAIN", None)
        # MOBILE fit: use `cover` on mobile too (was `contain`). The backdrop is
        # a 2160x1215 (16:9) LANDSCAPE image; `contain` on a tall PORTRAIT phone
        # shrank it into a short horizontal band in the vertical centre with dark
        # base filling top/bottom — and the frosted card sits right over that
        # band, so the art read as a dark frame with only thin edge slivers
        # visible (reported "check the backdrop"). `cover` fills the whole
        # screen; the crop is acceptable on a phone (the card covers the cropped
        # centre anyway) and the immersive circuit look actually shows. Desktop
        # was already `cover`, so both platforms now match.
        _mob_bg = platform_caps.is_mobile()
        _fit = _cover
        _fit_str = "cover"
        # HIGH filter quality → the 2752x1536 / 2048x1142 photo is resampled with
        # good interpolation when it's scaled to fill a high-DPI (Retina/4K) window,
        # so it stays crisp instead of looking soft/low-res. Guarded: not every Flet
        # build exposes FilterQuality / accepts the kwarg.
        _fq = getattr(getattr(ft, "FilterQuality", None), "HIGH", None)
        # Paint the image as a CONTAINER background (DecorationImage) so it
        # cover-fills the whole window reliably. A plain ft.Image won't stretch
        # to fill a Stack on this Flet build (leaves gutters), which is the
        # bug that left gray/dark bands around the photo.
        _base = "#05060F" if dark else T.BG
        # BUG FIX (black gutters left/right, esp. with a square-ish source image):
        # `Container(expand=True, image=DecorationImage(fit=COVER))` leaves
        # DecorationImage's own cover math to run against whatever box
        # `expand=True` resolves to inside the Stack — on this Flet build that
        # isn't always a definite size by the time the fit is computed, so
        # COVER silently fell back to the image's native aspect (letterboxed,
        # centered) instead of actually cropping to fill. Passing explicit
        # width/height (the real window size, read fresh every render) gives
        # the DecorationImage a concrete box to cover, so it fills edge-to-edge
        # regardless of the source image's aspect ratio. `expand=True` is kept
        # too so it still stretches correctly on window resizes between
        # renders (the explicit W/H is a same-render safety net, not instead of).
        try:
            W = int(app.page.width or 0) or 1440
            H = int(app.page.height or 0) or 900
        except Exception:
            W, H = 1440, 900
        if p and hasattr(ft, "DecorationImage"):
            # REVERTED (2026-07-14): tried a blurred-cover + sharp-contain layered
            # composite here to avoid cover's crop-zoom on the square artwork —
            # live screenshots showed it looking WORSE (visible padding/gaps
            # around a squarer-looking image, plus a seam bug on top of that).
            # Back to plain single-layer COVER — it crops more of the top/bottom
            # of the square composition than an exact-aspect image would, but it
            # fills edge-to-edge cleanly with no seams and no gaps, which reads
            # better than either problem the composite introduced.
            for _kw in ([dict(src=p, fit=_fit, filter_quality=_fq)] if (_fit and _fq) else []) \
                       + ([dict(src=p, fit=_fit)] if _fit else []) \
                       + [dict(src=p, fit=_fit_str)]:
                try:
                    return ft.Container(expand=True, width=W, height=H,
                                        image=ft.DecorationImage(**_kw), bgcolor=_base)
                except Exception:
                    continue
        if p:
            try:
                W = int(app.page.width or 0) or 1440
                H = int(app.page.height or 0) or 900
                _ik = dict(src=p, width=W, height=H)
                if _fit:
                    _ik["fit"] = _fit
                if _fq:
                    _ik["filter_quality"] = _fq
                try:
                    return ft.Image(**_ik)
                except Exception:
                    _ik.pop("filter_quality", None)
                    return ft.Image(**_ik)
            except Exception:
                pass
        return ft.Container(expand=True, bgcolor=_base)

    def _field(cap, hint, icon, value="", password=False):
        tf = ft.TextField(
            hint_text=hint, value=value or "", password=password,
            can_reveal_password=password, prefix_icon=icon, filled=True,
            bgcolor=FIELD_BG, border_color=FIELD_BD,
            focused_border_color=accent, focused_bgcolor=_op(accent, 0.06),
            cursor_color=accent, text_size=15, color=FIELD_INK,
            text_align=(ft.TextAlign.RIGHT if _login_rtl else ft.TextAlign.LEFT),
            hint_style=ft.TextStyle(size=14, color=FIELD_IC),
            content_padding=ft.Padding.symmetric(vertical=17, horizontal=14),
            border_radius=12)
        field_ctl = tf
        # MOBILE keyboard-reopen fix. On Android, dismissing the soft keyboard
        # (back gesture / tapping away) hides it but leaves the TextField still
        # "focused" in Flutter's eyes — so tapping the SAME field again is a
        # no-op (focus never changed) and the keyboard stays down, sometimes
        # for many taps until focus finally moves elsewhere. Reported live on
        # the password field. Wrapping the field in a GestureDetector lets us
        # catch the tap and force a focus CYCLE (blur → focus): the blur makes
        # the re-focus a real focus change, which is what re-opens the
        # keyboard. Mobile-only so the desktop login is completely unchanged.
        if platform_caps.is_mobile():
            def _refocus(e, _tf=tf):
                async def _cycle():
                    try:
                        _tf.blur()
                    except Exception:
                        pass
                    try:
                        import asyncio
                        await asyncio.sleep(0.05)
                    except Exception:
                        pass
                    try:
                        _tf.focus()
                    except Exception:
                        pass
                try:
                    app.page.run_task(_cycle)
                except Exception:
                    try:
                        _tf.focus()
                    except Exception:
                        pass
            # on_tap_down (NOT on_tap): down events are delivered to the
            # detector BEFORE Flutter's gesture arena resolves, so the refocus
            # fires even when the inner TextField wins the tap for cursor
            # placement — which is why the old on_tap was flaky (the TextField
            # almost always won the arena, so on_tap never fired and the
            # keyboard stayed down). Scrolling the field first biased the arena
            # even harder toward the field, matching the reported dead taps.
            field_ctl = ft.GestureDetector(content=tf, on_tap_down=_refocus)
        col = ft.Column([
            ft.Text(cap, size=11, color=CAP, font_family=MONO,
                    weight=ft.FontWeight.W_600,
                    text_align=(ft.TextAlign.RIGHT if _login_rtl else ft.TextAlign.LEFT),
                    style=ft.TextStyle(letter_spacing=1.8)),
            ft.Container(height=7),
            field_ctl,
        ], spacing=0, width=352)
        return tf, col

    name_tf, name_col = (_field(strings.t("login_full_name_label"), strings.t("login_full_name_hint"), ft.Icons.PERSON_OUTLINE,
                                getattr(app, "_auth_name", "")) if signup
                         else (None, None))
    email_tf, email_col = _field(strings.t("login_email_label"), strings.t("login_email_hint"), ft.Icons.MAIL_OUTLINE,
                                 getattr(app, "_auth_email", ""))
    pwd_tf, pwd_col = _field(strings.t("login_password_label"), strings.t("login_password_hint"), ft.Icons.LOCK_OUTLINE,
                             getattr(app, "_auth_prefill_pw", ""), password=True)

    newpw_tf = newpw_col = confpw_tf = confpw_col = None
    if reset_mode:
        newpw_tf, newpw_col = _field(strings.t("reset_new"), strings.t("reset_new"),
                                     ft.Icons.LOCK_OUTLINE, password=True)
        confpw_tf, confpw_col = _field(strings.t("reset_confirm"), strings.t("reset_confirm"),
                                       ft.Icons.LOCK_RESET, password=True)

    def _stash():
        app._auth_email = email_tf.value or ""
        app._auth_prefill_pw = pwd_tf.value or ""
        if name_tf is not None:
            app._auth_name = name_tf.value or ""

    def _switch(_e=None):
        _stash()
        app._auth_mode = "signin" if signup else "signup"
        app._auth_msg = None
        app.ui_safe(app.render)

    def _submit(_e=None):
        if getattr(app, "_gate_busy", False):
            return
        _stash()
        if not (email_tf.value or "").strip() or not (pwd_tf.value or ""):
            app._auth_msg = ("err", strings.t("login_err_enter_credentials"))
            app.ui_safe(app.render); return
        if signup and not auth.password_meets_policy(pwd_tf.value or ""):
            app._auth_msg = ("err", strings.t("reset_password_policy"))
            app.ui_safe(app.render); return
        app._gate_busy = True; app._auth_msg = None
        app.ui_safe(app.render)

        def work():
            try:
                if signup:
                    ok, m, user = auth.sign_up(
                        email_tf.value, pwd_tf.value,
                        name=(name_tf.value if name_tf is not None else None))
                else:
                    ok, m, user = auth.sign_in(email_tf.value, pwd_tf.value)
                app._gate_busy = False
                if user:
                    app.user = user; app._auth_msg = None
                    # Clear the post-sign-out restore block (see main._sign_out /
                    # _on_secure_creds_ready): the user has explicitly signed in
                    # again, so later vault bootstraps may restore normally.
                    app._user_signed_out = False
                    # Remember-me: persist this login to the SHARED creds file
                    # NOW, before _switch_user_creds() repoints the store at the
                    # per-user file, so it's offered on the login screen next time.
                    try:
                        if getattr(app, "_login_remember", True):
                            _save_login(app, email_tf.value, pwd_tf.value)
                    except Exception:
                        pass
                    app._auth_prefill_pw = ""   # don't leave the pw in memory state
                    # Load THIS user's own per-user creds file FIRST, then apply the
                    # login screen's chosen theme on top of it — order matters. This
                    # used to run the other way around: apply + save the login theme
                    # into whatever creds file was active BEFORE switching (the
                    # shared/pre-sign-in one), then immediately call
                    # _switch_user_creds(), which loads the signed-in account's OWN
                    # file and — per its own theme-sync logic — overwrote T.MODE with
                    # THAT file's saved theme. Net effect: picking dark on the login
                    # screen got silently discarded the moment sign-in completed,
                    # replaced by whatever this account last had saved (often light).
                    # Switching to the account's file restores its prior language.
                    # Preserve an explicit language selected on this sign-in screen
                    # before loading those account settings.
                    explicit_login_ui_lang = ""
                    if getattr(app, "_login_ui_lang_touched", False):
                        explicit_login_ui_lang = str(
                            getattr(app, "_login_ui_lang_selected", "") or ""
                        ).lower()
                        if explicit_login_ui_lang not in E.LANGUAGES:
                            explicit_login_ui_lang = ""

                    app._switch_user_creds()   # load this user's own per-user creds
                    # A deliberate language change made before sign-in is the
                    # user's new Settings default. If the picker was untouched,
                    # _switch_user_creds() has already restored this account's
                    # existing Settings preference instead.
                    if explicit_login_ui_lang:
                        chosen_ui_lang = explicit_login_ui_lang
                        app.ui_lang = chosen_ui_lang
                        strings.set_ui_lang(chosen_ui_lang)
                        try:
                            app.page.rtl = strings.ui_is_rtl()
                        except Exception:
                            pass
                        try:
                            app.creds["ui_lang"] = chosen_ui_lang
                            store.save(app.creds)
                        except Exception:
                            pass
                        app._login_ui_lang_touched = False
                        app._login_ui_lang_selected = ""
                    try:
                        T.apply_theme(app._login_theme)
                        # Mark the theme as user-authoritative for THIS session so
                        # the login screen's choice wins. On mobile
                        # _switch_user_creds() above kicks off an async keychain
                        # re-bootstrap that (on a fresh install) reads the empty
                        # vault and wipes this just-saved theme from cache; when
                        # _on_secure_creds_ready() then fires with
                        # _theme_touched False it re-applies the default "light",
                        # flipping the app to the OPPOSITE of the login screen
                        # (reported: login dark → app opens light, and vice
                        # versa). Setting this True routes that callback down its
                        # "keep what's on screen" branch instead.
                        app._theme_touched = True
                        # Startup cache so the next launch paints this theme on
                        # the FIRST frame instead of flashing light.
                        try:
                            app._persist_theme(app._login_theme)
                        except Exception:
                            pass
                        app.creds["theme"] = app._login_theme
                        store.save(app.creds)
                        app.page.bgcolor = T.RAIL
                        app.page.theme_mode = (ft.ThemeMode.DARK
                            if app._login_theme == "dark" else ft.ThemeMode.LIGHT)
                    except Exception:
                        pass
                    app.active = "setup"
                    app._land_app = True   # play the entrance on the app view
                    if platform_caps.is_mobile():
                        try:
                            import mobile_tilt
                            mobile_tilt.disable()
                        except Exception:
                            pass
                        # POST-LOGIN update check for a MANUAL email/password
                        # sign-in (the biometric/auto-restore path fires its own
                        # in _on_secure_creds_ready). Self-guarded + once-per-
                        # session, so it only shows to a now-signed-in user.
                        try:
                            app._check_mobile_update(force=True)
                        except Exception:
                            pass
                    app.ui_safe(app.render); return
                app._auth_msg = ("ok" if ok else "err", m)
                if ok and signup:
                    app._auth_mode = "signin"
                app.ui_safe(app.render)
            except Exception as ex:
                app._gate_busy = False
                app._auth_msg = ("err", strings.t("login_err_generic", error=ex))
                app.ui_safe(app.render)
        try:
            app._bg(work)
        except Exception:
            threading.Thread(target=work, daemon=True).start()

    def _reset_submit(_e=None):
        if getattr(app, "_gate_busy", False):
            return
        p1 = (newpw_tf.value or "") if newpw_tf is not None else ""
        p2 = (confpw_tf.value or "") if confpw_tf is not None else ""
        if not auth.password_meets_policy(p1):
            app._auth_msg = ("err", strings.t("reset_password_policy")); app.ui_safe(app.render); return
        if p1 != p2:
            app._auth_msg = ("err", strings.t("reset_mismatch")); app.ui_safe(app.render); return
        app._gate_busy = True; app._auth_msg = None
        app.ui_safe(app.render)

        def work():
            try:
                ok, m = auth.change_own_password(p1)
            except Exception as ex:
                ok, m = False, strings.t("login_err_generic", error=ex)
            app._gate_busy = False
            if not ok:
                app._auth_msg = ("err", m); app.ui_safe(app.render); return
            fresh = auth.revalidate()      # must_reset now cleared server-side
            if fresh:
                app.user = fresh
            elif isinstance(getattr(app, "user", None), dict):
                app.user["must_reset"] = False
            app._auth_msg = ("ok", strings.t("reset_done"))
            app.ui_safe(app.render)
        try:
            app._bg(work)
        except Exception:
            threading.Thread(target=work, daemon=True).start()

    # Enter in any credential field submits (sign in / sign up / set password).
    email_tf.on_submit = _submit
    pwd_tf.on_submit = _submit
    if name_tf is not None:
        name_tf.on_submit = _submit
    if reset_mode and newpw_tf is not None:
        newpw_tf.on_submit = _reset_submit
        confpw_tf.on_submit = _reset_submit

    def _forgot(_e=None):
        _stash()
        em = (email_tf.value or "").strip()
        if not em:
            app._auth_msg = ("err", strings.t("login_err_enter_email_first"))
            app.ui_safe(app.render); return
        app._gate_busy = True; app._auth_msg = None
        app.ui_safe(app.render)

        def work():
            try:
                ok, m = auth.request_password_reset(em)
            except Exception as ex:
                ok, m = False, strings.t("login_err_generic", error=ex)
            app._gate_busy = False
            app._auth_msg = ("ok" if ok else "err", m)
            app.ui_safe(app.render)
        try:
            app._bg(work)
        except Exception:
            threading.Thread(target=work, daemon=True).start()

    if reset_mode:
        blabel = (strings.t("login_btn_signing_in") if busy else strings.t("reset_submit"))
        _primary = _reset_submit
    else:
        blabel = (strings.t("login_btn_creating") if (busy and signup) else strings.t("login_btn_signing_in") if busy
                  else strings.t("login_btn_create_account") if signup else strings.t("login_btn_sign_in"))
        _primary = _submit

    def _btn_hover(e):
        try:
            hov = e.data in (True, "true", "True")
            e.control.scale = 1.02 if hov else 1.0
            e.control.shadow = ft.BoxShadow(
                blur_radius=(40 if hov else 30), spread_radius=-4,
                offset=ft.Offset(0, 12 if hov else 10),
                color=_op(accent, 0.75 if hov else 0.5))
            e.control.update()
        except Exception:
            pass

    btn = ft.Container(
        ft.Row([
            ft.Text(blabel, size=13.5, weight=ft.FontWeight.W_700, color=BTN_INK,
                    font_family=MONO, style=ft.TextStyle(letter_spacing=1.5)),
            (ft.ProgressRing(width=16, height=16, stroke_width=2.4, color=BTN_INK)
             if busy else ft.Icon(ft.Icons.ARROW_FORWARD, size=18, color=BTN_INK)),
        ], alignment=ft.MainAxisAlignment.CENTER, spacing=10, tight=True),
        height=54, border_radius=13, alignment=ft.Alignment.CENTER,
        gradient=ft.LinearGradient(begin=ft.Alignment.CENTER_LEFT,
                                   end=ft.Alignment.CENTER_RIGHT, colors=BTN),
        shadow=ft.BoxShadow(blur_radius=30, spread_radius=-4, offset=ft.Offset(0, 10),
                            color=_op(accent, 0.5)),
        ink=True, on_click=(None if busy else _primary),
        on_hover=(None if busy else _btn_hover),
        scale=1.0, animate_scale=140, animate=140,
        opacity=(0.7 if busy else 1.0))

    banner = None
    if msg:
        kind, text = msg
        ok = (kind == "ok")
        banner = ft.Container(
            ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE,
                            size=18, color=(T.GREEN if ok else T.RED)),
                    ft.Text(text, size=12.5, no_wrap=False, expand=True,
                            color=(T.GREEN if ok else T.RED))],
                   spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(vertical=11, horizontal=13),
            bgcolor=_op(T.GREEN if ok else T.RED, 0.12), border_radius=10,
            border=ft.Border.all(1, _op(T.GREEN if ok else T.RED, 0.4)))

    def _link(text, on_click):
        c = ft.Container(
            ft.Text(text, size=12.5, weight=ft.FontWeight.W_700, color=accent),
            on_click=on_click, ink=True, border_radius=8,
            padding=ft.Padding.symmetric(vertical=4, horizontal=8),
            scale=1.0, animate_scale=110, animate=110)

        def _h(e, _c=c):
            try:
                on = e.data in (True, "true", "True")
                _c.bgcolor = _op(accent, 0.12) if on else None
                _c.scale = 1.06 if on else 1.0
                _c.update()
            except Exception:
                pass
        c.on_hover = _h
        return c

    # ── Remember-me + saved-logins picker (sign-in mode only) ──────────────
    if not hasattr(app, "_login_remember"):
        app._login_remember = (bool(app.creds.get("remember_me", True))
                               if isinstance(getattr(app, "creds", None), dict) else True)

    def _toggle_remember(e=None):
        app._login_remember = (bool(e.control.value) if e is not None
                               else (not app._login_remember))
        try:
            if isinstance(getattr(app, "creds", None), dict):
                app.creds["remember_me"] = app._login_remember
                store.save(app.creds)
        except Exception:
            pass
    remember_cb = ft.Row([
        ft.Checkbox(value=bool(getattr(app, "_login_remember", True)),
                    on_change=_toggle_remember, active_color=accent, check_color="#FFFFFF",
                    scale=0.85),
        ft.Text(strings.t("login_remember"), size=12.5, color=INK2, weight=ft.FontWeight.W_600),
    ], spacing=2, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    saved_section = None
    _saved = _saved_logins(app)
    if (not signup) and (not reset_mode) and _saved:
        _saved_count_text = ft.Text(strings.t("login_saved_count", count=len(_saved)),
                                    size=10.5, color=CAP)
        # The former inline chip row was only 42px tall. Once two or more
        # addresses were saved, its nested scrolling was both hard to discover
        # and hard to use. Keep the login card stable and put account discovery
        # in a searchable dialog with a comfortably sized results list instead.
        def _open_saved_accounts(_e=None):
            results = ft.Column([], spacing=6, scroll=ft.ScrollMode.AUTO, height=300)

            def _select(em, pw):
                # Update the existing controls in place. Rendering the entire
                # login gate here would lose keyboard focus and makes choosing
                # an account look like a page refresh.
                app._auth_email = em
                app._auth_prefill_pw = pw
                email_tf.value = em
                pwd_tf.value = pw
                try:
                    email_tf.update()
                    pwd_tf.update()
                except Exception:
                    pass
                try:
                    app._close_dialog()
                except Exception:
                    pass

            def _render_accounts(query=""):
                q = (query or "").strip().lower()
                accounts = [item for item in _saved_logins(app)
                            if not q or q in (item.get("email") or "").lower()]
                rows = []
                for item in accounts:
                    em = (item.get("email") or "").strip()
                    if not em:
                        continue
                    pw = item.get("password") or ""

                    def _pick(_e=None, email=em, password=pw):
                        _select(email, password)

                    def _remove(_e=None, email=em):
                        _remove_login(app, email)
                        remaining = len(_saved_logins(app))
                        _saved_count_text.value = strings.t("login_saved_count", count=remaining)
                        saved_section.visible = bool(remaining)
                        try:
                            _saved_count_text.update()
                            saved_section.update()
                        except Exception:
                            pass
                        _render_accounts(search.value)

                    remove_btn = ft.Container(
                        ft.Icon(ft.Icons.CLOSE, size=16, color=CAP),
                        on_click=_remove, ink=True, border_radius=8, padding=7,
                        tooltip=strings.t("login_saved_remove"))
                    row = ft.Container(
                        ft.Row([
                            ft.Container(ft.Icon(ft.Icons.ACCOUNT_CIRCLE, size=20, color=accent),
                                         width=34, height=34, border_radius=10,
                                         bgcolor=_op(accent, 0.12), alignment=ft.Alignment.CENTER),
                            ft.Text(em, size=13, color=INK, weight=ft.FontWeight.W_600,
                                    no_wrap=True, expand=True),
                            remove_btn,
                        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        on_click=_pick, ink=True, border_radius=11,
                        padding=ft.Padding.symmetric(vertical=8, horizontal=10),
                        bgcolor=_op(accent, 0.07), border=ft.Border.all(1, _op(accent, 0.25)))
                    rows.append(row)
                if not rows:
                    rows = [ft.Container(
                        ft.Text(strings.t("login_saved_no_match"), size=12.5, color=CAP,
                                text_align=ft.TextAlign.CENTER),
                        padding=18, alignment=ft.Alignment.CENTER)]
                results.controls = rows
                try:
                    results.update()
                except Exception:
                    pass

            search = ft.TextField(
                hint_text=strings.t("login_saved_search"), autofocus=True,
                prefix_icon=ft.Icons.SEARCH, border_color=_op(accent, 0.35),
                focused_border_color=accent, border_radius=11, text_size=13,
                color=INK, hint_style=ft.TextStyle(color=CAP),
                content_padding=ft.Padding.symmetric(vertical=11, horizontal=12),
                on_change=lambda e: _render_accounts(e.control.value))
            _render_accounts()
            dlg = ft.AlertDialog(
                modal=True,
                title=ft.Row([
                    ft.Container(ft.Icon(ft.Icons.ACCOUNT_CIRCLE, size=19, color=accent),
                                 width=34, height=34, border_radius=10,
                                 bgcolor=_op(accent, 0.12), alignment=ft.Alignment.CENTER),
                    ft.Column([
                        ft.Text(strings.t("login_saved_accounts"), size=16,
                                weight=ft.FontWeight.W_700, color=INK),
                        ft.Text(strings.t("login_saved_accounts_hint"), size=11.5, color=CAP),
                    ], spacing=1, tight=True),
                ], spacing=10),
                content=ft.Container(
                    width=420,
                    content=ft.Column([search, ft.Container(height=10), results], spacing=0, tight=True)),
                actions=[ft.TextButton(strings.t("main_close"), on_click=lambda _e: app._close_dialog())])
            app._show_dialog(dlg)

        saved_section = ft.Column([
            ft.Text(strings.t("login_saved_logins"), size=11, color=CAP, font_family=MONO,
                    style=ft.TextStyle(letter_spacing=0.8)),
            ft.Container(height=8),
            ft.Container(
                ft.Row([
                    ft.Container(ft.Icon(ft.Icons.ACCOUNT_CIRCLE, size=17, color=accent),
                                 width=29, height=29, border_radius=9,
                                 bgcolor=_op(accent, 0.12), alignment=ft.Alignment.CENTER),
                    ft.Column([
                        ft.Text(strings.t("login_saved_accounts"), size=12.5, color=INK,
                                weight=ft.FontWeight.W_700),
                        _saved_count_text,
                    ], spacing=0, tight=True, expand=True),
                    ft.Icon(ft.Icons.KEYBOARD_ARROW_RIGHT, size=19, color=CAP),
                ], spacing=9, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                on_click=_open_saved_accounts, ink=True, border_radius=11,
                padding=ft.Padding.symmetric(vertical=8, horizontal=10),
                bgcolor=_op(accent, 0.08), border=ft.Border.all(1, _op(accent, 0.32)),
                tooltip=strings.t("login_saved_picker_tip"), width=352),
            ft.Container(height=8),
        ], spacing=0, width=352)

    _header_row = ft.Row([ft.Container(logo_img(48), width=48, height=48, border_radius=12,
                             bgcolor=None, alignment=ft.Alignment.CENTER),
                ft.Text("QA Studio", size=17, weight=ft.FontWeight.W_700,
                        color=HEAD, font_family=DISP),
                ft.Container(expand=True),
                login_lang_picker,
                theme_btn],
               spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    if reset_mode:
        # Same card chrome, "Set your password" body.
        rows = [
            _header_row,
            ft.Container(height=16),
            ft.Text(strings.t("reset_title"), size=34, weight=ft.FontWeight.W_700,
                    color=HEAD, font_family=DISP),
            ft.Container(height=6),
            ft.Text(strings.t("reset_sub"), size=13, color=INK2, font_family=MONO,
                    style=ft.TextStyle(letter_spacing=0.4)),
            ft.Container(height=18),
            newpw_col, ft.Container(height=12),
            confpw_col, ft.Container(height=10),
            ft.Text(strings.t("reset_hint"), size=11.5, color=CAP, font_family=MONO,
                    style=ft.TextStyle(letter_spacing=0.3)),
        ]
        if banner:
            rows += [ft.Container(height=12), banner]
        rows += [ft.Container(height=16), btn, ft.Container(height=12),
                 ft.Row([_link(strings.t("reset_signout"),
                               lambda _e=None: app._sign_out())],
                        alignment=ft.MainAxisAlignment.CENTER, tight=True)]
    else:
        rows = [
            _header_row,
            ft.Container(height=16),
            ft.Text(strings.t("login_welcome_back") if not signup else strings.t("login_create_account_title"),
                    size=34, weight=ft.FontWeight.W_700, color=HEAD, font_family=DISP),
            ft.Container(height=6),
            ft.Text(strings.t("login_signin_subtitle") if not signup
                    else strings.t("login_signup_subtitle"),
                    size=13, color=INK2, font_family=MONO,
                    style=ft.TextStyle(letter_spacing=0.4)),
            ft.Container(height=18),
        ]
        if saved_section is not None:
            rows.append(saved_section)
        for col in [c for c in (name_col, email_col, pwd_col) if c is not None]:
            rows += [col, ft.Container(height=12)]
        if not signup:
            rows.append(ft.Row([remember_cb, ft.Container(expand=True),
                                _link(strings.t("login_forgot_password"), _forgot)],
                               vertical_alignment=ft.CrossAxisAlignment.CENTER))
        if banner:
            rows += [ft.Container(height=8), banner]
        rows += [ft.Container(height=16), btn, ft.Container(height=12),
                 ft.Row([ft.Text(strings.t("login_new_prompt") if not signup
                                 else strings.t("login_have_account_prompt"),
                                 size=12.5, color=INK2, weight=ft.FontWeight.W_600),
                         _link(strings.t("login_create_one") if not signup else strings.t("login_sign_in_link"), _switch)],
                        spacing=6, alignment=ft.MainAxisAlignment.CENTER, tight=True)]
    form = ft.Column(rows, spacing=0, width=352, tight=True,
                     horizontal_alignment=(ft.CrossAxisAlignment.END if _login_rtl
                                           else ft.CrossAxisAlignment.START))

    card = ft.Container(
        form, width=440, padding=ft.Padding.symmetric(horizontal=42, vertical=26), border_radius=26,
        gradient=ft.LinearGradient(begin=ft.Alignment.TOP_LEFT,
                                   end=ft.Alignment.BOTTOM_RIGHT, colors=CARD_GRAD),
        border=ft.Border.all(1.5, CARD_BD),
        shadow=ft.BoxShadow(blur_radius=60, spread_radius=-10, offset=ft.Offset(0, 20),
                            color=CARD_GLOW))
    try:
        if hasattr(ft, "Blur"):
            card.blur = ft.Blur(24, 24)
    except Exception:
        pass

    def _card_hover(e):
        try:
            hov = e.data in (True, "true", "True")
            card.border = ft.Border.all(
                2.0 if hov else 1.5,
                _op(accent, (0.95 if hov else (0.38 if dark else 0.45))))
            card.shadow = ft.BoxShadow(
                blur_radius=(84 if hov else 60), spread_radius=-8,
                offset=ft.Offset(0, 20),
                color=_op(accent, (0.45 if hov else 0.22) if dark
                         else (0.30 if hov else 0.16)))
            card.update()
        except Exception:
            pass
    card.on_hover = _card_hover
    try:
        card.animate = 180
    except Exception:
        pass

    def _feature(icon, title, desc):
        return ft.Row([
            ft.Container(ft.Icon(icon, size=20, color=accent), width=44, height=44,
                         border_radius=13, alignment=ft.Alignment.CENTER,
                         bgcolor=_op(accent, 0.16 if dark else 0.10),
                         border=ft.Border.all(1, _op(accent, 0.5 if dark else 0.3))),
            ft.Column([
                ft.Text(title, size=15, color=LEFT_HEAD, weight=ft.FontWeight.W_700),
                ft.Container(height=2),
                ft.Text(desc, size=12.5, color=INK2, no_wrap=False),
            ], spacing=0, tight=True, expand=True)],
            spacing=15, vertical_alignment=ft.CrossAxisAlignment.START)

    value_prop = ft.Column([
        ft.Row([ft.Container(logo_img(60), width=60, height=60, border_radius=14,
                             bgcolor=None, alignment=ft.Alignment.CENTER),
                ft.Text("QA Studio", size=22, weight=ft.FontWeight.W_700,
                        color=LEFT_HEAD, font_family=DISP)],
               spacing=13, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=44),
        ft.Text(strings.t("login_hero_title"), size=52, weight=ft.FontWeight.W_700,
                color=LEFT_HEAD, font_family=DISP, style=ft.TextStyle(height=1.05)),
        ft.Container(height=16),
        ft.Text(strings.t("login_hero_desc"),
                size=14, color=INK2, no_wrap=False),
        ft.Container(height=34),
        _feature(ft.Icons.AUTO_AWESOME, strings.t("login_feature1_title"),
                 strings.t("login_feature1_desc")),
        ft.Container(height=20),
        _feature(ft.Icons.CHECKLIST, strings.t("login_feature2_title"),
                 strings.t("login_feature2_desc")),
        ft.Container(height=20),
        _feature(ft.Icons.DESCRIPTION_OUTLINED, strings.t("login_feature3_title"),
                 strings.t("login_feature3_desc")),
    ], spacing=0)

    bg = _bg()
    # Over-scale the backdrop so it ALWAYS over-covers the window (no dark
    # "shell" showing through) and leaves room to shift for the parallax.
    # scale 1.3 => 15% overhang on every side, so the parallax shift (max ~3.5%)
    # can never expose an edge/gap. animate kept short so it tracks the cursor.
    #
    # BUG FIX (black gutters left/right persisted even after giving _bg()'s
    # DecorationImage container explicit width/height): `expand=True` on a
    # Stack CHILD isn't the same as Flutter's Positioned.fill() — on this
    # Flet build it doesn't reliably give this Container a tight/bound size,
    # so it (and the DecorationImage inside it) sized itself to the image's
    # own aspect instead of the Stack's actual bounds, no matter what fit or
    # width/height the inner Image was given. Pinning all four edges
    # (left/top/right/bottom=0) is Flet's actual "fill the Stack" idiom —
    # it maps straight to Positioned.fill(), which forces a real tight
    # constraint down through the whole child tree.
    # Layer scale doubles as parallax headroom (the image must overflow the
    # viewport so a shift never exposes an edge). Now that mobile uses `cover`
    # (fills the screen, like desktop) both platforms use 1.3 — 15% overhang on
    # every side, ample headroom for the tilt parallax below. (Was 1.22 for the
    # old `contain` mobile fit, which no longer applies.)
    bg_layer = ft.Container(bg, left=0, top=0, right=0, bottom=0,
                            scale=1.3,
                            offset=ft.Offset(0, 0),
                            animate_offset=120, animate_scale=120)

    try:
        width = app.page.width or 0
    except Exception:
        width = 0

    # Play the card entrance on show / mode-switch / theme-toggle, but NOT on the
    # busy re-render triggered by clicking Sign in (that made it flash/rebuild
    # right before the app opens).
    _card_shown = card if busy else app._entrance(card, dy=0.06, scale=0.97, dur=480)
    centered_card = ft.Column([_card_shown],
                              alignment=ft.MainAxisAlignment.CENTER,
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                              expand=True)
    if width and width < 900:
        content = ft.Container(centered_card, expand=True, padding=24)
    else:
        content = ft.Row([
            ft.Container(ft.Column([value_prop], alignment=ft.MainAxisAlignment.CENTER,
                                   expand=True),
                         expand=5, padding=56,
                         gradient=ft.LinearGradient(
                             begin=ft.Alignment.CENTER_LEFT,
                             end=ft.Alignment.CENTER_RIGHT, colors=SCRIM)),
            # Reserve a little more room for the fixed footer. This moves the
            # card up by 16px without changing its content or card dimensions.
            ft.Container(centered_card, expand=4,
                         padding=ft.Padding.only(left=30, right=30, top=30, bottom=62)),
        ], spacing=0, expand=True)

    # Footer (version • copyright • status), like the original login.
    _ver = ""
    try:
        with open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                "VERSION"), encoding="utf-8") as _vf:
            _ver = _vf.read().strip()
    except Exception:
        _ver = ""
    _fmono = MONO
    if platform_caps.is_mobile():
        # The desktop footer is 3 segments (version / copyright / status)
        # separated by two expand=True spacers inside 44px of horizontal
        # padding — on a ~390px phone that's nowhere near enough room even
        # for the copyright line alone ("© 2026 QA Studio Terminal. All
        # rights reserved."), so it rendered cut off mid-sentence with
        # System Status pushed off-screen entirely. Condensed to two short,
        # centered lines with the copyright shortened and no expand spacers
        # (nothing left to fight over for space).
        footer = ft.Container(
            ft.Column([
                ft.Row([ft.Container(width=7, height=7, border_radius=4, bgcolor="#22c55e"),
                        ft.Text(strings.t("login_system_status"), size=10, color=INK2, font_family=_fmono,
                                style=ft.TextStyle(letter_spacing=0.8))],
                       spacing=6, tight=True, alignment=ft.MainAxisAlignment.CENTER,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=4),
                ft.Text((("QA STUDIO v" + _ver) if _ver else "QA STUDIO") + "  ·  © 2026",
                        size=9.5, color=_op(accent, 0.85), font_family=_fmono,
                        text_align=ft.TextAlign.CENTER,
                        style=ft.TextStyle(letter_spacing=0.6)),
            ], spacing=0, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            left=0, right=0, bottom=0,
            padding=ft.Padding.symmetric(vertical=10, horizontal=16))
    else:
        footer = ft.Container(
            ft.Row([
                ft.Text(("QA STUDIO v" + _ver) if _ver else "QA STUDIO", size=11,
                        color=_op(accent, 0.85), font_family=_fmono,
                        style=ft.TextStyle(letter_spacing=1.4)),
                ft.Container(expand=True),
                ft.Text(strings.t("login_copyright"), size=11,
                        color=INK2, font_family=_fmono,
                        style=ft.TextStyle(letter_spacing=0.4)),
                ft.Container(expand=True),
                ft.Row([ft.Container(width=8, height=8, border_radius=4, bgcolor="#22c55e"),
                        ft.Text(strings.t("login_system_status"), size=11, color=INK2, font_family=_fmono,
                                style=ft.TextStyle(letter_spacing=1.0))],
                       spacing=7, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            left=0, right=0, bottom=0,
            padding=ft.Padding.symmetric(vertical=14, horizontal=44))

    # Expose the backdrop layer so the app's top-level gesture layer can drive
    # the mouse-move parallax (see render() / _login_parallax).
    app._login_bg_layer = bg_layer

    # Mobile: no mouse, so on_hover above never fires there — the requested
    # equivalent is tilting/moving the PHONE instead of the cursor. Backed
    # by mobile_tilt.py (flet.Accelerometer, core-bundled, same posture as
    # ft.Wakelock/ft.Share this session). enable() is safe to call on every
    # render of this screen (it just re-arms the same stream + callback);
    # _submit()'s success path calls disable() once signed in so the sensor
    # isn't left streaming for the rest of the app session.
    if platform_caps.is_mobile():
        def _tilt(mx, my, _lay=bg_layer):
            # Same offset formula login_parallax uses for the mouse — see
            # mobile_tilt.py's docstring for why (mx, my) are normalized to
            # the same rough [-0.5, 0.5] range there.
            try:
                # Tilt parallax. Now that the mobile backdrop is `cover` at 1.3x
                # (15% side/vertical headroom, no dark bands), horizontal and
                # vertical use the same gentle shift and both stay well within
                # the overhang so no edge is ever exposed. (Was 0.10/0.16 for the
                # old `contain` fit, which had asymmetric bands to move within.)
                _lay.offset = ft.Offset(-mx * 0.12, -my * 0.12)
                _lay.update()
            except Exception:
                pass
        try:
            import mobile_tilt
            mobile_tilt.enable(_tilt)
        except Exception:
            pass

    # RE-CHECK for updates whenever the login screen renders (throttled to ~12s), so
    # a version published while the app sits on login pops the banner promptly. A
    # one-time / stale-guarded check meant a version bumped AFTER the first check
    # never showed. render() already wraps every view — including this one — with the
    # banner, so we don't build one here; we just keep the check fresh.
    try:
        import time as _tt, threading as _th
        if _tt.time() - getattr(app, "_login_update_check_ts", 0) > 12:
            app._login_update_check_ts = _tt.time()
            _th.Thread(target=app._run_update_check, daemon=True).start()
    except Exception:
        pass

    _stack = ft.Stack([bg_layer, content, footer], expand=True)
    # Mouse-move parallax via a GestureDetector wrapping the whole login
    # (on_hover passed as a constructor arg for reliable registration).
    try:
        return ft.GestureDetector(content=_stack, on_hover=app._login_parallax,
                                  hover_interval=16, expand=True)
    except Exception:
        try:
            return ft.GestureDetector(content=_stack,
                                      on_hover=app._login_parallax, expand=True)
        except Exception:
            return _stack

# ---- window shell ----
