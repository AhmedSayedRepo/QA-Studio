"""ui.py — QA Studio shared, stateless UI builders.

Extracted from main.py (Step 1 of the modular refactor). These builders depend
only on Flet and the theme tokens (no app state), so screens and screen-modules
can import them directly instead of reaching into the QAStudio app class.
"""
import flet as ft
import theme as T

# Flet version-compatibility shim (ft.icons<->Icons / ft.colors<->Colors).
if not hasattr(ft, "icons") and hasattr(ft, "Icons"):
    ft.icons = ft.Icons
if not hasattr(ft, "colors") and hasattr(ft, "Colors"):
    ft.colors = ft.Colors
if not hasattr(ft, "Icons") and hasattr(ft, "icons"):
    ft.Icons = ft.icons
if not hasattr(ft, "Colors") and hasattr(ft, "colors"):
    ft.Colors = ft.colors


# ═══════════════════════════════════════════════════════════════════════════════
#  Small reusable builders
# ═══════════════════════════════════════════════════════════════════════════════
def _ic(name, fallback="CIRCLE"):
    """Safe icon lookup: some Material icon names vary across Flet builds, so fall
    back to a always-present icon instead of raising AttributeError at render."""
    return getattr(ft.Icons, name, None) or getattr(ft.Icons, fallback, ft.Icons.CIRCLE)


def card(content, padding=18, expand=False, bg=None, radius=T.R_LG):
    # bg read at CALL time (not as a default) so cards follow theme switches.
    return ft.Container(content=content, padding=padding,
                        bgcolor=(bg if bg is not None else T.CARD),
                        border=ft.Border.all(1, T.BORDER),
                        border_radius=radius, expand=expand,
                        # soft indigo-tinted elevation for depth
                        shadow=ft.BoxShadow(blur_radius=22, spread_radius=-12,
                                            offset=ft.Offset(0, 9),
                                            color=ft.Colors.with_opacity(0.10, "#1B1F3A")))


def two_col_cards(cards, gap=14, card_width=300):
    """MOBILE: lay a list of cards out two-per-row inside horizontally
    scrollable Rows (swipe the row sideways when the pair is wider than the
    screen — e.g. when a card's content is expanded). Each card is given a
    fixed width so the row overflows and scrolls instead of cramming.

    DESKTOP / non-mobile: unchanged single column — returns the cards
    interleaved with `gap` spacers, exactly like the old manual assembly.

    Returns a LIST of controls to splice into a screen's body Column. Any
    falsy entries in `cards` are dropped, so callers can pass optional cards
    inline. Defensive: on any error, falls back to the plain vertical list."""
    items = [c for c in (cards or []) if c is not None]
    try:
        import platform_caps as _pc
        mobile = _pc.is_mobile()
    except Exception:
        mobile = False
    if not mobile:
        out = []
        for i, c in enumerate(items):
            if i:
                out.append(ft.Container(height=gap))
            out.append(c)
        return out
    try:
        rows = []
        for i in range(0, len(items), 2):
            pair = items[i:i + 2]
            wrapped = [ft.Container(c, width=card_width) for c in pair]
            row = ft.Row(wrapped, spacing=gap, scroll=ft.ScrollMode.AUTO,
                         vertical_alignment=ft.CrossAxisAlignment.START,
                         tight=True)
            if rows:
                rows.append(ft.Container(height=gap))
            rows.append(row)
        return rows
    except Exception:
        out = []
        for i, c in enumerate(items):
            if i:
                out.append(ft.Container(height=gap))
            out.append(c)
        return out


def empty_state(icon, title, hint, tone="violet"):
    """Friendly placeholder for empty panels: a soft icon badge, a bold title,
    and a dim one-line hint, centered. Theme-aware via tokens read at call time."""
    soft = {"violet": T.VIOLET_SOFT, "green": T.GREEN_SOFT,
            "amber": T.AMBER_SOFT}.get(tone, T.VIOLET_SOFT)
    ink = {"violet": T.VIOLET_INK, "green": T.GREEN,
           "amber": T.AMBER}.get(tone, T.VIOLET_INK)
    return ft.Container(
        ft.Column([
            ft.Container(ft.Icon(icon, size=23, color=ink),
                         width=50, height=50, bgcolor=soft, border_radius=14,
                         alignment=ft.Alignment.CENTER),
            ft.Container(height=12),
            ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=T.INK,
                    text_align=ft.TextAlign.CENTER),
            ft.Container(height=4),
            ft.Text(hint, size=12, color=T.INK_3, weight=ft.FontWeight.W_500,
                    text_align=ft.TextAlign.CENTER),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
           alignment=ft.MainAxisAlignment.CENTER, spacing=0, tight=True),
        alignment=ft.Alignment.CENTER, expand=True,
        padding=ft.Padding.symmetric(vertical=28, horizontal=20))


def grad_text(value, size=32, weight=None, stops=None, font_family=None):
    """Brand-gradient text via ShaderMask (falls back to solid indigo on older
    Flet). Use for hero KPI numbers so they tie to the logo gradient."""
    weight = weight or ft.FontWeight.BOLD
    stops = stops or T.GRAD_LOGO
    txt = ft.Text(value, size=size, weight=weight, color="#FFFFFF",
                  font_family=font_family)
    try:
        return ft.ShaderMask(
            content=txt, blend_mode=ft.BlendMode.SRC_IN,
            shader=ft.LinearGradient(begin=ft.Alignment.TOP_LEFT,
                                     end=ft.Alignment.BOTTOM_RIGHT, colors=list(stops)))
    except Exception:
        txt.color = T.VIOLET_INK
        return txt


def skeleton_rows(n=4, row_h=46):
    """Placeholder 'skeleton' rows for loading states — reads as content loading,
    nicer than a bare spinner. Subtle pulse where the Flet build supports it."""
    def _bar(w=None):
        c = ft.Container(height=row_h, expand=(w is None), width=w,
                         bgcolor=T.CARD_2, border_radius=T.R,
                         border=ft.Border.all(1, T.BORDER_2))
        try:
            c.opacity = 0.7
            c.animate_opacity = ft.Animation(800, ft.AnimationCurve.EASE_IN_OUT)
        except Exception:
            pass
        return c
    return ft.Column([ft.Row([_bar(110), _bar()], spacing=12) for _ in range(n)],
                     spacing=10)

def sec_head(num, title, right=None):
    row = [
        ft.Container(ft.Text(num, size=12, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK),
                     width=22, height=22, bgcolor=T.VIOLET_SOFT, border_radius=7,
                     alignment=ft.Alignment.CENTER),
        ft.Text(title, size=13.5, weight=ft.FontWeight.BOLD, color=T.INK),
    ]
    if right:
        row += [ft.Container(expand=True), right]
    return ft.Row(row, spacing=9, vertical_alignment=ft.CrossAxisAlignment.CENTER)

def field_label(text, req=False, hint=None, info=None, info_url=None, on_info=None):
    parts = [ft.Text(text, size=12, weight=ft.FontWeight.BOLD, color=T.INK_2)]
    if req:
        parts.append(ft.Text("*", size=12, color=T.RED, weight=ft.FontWeight.BOLD))
    if info or info_url or on_info:
        parts.append(ft.IconButton(
            icon=ft.Icons.INFO_OUTLINE, icon_size=15, icon_color=T.INK_3,
            tooltip=(info or "How to get this"), on_click=on_info,
            style=ft.ButtonStyle(padding=ft.Padding.all(0)),
            width=24, height=24))
    if hint:
        parts.append(ft.Container(
            ft.Text(hint, size=10, color=T.INK_3, weight=ft.FontWeight.BOLD),
            padding=ft.Padding.symmetric(vertical=2, horizontal=7),
            bgcolor=T.CARD_2, border_radius=10, margin=ft.Margin.only(left=4)))
    return ft.Row(parts, spacing=4, tight=True, height=24,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)

def grad(stops, diagonal=True):
    """Build an ft.LinearGradient from a list of hex stops.
    diagonal=True → top-left→bottom-right (buttons, tiles); False → top→bottom (rail)."""
    if diagonal:
        b, e = ft.Alignment.TOP_LEFT, ft.Alignment.BOTTOM_RIGHT
    else:
        b, e = ft.Alignment.TOP_CENTER, ft.Alignment.BOTTOM_CENTER
    return ft.LinearGradient(begin=b, end=e, colors=list(stops))


def _grad_button(text, icon, on_click, stops, shadow_rgb, shadow_a,
                 expand=False, disabled=False, height=46, wrap=False):
    """Gradient pill button (Container-based) matching the mockup CTAs.
    Same call shape as primary_btn/green_btn so call sites don't change."""
    inner = []
    if icon:
        inner.append(ft.Icon(icon, size=17, color="#FFFFFF"))
    if wrap:
        inner.append(ft.Text(text, size=14, weight=ft.FontWeight.W_700, color="#FFFFFF",
                             text_align=ft.TextAlign.CENTER, max_lines=2, expand=True))
    else:
        inner.append(ft.Text(text, size=14, weight=ft.FontWeight.W_700, color="#FFFFFF"))
    row = ft.Row(inner, spacing=8, tight=(not wrap),
                 alignment=ft.MainAxisAlignment.CENTER,
                 vertical_alignment=ft.CrossAxisAlignment.CENTER)
    c = ft.Container(
        row, height=height, border_radius=T.R, alignment=ft.Alignment.CENTER,
        padding=ft.Padding.symmetric(horizontal=18, vertical=0),
        gradient=grad(stops), ink=True,
        on_click=(None if disabled else on_click),
        opacity=(0.45 if disabled else 1),
        shadow=(None if disabled else _btn_shadow(shadow_rgb, shadow_a)))
    # subtle hover lift (press-ready CTA feel)
    if not disabled:
        try:
            c.animate_scale = ft.Animation(120, ft.AnimationCurve.EASE_OUT)

            def _hov(e, _c=c):
                try:
                    _c.scale = 1.018 if e.data == "true" else 1.0
                    _c.update()
                except Exception:
                    pass
            c.on_hover = _hov
        except Exception:
            pass
    if expand:
        c.expand = True
        return ft.Row([c], spacing=0)
    return c


def _btn_shadow(color_rgb, alpha=0.55):
    """BoxShadow matching the design CSS: 0 6px 16px -6px rgba(color,a).
    blur_style is version-dependent in Flet, so only pass it when available."""
    kwargs = dict(spread_radius=-6, blur_radius=16, offset=ft.Offset(0, 6),
                  color=ft.Colors.with_opacity(alpha, color_rgb))
    _bs = getattr(ft, "ShadowBlurStyle", None) or getattr(ft, "BlurStyle", None)
    if _bs is not None and hasattr(_bs, "OUTER"):
        try:
            kwargs["blur_style"] = _bs.OUTER
        except Exception:
            pass
    return ft.BoxShadow(**kwargs)

def _shadow_wrap(widget, color_rgb, alpha, expand, radius=None):
    """Wrap a button in a Container carrying the design drop-shadow.
    Keeps full-width behavior via the expand flag."""
    radius = radius if radius is not None else T.R
    cont = ft.Container(widget, border_radius=radius,
                        shadow=_btn_shadow(color_rgb, alpha))
    if expand:
        cont.expand = True
        return ft.Row([cont], spacing=0)   # full width, fixed height
    return cont

def _wrap_btn(btn, expand):
    # expand=True → full WIDTH only. The button expands horizontally inside a Row.
    # IMPORTANT: the Row must NOT have expand=True — in a Column that means vertical
    # flex, which would split the leftover height with any spacer and create gaps.
    if not expand:
        return btn
    btn.expand = True               # fill the Row horizontally
    return ft.Row([btn], spacing=0) # Row height = button's fixed height (no vertical flex)

# ── Brand logo (loaded once as base64 so it works without an assets_dir) ──────
_LOGO_B64 = None
def _logo_path():
    """Absolute path to the logo file on disk, or '' if none is present.
    A file-path image is cached by Flet's renderer and does NOT flash on
    re-mount (unlike base64), which is what caused the logo to flicker on
    every button click that rebuilds the page."""
    global _LOGO_PATH
    try:
        return _LOGO_PATH
    except NameError:
        pass
    _LOGO_PATH = ""
    try:
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        for name in ("app.png", "qa-logo.png"):
            p = os.path.join(here, name)
            if os.path.exists(p):
                _LOGO_PATH = p
                break
    except Exception:
        _LOGO_PATH = ""
    return _LOGO_PATH

def _logo_b64():
    global _LOGO_B64
    if _LOGO_B64 is None:
        _LOGO_B64 = ""
        try:
            import os, base64
            here = os.path.dirname(os.path.abspath(__file__))
            for name in ("app.png", "qa-logo.png"):
                p = os.path.join(here, name)
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        _LOGO_B64 = base64.b64encode(f.read()).decode("ascii")
                    break
        except Exception:
            _LOGO_B64 = ""
    return _LOGO_B64

_LOGO_CTL = {}
def logo_img(size=38, fallback_icon=None, fallback_color="#FFFFFF"):
    """Brand logo as an ft.Image; falls back to an icon if the file is missing.
    The built control is cached per (size, fallback) and REUSED across renders so
    Flet doesn't re-decode the base64 on every button click (which caused a flicker).
    Avoids ft.ImageFit / Image.border_radius hard deps (absent in some Flet builds)."""
    ckey = (size, fallback_icon, fallback_color)
    cached = _LOGO_CTL.get(ckey)
    if cached is not None:
        return cached
    b = _logo_b64()
    path = _logo_path()
    if path or b:
        img = None
        # Prefer a FILE PATH src: Flet's renderer caches file-path images and does
        # not re-fetch/flash them when the page is rebuilt on each click. Fall back
        # to a data: URI, then src_base64, for environments where the file isn't
        # reachable (e.g. web mode serving from a different working dir).
        for attempt in (
            (lambda: ft.Image(src=path, width=size, height=size)) if path else None,
            (lambda: ft.Image(src=f"data:image/png;base64,{b}", width=size, height=size)) if b else None,
            (lambda: ft.Image(src_base64=b, width=size, height=size)) if b else None,
        ):
            if attempt is None:
                continue
            try:
                img = attempt()
                break
            except Exception:
                img = None
        if img is not None:
            # ft.ImageFit was renamed ft.BoxFit in newer Flet (0.85.x+) — on
            # those builds getattr(ft, "ImageFit", None) is silently None, so
            # this always skipped and the logo never got an explicit fit (see
            # the same root cause fixed in login.py's login backdrop).
            _fit = getattr(ft, "BoxFit", None) or getattr(ft, "ImageFit", None)
            if _fit is not None and hasattr(_fit, "CONTAIN"):
                try:
                    img.fit = _fit.CONTAIN
                except Exception:
                    pass
            try:
                img.border_radius = int(size * 0.29)
            except Exception:
                pass
            _LOGO_CTL[ckey] = img
            return img
    fb = ft.Icon(fallback_icon or ft.Icons.SCIENCE_OUTLINED,
                 color=fallback_color, size=int(size * 0.55))
    _LOGO_CTL[ckey] = fb
    return fb


# Global read-only flag: when True (a signed-in Viewer), every button built via the
# helpers below renders disabled, so the whole app becomes look-but-don't-touch.
# render() sets this each frame based on the current user's role.
_READONLY = False


def primary_btn(text, icon=None, on_click=None, expand=False, disabled=False, wrap=False):
    return _grad_button(text, icon, on_click, T.GRAD_PRIMARY, T.VIOLET, 0.6,
                        expand=expand, disabled=disabled or _READONLY, height=46, wrap=wrap)


def _disabled_wrap(w, disabled, op=0.45):
    """Uniform disabled look across ALL button types: dim + drop the shadow.
    (Gradient buttons already dim themselves; this matches ghost/danger to them.)"""
    if disabled:
        try:
            w.opacity = op
            w.shadow = None
        except Exception:
            pass
    return w


def green_btn(text, icon=None, on_click=None, expand=False, height=42, disabled=False,
              ignore_ro=False):
    # ignore_ro=True: this button is gated by its OWN per-action permission (passed
    # as `disabled`), not by the screen-level read-only flag.
    return _grad_button(text, icon, on_click, T.GRAD_GREEN, T.GREEN, 0.5,
                        expand=expand, height=height,
                        disabled=disabled or (_READONLY and not ignore_ro))

def ghost_btn(text, icon=None, on_click=None, expand=False, disabled=False,
              ignore_ro=False):
    disabled = disabled or (_READONLY and not ignore_ro)
    btn = ft.OutlinedButton(
        text, icon=icon, on_click=(None if disabled else on_click), height=46,
        style=ft.ButtonStyle(color=(T.INK_3 if disabled else T.INK_2),
            side=ft.BorderSide(1, T.BORDER),
            shape=ft.RoundedRectangleBorder(radius=T.R),
            padding=ft.Padding.symmetric(horizontal=16, vertical=0)))
    return _disabled_wrap(_wrap_btn(btn, expand), disabled, op=0.55)

def danger_btn(text, icon=None, on_click=None, disabled=False, expand=False):
    # `expand` added to match green_btn/ghost_btn, which already had it. Without
    # it a danger action couldn't share a Row evenly with its sibling, so on
    # mobile the primary button stretched full-width and Stop wrapped onto its
    # own line at a mismatched size (Remote Runs' Resume/Stop pair).
    disabled = disabled or _READONLY
    btn = ft.FilledButton(
        text, icon=icon, on_click=(None if disabled else on_click), height=40,
        style=ft.ButtonStyle(
            bgcolor=T.RED, color="#FFFFFF", elevation=0,
            shape=ft.RoundedRectangleBorder(radius=T.R),
            padding=ft.Padding.symmetric(horizontal=18, vertical=0)))
    # design shadow: 0 6px 16px -6px rgba(224,71,77,.6)
    out = _disabled_wrap(_shadow_wrap(btn, T.RED, 0.55, False), disabled)
    if expand:
        try:
            out.expand = True
        except Exception:
            pass
    return out

def searchable_dropdown(**kwargs):
    """ft.Dropdown that is type-to-filter on newer Flet, degrading gracefully."""
    try:
        return ft.Dropdown(editable=True, enable_filter=True, menu_height=320, **kwargs)
    except TypeError:
        try:
            return ft.Dropdown(menu_height=320, **kwargs)
        except TypeError:
            return ft.Dropdown(**kwargs)


def hover_border_cb(field, base=None):
    """Return an on_hover handler that tints a field's frame violet on hover.
    Attach it to the Container that ALREADY wraps the field (no extra nesting,
    so layout/width is untouched). While focused, Flet draws
    focused_border_color regardless, so the hover tint only affects the
    unfocused state — no conflict."""
    base = base if base is not None else (getattr(field, "border_color", None) or T.BORDER)

    def _h(e, _f=field, _b=base):
        try:
            if getattr(_f, "read_only", False) or getattr(_f, "disabled", False):
                return
            _f.border_color = T.VIOLET if e.data in (True, "true", "True") else _b
            _f.update()
        except Exception:
            pass
    return _h


def hover_field(field, base=None):
    """Wrap a field so its frame highlights on hover, for use INSIDE a Row
    (e.g. field + Update button). The wrapper mirrors the field's `expand` so
    it fills the row exactly as the bare field did. For a field that sits alone
    in a Column-level Container, prefer attaching hover_border_cb() to that
    existing container instead (avoids nesting). Read-only / disabled fields are
    returned unwrapped (no hover affordance)."""
    try:
        if getattr(field, "read_only", False) or getattr(field, "disabled", False):
            return field
        return ft.Container(
            field, on_hover=hover_border_cb(field, base),
            expand=bool(getattr(field, "expand", False)),
            border_radius=(getattr(field, "border_radius", None) or T.R))
    except Exception:
        return field


def progress_ring(pct, color, size=44, label=None):
    """A circular progress ring with a percentage in the center."""
    pct = max(0, min(100, int(pct)))
    ring = ft.ProgressRing(value=pct/100, width=size, height=size, stroke_width=4,
                           color=color, bgcolor="#ECEAF2")
    center = ft.Text(f"{label if label is not None else pct}%", size=12,
                     weight=ft.FontWeight.BOLD, color=color)
    return ft.Stack([ring, ft.Container(center, width=size, height=size,
                                        alignment=ft.Alignment.CENTER)],
                    width=size, height=size)

def stat_tile(label, num, tone=None, sub=None):
    tone_colors = {"green": T.GREEN, "amber": T.AMBER, "red": T.RED, "violet": T.VIOLET_INK}
    numc = tone_colors.get(tone, T.INK)
    label_row = [ft.Text(label, size=10.5, color=T.INK_2, weight=ft.FontWeight.BOLD,
                         expand=True, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                         tooltip=label)]
    if tone:  # colored status dot (matches design)
        label_row.append(ft.Container(width=8, height=8, bgcolor=numc, border_radius=5))
    _numstops = {"green": T.GRAD_GREEN, "violet": T.GRAD_LOGO}.get(tone, T.GRAD_LOGO)
    children = [
        ft.Row(label_row, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Row([
            grad_text(str(num), size=22, weight=ft.FontWeight.BOLD, stops=_numstops),
            ft.Text(sub or "", size=12, color=T.INK_3, weight=ft.FontWeight.BOLD),
        ], spacing=2, vertical_alignment=ft.CrossAxisAlignment.END),
    ]
    return ft.Container(ft.Column(children, spacing=3), padding=ft.Padding.symmetric(vertical=14, horizontal=12),
                        bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER),
                        border_radius=T.R, expand=True)

def badge(text, kind="grey", icon=None):
    palette = {
        "green": (T.GREEN_SOFT, T.GREEN), "amber": (T.AMBER_SOFT, T.AMBER),
        "red": (T.RED_SOFT, T.RED), "violet": (T.VIOLET_SOFT, T.VIOLET_INK),
        "grey": (T.CARD_2, T.INK_2),
    }
    bg, fg = palette.get(kind, palette["grey"])
    row = []
    if icon: row.append(ft.Icon(icon, size=12, color=fg))
    row.append(ft.Text(text, size=11, weight=ft.FontWeight.BOLD, color=fg))
    return ft.Container(ft.Row(row, spacing=4, tight=True),
                        padding=ft.Padding.symmetric(vertical=8, horizontal=3), bgcolor=bg, border_radius=20)
