"""auth_screen.py — the sign-in / sign-up / forgot-password gate for QA Studio.

A theme-aware split login modeled on the Stitch design:
  • Full-bleed perspective neon-grid backdrop (dark cyan-on-navy / light gray).
  • Left: brand value prop over the grid.
  • Right: a glowing-bordered glass card with icon fields, a gradient cyan
    "Sign in" button, hover/entrance animation.
On narrow windows it collapses to a single centered card. Uses only Flet APIs the
app's build supports; the grid images are base64-embedded (login_assets.py).
"""
import os
import base64
import tempfile

import flet as ft
import theme as T
import auth_supabase as auth

try:
    import login_assets as LA
except Exception:
    LA = None

# Image fit enum name differs across Flet builds (ImageFit vs BoxFit).
_FIT = getattr(ft, "ImageFit", None) or getattr(ft, "BoxFit", None)
_COVER = getattr(_FIT, "COVER", None) if _FIT else None


def _op(c, o):
    return ft.Colors.with_opacity(o, c)


def _grid_bg(app, dark):
    """Full-window grid backdrop. Decodes the embedded JPEG to a cached temp file and
    uses ft.Image(src=path) sized to the WHOLE window (Image with `expand` doesn't
    fill a Stack reliably on this Flet build, which left the right half empty). Falls
    back to a gradient if anything is unavailable."""
    try:
        W = int(app.page.width or 0) or 1280
        H = int(app.page.height or 0) or 800
    except Exception:
        W, H = 1280, 800
    img = None
    if LA is not None:
        key = "_grid_path_dark" if dark else "_grid_path_light"
        path = getattr(app, key, None)
        if not path or not os.path.exists(path):
            try:
                b64 = LA.GRID_DARK_B64 if dark else LA.GRID_LIGHT_B64
                tf = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                tf.write(base64.b64decode(b64))
                tf.close()
                path = tf.name
                setattr(app, key, path)
            except Exception:
                path = None
        if path:
            try:
                img = (ft.Image(src=path, width=W, height=H, fit=_COVER) if _COVER
                       else ft.Image(src=path, width=W, height=H))
            except Exception:
                try:
                    img = ft.Image(src=path, width=W, height=H)   # without `fit`
                except Exception:
                    img = None
    if img is not None:
        return img
    return ft.Container(expand=True, gradient=ft.LinearGradient(
        begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
        colors=(["#0B1024", "#0E1F38", "#0A1A2E"] if dark
                else ["#FFFFFF", "#EEF3F9", "#FFFFFF"])))


def _init(app):
    for k, v in (("_auth_mode", "signin"), ("_auth_busy", False),
                 ("_auth_msg", None), ("_auth_email", ""), ("_auth_name", "")):
        if not hasattr(app, k):
            setattr(app, k, v)


def _field(label, icon, dark, value="", password=False):
    return ft.TextField(
        label=label, value=value or "", password=password,
        can_reveal_password=password, prefix_icon=icon, filled=True,
        bgcolor=(_op(T.VIOLET, 0.06) if dark else T.CARD_2),
        border_color=(_op(T.VIOLET, 0.45) if dark else T.BORDER),
        focused_border_color=T.VIOLET, focused_bgcolor=_op(T.VIOLET, 0.10),
        cursor_color=T.VIOLET, text_size=14.5, color=T.INK,
        label_style=ft.TextStyle(size=13, color=T.INK_2),
        content_padding=ft.Padding.symmetric(vertical=18, horizontal=14),
        border_radius=13)


def screen(app):
    _init(app)
    from main import logo_img

    dark = (getattr(T, "MODE", "light") == "dark")
    signup = (app._auth_mode == "signup")
    accent = T.VIOLET
    accent_h = getattr(T, "VIOLET_H", T.VIOLET)

    name_f = _field("Full name", ft.Icons.PERSON_OUTLINE, dark, app._auth_name) if signup else None
    email_f = _field("Email", ft.Icons.MAIL_OUTLINE, dark, app._auth_email)
    pwd_f = _field("Password", ft.Icons.LOCK_OUTLINE, dark, password=True)

    def _stash():
        app._auth_email = email_f.value or ""
        if name_f is not None:
            app._auth_name = name_f.value or ""

    def _switch(_e):
        _stash()
        app._auth_mode = "signup" if not signup else "signin"
        app._auth_msg = None
        app.ui_safe(app.render)

    def _submit(_e=None):
        if app._auth_busy:
            return
        _stash()
        if not (email_f.value or "").strip() or not (pwd_f.value or ""):
            app._auth_msg = ("err", "Enter your email and password.")
            app.ui_safe(app.render)
            return
        app._auth_busy = True
        app._auth_msg = None
        app.ui_safe(app.render)

        def work():
            try:
                if signup:
                    ok, msg, user = auth.sign_up(
                        email_f.value, pwd_f.value,
                        name=(name_f.value if name_f is not None else None))
                else:
                    ok, msg, user = auth.sign_in(email_f.value, pwd_f.value)
                app._auth_busy = False
                if user:
                    app.user = user
                    app._auth_msg = None
                    app.ui_safe(app.render)
                    return
                app._auth_msg = ("ok" if ok else "err", msg)
                if ok and signup:
                    app._auth_mode = "signin"
                app.ui_safe(app.render)
            except Exception as ex:
                app._auth_busy = False
                app._auth_msg = ("err", f"Something went wrong: {ex}")
                app.ui_safe(app.render)
        app._bg(work)

    def _forgot(_e):
        _stash()
        em = (email_f.value or "").strip()
        if not em:
            app._auth_msg = ("err", "Enter your email above first, then tap "
                                    "“Forgot password”.")
            app.ui_safe(app.render)
            return
        app._auth_busy = True
        app._auth_msg = None
        app.ui_safe(app.render)

        def work():
            ok, msg = auth.request_password_reset(em)
            app._auth_busy = False
            app._auth_msg = ("ok" if ok else "err", msg)
            app.ui_safe(app.render)
        app._bg(work)

    # ── interactive helpers ──────────────────────────────────────────────────
    def _btn_hover(e):
        try:
            hov = e.data in (True, "true", "True")
            e.control.scale = 1.03 if hov else 1.0
            e.control.offset = ft.Offset(0, -0.04) if hov else ft.Offset(0, 0)
            e.control.update()
        except Exception:
            pass

    def _link_hover(e):
        try:
            hov = e.data in (True, "true", "True")
            e.control.bgcolor = _op(T.VIOLET, 0.14) if hov else ft.Colors.TRANSPARENT
            e.control.update()
        except Exception:
            pass

    def _link(text, on_click):
        return ft.Container(
            ft.Text(text, size=12, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK),
            on_click=on_click, ink=True, border_radius=7, on_hover=_link_hover,
            padding=ft.Padding.symmetric(vertical=4, horizontal=7),
            bgcolor=ft.Colors.TRANSPARENT)

    busy = app._auth_busy
    btn_label = ("Creating account…" if (busy and signup) else "Signing in…" if busy
                 else "Create account" if signup else "Sign in")
    btn = ft.Container(
        ft.Row([
            (ft.ProgressRing(width=17, height=17, stroke_width=2.4, color="#FFFFFF")
             if busy else ft.Icon(ft.Icons.ARROW_FORWARD, size=18, color="#FFFFFF")),
            ft.Text(btn_label, size=14.5, weight=ft.FontWeight.W_800, color="#FFFFFF"),
        ], alignment=ft.MainAxisAlignment.CENTER, spacing=10, tight=True),
        height=52, border_radius=13, alignment=ft.Alignment.CENTER,
        gradient=ft.LinearGradient(begin=ft.Alignment.CENTER_LEFT,
                                   end=ft.Alignment.CENTER_RIGHT,
                                   colors=["#19BDDC", accent, accent_h]),
        border=ft.Border.all(1, _op("#FFFFFF", 0.25)),
        shadow=ft.BoxShadow(blur_radius=30, spread_radius=-4, offset=ft.Offset(0, 10),
                            color=_op(accent, 0.6)),
        ink=True, on_click=(None if busy else _submit),
        on_hover=(None if busy else _btn_hover),
        scale=1.0, animate_scale=130, offset=ft.Offset(0, 0), animate_offset=130,
        opacity=(0.75 if busy else 1.0), animate_opacity=160)

    banner = None
    if app._auth_msg:
        kind, text = app._auth_msg
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

    # ── form column (shared by split + narrow) ───────────────────────────────
    def _form(with_logo):
        head = []
        if with_logo:
            head = [ft.Row([
                ft.Container(logo_img(34), width=44, height=44, border_radius=13,
                             bgcolor="#FFFFFF", alignment=ft.Alignment.CENTER),
                ft.Text("QA Studio", size=18, weight=ft.FontWeight.W_900, color=T.INK),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=18)]
        head += [
            ft.Text("Welcome back" if not signup else "Create your account",
                    size=27, weight=ft.FontWeight.W_900, color=T.INK),
            ft.Text("Sign in to continue to QA Studio" if not signup
                    else "It only takes a moment to get started",
                    size=13.5, color=T.INK_2, weight=ft.FontWeight.W_500),
            ft.Container(height=22),
        ]
        fields = [c for c in (name_f, email_f, pwd_f) if c is not None]
        rows = list(head)
        for f in fields:
            rows += [f, ft.Container(height=14)]
        if not signup:
            rows.append(ft.Row([_link("Forgot password?", _forgot)],
                               alignment=ft.MainAxisAlignment.END))
        if banner:
            rows += [ft.Container(height=6), banner]
        rows += [ft.Container(height=16), btn, ft.Container(height=16),
                 ft.Row([ft.Text("New to QA Studio?" if not signup
                                 else "Already have an account?",
                                 size=12.5, color=T.INK_2, weight=ft.FontWeight.W_600),
                         _link("Create one" if not signup else "Sign in", _switch)],
                        spacing=6, alignment=ft.MainAxisAlignment.CENTER, tight=True)]
        return ft.Column(rows, spacing=0, width=400)

    # entrance: rise+scale in place (Flet animates only existing controls)
    def _entrance(child):
        # wraps the card and animates it in place (no expand — the centering is done
        # by the parent Column so the card hugs its content instead of stretching).
        c = ft.Container(child, offset=ft.Offset(0, 0.05), scale=0.97,
                         animate_offset=460, animate_scale=460)

        def _go():
            import time
            time.sleep(0.06)
            try:
                c.offset = ft.Offset(0, 0)
                c.scale = 1.0
                c.update()
            except Exception:
                pass
        try:
            app._bg(_go)
        except Exception:
            import threading
            threading.Thread(target=_go, daemon=True).start()
        return c

    def _centered(card):
        return ft.Column([_entrance(card)],
                         alignment=ft.MainAxisAlignment.CENTER,
                         horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                         expand=True)

    def _card(form):
        card = ft.Container(
            form, width=464, padding=42, border_radius=26,
            bgcolor=(_op(T.CARD, 0.86) if dark else T.CARD),
            border=ft.Border.all(2 if dark else 1.5, _op(accent, 0.9 if dark else 0.85)),
            shadow=ft.BoxShadow(blur_radius=54, spread_radius=-8, offset=ft.Offset(0, 0),
                                color=_op(accent, 0.6 if dark else 0.3)))
        if dark and hasattr(ft, "Blur"):
            try:
                card.blur = ft.Blur(18, 18)
            except Exception:
                pass
        return card

    # ── left value prop ──────────────────────────────────────────────────────
    def _feature(icon, text):
        return ft.Row([
            ft.Container(ft.Icon(icon, size=19, color=accent),
                         width=38, height=38, border_radius=12,
                         bgcolor=_op(accent, 0.18 if dark else 0.12),
                         border=ft.Border.all(1, _op(accent, 0.55 if dark else 0.25)),
                         alignment=ft.Alignment.CENTER),
            ft.Text(text, size=14, color=T.INK, weight=ft.FontWeight.W_700,
                    no_wrap=False, expand=True),
        ], spacing=15, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    value_prop = ft.Column([
        ft.Row([
            ft.Container(logo_img(42), width=58, height=58, border_radius=17,
                         bgcolor="#FFFFFF", alignment=ft.Alignment.CENTER),
            ft.Text("QA Studio", size=24, weight=ft.FontWeight.W_900, color=T.INK),
        ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=40),
        ft.Text("Ship better tests,\nfaster.", size=40, weight=ft.FontWeight.W_900,
                color=T.INK),
        ft.Container(height=12),
        ft.Text("AI-generated Azure DevOps test cases, regression & sprint plans, "
                "and one-click sprint closure reports — in Arabic or English.",
                size=14, color=T.INK_2, no_wrap=False, weight=ft.FontWeight.W_500),
        ft.Container(height=30),
        _feature(ft.Icons.AUTO_AWESOME, "Generate test titles & steps with AI"),
        ft.Container(height=15),
        _feature(ft.Icons.CHECKLIST, "Regression & sprint test plans"),
        ft.Container(height=15),
        _feature(ft.Icons.DESCRIPTION_OUTLINED, "One-click sprint closure reports"),
        ft.Container(expand=True),
        ft.Text("World of System & Software", size=11.5,
                color=T.INK_3, weight=ft.FontWeight.BOLD),
    ], spacing=0)

    # background grid (with graceful gradient fallback)
    bg = _grid_bg(app, dark)

    try:
        width = app.page.width or 0
    except Exception:
        width = 0

    # narrow: single centered card on the grid
    if width and width < 900:
        return ft.Stack([
            bg,
            ft.Container(_centered(_card(_form(with_logo=True))), expand=True,
                         padding=24),
        ], expand=True)

    # wide: value prop (left, with a readability scrim) + card (right)
    left = ft.Container(
        value_prop, expand=5, padding=48,
        gradient=ft.LinearGradient(
            begin=ft.Alignment.CENTER_LEFT, end=ft.Alignment.CENTER_RIGHT,
            colors=[_op(T.BG, 0.78), _op(T.BG, 0.30), _op(T.BG, 0.0)]))
    right = ft.Container(_centered(_card(_form(with_logo=False))),
                         expand=5, padding=30)

    return ft.Stack([
        bg,
        ft.Row([left, right], spacing=0, expand=True),
    ], expand=True)
