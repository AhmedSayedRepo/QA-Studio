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
from ui import card, grad, logo_img


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
        lay.offset = ft.Offset(-mx * 0.06, -my * 0.06)
        lay.update()
    except Exception:
        pass

def login_gate(app):
    """Native Flet sign-in — the app's only login (replaces the old WebView2
    window that kept freezing). Full-bleed neon backdrop + frosted-glass card,
    email/password with sign-up + forgot-password, matching the previous look
    but 100% Flet, so there is no embedded browser that can freeze."""
    import os as _os
    # The login keeps its OWN theme (defaults to dark, like the old WebView2
    # login) with a working toggle; the choice is applied to the whole app on
    # successful sign-in.
    if getattr(app, "_login_theme", None) not in ("dark", "light"):
        # Default the login to DARK (the neon look). Safe now that the window is
        # frameless — there's no OS title bar to mismatch the theme.
        app._login_theme = "dark"
    dark = (app._login_theme == "dark")
    signup = (getattr(app, "_auth_mode", "signin") == "signup")
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
        on_click=_toggle_login_theme, tooltip="Toggle theme",
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
        _cover = getattr(getattr(ft, "ImageFit", None), "COVER", None)
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
        if p and hasattr(ft, "DecorationImage"):
            for _kw in ([dict(src=p, fit=_cover, filter_quality=_fq)] if (_cover and _fq) else []) \
                       + ([dict(src=p, fit=_cover)] if _cover else []) + [dict(src=p)]:
                try:
                    return ft.Container(expand=True, image=ft.DecorationImage(**_kw), bgcolor=_base)
                except Exception:
                    continue
        if p:
            try:
                W = int(app.page.width or 0) or 1440
                H = int(app.page.height or 0) or 900
                _ik = dict(src=p, width=W, height=H)
                if _cover:
                    _ik["fit"] = _cover
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
            hint_style=ft.TextStyle(size=14, color=FIELD_IC),
            content_padding=ft.Padding.symmetric(vertical=17, horizontal=14),
            border_radius=12)
        col = ft.Column([
            ft.Text(cap, size=11, color=CAP, font_family=MONO,
                    weight=ft.FontWeight.W_600,
                    style=ft.TextStyle(letter_spacing=1.8)),
            ft.Container(height=7),
            tf,
        ], spacing=0)
        return tf, col

    name_tf, name_col = (_field("FULL NAME", "Full name", ft.Icons.PERSON_OUTLINE,
                                getattr(app, "_auth_name", "")) if signup
                         else (None, None))
    email_tf, email_col = _field("ACCESS IDENTIFIER", "Email", ft.Icons.MAIL_OUTLINE,
                                 getattr(app, "_auth_email", ""))
    pwd_tf, pwd_col = _field("SECURE PROTOCOL", "Password", ft.Icons.LOCK_OUTLINE,
                             password=True)

    def _stash():
        app._auth_email = email_tf.value or ""
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
            app._auth_msg = ("err", "Enter your email and password.")
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
                    app._switch_user_creds()   # load this user's own per-user creds
                    try:
                        T.apply_theme(app._login_theme)
                        app.creds["theme"] = app._login_theme
                        store.save(app.creds)
                        app.page.bgcolor = T.RAIL
                        app.page.theme_mode = (ft.ThemeMode.DARK
                            if app._login_theme == "dark" else ft.ThemeMode.LIGHT)
                    except Exception:
                        pass
                    app.active = "setup"
                    app._land_app = True   # play the entrance on the app view
                    app.ui_safe(app.render); return
                app._auth_msg = ("ok" if ok else "err", m)
                if ok and signup:
                    app._auth_mode = "signin"
                app.ui_safe(app.render)
            except Exception as ex:
                app._gate_busy = False
                app._auth_msg = ("err", f"Something went wrong: {ex}")
                app.ui_safe(app.render)
        try:
            app._bg(work)
        except Exception:
            threading.Thread(target=work, daemon=True).start()

    # Enter in any credential field submits (sign in / sign up).
    email_tf.on_submit = _submit
    pwd_tf.on_submit = _submit
    if name_tf is not None:
        name_tf.on_submit = _submit

    def _forgot(_e=None):
        _stash()
        em = (email_tf.value or "").strip()
        if not em:
            app._auth_msg = ("err", "Enter your email above first, then tap "
                                     "Forgot password.")
            app.ui_safe(app.render); return
        app._gate_busy = True; app._auth_msg = None
        app.ui_safe(app.render)

        def work():
            try:
                ok, m = auth.request_password_reset(em)
            except Exception as ex:
                ok, m = False, f"Something went wrong: {ex}"
            app._gate_busy = False
            app._auth_msg = ("ok" if ok else "err", m)
            app.ui_safe(app.render)
        try:
            app._bg(work)
        except Exception:
            threading.Thread(target=work, daemon=True).start()

    blabel = ("CREATING…" if (busy and signup) else "SIGNING IN…" if busy
              else "CREATE ACCOUNT" if signup else "SIGN IN")

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
        ink=True, on_click=(None if busy else _submit),
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

    rows = [
        ft.Row([ft.Container(logo_img(28), width=40, height=40, border_radius=12,
                             bgcolor="#FFFFFF", alignment=ft.Alignment.CENTER),
                ft.Text("QA Studio", size=17, weight=ft.FontWeight.W_700,
                        color=HEAD, font_family=DISP),
                ft.Container(expand=True),
                theme_btn],
               spacing=11, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=24),
        ft.Text("Welcome back" if not signup else "Create your account",
                size=34, weight=ft.FontWeight.W_700, color=HEAD, font_family=DISP),
        ft.Container(height=6),
        ft.Text("Sign in to continue to QA Studio" if not signup
                else "It only takes a moment to get started",
                size=13, color=INK2, font_family=MONO,
                style=ft.TextStyle(letter_spacing=0.4)),
        ft.Container(height=26),
    ]
    for col in [c for c in (name_col, email_col, pwd_col) if c is not None]:
        rows += [col, ft.Container(height=16)]
    if not signup:
        rows.append(ft.Row([_link("Forgot password?", _forgot)],
                           alignment=ft.MainAxisAlignment.END))
    if banner:
        rows += [ft.Container(height=8), banner]
    rows += [ft.Container(height=20), btn, ft.Container(height=18),
             ft.Row([ft.Text("New to QA Studio?" if not signup
                             else "Already have an account?",
                             size=12.5, color=INK2, weight=ft.FontWeight.W_600),
                     _link("Create one" if not signup else "Sign in", _switch)],
                    spacing=6, alignment=ft.MainAxisAlignment.CENTER, tight=True)]
    form = ft.Column(rows, spacing=0, width=352, tight=True)

    card = ft.Container(
        form, width=440, padding=42, border_radius=26,
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
        ft.Row([ft.Container(logo_img(34), width=48, height=48, border_radius=14,
                             bgcolor="#FFFFFF", alignment=ft.Alignment.CENTER),
                ft.Text("QA Studio", size=22, weight=ft.FontWeight.W_700,
                        color=LEFT_HEAD, font_family=DISP)],
               spacing=13, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=44),
        ft.Text("Ship better\ntests, faster.", size=52, weight=ft.FontWeight.W_700,
                color=LEFT_HEAD, font_family=DISP, style=ft.TextStyle(height=1.05)),
        ft.Container(height=16),
        ft.Text("AI-generated Azure DevOps test cases, regression & sprint plans, "
                "and one-click sprint closure reports.",
                size=14, color=INK2, no_wrap=False),
        ft.Container(height=34),
        _feature(ft.Icons.AUTO_AWESOME, "Generate test titles & steps with AI",
                 "Leverage advanced LLMs to automate boilerplate test creation."),
        ft.Container(height=20),
        _feature(ft.Icons.CHECKLIST, "Regression & sprint test plans",
                 "Orchestrate complex release cycles with modular planning tools."),
        ft.Container(height=20),
        _feature(ft.Icons.DESCRIPTION_OUTLINED, "One-click sprint closure reports",
                 "Instant stakeholder visibility with automated PDF exports."),
    ], spacing=0)

    bg = _bg()
    # Over-scale the backdrop so it ALWAYS over-covers the window (no dark
    # "shell" showing through) and leaves room to shift for the parallax.
    # scale 1.3 => 15% overhang on every side, so the parallax shift (max ~3.5%)
    # can never expose an edge/gap. animate kept short so it tracks the cursor.
    bg_layer = ft.Container(bg, expand=True, scale=1.3, offset=ft.Offset(0, 0),
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
            ft.Container(centered_card, expand=4, padding=30),
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
    footer = ft.Container(
        ft.Row([
            ft.Text(("QA STUDIO v" + _ver) if _ver else "QA STUDIO", size=11,
                    color=_op(accent, 0.85), font_family=_fmono,
                    style=ft.TextStyle(letter_spacing=1.4)),
            ft.Container(expand=True),
            ft.Text("© 2026 QA Studio Terminal. All rights reserved.", size=11,
                    color=INK2, font_family=_fmono,
                    style=ft.TextStyle(letter_spacing=0.4)),
            ft.Container(expand=True),
            ft.Row([ft.Container(width=8, height=8, border_radius=4, bgcolor="#22c55e"),
                    ft.Text("System Status", size=11, color=INK2, font_family=_fmono,
                            style=ft.TextStyle(letter_spacing=1.0))],
                   spacing=7, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
        left=0, right=0, bottom=0,
        padding=ft.Padding.symmetric(vertical=14, horizontal=44))

    # Expose the backdrop layer so the app's top-level gesture layer can drive
    # the mouse-move parallax (see render() / _login_parallax).
    app._login_bg_layer = bg_layer

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
