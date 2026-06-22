"""main.py — QA Studio (Flet desktop app).
Run:  pip install flet pillow anthropic openai azure-devops requests
      flet run main.py        (or)   python main.py
"""
import threading, traceback
import flet as ft

import theme as T
import store
import engine as E
import regression

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



# ═══════════════════════════════════════════════════════════════════════════════
#  Small reusable builders
# ═══════════════════════════════════════════════════════════════════════════════
def card(content, padding=18, expand=False, bg=T.CARD, radius=T.R_LG):
    return ft.Container(content=content, padding=padding, bgcolor=bg,
                        border=ft.Border.all(1, T.BORDER),
                        border_radius=radius, expand=expand)

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
            _fit = getattr(ft, "ImageFit", None)
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


def primary_btn(text, icon=None, on_click=None, expand=False, disabled=False):
    btn = ft.FilledButton(
        text, icon=icon, on_click=on_click, disabled=disabled, height=46,
        style=ft.ButtonStyle(
            bgcolor=T.VIOLET, color="#FFFFFF", elevation=0,
            shape=ft.RoundedRectangleBorder(radius=T.R),
            padding=ft.Padding.symmetric(horizontal=18, vertical=0)))
    btn.width = None
    # design shadow: 0 6px 16px -6px rgba(106,77,255,.7)
    if not disabled:
        return _shadow_wrap(btn, T.VIOLET, 0.6, expand)
    return _wrap_btn(btn, expand)



def green_btn(text, icon=None, on_click=None, expand=False, height=42):
    btn = ft.FilledButton(text, icon=icon, on_click=on_click, height=height,
        style=ft.ButtonStyle(
            bgcolor=T.GREEN, color="#FFFFFF", elevation=0,
            shape=ft.RoundedRectangleBorder(radius=T.R),
            padding=ft.Padding.symmetric(horizontal=16, vertical=0)))
    # design shadow tuned for green
    return _shadow_wrap(btn, T.GREEN, 0.5, expand)

def ghost_btn(text, icon=None, on_click=None, expand=False):
    btn = ft.OutlinedButton(text, icon=icon, on_click=on_click, height=46,
        style=ft.ButtonStyle(color=T.INK_2, side=ft.BorderSide(1, T.BORDER),
            shape=ft.RoundedRectangleBorder(radius=T.R),
            padding=ft.Padding.symmetric(horizontal=16, vertical=0)))
    return _wrap_btn(btn, expand)

def danger_btn(text, icon=None, on_click=None):
    btn = ft.FilledButton(text, icon=icon, on_click=on_click, height=40,
        style=ft.ButtonStyle(
            bgcolor=T.RED, color="#FFFFFF", elevation=0,
            shape=ft.RoundedRectangleBorder(radius=T.R),
            padding=ft.Padding.symmetric(horizontal=18, vertical=0)))
    # design shadow: 0 6px 16px -6px rgba(224,71,77,.6)
    return _shadow_wrap(btn, T.RED, 0.55, False)

def searchable_dropdown(**kwargs):
    """ft.Dropdown that is type-to-filter on newer Flet, degrading gracefully."""
    try:
        return ft.Dropdown(editable=True, enable_filter=True, menu_height=320, **kwargs)
    except TypeError:
        try:
            return ft.Dropdown(menu_height=320, **kwargs)
        except TypeError:
            return ft.Dropdown(**kwargs)


def progress_ring(pct, color, size=44, label=None):
    """A circular progress ring with a percentage in the center."""
    pct = max(0, min(100, int(pct)))
    ring = ft.ProgressRing(value=pct/100, width=size, height=size, stroke_width=4,
                           color=color, bgcolor="#ECEAF2")
    center = ft.Text(str(label if label is not None else pct), size=12,
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
    children = [
        ft.Row(label_row, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Row([
            ft.Text(str(num), size=22, weight=ft.FontWeight.BOLD, color=numc),
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


# ═══════════════════════════════════════════════════════════════════════════════
#  APP
# ═══════════════════════════════════════════════════════════════════════════════
class QAStudio:
    def __init__(self, page: ft.Page):
        self.page = page
        self.creds = store.load()
        # Apply saved org / email sender to the engine immediately so they
        # persist across restarts without needing to reconnect first.
        try:
            _saved_org = (self.creds.get("org") or "").strip()
            _saved_sender = (self.creds.get("gmail_sender") or "").strip()
            E.set_credentials(org=_saved_org or None,
                              gmail_sender=_saved_sender or None,
                              gmail=self.creds.get("gmail") or None)
        except Exception:
            pass
        self.connected = False
        self.active = "setup"          # setup | run | report
        self.tool = "steps"            # steps | titles
        self.lang = "ar"               # ar | en  (output language for titles/steps)
        try:
            self.lang = "en" if (self.creds.get("lang") == "en") else "ar"
        except Exception:
            self.lang = "ar"
        self.nav_state = {"setup": "active"}

        # task selections
        self.project = None
        self.plan_id = None
        self.plan_name = None
        self.story_ids = []
        self._setup_story_open = False
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
        self._auto_log = []
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
        self._updating = False
        self._update_dismissed = False
        self._closing = False        # set on close to stop background loops

        # Regression Plan tab (after Report)
        if not any(n.get("id") == "regression" for n in T.NAV):
            _ri = next((i for i, n in enumerate(T.NAV) if n.get("id") == "report"), len(T.NAV) - 1)
            T.NAV.insert(_ri + 1, {"id": "regression", "label": "Regression Plan",
                                   "icon": "FACT_CHECK", "ix": "R"})

        # Sprint Plan tab (after Regression Plan)
        if not any(n.get("id") == "testplan" for n in T.NAV):
            _ti = next((i for i, n in enumerate(T.NAV) if n.get("id") == "regression"), len(T.NAV) - 1)
            T.NAV.insert(_ti + 1, {"id": "testplan", "label": "Sprint Plan",
                                   "icon": "ASSIGNMENT", "ix": "T"})

        # Useful Links tab (last in the rail)
        if not any(n.get("id") == "links" for n in T.NAV):
            T.NAV.append({"id": "links", "label": "Useful Links",
                          "icon": "BOOKMARKS", "ix": "L"})

        self._build()

    # ---- credential helpers ----
    def _provider_options(self):
        names = list(E.AI_CONFIG.keys())
        orig_index = {n: i for i, n in enumerate(names)}   # stable order captured first
        def _is_active(n):
            return (n in E.active_providers()) or bool(self.creds["keys"].get(n))
        # active providers first, preserving original order within each group
        names.sort(key=lambda n: (not _is_active(n), orig_index[n]))
        opts = []
        for name in names:
            active = _is_active(name)
            dot = "●" if active else "○"
            opts.append(ft.DropdownOption(key=name,
                text=f"{dot}  {T.disp_name(name)}  ({'active' if active else 'inactive'})"))
        return opts

    def _saved_key(self, name):
        s = (self.creds["keys"].get(name) or "").strip()
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
        return bool((self.creds["keys"].get(name) or "").strip())

    # ---- window shell ----
    def rail(self):
        nav_items = []
        for n in T.NAV:
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
                         or (n["id"] == "regression")
                         or (n["id"] == "testplan")
                         or (n["id"] == "links")
                         or (n["id"] == "run" and (getattr(self, "_run_active", False)
                                                   or st == "active"
                                                   or self.last_report is not None)))
            # active indicator bar on the far left
            indicator = ft.Container(width=3, height=22,
                                     bgcolor=(T.VIOLET if is_active else ft.Colors.TRANSPARENT),
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
                        ft.Text(n["label"], size=13.5, weight=ft.FontWeight.BOLD, color=color),
                        ft.Container(expand=True),
                        ft.Text(ix, size=10.5, weight=ft.FontWeight.BOLD, color=ixcolor,
                                font_family=T.F_MONO),
                    ], spacing=9),
                    padding=ft.Padding.only(left=6, right=12, top=12, bottom=12),
                    bgcolor=bg, border_radius=11,
                    offset=ft.Offset(0, 0), animate=150, animate_offset=150,
                    on_hover=(_nav_hover if (clickable and not is_active) else None),
                    on_click=(lambda e, nid=n["id"]: self.goto(nid)) if clickable else None,
                ))
        conn_color  = T.GREEN if self.connected else T.INK_3
        _prov = self.current_provider()
        conn_text   = (T.disp_name(_prov) + " · Claude") if (self.connected and _prov=="anthropic")                       else (T.disp_name(_prov) if self.connected else "Not connected")
        conn_sub    = "Connected" if self.connected else "Enter credentials"
        return ft.Container(
            width=244, bgcolor=T.RAIL,
            content=ft.Column([
                ft.Container(
                    ft.Row([
                        ft.Container(width=12, height=12, bgcolor="#FF5F57", border_radius=6),
                        ft.Container(width=12, height=12, bgcolor="#FEBC2E", border_radius=6),
                        ft.Container(width=12, height=12, bgcolor="#28C840", border_radius=6),
                    ], spacing=8),
                    padding=ft.Padding.only(left=16, top=14, bottom=2)),
                ft.Container(
                    ft.Row([
                        ft.Container(logo_img(38),
                                     width=38, height=38,
                                     bgcolor=(None if _logo_b64() else T.VIOLET), border_radius=11,
                                     alignment=ft.Alignment.CENTER),
                        ft.Column([
                            ft.Text("QA Studio", size=15, weight=ft.FontWeight.BOLD, color=T.RAIL_INK),
                            ft.Container(
                                ft.Text(f"v{E.local_version()}  ·  check updates",
                                        size=10, color=T.RAIL_DIM, weight=ft.FontWeight.BOLD),
                                on_click=lambda e: self._manual_update_check(),
                                tooltip="Check for a newer version"),
                        ], spacing=1),
                    ], spacing=11), padding=ft.Padding.symmetric(vertical=16, horizontal=6)),
                ft.Container(ft.Text("PIPELINE", size=10, weight=ft.FontWeight.BOLD,
                                     color="#615E6E"), padding=ft.Padding.only(left=18, top=14, bottom=6)),
                ft.Container(ft.Column(nav_items, spacing=3), padding=ft.Padding.symmetric(vertical=10, horizontal=12)),
                ft.Container(expand=True),
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

    def topbar(self, title, sub=None, right=None, badge=None):
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
        if sub:
            left.append(ft.Text(sub, size=14, color=T.INK_2, weight=ft.FontWeight.W_500))
        row = [ft.Column(left, spacing=3, tight=True), ft.Container(expand=True)]
        if right:
            row.append(right)
        return ft.Container(ft.Row(row, spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            height=94,
                            padding=ft.Padding.symmetric(vertical=0, horizontal=24),
                            alignment=ft.Alignment.CENTER_LEFT,
                            border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER)),
                            bgcolor="#FFFFFF")

    def shell(self, title, sub, body, right=None, badge=None):
        # Sticky-header pattern: the opaque header is PINNED ON TOP of the scroll
        # area in a Stack, so scrolled content passes BEHIND it and is covered.
        # (Container clip does not contain a scroll viewport's overflow in Flet,
        # which is why every clip-based attempt left a band under the header.)
        try:
            body.clip_behavior = ft.ClipBehavior.HARD_EDGE
        except Exception:
            pass
        try:
            body.on_scroll = self._track_scroll
            self._left_scroll = body
        except Exception:
            pass
        HEADER_H = 94
        header = self.topbar(title, sub, right, badge)
        header.top = 0
        header.left = 0
        header.right = 0
        return ft.Row([
            self.rail(),
            ft.Container(
                ft.Stack([
                    ft.Container(
                        body, expand=True,
                        padding=ft.Padding.only(top=HEADER_H, left=22, right=22, bottom=22),
                        clip_behavior=ft.ClipBehavior.HARD_EDGE),
                    header,
                ], expand=True),
                expand=True,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                    colors=["#F7F8FE", "#EDF0F8"])),
        ], spacing=0, expand=True)

    # ---- Useful Links ----
    def _links_path(self):
        import os
        d = os.path.join(os.path.expanduser("~"), ".qa_tool")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return os.path.join(d, "links.json")

    def _load_links(self):
        import os, json
        try:
            p = self._links_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict) and x.get("url")]
        except Exception:
            pass
        return []

    def _save_links(self):
        import json
        try:
            with open(self._links_path(), "w", encoding="utf-8") as f:
                json.dump(self._links, f)
        except Exception:
            pass

    def useful_links_screen(self):
        if not hasattr(self, "_links"):
            self._links = self._load_links()

        name_field = ft.TextField(
            hint_text="e.g. Azure DevOps", text_size=14, border_color=T.BORDER,
            focused_border_color=T.VIOLET, border_radius=T.R, bgcolor=T.CARD_2,
            content_padding=ft.Padding.symmetric(vertical=11, horizontal=13))
        url_field = ft.TextField(
            hint_text="https://dev.azure.com/your-org", text_size=14,
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            bgcolor=T.CARD_2, expand=True,
            content_padding=ft.Padding.symmetric(vertical=11, horizontal=13))

        def _add(e=None):
            u = (url_field.value or "").strip()
            if not u:
                self._toast("Enter a URL."); return
            if not u.lower().startswith(("http://", "https://")):
                u = "https://" + u
            nm = (name_field.value or "").strip() or u
            self._links.append({"name": nm, "url": u})
            self._save_links()
            self.render()
        url_field.on_submit = _add

        def _open(u):
            def _o(e):
                import webbrowser
                try:
                    webbrowser.open(u)
                except Exception:
                    self._toast("Couldn't open the link.")
            return _o

        def _del(idx):
            def _d(e):
                try:
                    self._links.pop(idx)
                except Exception:
                    pass
                self._save_links(); self.render()
            return _d

        def _open_btn(u):
            return ft.FilledButton(
                "Open", icon=ft.Icons.OPEN_IN_NEW, on_click=_open(u), height=40,
                style=ft.ButtonStyle(
                    bgcolor={"": T.VIOLET}, color={"": "#FFFFFF"}, elevation=0,
                    shape=ft.RoundedRectangleBorder(radius=T.R),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=0)))

        add_card = card(ft.Column([
            ft.Row([
                ft.Container(ft.Icon(ft.Icons.ADD, size=16, color=T.VIOLET), width=30,
                             height=30, bgcolor=T.VIOLET_SOFT, border_radius=9,
                             alignment=ft.Alignment.CENTER),
                ft.Text("Add a link", size=16, weight=ft.FontWeight.BOLD, color=T.INK),
            ], spacing=11),
            ft.Container(height=16),
            ft.Row([
                ft.Column([field_label("App name"),
                           ft.Container(name_field, width=230,
                                        padding=ft.Padding.only(top=4))],
                          spacing=0, tight=True),
                ft.Column([field_label("URL"),
                           ft.Container(url_field, padding=ft.Padding.only(top=4))],
                          spacing=0, expand=True),
                green_btn("Add link", icon=ft.Icons.ADD, on_click=_add, height=44),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.END),
        ], spacing=0))

        palette = ["#4d5ad6", "#0f9586", "#7c45d4", "#C2860C", "#1C80E0", "#E0474D"]
        rows = []
        for i, l in enumerate(self._links):
            nm = (l.get("name") or l.get("url") or "?")
            init = nm.strip()[:1].upper() if nm.strip() else "?"
            col = palette[sum(ord(c) for c in nm) % len(palette)]
            rows.append(ft.Container(
                ft.Row([
                    ft.Container(ft.Text(init, size=15, weight=ft.FontWeight.BOLD,
                                         color="#FFFFFF"), width=40, height=40,
                                 bgcolor=col, border_radius=11,
                                 alignment=ft.Alignment.CENTER),
                    ft.Column([
                        ft.Text(nm, size=14.5, weight=ft.FontWeight.BOLD, color=T.INK,
                                no_wrap=True),
                        ft.Text(l.get("url", ""), size=12.5, color=T.INK_2,
                                font_family=T.F_MONO, no_wrap=True),
                    ], spacing=1, tight=True, expand=True),
                    _open_btn(l.get("url", "")),
                    ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=18,
                                  icon_color=T.INK_3, tooltip="Remove", on_click=_del(i),
                                  style=ft.ButtonStyle(
                                      shape=ft.RoundedRectangleBorder(radius=8))),
                ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=12, horizontal=16),
                bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER), border_radius=14))

        if rows:
            listing = ft.Column(rows, spacing=10)
        else:
            listing = ft.Container(
                ft.Column([
                    ft.Container(ft.Icon(ft.Icons.LINK, size=22, color=T.VIOLET),
                                 width=48, height=48, bgcolor=T.VIOLET_SOFT,
                                 border_radius=13, alignment=ft.Alignment.CENTER),
                    ft.Container(height=14),
                    ft.Text("No links yet", size=15, weight=ft.FontWeight.BOLD,
                            color=T.INK),
                    ft.Text("Add a name and URL above — they're saved on this device "
                            "and open in your browser.", size=13, color=T.INK_2,
                            text_align=ft.TextAlign.CENTER),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=3),
                alignment=ft.Alignment.CENTER,
                padding=ft.Padding.symmetric(vertical=46, horizontal=20),
                bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER), border_radius=16)

        body = ft.Column([
            ft.Container(height=14),
            add_card,
            ft.Container(height=24),
            ft.Row([ft.Text("SAVED LINKS", size=10.5, weight=ft.FontWeight.BOLD,
                            color=T.INK_3),
                    ft.Container(expand=True),
                    ft.Text(f"{len(self._links)} "
                            + ("link" if len(self._links) == 1 else "links"),
                            size=12, color=T.INK_3, weight=ft.FontWeight.W_500)],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=12),
            listing,
        ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

        return self.shell(
            "Useful Links",
            "Save links to the boards & apps you use — open them in one click", body)

    # ---- navigation ----
    def goto(self, screen):
        # Persist automation inputs when leaving the Automation screen so they
        # are preserved until the user changes them.
        if self.active == "automation" and screen != "automation":
            try:
                self._save_git_creds()
            except Exception:
                pass
        self.active = screen
        self.render()
        # Opportunistically check for a newer version when the user navigates.
        self._maybe_check_update_on_nav()

    def render(self):
        try:
            if getattr(self, "_last_active", None) != self.active:
                self._scroll_offset = 0
                self._last_active = self.active
            if self.active == "setup":
                view = self.setup_screen()
            elif self.active == "run":
                view = self.run_screen()
            elif self.active == "automation":
                view = self.automation_screen()
            elif self.active == "regression":
                view = regression.screen(self)
            elif self.active == "testplan":
                view = regression.test_plan_screen(self)
            elif self.active == "links":
                view = self.useful_links_screen()
            else:
                view = self.report_screen()
            try:
                view = ft.GestureDetector(content=view, on_tap=self._close_dropdowns,
                                          expand=True)
            except Exception:
                pass
            self.page.controls.clear()
            banner = None
            try:
                banner = self._update_banner()
            except Exception:
                banner = None
            if banner is not None:
                self.page.add(ft.Column([banner, ft.Container(view, expand=True)],
                                        spacing=0, expand=True))
            else:
                self.page.add(view)
            self.page.update()
            self._restore_scroll()
        except Exception as ex:
            # Never leave the user on a blank "Working…" screen — show the error.
            import traceback
            tb = traceback.format_exc()
            try:
                self.page.controls.clear()
                self.page.add(ft.Container(
                    ft.Column([
                        ft.Text("QA Studio hit an error while drawing this screen.",
                                size=15, weight=ft.FontWeight.BOLD, color="#E0474D"),
                        ft.Text(str(ex), size=12, color="#1B1A22"),
                        ft.Container(
                            ft.Text(tb, size=10, selectable=True,
                                    font_family="monospace", color="#74727E"),
                            bgcolor="#F6F5FA", padding=12, border_radius=8),
                    ], spacing=10, scroll=ft.ScrollMode.AUTO),
                    padding=24, expand=True, bgcolor="#FFFFFF"))
                self.page.update()
            except Exception:
                pass

    def _build(self):
        self.page.title = "QA Studio"
        self.page.bgcolor = T.RAIL
        self.page.padding = 0
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
            if _icon:
                if hasattr(self.page, "window") and self.page.window is not None:
                    self.page.window.icon = _icon
                else:
                    self.page.window_icon = _icon
        except Exception:
            pass
        try:
            # Flet >= 0.23 uses page.window.* ; older uses page.window_*
            if hasattr(self.page, "window") and self.page.window is not None:
                self.page.window.width = 1120
                self.page.window.height = 760
                self.page.window.min_width = 980
                self.page.window.min_height = 660
            else:
                self.page.window_width = 1120
                self.page.window_height = 760
                self.page.window_min_width = 980
                self.page.window_min_height = 660
        except Exception:
            pass
        # Window close handling (desktop only). We do NOT force prevent_close,
        # because that makes the X button unreliable across Flet versions. The X
        # closes the app normally. We only attach a listener to (best-effort)
        # confirm when a run is active; if the listener isn't supported, the
        # window still closes cleanly.
        import os
        _web = os.environ.get("WEB_MODE", "").strip() in ("1", "true", "yes")
        if not _web:
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
        _what = "automation task" if _is_auto else "run"
        def do_quit(_=None):
            self._close_dialog()
            self._force_close()
        def keep(_=None):
            self._close_dialog()
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([ft.Icon(ft.Icons.WARNING_AMBER, color=T.AMBER, size=20),
                          ft.Text(f"A {_what} is in progress", weight=ft.FontWeight.BOLD, size=16)],
                         spacing=8, tight=True),
            content=ft.Container(
                ft.Text(f"Closing now will stop the current {_what}. Quit anyway?",
                        size=13, color=T.INK_2, weight=ft.FontWeight.W_500), width=380),
            actions=[ft.TextButton("Quit", on_click=do_quit,
                                    style=ft.ButtonStyle(color=T.RED)),
                     green_btn("Keep running", on_click=keep)],
            actions_alignment=ft.MainAxisAlignment.END)
        self._show_dialog(dlg)

    def _force_close(self):
        """Close the OS window / exit the process, trying every Flet API, and
        as a final guarantee terminate the flet client window process so it can
        never be left orphaned on screen."""
        self._closing = True   # stop background loops (update checker, etc.)
        closed = False
        # 1) modern Flet: window.destroy()
        try:
            if hasattr(self.page, "window") and self.page.window is not None:
                try:
                    self.page.window.prevent_close = False
                except Exception:
                    pass
                self.page.window.destroy()
                closed = True
        except Exception:
            pass
        # 2) older Flet: page.window_destroy()
        if not closed:
            try:
                self.page.window_destroy()
                closed = True
            except Exception:
                pass
        # 3) Guarantee: terminate this process tree (kills the paired flet.exe
        #    client window so it can't linger). Done last, slightly delayed so
        #    the graceful close above can paint first.
        def _hard_exit():
            import os, time
            time.sleep(0.4)
            if os.name == "nt":
                # Kill this process AND its children (the flet.exe window client)
                # so no orphan taskbar entry remains.
                try:
                    import subprocess
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(os.getpid())],
                                   creationflags=0x08000000, check=False)
                    return
                except Exception:
                    pass
            try:
                import signal
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                try:
                    import sys; sys.exit(0)
                except Exception:
                    pass
        try:
            threading.Thread(target=_hard_exit, daemon=True).start()
        except Exception:
            _hard_exit()

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
        try:
            info = E.check_for_update()
            self._update_info = info
            # Repaint whenever an update is available and not dismissed, so the
            # banner appears on the next interaction even if a prior check missed
            # it or was still in flight. (render() rebuilds the banner from
            # _update_info, so a repaint is all that's needed.)
            if info.get("update") and not self._update_dismissed:
                self.ui_safe(self.render)
        except Exception:
            pass

    def _manual_update_check(self):
        """User-triggered check. Always reports the outcome (up-to-date / newer /
        why it couldn't check), unlike the silent background check."""
        self._toast("Checking for updates…")
        def work():
            info = E.check_for_update()
            self._update_info = info
            local = info.get("local", "?")
            remote = info.get("remote")
            if info.get("update"):
                self._update_dismissed = False
                self.ui_safe(self.render)   # the banner will appear
            elif info.get("error"):
                self.ui_safe(lambda: self._toast(f"Couldn't check: {info['error']}"))
            elif remote:
                self.ui_safe(lambda: self._toast(f"Up to date (v{local}, latest v{remote})."))
            else:
                self.ui_safe(lambda: self._toast(f"Up to date (v{local})."))
        try:
            self._bg(work)
        except Exception:
            pass

    def _maybe_check_update_on_nav(self):
        """Throttled check fired on navigation — at most once every 30 seconds.
        Keeps the banner current as the user moves around the app without
        hammering GitHub on every single click."""
        import time as _t
        now = _t.time()
        last = getattr(self, "_last_nav_update_check", 0)
        if now - last < 30:
            return
        self._last_nav_update_check = now
        try:
            self._bg(self._run_update_check)
        except Exception:
            pass

    def _update_banner(self):
        """A slim banner shown at the top when a newer version is available."""
        info = self._update_info or {}
        if not info.get("update") or self._update_dismissed:
            return None
        remote = info.get("remote", "")
        local = info.get("local", "")
        if self._updating:
            inner = ft.Row([
                ft.ProgressRing(width=16, height=16, stroke_width=2, color="#FFFFFF"),
                ft.Text("Updating…", size=12.5, color="#FFFFFF", weight=ft.FontWeight.BOLD),
            ], spacing=10)
        else:
            update_btn = ft.Container(
                ft.Row([
                    ft.Icon(ft.Icons.DOWNLOAD, size=16, color=T.VIOLET_INK),
                    ft.Text("Update now", size=13, color=T.VIOLET_INK,
                            weight=ft.FontWeight.BOLD),
                ], spacing=8, tight=True),
                bgcolor="#FFFFFF", border_radius=T.R_SM,
                padding=ft.Padding.symmetric(horizontal=18, vertical=11),
                on_click=lambda e: self._do_update(), ink=True,
                tooltip="Download and install the latest version",
                shadow=ft.BoxShadow(blur_radius=14, spread_radius=-4,
                                    offset=ft.Offset(0, 4),
                                    color=ft.Colors.with_opacity(0.30, "#160F2E")))
            inner = ft.Row([
                ft.Container(ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, size=17, color="#FFFFFF"),
                             width=32, height=32, border_radius=9,
                             bgcolor=ft.Colors.with_opacity(0.18, "#FFFFFF"),
                             alignment=ft.Alignment.CENTER),
                ft.Column([
                    ft.Text("Update available", size=12.5, color="#FFFFFF",
                            weight=ft.FontWeight.BOLD),
                    ft.Text(f"Version {remote} is ready \u2014 you\u2019re on {local}",
                            size=11, weight=ft.FontWeight.W_500,
                            color=ft.Colors.with_opacity(0.82, "#FFFFFF")),
                ], spacing=1, expand=True),
                update_btn,
                ft.IconButton(ft.Icons.CLOSE, icon_size=16,
                              icon_color=ft.Colors.with_opacity(0.85, "#FFFFFF"),
                              tooltip="Dismiss",
                              on_click=lambda e: self._dismiss_update()),
            ], spacing=13, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        return ft.Container(inner, bgcolor=T.VIOLET,
                            padding=ft.Padding.symmetric(horizontal=18, vertical=11),
                            shadow=ft.BoxShadow(blur_radius=18, spread_radius=-6,
                                                offset=ft.Offset(0, 6),
                                                color=ft.Colors.with_opacity(0.30, T.VIOLET)))

    def _dismiss_update(self):
        self._update_dismissed = True
        self.render()

    def _do_update(self):
        self._updating = True
        self.render()

        def work():
            ok, msg = E.apply_update(cb=lambda m, t="dim": None)
            def finish():
                self._updating = False
                if ok:
                    self._update_info = {"update": False}
                    self._update_dismissed = True
                    self._show_restart_dialog(msg)
                else:
                    self.render()
                    self._show_update_error(msg)
            self.ui_safe(finish)
        self._bg(work)

    def _show_update_error(self, msg):
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([ft.Icon(ft.Icons.ERROR_OUTLINE, color=T.RED, size=20),
                          ft.Text("Update failed", weight=ft.FontWeight.BOLD, size=16)],
                         spacing=8, tight=True),
            content=ft.Container(
                ft.Column([
                    ft.Text(msg, size=12.5, color=T.INK, selectable=True),
                    ft.Container(height=6),
                    ft.Text("If a new .exe isn't attached to the latest GitHub "
                            "release, the app can't self-update — attach it as a "
                            "release asset and try again.",
                            size=11.5, color=T.INK_3, weight=ft.FontWeight.W_500),
                ], spacing=2, tight=True), width=460),
            actions=[green_btn("OK", on_click=lambda e: self._close_dialog())],
            actions_alignment=ft.MainAxisAlignment.END)
        self._show_dialog(dlg)

    def _show_restart_dialog(self, msg):
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE, color=T.GREEN, size=20),
                          ft.Text("Update complete", weight=ft.FontWeight.BOLD, size=16)],
                         spacing=8, tight=True),
            content=ft.Container(
                ft.Column([
                    ft.Text("QA Studio has been updated to the latest version.",
                            size=13, color=T.INK, weight=ft.FontWeight.BOLD),
                    ft.Container(height=4),
                    ft.Text("QA Studio will restart to finish updating — it "
                            "closes and reopens on the new version automatically.",
                            size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500),
                ], spacing=2, tight=True),
                width=430),
            actions=[
                green_btn("Restart now", on_click=lambda e: self._restart_app()),
                ghost_btn("Later", on_click=lambda e: self._close_dialog()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dialog(dlg)

    def _quit_after_update(self):
        """Simply close the app cleanly so the user can reopen the updated version.
        No auto-relaunch (that proved unreliable across Flet/Windows builds)."""
        self._close_dialog()
        self._run_active = False
        self._auto_running = False
        self._restart_close()

    def _restart_app(self):
        """Relaunch cleanly. We spawn a helper that is REPARENTED out of our
        process tree (via `start`), so the `taskkill /T` we run on ourselves
        below can't kill it. The helper waits for THIS pid to exit, then starts
        a fresh app."""
        self._close_dialog()
        try:
            import sys, os, subprocess, tempfile
            app_dir = os.path.dirname(os.path.abspath(__file__))
            main_py = os.path.join(app_dir, "main.py")
            pyw = sys.executable
            try:
                cand = os.path.join(os.path.dirname(pyw), "pythonw.exe")
                if os.path.exists(cand):
                    pyw = cand
            except Exception:
                pass
            pid = os.getpid()
            if os.name == "nt":
                bat = os.path.join(tempfile.gettempdir(), "qastudio_relaunch.bat")
                script = ("@echo off\r\n"
                          f'set "PID={pid}"\r\n'
                          ":wait\r\n"
                          'tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul '
                          "&& (ping -n 2 127.0.0.1 >nul & goto wait)\r\n"
                          "ping -n 2 127.0.0.1 >nul\r\n"
                          f'start "" /d "{app_dir}" "{pyw}" "{main_py}"\r\n'
                          'del "%~f0" >nul 2>&1\r\n')
                with open(bat, "w", encoding="ascii", errors="ignore", newline="") as f:
                    f.write(script)
                DETACHED, NEW_GROUP = 0x00000008, 0x00000200
                # `cmd /c start … cmd /c bat` reparents the helper away from us.
                subprocess.Popen(["cmd", "/c", "start", "", "/min", "cmd", "/c", bat],
                                 creationflags=DETACHED | NEW_GROUP, close_fds=True)
            else:
                subprocess.Popen([pyw, main_py], cwd=app_dir, start_new_session=True)
        except Exception:
            pass
        self._run_active = False
        self._auto_running = False
        self._restart_close()

    def _restart_close(self):
        """Close this process tree (old window + its flet client)."""
        try:
            if hasattr(self.page, "window") and self.page.window is not None:
                try:
                    self.page.window.prevent_close = False
                except Exception:
                    pass
                self.page.window.destroy()
        except Exception:
            pass
        def _hard():
            import os, time
            time.sleep(0.4)
            if os.name == "nt":
                try:
                    import subprocess
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(os.getpid())],
                                   creationflags=0x08000000, check=False)
                    return
                except Exception:
                    pass
            try:
                import signal
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                try:
                    import sys; sys.exit(0)
                except Exception:
                    pass
        try:
            threading.Thread(target=_hard, daemon=True).start()
        except Exception:
            _hard()

    def _confirm_close(self):
        def do_close(e=None):
            self.stop_flag = True  # behave like "stop after current"
            self._close_dialog()
            try:
                self.page.window.prevent_close = False
                self.page.update()
            except Exception:
                pass
            # window.destroy() is async in Flet 0.90 — schedule it on the loop
            def _destroy():
                try:
                    import os, signal
                    os.kill(os.getpid(), signal.SIGTERM)
                except Exception:
                    pass
            try:
                rt = getattr(self.page, "run_task", None)
                if callable(rt) and hasattr(self.page.window, "destroy"):
                    rt(self.page.window.destroy)
                else:
                    _destroy()
            except Exception:
                _destroy()
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=20, color=T.AMBER),
                ft.Text("Close QA Studio?", size=15, weight=ft.FontWeight.BOLD, color=T.INK),
            ], spacing=9),
            content=ft.Container(width=380, content=ft.Text(
                "If a run is in progress it will stop after the current test case. "
                "Any unfinished test cases won't be processed. Close anyway?",
                size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500)),
            actions=[
                ghost_btn("Keep working", on_click=lambda e: self._close_dialog()),
                danger_btn("Stop & close", icon=ft.Icons.STOP, on_click=do_close),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=T.R_LG))
        self._show_dialog(dlg)

    # ═══════════════════════════════════════════════════════════════════════════
    #  SETUP SCREEN
    # ═══════════════════════════════════════════════════════════════════════════
    def setup_screen(self):
        # default provider choice
        if not hasattr(self, "_provider_choice"):
            names = list(E.AI_CONFIG.keys())
            self._provider_choice = names[0] if names else "anthropic"

        # ── Connection card ──
        self.err_text = ft.Text(getattr(self, "_err_msg", "") or "", size=12, color=T.RED, weight=ft.FontWeight.BOLD)

        if self.connected:
            conn_body = self._connection_saved()
        else:
            conn_body = self._connection_edit()

        connection_card = card(ft.Column([
            sec_head("1", "Connection",
                     ft.Text("set once · reused every run", size=11, color=T.INK_3, weight=ft.FontWeight.BOLD)),
            ft.Container(height=12),
            conn_body,
        ], spacing=0))

        # ── Tool selector card ──
        _lang_word = "Arabic" if self.lang == "ar" else "English"
        tool_card = card(ft.Column([
            sec_head("2", "What to generate",
                     ft.Text("one app · two generators", size=11, color=T.INK_3, weight=ft.FontWeight.BOLD)),
            ft.Container(height=12),
            self._tool_segment(),
            ft.Container(height=14),
            # Output language toggle
            ft.Row([
                ft.Text("Output language", size=12, weight=ft.FontWeight.BOLD, color=T.INK_2),
                ft.Container(expand=True),
                self._lang_segment(),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=10),
            ft.Text(
                f"Adds detailed {_lang_word} steps — precondition · action · expected — to existing test cases."
                if self.tool == "steps" else
                f"Reads each user story and writes {_lang_word} test-case titles into the plan, skipping duplicates.",
                size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500),
        ], spacing=0))

        # ── Task card (gated) ──
        task_card = self._task_card() if self.connected else self._task_locked()

        self._left_scroll = ft.Column(
            [connection_card, tool_card, task_card],
            spacing=14, scroll=ft.ScrollMode.AUTO, expand=True,
            key="setup_scroll", on_scroll=self._track_scroll)
        left = self._left_scroll

        right = self._setup_right()

        body = ft.Row([
            ft.Container(left, expand=True),
            ft.Container(right, width=290),
        ], spacing=22, vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)

        sub = "1 of 2 — configure & run" if self.connected else "1 of 2 — connect first"
        right_tag = ft.Container(
            ft.Row([ft.Icon(ft.Icons.SHIELD_OUTLINED, size=13, color=T.INK_2),
                    ft.Text("Credentials saved on this device", size=11, color=T.INK_2, weight=ft.FontWeight.BOLD)],
                   spacing=5, tight=True),
            padding=ft.Padding.symmetric(vertical=10, horizontal=5), bgcolor=T.CARD_2, border_radius=20,
            border=ft.Border.all(1, T.BORDER))
        return self.shell("Setup", sub, body, right_tag)

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
            "url": f"https://dev.azure.com/{E.AZURE_ORG}/_usersSettings/tokens",
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
        "git_pat": {
            "title": "Git access token (PAT) for pushing tests",
            "steps": [
                "This token lets QA Studio push the generated tests to your repo. Use one "
                "scoped to just that repository.",
                "GitHub: Settings → Developer settings → Personal access tokens → "
                "Fine-grained tokens → Generate. Give it 'Contents: Read and write' on the "
                "automation-tests repo only. Copy the token (shown once).",
                "Azure DevOps Repos: User settings → Personal access tokens → New token → "
                "scope 'Code (Read & Write)'.",
                "Paste it here. It's stored locally like your other credentials and is "
                "scrubbed from logs. Keep the repo private.",
            ],
            "url": "https://github.com/settings/tokens?type=beta",
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
    }

    def _show_help(self, key):
        # Both the AI-Provider and API-Key info icons are provider-aware: they show
        # the SELECTED provider's instructions + the correct console URL.
        if key in ("api_key", "provider"):
            name = getattr(self, "_provider_choice", None) or "anthropic"
            how, url, label = self.PROVIDER_KEY_HELP.get(
                name, self.PROVIDER_KEY_HELP["anthropic"])
            if key == "provider":
                h = {"title": f"Activating {T.disp_name(name)}",
                     "steps": [
                         f"A provider becomes 'active' once you save a valid {T.disp_name(name)} API key.",
                         f"Get the key: {how}",
                         "Paste it in the API Key field below, then click Save.",
                         "The dropdown shows '(active)'. Then click Connect.",
                         "Each provider stores its own key — switching keeps them all.",
                     ],
                     "url": url, "url_label": label}
            else:
                h = {"title": f"{T.disp_name(name)} API Key",
                     "steps": [f"{T.disp_name(name)}: {how}",
                               "Copy the key and paste it here, then click Save.",
                               "It is stored only on this device, per provider."],
                     "url": url, "url_label": label}
        else:
            h = self.HELP.get(key)
        if not h:
            return
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
            modal=True, bgcolor=T.CARD,
            shape=ft.RoundedRectangleBorder(radius=T.R_LG),
            title=ft.Row([
                ft.Container(ft.Icon(ft.Icons.HELP_OUTLINE, size=18, color=T.VIOLET_INK),
                             width=34, height=34, bgcolor=T.VIOLET_SOFT, border_radius=9,
                             alignment=ft.Alignment.CENTER),
                ft.Text(h["title"], size=15, weight=ft.FontWeight.BOLD, color=T.INK, expand=True),
            ], spacing=10),
            content=ft.Container(width=460, content=ft.Column(
                step_rows + ([
                    ft.Container(height=6),
                    ft.Container(
                        ft.Row([ft.Icon(ft.Icons.OPEN_IN_NEW, size=14, color=T.VIOLET_INK),
                                ft.Text(h.get("url_label", "Open link"), size=12.5,
                                        color=T.VIOLET_INK, weight=ft.FontWeight.BOLD)],
                               spacing=6, tight=True),
                        on_click=lambda e, u=url: self._open_url(u),
                        padding=ft.Padding.symmetric(vertical=10, horizontal=12),
                        bgcolor=T.VIOLET_SOFT, border_radius=T.R,
                        border=ft.Border.all(1, "#E0DAFF")),
                ] if url else []),
                spacing=11, tight=True)),
            actions=[primary_btn("Got it", on_click=lambda e: self._close_dialog())],
            actions_alignment=ft.MainAxisAlignment.END)
        self._show_dialog(dlg)

    # ---- connection: editable (not connected) ----
    def _connection_edit(self):
        name = self._provider_choice
        self.prov_dd = ft.Dropdown(
            value=name, options=self._provider_options(), on_select=self._on_provider_change,
            border_color=T.BORDER, focused_border_color=T.VIOLET,
            border_radius=T.R, content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
            text_size=13, filled=True, bgcolor=T.CARD, expand=True)

        # Key field: editable if no saved key, or unlocked by Update button
        active = self._provider_active(name)
        key_editable = (not active) or self._key_unlocked
        self.api_key_field = ft.TextField(
            value=self._saved_key(name), password=True, can_reveal_password=True,
            hint_text=f"Paste key for {T.disp_name(name)}",
            read_only=not key_editable,
            bgcolor=(T.CARD if key_editable else T.CARD_2),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12), text_size=13, expand=True)
        self.api_btn = green_btn("Save", on_click=self._save_key) if key_editable                   else ghost_btn("Update", on_click=self._unlock_key)

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
            hint_text="Select a model",
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

        # PAT field
        pat_has = bool(self.creds.get("pat"))
        pat_editable = (not pat_has) or self._pat_unlocked
        self.pat_field = ft.TextField(
            value=self.creds.get("pat", ""), password=True, can_reveal_password=True,
            hint_text="Paste PAT", read_only=not pat_editable,
            bgcolor=(T.CARD if pat_editable else T.CARD_2),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12), text_size=13, expand=True)
        self.pat_btn = green_btn("Save", on_click=self._save_pat) if pat_editable                   else ghost_btn("Update", on_click=self._unlock_pat)

        # Gmail field
        gmail_has = bool(self.creds.get("gmail"))
        gmail_editable = (not gmail_has) or self._gmail_unlocked
        self.gmail_field = ft.TextField(
            value=self.creds.get("gmail", ""), password=True, can_reveal_password=True,
            hint_text="Gmail app password (optional)", read_only=not gmail_editable,
            bgcolor=(T.CARD if gmail_editable else T.CARD_2),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12), text_size=13, expand=True)
        self.gmail_btn = green_btn("Save", on_click=self._save_gmail) if gmail_editable                     else ghost_btn("Update", on_click=self._unlock_gmail)

        # Azure Organization field (one-time set, preserved, Update to change)
        org_val = self.creds.get("org", "") or E.AZURE_ORG
        org_has = bool(self.creds.get("org"))
        org_editable = (not org_has) or self._org_unlocked
        self.org_field = ft.TextField(
            value=org_val, hint_text="Azure DevOps organization name",
            read_only=not org_editable,
            bgcolor=(T.CARD if org_editable else T.CARD_2),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12), text_size=13, expand=True)
        self.org_btn = green_btn("Save", on_click=self._save_org) if org_editable                   else ghost_btn("Update", on_click=self._unlock_org)

        # Gmail sender field (one-time set, preserved, Update to change)
        sender_val = self.creds.get("gmail_sender", "") or E.GMAIL_SENDER
        sender_has = bool(self.creds.get("gmail_sender"))
        sender_editable = (not sender_has) or self._sender_unlocked
        self.sender_field = ft.TextField(
            value=sender_val, hint_text="Sender Gmail address",
            read_only=not sender_editable,
            bgcolor=(T.CARD if sender_editable else T.CARD_2),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12), text_size=13, expand=True)
        self.sender_btn = green_btn("Save", on_click=self._save_sender) if sender_editable                      else ghost_btn("Update", on_click=self._unlock_sender)

        return ft.Column([
            field_label("AI Provider", req=True, info="How to make a provider active",
                        on_info=lambda e: self._show_help("provider")),
            ft.Container(self.prov_dd, padding=ft.Padding.only(top=4, bottom=12)),
            field_label("Model", req=False, hint=self._model_src_hint(),
                        info="Which model this provider should use",
                        on_info=lambda e: self._show_help("model")),
            ft.Container(self.model_dd, padding=ft.Padding.only(top=4, bottom=12)),
            field_label("API Key", req=True, info="How to get your AI provider API key",
                        on_info=lambda e: self._show_help("api_key")),
            ft.Container(ft.Row([self.api_key_field, self.api_btn], spacing=8),
                        padding=ft.Padding.only(top=4, bottom=12)),
            field_label("Azure Organization", req=True,
                        info="How to find your Azure organization name",
                        on_info=lambda e: self._show_help("org")),
            ft.Container(ft.Row([self.org_field, self.org_btn], spacing=8),
                        padding=ft.Padding.only(top=4, bottom=12)),
            ft.Row([
                ft.Column([
                    field_label("Azure DevOps PAT", req=True,
                                info="How to create an Azure DevOps PAT",
                                on_info=lambda e: self._show_help("pat")),
                    ft.Container(ft.Row([self.pat_field, self.pat_btn], spacing=8),
                                 padding=ft.Padding.only(top=4)),
                ], expand=True, spacing=0),
            ]),
            ft.Container(height=12),
            field_label("Email Sender", hint="optional"),
            ft.Container(ft.Row([self.sender_field, self.sender_btn], spacing=8),
                        padding=ft.Padding.only(top=4, bottom=12)),
            ft.Row([
                ft.Column([
                    field_label("Gmail App Password", hint="optional", req=False,
                                info="How to create a Gmail App Password",
                                on_info=lambda e: self._show_help("gmail")),
                    ft.Container(ft.Row([self.gmail_field, self.gmail_btn], spacing=8),
                                 padding=ft.Padding.only(top=4)),
                ], expand=True, spacing=0),
            ]),
        ], spacing=0)

    # ---- connection: saved (connected) ----
    def _cred_saved_row(self, icon, k, v, badge_ctrl):
        return ft.Row([
            ft.Icon(icon, size=16, color=T.INK_2),
            ft.Column([
                ft.Text(k, size=11, color=T.INK_2, weight=ft.FontWeight.BOLD),
                ft.Text(v, size=12.5, color=T.INK, weight=ft.FontWeight.BOLD),
            ], spacing=1, expand=True),
            badge_ctrl,
            ghost_btn("Update", icon=ft.Icons.EDIT_OUTLINED,
                      on_click=lambda e: self._edit_connection()),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _connection_saved(self):
        name = self.current_provider()
        pat = self.creds.get("pat", "")
        masked_pat = "••••••••••" + (pat[-4:] if len(pat) >= 4 else "")
        gm = self.creds.get("gmail", "")
        masked_gm = ("•••• •••• ••••" if gm else "—")
        div = ft.Container(height=1, bgcolor=T.BORDER_2, margin=ft.Margin.symmetric(vertical=8))
        _model = self._saved_model(name)
        prov_val = f"{T.disp_name(name)}  ·  {_model}" if _model else T.disp_name(name)
        return ft.Column([
            self._cred_saved_row(ft.Icons.AUTO_AWESOME, "AI Provider", prov_val,
                                 badge("Active", "green", ft.Icons.CHECK)),
            div,
            self._cred_saved_row(ft.Icons.KEY_OUTLINED, "Azure DevOps PAT", masked_pat,
                                 badge("Valid", "green", ft.Icons.CHECK)),
            div,
            self._cred_saved_row(ft.Icons.MAIL_OUTLINED, "Gmail App Password", masked_gm,
                                 badge("optional", "grey")),
        ], spacing=0)

    def _edit_connection(self):
        self.connected = False
        self.render()

    # ---- tool segment ----
    def _tool_segment(self):
        def seg(label, icon, key):
            sel = (self.tool == key)
            return ft.Container(
                ft.Row([ft.Icon(icon, size=16, color=(T.VIOLET_INK if sel else T.INK_2)),
                        ft.Text(label, size=12.5, weight=ft.FontWeight.BOLD,
                                color=(T.VIOLET_INK if sel else T.INK_2))],
                       spacing=7, alignment=ft.MainAxisAlignment.CENTER, tight=True),
                expand=True, height=40, alignment=ft.Alignment.CENTER,
                padding=ft.Padding.symmetric(vertical=0, horizontal=9),
                bgcolor=(T.CARD if sel else None), border_radius=T.R_SM,
                border=ft.Border.all(1, T.BORDER) if sel else None,
                shadow=(ft.BoxShadow(blur_radius=6, color=ft.Colors.with_opacity(0.08, "#000000"),
                                     offset=ft.Offset(0, 2)) if sel else None),
                on_click=lambda e, k=key: self._set_tool(k))
        return ft.Container(
            ft.Row([seg("Test Case Titles", ft.Icons.DESCRIPTION_OUTLINED, "titles"),
                    seg("Test Case Steps", ft.Icons.LAYERS_OUTLINED, "steps")], spacing=4),
            padding=4, bgcolor=T.CARD_2, border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    def _set_tool(self, k):
        self.tool = k
        self.render()

    # ---- output-language segment ----
    def _lang_segment(self):
        def seg(label, key):
            sel = (self.lang == key)
            return ft.Container(
                ft.Text(label, size=12, weight=ft.FontWeight.BOLD,
                        color=(T.VIOLET_INK if sel else T.INK_2)),
                height=32, alignment=ft.Alignment.CENTER,
                padding=ft.Padding.symmetric(vertical=0, horizontal=16),
                bgcolor=(T.CARD if sel else None), border_radius=T.R_SM,
                border=ft.Border.all(1, T.BORDER) if sel else None,
                shadow=(ft.BoxShadow(blur_radius=6, color=ft.Colors.with_opacity(0.08, "#000000"),
                                     offset=ft.Offset(0, 2)) if sel else None),
                on_click=lambda e, k=key: self._set_lang(k))
        return ft.Container(
            ft.Row([seg("العربية", "ar"), seg("English", "en")], spacing=4, tight=True),
            padding=4, bgcolor=T.CARD_2, border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    def _set_lang(self, k):
        self.lang = "en" if k == "en" else "ar"
        try:
            self.creds["lang"] = self.lang
            store.save(self.creds)
        except Exception:
            pass
        self.render()

    # ---- credential handlers ----
    def _on_provider_change(self, e):
        prev = getattr(self, "_provider_choice", None)
        self._provider_choice = self.prov_dd.value
        name = self._provider_choice
        # changing provider while connected invalidates the connection
        if prev and prev != name and getattr(self, "connected", False):
            self._disconnect(f"Provider changed to {T.disp_name(name)} — reconnect to continue.")
        # reset the model list so it refetches for the newly selected provider
        self._models_for = None
        self._model_choices = None
        active = self._provider_active(name)
        self.api_key_field.value = self._saved_key(name)
        self.api_key_field.read_only = active
        self.api_key_field.bgcolor = T.CARD_2 if active else T.CARD
        self.api_key_field.hint_text = f"Paste key for {T.disp_name(name)}"
        # switch the live engine provider (+ its saved key & model, if any) so a
        # PAUSED automation can Resume on this provider without re-running Connect
        try:
            E.set_credentials(provider=name, api_key=(self._saved_key(name) or None),
                              model=(self._saved_model(name) or None))
        except Exception:
            pass
        self.render()

    def _save_key(self, e=None):
        name = self._provider_choice
        val = (self.api_key_field.value or "").strip()
        if not val:
            self._err("API Key is required."); return
        self.creds["keys"][name] = val; store.save(self.creds)
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
        self._toast(f"API key saved & {T.disp_name(name)} activated.")
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
        return [ft.DropdownOption(key=m, text=m) for m in choices]

    def _fetch_models_async(self, name):
        """Fetch the live model catalogue off the UI thread, then re-render."""
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
        new_opts = [ft.DropdownOption(key=m, text=m) for m in (self._model_choices or [])]
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
            E.set_credentials(provider=name, model=val)
        except Exception:
            pass
        # changing the model while connected invalidates the connection
        if getattr(self, "connected", False):
            self._disconnect(f"Model changed to {val} — reconnect to continue.")
        self._toast(f"Model set to {val}.")
        self.render()

    def _unlock_key(self, e=None):
        self._key_unlocked = True; self.render()

    def _save_pat(self, e=None):
        val = (self.pat_field.value or "").strip()
        if not val:
            self._err("Azure DevOps PAT is required."); return
        self.creds["pat"] = val; store.save(self.creds)
        self._pat_unlocked = False
        self._toast("PAT saved."); self.render()

    def _unlock_pat(self, e=None):
        self._pat_unlocked = True; self.render()

    def _save_gmail(self, e=None):
        val = (self.gmail_field.value or "").strip()
        self.creds["gmail"] = val; store.save(self.creds)
        self._gmail_unlocked = False
        self._toast("Gmail password saved."); self.render()

    def _unlock_gmail(self, e=None):
        self._gmail_unlocked = True; self.render()

    def _save_org(self, e=None):
        val = (self.org_field.value or "").strip()
        if not val:
            self._err("Azure Organization is required."); return
        self.creds["org"] = val; store.save(self.creds)
        try:
            E.set_credentials(org=val)
        except Exception:
            pass
        self._org_unlocked = False
        self._toast("Organization saved."); self.render()

    def _unlock_org(self, e=None):
        self._org_unlocked = True; self.render()

    def _save_sender(self, e=None):
        val = (self.sender_field.value or "").strip()
        self.creds["gmail_sender"] = val; store.save(self.creds)
        try:
            E.set_credentials(gmail_sender=val)
        except Exception:
            pass
        self._sender_unlocked = False
        self._toast("Email sender saved."); self.render()

    def _unlock_sender(self, e=None):
        self._sender_unlocked = True; self.render()

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
            # drop any stale snackbars, then mount + open this one explicitly
            # (most reliable across Flet builds; page.open alone wasn't showing).
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
                    # why the picker used to stay empty/disabled.
                    ss = E.fetch_stories_in_plan(self.project, self.plan_id)
                    if not ss:
                        # Fallback: the plan's sprint/iteration stories.
                        plan = E._azure_get(
                            f"https://dev.azure.com/{E.AZURE_ORG}/{self.project}"
                            f"/_apis/testplan/plans/{self.plan_id}?api-version=7.0")
                        itr = plan.get("iteration")
                        ss = E.fetch_stories_in_iteration(self.project, itr) if itr else []
                except Exception:
                    ss = []
                self._setup_stories = ss
                self._setup_stories_loading = False
                self.ui_safe(self.render)
            threading.Thread(target=_load_ss, daemon=True).start()

        self.project_dd = ft.Dropdown(
            value=self.project, hint_text="Select project",
            options=[ft.DropdownOption(p) for p in self._projects],
            on_select=self._on_project_change,
            tooltip=(self.project or None),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=8), text_size=13, filled=True,
            bgcolor=T.CARD, expand=True)

        _plan_tip = next((f"[{p['id']}] {p['name']}" for p in self._plans if p["id"] == self.plan_id), None)
        self.plan_dd = searchable_dropdown(
            value=(str(self.plan_id) if self.plan_id else None), hint_text="Select test plan",
            options=[ft.DropdownOption(key=str(p["id"]), text=f"[{p['id']}] {p['name']}") for p in self._plans],
            on_select=self._on_plan_change,
            tooltip=_plan_tip,
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=8), text_size=13, filled=True,
            bgcolor=T.CARD, expand=True)

        self.plan_id_field = ft.TextField(
            value=(str(self.plan_id) if self.plan_id else ""), read_only=True,
            hint_text="— none —", bgcolor=T.CARD_2, color=T.VIOLET_INK,
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
                    border=ft.Border.all(1, "#D9D2FF")))
            self._chip_row.controls = chips
            self._chip_wrap.visible = bool(self.story_ids)

        def _update_summary_inplace():
            # update the THIS RUN stats + estimate labels without a full render
            try:
                if hasattr(self, "_sum_stories"):
                    self._sum_stories.value = f"{len(self.story_ids)} selected"
                    self._sum_stories.update()
            except Exception:
                pass
            try:
                if hasattr(self, "_est_sub"):
                    self._est_sub.value = f"test cases\nacross {len(self.story_ids)} stories"
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

        self.story_field = ft.TextField(
            value="",
            hint_text="Paste an ID and press Enter (or comma). Repeat to add more.",
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
            text_size=13, expand=True, on_submit=_commit_stories,
            on_change=_on_story_change)
        def _add_from_dd(e):
            v = self._setup_story_dd.value
            if v and str(v).strip().isdigit():
                i = int(v)
                if i not in self.story_ids:
                    self.story_ids.append(i)
                    self._err_msg = ""
                    _build_chips()
                    self._estimated_tc = None
                    # Patch the dropdown IN PLACE (drop the picked story, clear the
                    # value) instead of a full render, so the scroll stays put.
                    try:
                        _ss2 = self._setup_stories or []
                        self._setup_story_dd.options = [
                            ft.DropdownOption(key=str(s["id"]),
                                              text=f"[{s['id']}] {(s['title'] or '')[:48]}")
                            for s in _ss2 if s["id"] not in self.story_ids]
                        self._setup_story_dd.value = None
                        self._setup_story_dd.update()
                    except Exception:
                        pass
                    try:
                        self._chip_row.update(); self._chip_wrap.update()
                    except Exception:
                        pass
                    _update_summary_inplace()
                    self._fetch_estimate()
            # NOTE: no self.render() here — the in-place updates above keep the
            # scroll where it was (a full render snapped it back to the top).

        _ss = self._setup_stories or []
        self._setup_story_dd = searchable_dropdown(
            hint_text=("Search & add a story from this plan" if _ss
                       else ("Loading stories…" if (self.plan_id and self._setup_stories_loading)
                             else "Select a test plan to list its stories")),
            options=[ft.DropdownOption(key=str(s["id"]),
                                       text=f"[{s['id']}] {(s['title'] or '')[:48]}")
                     for s in _ss if s["id"] not in self.story_ids],
            on_select=_add_from_dd, disabled=not _ss,
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
            text_size=13, filled=True, bgcolor=T.CARD, expand=True)

        _build_chips()

        # Checkbox multiselect for the plan's stories (same component as the
        # Regression/Sprint screens), driven by self.story_ids.
        def _toggle_setup_story(key, checked):
            sid = int(key)
            if checked and sid not in self.story_ids:
                self.story_ids.append(sid)
            elif not checked:
                self.story_ids = [s for s in self.story_ids if s != sid]
            self.story_field.value = ""
            self._err_msg = ""
            self._estimated_tc = None
            self.render()
            self._fetch_estimate()

        def _all_setup_stories(checked):
            if checked:
                have = set(self.story_ids)
                for s in (self._setup_stories or []):
                    if s["id"] not in have:
                        self.story_ids.append(s["id"])
            else:
                self.story_ids = []
            self._err_msg = ""
            self._estimated_tc = None
            self.render()
            self._fetch_estimate()

        def _open_setup_stories():
            self._setup_story_open = not self._setup_story_open
            self.render()

        if self._setup_stories_loading:
            story_picker = ft.Container(
                ft.Text("Loading stories…", size=12, color=T.INK_3), padding=10)
        elif not self.plan_id:
            story_picker = ft.Container(
                ft.Text("Select a test plan to list its stories", size=12, color=T.INK_3),
                padding=10)
        elif not _ss:
            story_picker = ft.Container(
                ft.Text("No stories found in this plan.", size=12, color=T.INK_3), padding=10)
        else:
            story_picker = regression._checkbox_multiselect(
                [(str(s["id"]), f"[{s['id']}] {(s['title'] or '')[:60]}") for s in _ss],
                [str(s) for s in self.story_ids],
                _toggle_setup_story, _all_setup_stories,
                is_open=self._setup_story_open, on_open=_open_setup_stories,
                placeholder="Select stories", height=260,
                empty="No stories found in this plan.")

        story_box = ft.Column([
            story_picker,
            ft.Container(height=10),
            ft.Text("Or paste IDs manually", size=10.5, weight=ft.FontWeight.BOLD,
                    color=T.INK_3),
            ft.Container(height=4),
            self.story_field, self._chip_wrap], spacing=0, tight=True)

        self.email_field = ft.TextField(
            value=self.emails, hint_text="qa-leads@wss.com  (optional)",
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12), text_size=13, expand=True,
            on_change=lambda e: setattr(self, "emails", self.email_field.value))

        # Sprint summary button — green (like Create) when a plan is selected,
        # grey/disabled when no plan is chosen yet.
        _sum_enabled = bool(self.plan_id)
        self._summary_btn = ft.FilledButton(
            "Sprint Summary report",
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
        self._open_plan_btn = ft.IconButton(
            ft.Icons.OPEN_IN_NEW, icon_size=17,
            icon_color=(T.VIOLET_INK if self.plan_id else T.INK_3),
            tooltip=("Open this test plan in Azure DevOps"
                     if self.plan_id else "Select a test plan first"),
            disabled=not bool(self.plan_id),
            on_click=lambda e: self._open_azure(),
            style=ft.ButtonStyle(
                bgcolor={"": T.VIOLET_SOFT} if self.plan_id else {"": T.CARD_2},
                shape=ft.RoundedRectangleBorder(radius=T.R)),
            width=46, height=46)

        rows = [
            sec_head("3", "Task",
                     ft.Row([ft.Icon(ft.Icons.ARROW_FORWARD, size=13, color=T.INK_3),
                             ft.Text("from your connection", size=11, color=T.INK_3, weight=ft.FontWeight.BOLD)],
                            spacing=4, tight=True)),
            ft.Container(height=12),
            # Row 1 — Project (full width)
            field_label("Project", req=True),
            ft.Container(self.project_dd, padding=ft.Padding.only(top=4, bottom=12)),
            # Row 2 — Test Plan (50%) · Test Plan ID (50%)
            ft.Row([
                ft.Column([field_label("Test Plan", req=True),
                           ft.Container(self.plan_dd, padding=ft.Padding.only(top=4))],
                          expand=1, spacing=0),
                ft.Column([field_label("Test Plan ID", hint="auto"),
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
                    green_btn("Create Plan", icon=ft.Icons.ADD, expand=True,
                              on_click=lambda e: self._open_create_plan()),
                    expand=1),
                ft.Container(_summary_row, expand=1),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=14),
            field_label("User Story IDs", req=True, hint="comma-separated"),
            ft.Container(story_box, padding=ft.Padding.only(top=4, bottom=12)),
        ]

        rows += [
            field_label("Report Emails", hint="optional", req=False),
            ft.Container(self.email_field, padding=ft.Padding.only(top=4)),
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
            ft.Row([seg("Skip", "skip"), seg("Evaluate", "evaluate", ft.Icons.AUTO_AWESOME)], spacing=4),
            padding=4, bgcolor=T.CARD_2, border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    def _set_existing(self, k):
        self.existing_mode = k; self.render()

    def _on_project_change(self, e):
        self.project = self.project_dd.value
        self.plan_id = None; self.plan_name = None
        self._load_plans()
        self.render()

    def _on_plan_change(self, e):
        self.plan_id = int(self.plan_dd.value)
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
        # Enable/recolor the Open-in-Azure button now that a plan is chosen
        try:
            if hasattr(self, "_open_plan_btn"):
                self._open_plan_btn.disabled = False
                self._open_plan_btn.icon_color = T.VIOLET_INK
                self._open_plan_btn.tooltip = "Open this test plan in Azure DevOps"
                self._open_plan_btn.style = ft.ButtonStyle(
                    bgcolor={"": T.VIOLET_SOFT},
                    shape=ft.RoundedRectangleBorder(radius=T.R))
                self._open_plan_btn.update(); updated_any = True
        except Exception:
            pass
        # Enable/recolor the Sprint Summary button now that a plan is chosen
        try:
            if hasattr(self, "_summary_btn"):
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

    def _load_setup_stories_inplace(self):
        """Load the selected plan's stories (from its requirement suites) and patch
        the story dropdown IN PLACE — fills the picker the instant a plan is chosen,
        with no full re-render (so the scroll position is preserved)."""
        if not (self.connected and self.project and self.plan_id):
            return
        self._setup_stories = None
        self._setup_stories_loading = True
        dd = getattr(self, "_setup_story_dd", None)
        if dd is not None:
            try:
                dd.options = []; dd.value = None; dd.disabled = True
                dd.hint_text = "Loading stories…"; dd.update()
            except Exception:
                pass
        pid = self.plan_id

        def work():
            try:
                ss = E.fetch_stories_in_plan(self.project, pid)
                if not ss:
                    plan = E._azure_get(
                        f"https://dev.azure.com/{E.AZURE_ORG}/{self.project}"
                        f"/_apis/testplan/plans/{pid}?api-version=7.0")
                    itr = plan.get("iteration")
                    ss = E.fetch_stories_in_iteration(self.project, itr) if itr else []
            except Exception:
                ss = []
            if pid != self.plan_id:   # plan changed again mid-load — drop stale result
                return
            self._setup_stories = ss
            self._setup_stories_loading = False

            def apply():
                d = getattr(self, "_setup_story_dd", None)
                if d is None:
                    self.render(); return
                try:
                    d.options = [ft.DropdownOption(key=str(s["id"]),
                                     text=f"[{s['id']}] {(s['title'] or '')[:48]}")
                                 for s in ss if s["id"] not in self.story_ids]
                    d.disabled = not ss
                    d.hint_text = ("Search & add a story from this plan" if ss
                                   else "No stories found in this plan")
                    d.update()
                except Exception:
                    self.render()
            self.ui_safe(apply)
        self._bg(work)

    def _fetch_estimate(self):
        """Fetch the real number of test cases across selected stories (steps mode).
        Updates only the estimate labels in place — no full render, so scroll stays put."""
        if not (self.connected and self.project and self.plan_id and self.story_ids):
            return
        def work():
            try:
                if self.tool == "steps":
                    have, total = E.count_existing_steps(self.project, self.plan_id, self.story_ids)
                    self._estimated_tc = total
                else:
                    self._estimated_tc = len(self.story_ids) * 6
            except Exception:
                return
            # Update just the two labels, not the whole page
            try:
                if hasattr(self, "_est_num"):
                    self._est_num.value = f"~{self._estimated_tc}"
                    self._est_sub.value = f"test cases\nacross {len(self.story_ids)} stories"
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
                sec_head("3", "Task",
                         ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, size=13, color=T.INK_3),
                                 ft.Text("locked", size=11, color=T.INK_3, weight=ft.FontWeight.BOLD)],
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
                            ft.Text("Connect to load projects, plans & stories", size=12,
                                    color=T.INK_2, weight=ft.FontWeight.BOLD)], spacing=6, tight=True),
                    padding=ft.Padding.symmetric(vertical=14, horizontal=9), bgcolor=T.CARD, border_radius=20,
                    border=ft.Border.all(1, T.BORDER)),
                alignment=ft.Alignment.CENTER, expand=True),
        ]), expand=True)

    # ---- right rail ----
    def _setup_right(self):
        if self.connected:
            est = getattr(self, "_estimated_tc", None)
            if est is None: est = len(self.story_ids) * 6 if self.story_ids else 0
            self._est_num = ft.Text(f"~{est}", size=32, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK)
            self._est_sub = ft.Text(f"test cases\nacross {len(self.story_ids)} stories", size=12,
                                    color=T.INK_2, weight=ft.FontWeight.BOLD)
            rows = [("Generator", "Steps" if self.tool == "steps" else "Titles"),
                    ("Language", "Arabic" if self.lang == "ar" else "English"),
                    ("Project", (self.project or "—")[:16]),
                    ("Test plan", f"#{self.plan_id}" if self.plan_id else "—"),
                    ("Stories", f"{len(self.story_ids)} selected"),
                    ("Email", "1 recipient" if self.emails.strip() else "—")]
            if self.tool == "steps":
                rows.insert(5, ("Existing", self.existing_mode.title()))
            full_vals = {"Project": (self.project or "—"),
                         "Test plan": (f"#{self.plan_id}" if self.plan_id else "—")}
            detail_rows = []
            for i, (k, v) in enumerate(rows):
                val_text = ft.Text(v, size=12, color=T.INK, weight=ft.FontWeight.BOLD,
                                   tooltip=full_vals.get(k))
                if k == "Stories":
                    self._sum_stories = val_text
                if k == "Test plan":
                    self._sum_plan = val_text
                detail_rows.append(ft.Container(
                    ft.Row([ft.Text(k, size=12, color=T.INK_2, weight=ft.FontWeight.BOLD),
                            ft.Container(expand=True),
                            val_text]),
                    padding=ft.Padding.symmetric(vertical=0, horizontal=8),
                    border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER_2)) if i < len(rows)-1 else None))
            ready = bool(self.project and self.plan_id and self.story_ids)
            return card(ft.Column([
                ft.Text("THIS RUN", size=11, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK),
                ft.Container(height=13),
                *detail_rows,
                ft.Container(expand=True),
                ft.Container(
                    ft.Row([self._est_num, self._est_sub],
                           spacing=8, vertical_alignment=ft.CrossAxisAlignment.END),
                    padding=ft.Padding.only(bottom=14)),
                primary_btn("Start run", icon=ft.Icons.PLAY_ARROW, expand=True,
                            on_click=lambda e: self._start_run()),
            ], spacing=0, expand=True), expand=True)
        else:
            return card(ft.Column([
                ft.Text("STEP 1 · CONNECT", size=11, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK),
                ft.Container(height=13),
                ft.Row([ft.Container(width=8, height=8, bgcolor=T.RED, border_radius=10),
                        ft.Text("Not connected yet", size=12, color=T.RED, weight=ft.FontWeight.BOLD)], spacing=6),
                ft.Container(height=14),
                ft.Text("Save your credentials, then connect. We validate the PAT and load this org's projects and plans.",
                        size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500),
                ft.Container(height=14),
                *[ft.Container(ft.Row([
                    ft.Container(ft.Text(str(i+1), size=11, color=T.INK_3, weight=ft.FontWeight.BOLD),
                                 width=20, height=20, bgcolor=T.CARD_2, border_radius=6,
                                 border=ft.Border.all(1, T.BORDER), alignment=ft.Alignment.CENTER),
                    ft.Text(t, size=12, color=T.INK_2, weight=ft.FontWeight.W_500)], spacing=8),
                    padding=ft.Padding.only(bottom=8))
                  for i, t in enumerate(["Validates the Azure DevOps PAT",
                                         "Loads projects in this organization",
                                         "Fetches existing test plans"])],
                ft.Container(expand=True),
                *([ ft.Container(
                        ft.Row([
                            ft.ProgressRing(width=16, height=16, stroke_width=2, color=T.VIOLET),
                            ft.Text(self._connect_status, size=12, color=T.INK_2,
                                    weight=ft.FontWeight.BOLD),
                        ], spacing=10),
                        padding=ft.Padding.symmetric(vertical=10, horizontal=0),
                    )] if self._connecting else []),
                primary_btn("Connect & load projects", icon=ft.Icons.POWER, expand=True,
                            disabled=self._connecting,
                            on_click=lambda e: self._connect()),
                ft.Container(
                    ft.Row([
                        ft.Icon(ft.Icons.LOCK_OUTLINE, size=12, color=T.INK_3),
                        ft.Text("Task setup unlocks once connected", size=11,
                                color=T.INK_3, weight=ft.FontWeight.BOLD),
                    ], spacing=5, tight=True,
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
            self._err("API Key is required for the selected provider."); return
        self.creds["keys"][name] = key
        pat = self._field_or_saved("pat_field", self.creds.get("pat", ""))
        if not pat:
            self._err("Azure DevOps PAT is required."); return
        self.creds["pat"] = pat
        gmail = self._field_or_saved("gmail_field", self.creds.get("gmail", ""))
        self.creds["gmail"] = gmail
        org = self._field_or_saved("org_field", self.creds.get("org", "")) or E.AZURE_ORG
        self.creds["org"] = org
        sender = self._field_or_saved("sender_field", self.creds.get("gmail_sender", "")) or E.GMAIL_SENDER
        self.creds["gmail_sender"] = sender
        store.save(self.creds)

        E.set_credentials(provider=name, api_key=key, pat=pat, gmail=gmail,
                          org=org, gmail_sender=sender,
                          model=(self._saved_model(name) or None))
        self._err("")
        self._connecting = True
        self._connect_status = "Validating PAT & loading projects…"
        self.render()   # show the spinner immediately

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
            try:
                # 1) Validate the AI provider key first (cheap ping)
                self._connect_status = "Checking AI provider key…"
                self._safe_render()
                kok, kmsg = E.validate_api_key()
                if not kok:
                    prov = E.T_disp(E.AI_PROVIDER)
                    if kmsg == "auth":
                        self._err(f"{prov}: API key rejected. Check the key is correct and active.")
                    elif kmsg == "network":
                        self._err(f"{prov}: cannot reach the provider — check your network/firewall.")
                    elif kmsg == "timeout":
                        self._err(f"{prov}: the provider timed out. Try again in a moment.")
                    elif kmsg in ("server", "overloaded"):
                        self._err(f"{prov}: the provider is temporarily unavailable. Try again shortly.")
                    elif kmsg == "content_filter":
                        self._err(f"{prov}: the test request was blocked by a safety filter. Try a different model.")
                    elif kmsg.startswith("missing-package:"):
                        pkg = kmsg.split(":", 1)[1]
                        self._err(f"{prov}: the '{pkg}' package isn't installed. "
                                  f"Re-run the installer or: pip install {pkg}")
                    elif kmsg.startswith("error:"):
                        # already a friendly classified message (e.g. bad model)
                        self._err(kmsg.split(":", 1)[1].strip())
                    else:
                        self._err(f"{prov} key check failed: {kmsg}")
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
                # 2) Validate the Azure PAT
                self._connect_status = "Validating PAT & loading projects…"
                self._safe_render()
                ok, msg = E.validate_pat(pat)
                if not ok:
                    self._err(_friendly(msg))
                    return
                self._projects = E.fetch_projects(pat)
                self.connected = True
                self._run_finished = False
                self.last_report = None
                self.nav_state = {"setup": "active"}
                if self._projects:
                    self.project = self._projects[0]
                    self._load_plans()
            except Exception as ex:
                self._err(_friendly(str(ex)))
            finally:
                # ALWAYS clear the loading state and repaint, success or fail
                self._connecting = False
                self._connect_status = ""
                self._safe_render()
        self._bg(work)

    def _bg(self, fn):
        """Run fn in a background thread using Flet's loop-aware runner when available.
        This fixes the 0.85 bug where thread updates don't repaint until refocus."""
        runner = getattr(self.page, "run_thread", None)
        if callable(runner):
            runner(fn)
        else:
            threading.Thread(target=fn, daemon=True).start()

    def _track_scroll(self, e):
        try:
            self._scroll_offset = e.pixels
        except Exception:
            pass

    def _restore_scroll(self):
        # Restore scroll after a full render so opening a dropdown / ticking a
        # checkbox doesn't snap to the top. scroll_to must run AFTER the rebuilt
        # control is laid out, so defer it onto the page loop.
        col = getattr(self, "_left_scroll", None)
        off = getattr(self, "_scroll_offset", 0) or 0
        if not (col is not None and off):
            return

        def _do():
            try:
                col.scroll_to(offset=off, duration=0)
            except Exception:
                pass
            try:
                self.page.update()
            except Exception:
                pass
        ru = getattr(self.page, "run_thread", None)
        if callable(ru):
            try:
                ru(_do)
                return
            except Exception:
                pass
        _do()

    def _close_dropdowns(self, e=None):
        # Click-away: tapping outside an open dropdown closes it.
        changed = False
        for attr in ("_setup_story_open", "_reg_plan_open",
                     "_reg_story_open", "_cp_sprint_open"):
            if getattr(self, attr, False):
                setattr(self, attr, False)
                changed = True
        if changed:
            self.render()

    def _safe_render(self):
        """Render and force the update onto Flet's event loop."""
        try:
            self.render()
        except Exception:
            try:
                self.page.update()
            except Exception:
                pass
        # Force a second update via the page loop (works around focus-repaint bug)
        try:
            ru = getattr(self.page, "run_thread", None)
            if callable(ru):
                ru(lambda: self.page.update())
        except Exception:
            pass

    def _load_plans(self):
        if not self.project:
            self._plans = []; return
        try:
            self._plans = E.fetch_test_plans(self.project)
        except Exception as ex:
            self._plans = []
            self._err(f"Could not load test plans: {ex}")

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
        if not self.project:
            self._err("Select a project first."); return

        name_field = ft.TextField(
            hint_text="e.g. Sprint 24 — Regression",
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
            text_size=13, expand=True)
        iter_dd = ft.Dropdown(
            hint_text="Loading sprints…", options=[],
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
            text_size=13, filled=True, bgcolor=T.CARD, expand=True)
        path_box = ft.Text("—", size=12.5, color=T.VIOLET_INK, weight=ft.FontWeight.BOLD, font_family=T.F_MONO)
        modal_err = ft.Text("", size=12, color=T.RED, weight=ft.FontWeight.BOLD)

        # Auto-create requirement suites for every sprint story (PAT-only, no AI)
        auto_suites = ft.Checkbox(value=True, label="", scale=0.9,
                                  active_color=T.VIOLET, check_color="#FFFFFF")

        # In-modal progress UI (design: 8px rounded track + violet gradient fill)
        prog_label = ft.Text("", size=12, color=T.INK_2, weight=ft.FontWeight.BOLD)
        prog_pct = ft.Text("", size=12, color=T.VIOLET_INK, weight=ft.FontWeight.BOLD)
        prog_spin = ft.ProgressRing(width=14, height=14, stroke_width=2, color=T.VIOLET)
        prog_bar = ft.ProgressBar(value=0, color=T.VIOLET, bgcolor="#E9E8F0",
                                  bar_height=8, border_radius=99)
        prog_box = ft.Container(
            ft.Column([
                ft.Row([prog_spin, prog_label, ft.Container(expand=True), prog_pct],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                prog_bar,
            ], spacing=8),
            padding=ft.Padding.only(top=10), visible=False)

        iters_cache = {"list": []}
        def load_iters():
            try:
                lst = E.fetch_iterations(self.project)
                iters_cache["list"] = lst
                iter_dd.options = [ft.DropdownOption(key=it["path"], text=it["path"]) for it in lst]
                if lst:
                    iter_dd.value = lst[-1]["path"]
                    path_box.value = iter_dd.value
                    iter_dd.hint_text = "Select sprint"
                else:
                    iter_dd.hint_text = "No sprints found"
                modal_err.value = ""
            except Exception as ex:
                iter_dd.hint_text = "Failed to load"
                modal_err.value = f"Could not load sprints: {str(ex)[:100]}"
            try:
                iter_dd.update()
            except Exception:
                pass
            try:
                self.page.update()
            except Exception:
                pass

        def on_iter_change(e):
            path_box.value = iter_dd.value or "—"
            self.page.update()
        iter_dd.on_select = on_iter_change

        def _set_prog(pct, label):
            prog_box.visible = True
            prog_bar.value = max(0.0, min(1.0, pct))
            prog_label.value = label
            prog_pct.value = f"{int(pct*100)}%"
            # hide the spinner once finished
            prog_spin.visible = pct < 1.0
            def _paint():
                try: self.page.update()
                except Exception: pass
            _paint()
            # background-thread updates don't always repaint until the loop ticks;
            # force a second update via the page loop (same trick as _safe_render)
            try:
                ru = getattr(self.page, "run_thread", None)
                if callable(ru):
                    ru(_paint)
            except Exception:
                pass

        def do_create(e):
            nm = (name_field.value or "").strip()
            pth = (iter_dd.value or "").strip()
            if not nm:
                modal_err.value = "Plan name is required."; self.page.update(); return
            if not pth:
                modal_err.value = "Select a sprint/iteration."; self.page.update(); return
            modal_err.value = ""
            create_btn.visible = False; cancel_btn.visible = False
            # show the progress bar immediately at 0% (before any slow work begins)
            _set_prog(0.0, "Starting…")

            def work():
                try:
                    if not auto_suites.value:
                        _set_prog(0.15, "Creating test plan…")
                        new_id = E.create_test_plan(self.project, nm, pth)
                        self.plan_id = new_id; self.plan_name = nm
                        _set_prog(1.0, "Done")
                        self._load_plans(); self._close_dialog(); self.render()
                        return

                    def cb(ev, payload):
                        if ev == "plan":
                            _set_prog(0.10, "Plan created · finding sprint stories…")
                        elif ev == "stories":
                            n = payload["total"]
                            if n == 0:
                                _set_prog(1.0, "Plan created · no stories in this sprint")
                            else:
                                _set_prog(0.15, f"Found {n} stories · creating suites…")
                        elif ev == "suite":
                            i, n = payload["done"], payload["total"]
                            frac = 0.15 + 0.85 * (i / n) if n else 1.0
                            _set_prog(frac, f"Creating suite {i} of {n}…")
                        elif ev == "done":
                            self.plan_id = payload["plan_id"]; self.plan_name = nm
                            # NOTE: story IDs field is intentionally left untouched —
                            # the suites are created in Azure, but the user enters the
                            # story IDs they actually want to run themselves.
                            c = payload.get("created", 0); s = payload.get("skipped", 0)
                            f = payload.get("failed", 0)
                            _set_prog(1.0, f"Done · {c} created · {s} existed"
                                      + (f" · {f} failed" if f else ""))

                    E.create_plan_with_sprint_suites(self.project, nm, pth, cb=cb)
                    import time as _t; _t.sleep(0.4)
                    self._load_plans(); self._close_dialog(); self.render()
                except Exception as ex:
                    create_btn.visible = True; cancel_btn.visible = True
                    prog_box.visible = False
                    modal_err.value = f"Create failed: {str(ex)[:140]}"
                    try: self.page.update()
                    except Exception: pass
            self._bg(work)

        cancel_btn = ghost_btn("Cancel", on_click=lambda e: self._close_dialog())
        create_btn = green_btn("Create plan", icon=ft.Icons.ADD, on_click=do_create)

        dlg = ft.AlertDialog(
            modal=True,
            bgcolor=T.CARD,
            shape=ft.RoundedRectangleBorder(radius=T.R_LG),
            title=ft.Row([
                ft.Container(ft.Icon(ft.Icons.ADD, size=18, color=T.GREEN),
                             width=34, height=34, bgcolor=T.GREEN_SOFT, border_radius=9,
                             alignment=ft.Alignment.CENTER),
                ft.Column([
                    ft.Text("Create test plan", size=15, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Text("Created under the selected iteration in this project.",
                            size=11, color=T.INK_2, weight=ft.FontWeight.W_500),
                ], spacing=1, expand=True),
            ], spacing=10),
            content=ft.Container(width=470, content=ft.Column([
                field_label("Plan name", req=True),
                ft.Container(name_field, padding=ft.Padding.only(top=4, bottom=14)),
                field_label("Iteration / Sprint", req=True),
                ft.Container(iter_dd, padding=ft.Padding.only(top=4, bottom=10)),
                ft.Text("Will be created at", size=11, color=T.INK_3, weight=ft.FontWeight.BOLD),
                ft.Container(
                    path_box,
                    padding=ft.Padding.symmetric(vertical=11, horizontal=13),
                    bgcolor=T.VIOLET_SOFT, border_radius=T.R,
                    border=ft.Border.all(1, "#E0DAFF"), margin=ft.Margin.only(top=5),
                    width=9999),
                # Auto-suites option
                ft.Container(
                    ft.Row([
                        auto_suites,
                        ft.Column([
                            ft.Text("Add requirement suites for sprint stories",
                                    size=12.5, color=T.INK, weight=ft.FontWeight.BOLD),
                            ft.Text("Creates one suite per User Story in the sprint (Azure only — no AI).",
                                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
                        ], spacing=1, expand=True),
                    ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.Padding.only(top=12)),
                prog_box,
                ft.Container(modal_err, padding=ft.Padding.only(top=8)),
            ], spacing=0, tight=True)),
            actions=[cancel_btn, create_btn],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dialog(dlg)
        self._bg(load_iters)

    # ── Sprint summary report (read-only, before a run) ────────────────────
    def _open_sprint_summary(self):
        if not (self.project and self.plan_id):
            self._toast("Select a test plan first.")
            return

        # State → brand color/soft-bg mapping for status chips
        def _state_kind(state):
            s = (state or "").lower()
            if s in ("done", "closed", "completed", "resolved"):
                return "green"
            if s in ("active", "in progress", "committed", "doing"):
                return "violet"
            if s in ("new", "to do", "proposed", "open"):
                return "amber"
            return "grey"

        # animated 'scanning' loading state — motion while the summary is generated
        _sum_status = ft.Text("Connecting to Azure DevOps…", size=12.5, color=T.INK_2,
                              weight=ft.FontWeight.W_500)
        _scan = ft.Column([
            ft.Container(ft.Stack([
                ft.ProgressRing(width=76, height=76, stroke_width=3,
                                color=ft.Colors.with_opacity(0.85, T.VIOLET)),
                ft.Container(ft.Icon(ft.Icons.SUMMARIZE_OUTLINED, size=26, color=T.VIOLET),
                             width=54, height=54, bgcolor=T.VIOLET_SOFT, border_radius=16,
                             alignment=ft.Alignment.CENTER, left=11, top=11),
            ], width=76, height=76), width=76, height=76, alignment=ft.Alignment.CENTER),
            ft.Container(height=18),
            ft.Text("Generating sprint summary", size=16, weight=ft.FontWeight.BOLD,
                    color=T.INK),
            ft.Container(height=5),
            _sum_status,
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
           alignment=ft.MainAxisAlignment.CENTER, spacing=0)
        body_col = ft.Column(
            [ft.Container(_scan, alignment=ft.Alignment.CENTER, height=380, expand=True)],
            spacing=12, tight=True, scroll=ft.ScrollMode.AUTO)

        # cycle the status line every ~0.9s until the data arrives
        self._sum_loading = True
        def _cycle_status():
            msgs = ["Connecting to Azure DevOps…", "Fetching sprint results…",
                    "Counting test cases…", "Summarizing stories…"]
            ev = threading.Event()
            i = 0
            while getattr(self, "_sum_loading", False):
                ev.wait(0.9)
                if not getattr(self, "_sum_loading", False):
                    break
                i += 1
                def upd(m=msgs[i % len(msgs)]):
                    _sum_status.value = m
                    try: _sum_status.update()
                    except Exception: pass
                self.ui_safe(upd)
        self._bg(_cycle_status)

        # email recipients field (asked each time) + status text
        self._sum_data = None
        email_field = ft.TextField(
            hint_text="recipient@example.com, another@example.com",
            value=(self.emails or ""),
            bgcolor=T.CARD, filled=True,
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=10, horizontal=12),
            text_size=12.5, dense=True, expand=True)
        email_status = ft.Text("", size=11.5, weight=ft.FontWeight.BOLD, visible=False)

        def do_email(e=None):
            if not self._sum_data:
                return
            if not E.GMAIL_APP_PASS:
                email_status.value = "Set a Gmail App Password in Setup → Connection first."
                email_status.color = T.AMBER
                email_status.visible = True
                try: email_status.update()
                except Exception: self.render()
                return
            to = [x.strip() for x in (email_field.value or "").split(",") if x.strip()]
            if not to:
                email_status.value = "Enter at least one recipient."
                email_status.color = T.RED
                email_status.visible = True
                try: email_status.update()
                except Exception: self.render()
                return
            email_status.value = "Sending…"; email_status.color = T.INK_2
            email_status.visible = True
            try: email_status.update()
            except Exception: self.render()

            def work():
                html = E.build_sprint_summary_email(self._sum_data)
                plan = self._sum_data.get("plan_name", "")
                ok, err = E.send_report(to, f"QA Studio — Sprint Summary — {plan}", html)
                def show():
                    if ok:
                        email_status.value = f"Summary emailed to {', '.join(to)}"
                        email_status.color = T.GREEN
                    else:
                        email_status.value = f"Email failed — {err}"
                        email_status.color = T.RED
                    email_status.visible = True
                    try: email_status.update()
                    except Exception: self.render()
                self.ui_safe(show)
            self._bg(work)

        email_btn = green_btn("Email summary", icon=ft.Icons.MAIL_OUTLINED,
                              on_click=do_email)
        close_btn = ghost_btn("Close", on_click=lambda e: self._close_dialog())

        email_bar = ft.Column([
            ft.Container(height=1, bgcolor=T.BORDER_2),
            ft.Container(height=6),
            ft.Text("EMAIL THIS SUMMARY", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=5),
            ft.Row([email_field, email_btn], spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            email_status,
        ], spacing=0, tight=True)
        email_bar.visible = False  # shown only after data loads

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([ft.Container(logo_img(24, ft.Icons.SUMMARIZE_OUTLINED, T.VIOLET_INK),
                                       width=24, height=24, alignment=ft.Alignment.CENTER),
                          ft.Text("Sprint Summary", weight=ft.FontWeight.BOLD, size=16)],
                         spacing=9, tight=True),
            content=ft.Container(
                ft.Column([ft.Container(body_col, expand=True), email_bar],
                          spacing=4, tight=False),
                width=560, height=540),
            actions=[close_btn],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dialog(dlg)

        def load():
            try:
                data = E.sprint_summary(self.project, self.plan_id)
            except Exception as ex:
                self._sum_loading = False
                def show_err():
                    body_col.controls = [ft.Row([
                        ft.Icon(ft.Icons.ERROR_OUTLINE, color=T.RED, size=20),
                        ft.Text(f"Could not load summary: {str(ex)[:160]}",
                                size=12.5, color=T.RED, weight=ft.FontWeight.W_500, expand=True)],
                        spacing=8)]
                    try: body_col.update()
                    except Exception: self.render()
                self.ui_safe(show_err)
                return

            def render_summary():
                self._sum_data = data
                self._sum_loading = False
                total = data["total_stories"]
                by_state = data["by_state"]
                total_tc = data["total_test_cases"]

                # Header line
                header = ft.Column([
                    ft.Row([ft.Container(
                        ft.Text("SPRINT SNAPSHOT", size=10, weight=ft.FontWeight.BOLD,
                                color=T.VIOLET_INK),
                        bgcolor=T.VIOLET_SOFT, border_radius=20,
                        padding=ft.Padding.symmetric(vertical=4, horizontal=11))], tight=True),
                    ft.Container(height=8),
                    ft.Text(data["plan_name"], size=18, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Text(data["iteration"] or "—", size=11, color=T.INK_3,
                            weight=ft.FontWeight.BOLD, font_family=T.F_MONO),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.START)

                # Stat tiles: total stories + total test cases
                tiles = ft.Row([
                    stat_tile("Stories", total, tone="violet"),
                    stat_tile("Test Cases", total_tc, tone="green"),
                    stat_tile("Statuses", len(by_state), tone="amber"),
                ], spacing=10)

                # Status breakdown — small color-coded cards (count + label)
                _kind_colors = {
                    "green":  (T.GREEN, "#E5F6EC"),
                    "violet": (T.VIOLET_INK, "#ECE8FF"),
                    "amber":  (T.AMBER, "#FAF1DD"),
                    "grey":   (T.INK_2, "#F1F0F5"),
                }
                def _status_card(label, count, kind):
                    fg, bg = _kind_colors.get(kind, _kind_colors["grey"])
                    return ft.Container(
                        ft.Column([
                            ft.Text(str(count), size=22, weight=ft.FontWeight.BOLD, color=fg),
                            ft.Text(label, size=11, weight=ft.FontWeight.BOLD, color=T.INK_2,
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ], spacing=1, horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True),
                        bgcolor=bg, border_radius=T.R, padding=ft.Padding.symmetric(vertical=12, horizontal=14),
                        width=104, tooltip=f"{label}: {count}")
                state_cards = []
                for st, cnt in sorted(by_state.items(), key=lambda x: -x[1]):
                    state_cards.append(_status_card(st, cnt, _state_kind(st)))
                status_row = ft.Row(state_cards, wrap=True, spacing=10, run_spacing=10) \
                    if state_cards else ft.Text("No stories in this sprint.",
                                                size=12, color=T.INK_3, weight=ft.FontWeight.W_500)
                dist_bar = ft.Container(
                    ft.Row([ft.Container(expand=max(1, c),
                                         bgcolor=_kind_colors.get(_state_kind(stt),
                                                                  _kind_colors["grey"])[0],
                                         tooltip=f"{stt}: {c}")
                            for stt, c in sorted(by_state.items(), key=lambda x: -x[1])],
                           spacing=2),
                    height=10, border_radius=6,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE) if by_state else ft.Container()

                # Per-story rows
                story_rows = []
                for s in data["stories"]:
                    rtl = any('\u0600' <= c <= '\u06ff' for c in s["title"])
                    _wi_url = (f"https://dev.azure.com/{E.AZURE_ORG}/{self.project}"
                               f"/_workitems/edit/{s['id']}")
                    story_rows.append(ft.Container(
                        ft.Row([
                            ft.Column([
                                ft.Text(s["title"] or "(no title)", size=12.5,
                                        weight=ft.FontWeight.BOLD, color=T.INK,
                                        font_family=(T.F_AR if rtl else None),
                                        text_align=(ft.TextAlign.RIGHT if rtl else ft.TextAlign.LEFT),
                                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(f"#{s['id']}", size=10.5, color=T.INK_3,
                                        weight=ft.FontWeight.BOLD, font_family=T.F_MONO),
                            ], spacing=2, expand=True),
                            badge(f"{s['test_cases']} TC", "grey"),
                            badge(s["state"], _state_kind(s["state"])),
                            ft.Icon(ft.Icons.OPEN_IN_NEW, size=14, color=T.INK_3),
                        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        padding=ft.Padding.symmetric(vertical=10, horizontal=12),
                        border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER_2)),
                        tooltip=f"{s['title']}  ·  open #{s['id']} in Azure DevOps",
                        on_click=lambda e, u=_wi_url: self._open_url(u),
                        ink=True))
                if not story_rows:
                    story_rows = [ft.Text("No user stories found in this sprint.",
                                          size=12, color=T.INK_3, weight=ft.FontWeight.W_500)]

                body_col.controls = [
                    header,
                    ft.Container(height=4),
                    tiles,
                    ft.Container(height=10),
                    dist_bar,
                    ft.Container(height=8),
                    ft.Text("STATUS BREAKDOWN", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
                    status_row,
                    ft.Container(height=6),
                    ft.Text("STORIES", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
                    ft.Container(ft.Column(story_rows, spacing=0, scroll=ft.ScrollMode.AUTO),
                                 bgcolor="#FCFCFE", border=ft.Border.all(1, T.BORDER),
                                 border_radius=T.R, padding=ft.Padding.symmetric(vertical=2, horizontal=4),
                                 height=240),
                ]
                email_bar.visible = True
                try:
                    body_col.update(); email_bar.update()
                except Exception:
                    self.render()

            self.ui_safe(render_summary)

        self._bg(load)

    def ui_safe(self, fn):
        """Run a UI mutation on the page thread; fall back to direct call."""
        try:
            ru = getattr(self.page, "run_thread", None)
            if callable(ru):
                ru(fn); return
        except Exception:
            pass
        try:
            fn()
        except Exception:
            pass

    def _show_dialog(self, dlg):
        self._dialog = dlg
        # Flet 0.85 uses page.show_dialog(); 0.24-0.79 uses page.open(); older sets page.dialog
        if hasattr(self.page, "show_dialog"):
            self.page.show_dialog(dlg)
        elif hasattr(self.page, "open"):
            self.page.open(dlg)
        else:
            self.page.dialog = dlg
            dlg.open = True
            self.page.update()

    def _close_dialog(self):
        self._sum_loading = False
        dlg = getattr(self, "_dialog", None)
        # Flet 0.85 uses page.pop_dialog(); older uses page.close(dlg)
        if hasattr(self.page, "pop_dialog"):
            try:
                self.page.pop_dialog()
                return
            except Exception:
                pass
        if dlg is not None and hasattr(self.page, "close"):
            try:
                self.page.close(dlg)
                return
            except Exception:
                pass
        if dlg is not None:
            dlg.open = False
            self.page.update()

    # ═══════════════════════════════════════════════════════════════════════════
    #  EXISTING STEPS MODAL
    # ═══════════════════════════════════════════════════════════════════════════
    def _open_existing_steps_modal(self, have, total, on_choice):
        chosen = {"mode": "evaluate"}

        def opt(title, desc, key, icon, recommended=False):
            sel = (chosen["mode"] == key)
            head = [ft.Icon(icon, size=15, color=(T.VIOLET_INK if key == "evaluate" else T.INK_2)),
                    ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=T.INK)]
            if recommended:
                head.append(badge("Recommended", "violet"))
            box = ft.Container(
                ft.Row([
                    ft.Container(width=16, height=16, border_radius=10,
                                 border=ft.Border.all(2, (T.VIOLET if sel else T.BORDER)),
                                 bgcolor=(T.VIOLET if sel else None)),
                    ft.Column([ft.Row(head, spacing=7),
                               ft.Text(desc, size=11.5, color=T.INK_2, weight=ft.FontWeight.W_500)],
                              spacing=4, expand=True),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
                padding=12, border_radius=T.R,
                border=ft.Border.all(1, (T.VIOLET if sel else T.BORDER)),
                bgcolor=(T.VIOLET_SOFT if sel else T.CARD_2),
                on_click=lambda e, k=key: select(k))
            return box

        body = ft.Column(spacing=10)
        def select(k):
            chosen["mode"] = k
            rebuild()
        def rebuild():
            body.controls = [
                opt("Skip existing steps",
                    "Leave them untouched and only fill the empty ones. Fast — uses no AI credits on cases that already have steps.",
                    "skip", ft.Icons.CHECK),
                opt("Evaluate with AI",
                    "Checks each existing test case against the requirements and regenerates only the inadequate ones. Flagged cases appear in the report.",
                    "evaluate", ft.Icons.AUTO_AWESOME, recommended=True),
            ]
            self.page.update()
        rebuild()

        def cont(e):
            self._close_dialog()
            on_choice(chosen["mode"])

        dlg = ft.AlertDialog(
            modal=True,
            content=ft.Container(width=496, content=ft.Column([
                ft.Row([
                    ft.Container(ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=20, color=T.AMBER),
                                 width=38, height=38, bgcolor=T.AMBER_SOFT, border_radius=11,
                                 alignment=ft.Alignment.CENTER),
                    ft.Column([
                        ft.Text("Some test cases already have steps", size=15, weight=ft.FontWeight.BOLD, color=T.INK),
                        ft.Text(f"{have} of {total} test cases in this plan already contain steps. Choose how to handle them.",
                                size=12, color=T.INK_2, weight=ft.FontWeight.W_500),
                    ], spacing=1, expand=True),
                ], spacing=11),
                ft.Container(height=14),
                body,
            ], spacing=0, tight=True)),
            actions=[
                ghost_btn("Cancel", on_click=lambda e: self._close_dialog()),
                primary_btn("Continue", icon=ft.Icons.ARROW_FORWARD, on_click=cont),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=T.R_LG),
        )
        self._show_dialog(dlg)

    # ═══════════════════════════════════════════════════════════════════════════
    #  RUN — start + live screen
    # ═══════════════════════════════════════════════════════════════════════════
    def _start_run(self):
        # Commit any ID still sitting in the input box
        inp = getattr(self, "_story_input", None)
        if inp is not None and (inp.value or "").strip():
            for x in (inp.value or "").strip().strip("()[],").replace(",", " ").split():
                if x.isdigit() and int(x) not in self.story_ids:
                    self.story_ids.append(int(x))
            inp.value = ""
        if not self.project:
            self._err("Select a project first."); return
        if not self.plan_id:
            self._err("Select or create a test plan first."); return
        if not self.story_ids:
            self._err("Add at least one User Story ID."); return
        self._err_msg = ""  # all good — clear any prior validation error
        # Steps tool: check existing steps first
        if self.tool == "steps":
            self._busy("Checking existing steps…")
            def precheck():
                # First: confirm every story actually belongs to the selected plan
                try:
                    found, missing = E.validate_stories_in_plan(
                        self.project, self.plan_id, self.story_ids)
                except Exception as ex:
                    self._unbusy()
                    self._err(f"Could not verify stories: {str(ex)[:90]}")
                    return
                if missing:
                    self._unbusy()
                    ids = ", ".join(str(m) for m in missing)
                    if not found:
                        self._err(f"Story {ids} is not in test plan #{self.plan_id}. "
                                  f"Add the story to the plan in Azure, or pick the correct plan.")
                    else:
                        self._err(f"These stories aren't in plan #{self.plan_id}: {ids}. "
                                  f"Remove them or switch to the plan that contains them.")
                    return
                try:
                    have, total = E.count_existing_steps(self.project, self.plan_id, self.story_ids)
                except Exception:
                    have, total = 0, 0
                self._unbusy()
                if total == 0:
                    # No test cases yet — the Steps run will generate titles first,
                    # create the test cases, then add steps. No Skip/Evaluate needed.
                    self.existing_mode = "skip"
                    self._launch_run("skip")
                    return
                if have > 0:
                    # some cases already have steps → ask Skip vs Evaluate
                    self._open_existing_steps_modal(have, total,
                        on_choice=lambda mode: self._launch_run(mode))
                else:
                    # cases exist but none have steps → just generate, no prompt
                    self.existing_mode = "skip"
                    self._launch_run("skip")
            self._bg(precheck)
        else:
            # Titles tool: still verify stories belong to the plan first
            self._busy("Verifying stories…")
            def precheck_titles():
                try:
                    found, missing = E.validate_stories_in_plan(
                        self.project, self.plan_id, self.story_ids)
                except Exception as ex:
                    self._unbusy(); self._err(f"Could not verify stories: {str(ex)[:90]}"); return
                self._unbusy()
                if missing:
                    ids = ", ".join(str(m) for m in missing)
                    self._err(f"Story {ids} is not in test plan #{self.plan_id}. "
                              f"Add it to the plan in Azure, or pick the correct plan.")
                    return
                self._launch_run(None)
            self._bg(precheck_titles)

    def _launch_run(self, existing_mode):
        if existing_mode:
            self.existing_mode = existing_mode
        # apply the chosen output language for this run
        try:
            E.set_output_lang(self.lang)
        except Exception:
            pass
        self.stop_flag = False
        self._stopping = False
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
        self._current_wip = None
        self._story_prog = {}
        self._emailed_to = None
        self._run_finished = False
        self._run_started = False
        self._set_run_active(True)
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
                # If this result replaces a "generating…" spinner line, remove that line
                rw = payload.get("replace_wip")
                if rw is not None:
                    self._log_lines = [l for l in self._log_lines if l.get("wip_id") != rw]
                    # force a full re-render of the log since we removed a line
                    self._rendered_count = -1
                self._log_lines.append(payload)
                if payload.get("detail"):
                    self._log_lines.append({"tone": "dim", "indent": True, "msg": payload["detail"]})
            elif ev == "done":
                self.last_report = payload
                reason = payload.get("reason")
                if reason == "credit":
                    self._log_lines.append({"tone": "err", "ico": "✕",
                        "msg": "Out of AI credits — run stopped. Add credits to your provider and retry."})
                elif payload.get("errors", 0) and "failed" in str(payload.get("summary","")).lower():
                    pass  # individual errors already logged
            self._refresh_run()

        def work():
            try:
                if self.tool == "steps":
                    E.run_steps(self.project, self.plan_id, self.story_ids, cb,
                                should_stop=lambda: self.stop_flag,
                                existing_mode=self.existing_mode)
                else:
                    E.run_titles(self.project, self.plan_id, self.story_ids, cb,
                                 should_stop=lambda: self.stop_flag)
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
            elif not E.GMAIL_APP_PASS:
                self._log_lines.append({"tone": "warn",
                    "msg": "No email sent — Gmail App Password not set in Setup → Connection."})
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
                    plan_url = (f"https://dev.azure.com/{E.AZURE_ORG}/{self.project}"
                                f"/_testPlans/define?planId={self.plan_id}")
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
                html = E.build_report_email(tool_name, rpt.get("summary",""), stats,
                                            rpt.get("action_items",[]),
                                            rpt.get("skipped_items",[]),
                                            per_story=rpt.get("per_story", []),
                                            plan_url=plan_url,
                                            total_secs=_secs,
                                            log_lines=email_log,
                                            org=E.AZURE_ORG, project=self.project)
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
        self._toast("Will stop after the current test case…")
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
            return [ft.Container(
                ft.Row([ft.ProgressRing(width=14, height=14, stroke_width=2, color=T.VIOLET),
                        ft.Text("Preparing stories…", size=12.5, color=T.INK_3,
                                weight=ft.FontWeight.BOLD)], spacing=10),
                padding=14)]
        # 2-column grid
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
        _apply()
        try:
            ru = getattr(self.page, "run_thread", None)
            if callable(ru): ru(_apply)
        except Exception:
            pass

    def run_screen(self):
        s = getattr(self, "_stats", {"total": 0, "stories_done": 0, "total_stories": 0,
                                     "done": 0, "skipped": 0, "errors": 0, "created": 0})
        p = getattr(self, "_progress", {"pct": 0, "label": "Starting…"})
        if not hasattr(self, "_story_prog"):
            self._story_prog = {}
        # NOTE: do NOT reset _run_finished / _run_started / _emailed_to here.
        # run_screen() also runs when navigating BACK to Run after a finished run;
        # resetting them would make the spinner animate and stories show "Running"
        # again. These flags are initialized only in _launch_run().

        is_steps = (self.tool == "steps")
        if is_steps:
            self._stats_row = ft.Row([
                stat_tile("Test Cases", s["total"]),
                stat_tile("Created", s.get("created", 0), tone="violet"),
                stat_tile("Updated", s["done"], tone="green"),
                stat_tile("Skipped", s["skipped"], tone="amber"),
                stat_tile("Errors", s["errors"], tone="red"),
            ], spacing=11)
        else:
            self._stats_row = ft.Row([
                stat_tile("Test Cases", s["total"]),
                stat_tile("Stories", f"{s['stories_done']}", tone="violet", sub=f"/{s['total_stories']}"),
                stat_tile("Created", s["done"], tone="green"),
                stat_tile("Skipped", s["skipped"], tone="amber"),
                stat_tile("Errors", s["errors"], tone="red"),
            ], spacing=11)

        _stopping = getattr(self, "_stopping", False)
        _finished = getattr(self, "_run_finished", False)
        _done = p["pct"] >= 100 or _finished
        _idle = _done or _finished  # no spinner when finished/stopped-and-done
        self._bar = ft.ProgressBar(value=(p["pct"]/100 if (p["pct"] > 0 or _finished) else None),
                                   color=(T.AMBER if (_stopping and not _finished) else T.VIOLET),
                                   bgcolor="#EAE8F4", bar_height=7, border_radius=4)
        if _finished:
            self._bar.value = 1.0
        spinner = (ft.Container(width=14, height=14) if (_stopping or _idle)
                   else ft.ProgressRing(width=14, height=14, stroke_width=2, color=T.VIOLET))
        _started = getattr(self, "_run_started", False)
        _label = ("Completed" if _finished
                  else ("Stopping after current test case…" if _stopping
                        else ("Completed" if _done
                              else (p["label"] if _started else "Discovering suites & test cases…"))))
        self._prow = ft.Row([
            spinner,
            ft.Text(_label, size=12, color=T.INK_2, weight=ft.FontWeight.BOLD),
            ft.Container(expand=True),
            ft.Text((f"{p['pct']}%" if (p["pct"] > 0 or _started) else "Starting…"),
                    size=12, color=T.VIOLET_INK, weight=ft.FontWeight.BOLD),
        ], spacing=8)

        # Per-story cards grid
        self._story_grid = ft.Column(self._build_story_cards(), spacing=12)

        # Recent activity log (compact)
        log_lines = self._render_log_lines()
        if not log_lines:
            log_lines = [ft.Row([
                ft.ProgressRing(width=14, height=14, stroke_width=2, color=T.VIOLET),
                ft.Text("Starting run — discovering suites & test cases…",
                        size=12.5, color=T.INK_3, weight=ft.FontWeight.BOLD),
            ], spacing=10)]
        self._log_col = ft.Column(log_lines, spacing=2,
                                  scroll=ft.ScrollMode.AUTO, expand=True, auto_scroll=True)
        log_card = card(ft.Column([
            ft.Row([ft.Text("RECENT ACTIVITY", size=11, weight=ft.FontWeight.BOLD, color=T.INK_3),
                    ft.Container(expand=True),
                    ft.Text("select to copy", size=10, color=T.INK_3,
                            weight=ft.FontWeight.W_500)]),
            ft.Container(height=8),
            ft.Container(ft.SelectionArea(content=self._log_col), height=230, bgcolor="#FCFCFE",
                         border=ft.Border.all(1, T.BORDER), border_radius=T.R, padding=12),
        ], spacing=0))

        body = ft.Column([
            self._stats_row,
            ft.Container(ft.Column([self._prow, self._bar], spacing=7),
                         padding=ft.Padding.symmetric(vertical=0, horizontal=2)),
            self._story_grid,
            log_card,
        ], spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)

        self._stop_btn_text = ft.Text(
            "Stopping…" if _stopping else "Stop after current test case",
            size=13, color="#FFFFFF", weight=ft.FontWeight.BOLD)
        stop_btn = ft.FilledButton(
            content=ft.Row([ft.Icon(ft.Icons.STOP, size=14, color="#FFFFFF"), self._stop_btn_text],
                           spacing=8, tight=True),
            height=40, on_click=lambda e: self._stop_run(),
            disabled=_stopping,
            style=ft.ButtonStyle(bgcolor=T.RED, color="#FFFFFF", elevation=0,
                shape=ft.RoundedRectangleBorder(radius=T.R),
                padding=ft.Padding.symmetric(horizontal=16, vertical=0)))
        # design red shadow (skip when disabled/stopping)
        stop = (stop_btn if _stopping
                else ft.Container(stop_btn, border_radius=T.R,
                                  shadow=_btn_shadow(T.RED, 0.55)))
        sub = f"live — story {s['stories_done']} of {s['total_stories']}" if s['total_stories'] else "live"
        return self.shell("Run", sub, body, stop)

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
        txt = ft.Text(ln.get("msg", ""), size=12,
                      color=color,
                      weight=ft.FontWeight.BOLD if tone in ("story", "ok") else ft.FontWeight.W_500,
                      font_family=(T.F_AR if ln.get("ar") else T.F_UI),
                      expand=True,
                      text_align=ft.TextAlign.LEFT)
        # Left cluster = icon + id (always on the left margin for consistency)
        left = [c for c in (icon, idtxt) if c is not None]
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
                            stat_tile("Test Cases", s["total"]),
                            stat_tile("Created", s.get("created", 0), tone="violet"),
                            stat_tile("Updated", s["done"], tone="green"),
                            stat_tile("Skipped", s["skipped"], tone="amber"),
                            stat_tile("Errors", s["errors"], tone="red"),
                        ]
                    else:
                        self._stats_row.controls = [
                            stat_tile("Test Cases", s["total"]),
                            stat_tile("Stories", f"{s['stories_done']}", tone="violet", sub=f"/{s['total_stories']}"),
                            stat_tile("Created", s["done"], tone="green"),
                            stat_tile("Skipped", s["skipped"], tone="amber"),
                            stat_tile("Errors", s["errors"], tone="red"),
                        ]
                if hasattr(self, "_bar"):
                    self._bar.value = (self._progress["pct"]/100) if self._progress["pct"] > 0 else None
                # _prow children: [spinner, label, spacer, pct]
                if hasattr(self, "_prow"):
                    try:
                        _stopping = getattr(self, "_stopping", False)
                        _done = self._progress["pct"] >= 100
                        # swap spinner → static container when stopping/done
                        if _stopping or _done:
                            self._prow.controls[0] = ft.Container(width=14, height=14)
                        self._prow.controls[1].value = (
                            "Stopping after current test case…" if _stopping
                            else ("Completed" if _done else self._progress["label"]))
                        self._prow.controls[-1].value = (f"{self._progress['pct']}%"
                                                         if self._progress["pct"] > 0 else "Starting…")
                    except Exception:
                        pass
                if hasattr(self, "_log_col"):
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
        _apply()
        # Force onto the event loop to defeat the focus-repaint bug
        try:
            ru = getattr(self.page, "run_thread", None)
            if callable(ru): ru(_apply)
        except Exception:
            pass

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
        r = self.last_report or {"summary": "No run data", "updated": 0, "skipped": 0, "errors": 0}
        is_steps = (self.tool == "steps")
        updated = r.get("updated", r.get("created", 0))
        created = r.get("created", 0)
        skipped = r.get("skipped", 0)
        errors = r.get("errors", 0)
        stories_done = r.get("stories_done", 0)
        total_stories = r.get("total_stories", 0)
        action_items = r.get("action_items", [])

        if is_steps:
            _sub = (f"Test Case Steps · {created} created · {updated} updated with steps · "
                    f"{skipped} skipped · {errors} failed across {total_stories} "
                    f"{'story' if total_stories == 1 else 'stories'}.")
        else:
            _sub = (f"Test Case Titles · {updated} created · {skipped} skipped · "
                    f"{errors} failed across {total_stories} "
                    f"{'story' if total_stories == 1 else 'stories'}.")

        head_card = ft.Container(
            ft.Row([
                ft.Container(ft.Icon(ft.Icons.CHECK, size=26, color="#FFFFFF"),
                             width=52, height=52, bgcolor=T.GREEN, border_radius=14,
                             alignment=ft.Alignment.CENTER,
                             shadow=_btn_shadow(T.GREEN, 0.45)),
                ft.Column([
                    ft.Text("Run complete", size=18, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Text(_sub, size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500),
                ], spacing=3, expand=True),
            ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=18, bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER), border_radius=T.R_LG)

        if is_steps:
            stats = ft.Row([
                stat_tile("Created", created, tone="violet"),
                stat_tile("Updated", updated, tone="green"),
                stat_tile("Skipped", skipped, tone="amber"),
                stat_tile("Failed", errors, tone="red"),
                stat_tile("Stories", stories_done, tone="violet", sub=f"/{total_stories}"),
            ], spacing=11)
        else:
            stats = ft.Row([
                stat_tile("Created", updated, tone="green"),
                stat_tile("Skipped", skipped, tone="amber"),
                stat_tile("Failed", errors, tone="red"),
                stat_tile("Stories", stories_done, tone="violet", sub=f"/{total_stories}"),
            ], spacing=11)

        # Per-story breakdown (matches design)
        per_story = r.get("per_story", [])
        story_rows = []
        for sp in per_story:
            total = sp.get("total", 0); ok = sp.get("ok", 0)
            skipped = sp.get("skipped", 0); err = sp.get("err", 0)
            processed = ok + skipped + err
            pct = int(processed / total * 100) if total else 0
            ring_c = T.AMBER if err else (T.GREEN if processed >= total and total else T.VIOLET)
            chips = []
            if ok: chips.append(badge(f"✓ {ok}", "green"))
            if skipped: chips.append(badge(f"⏭ {skipped}", "amber"))
            if err: chips.append(badge(f"✕ {err}", "red"))
            _sid = sp.get('id', '')
            _su = (f"https://dev.azure.com/{E.AZURE_ORG}/{self.project}"
                   f"/_workitems/edit/{_sid}") if _sid else None
            story_rows.append(ft.Container(
                ft.Row([
                    progress_ring(pct, ring_c, size=46, label=pct),
                    ft.Column([
                        ft.Text(sp.get("title", ""), size=13, weight=ft.FontWeight.BOLD,
                                color=T.INK, font_family=T.F_AR, text_align=ft.TextAlign.RIGHT,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(f"#{sp.get('id','')}", size=11, color=T.INK_3,
                                weight=ft.FontWeight.BOLD, font_family=T.F_MONO),
                    ], spacing=2, expand=True),
                    ft.Row(chips, spacing=5, tight=True),
                ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=12, horizontal=14),
                border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER_2)),
                tooltip=(f"{sp.get('title','')}  ·  open #{_sid}" if sp.get('title') else None),
                on_click=(lambda e, u=_su: self._open_url(u)) if _su else None,
                ink=bool(_su)))
        if not story_rows:
            story_rows = [ft.Text("No per-story data.", size=12, color=T.INK_3,
                                  weight=ft.FontWeight.W_500)]
        breakdown_card = card(ft.Column([
            ft.Row([ft.Text("Per-story breakdown", size=13, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Container(expand=True),
                    ft.Text(f"{len(per_story)} stories", size=11, color=T.INK_3,
                            weight=ft.FontWeight.BOLD)]),
            ft.Container(height=6),
            ft.Column(story_rows, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True),
        ], spacing=0), expand=True)

        # Collapsible run activity log below the breakdown
        log_lines = self._render_log_lines() if getattr(self, "_log_lines", None) else [
            ft.Text("No activity recorded.", size=12, color=T.INK_3, weight=ft.FontWeight.W_500)]
        log_card = card(ft.Column([
            ft.Row([ft.Text("Run activity log", size=13, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Container(expand=True),
                    ft.Text(f"{len(getattr(self,'_log_lines',[]))} lines", size=11,
                            color=T.INK_3, weight=ft.FontWeight.BOLD)]),
            ft.Container(height=8),
            ft.Container(
                ft.SelectionArea(content=ft.Column(log_lines, spacing=2,
                                                    scroll=ft.ScrollMode.AUTO, expand=True)),
                height=240, bgcolor="#FCFCFE", border=ft.Border.all(1, T.BORDER),
                border_radius=T.R, padding=12),
        ], spacing=0))
        left = ft.Column([head_card, stats, breakdown_card, log_card], spacing=14,
                         expand=True, scroll=ft.ScrollMode.AUTO)

        # right: needs review + buttons
        review_items = []
        for a in action_items:
            _tc_id = a.get("id")
            _tc_url = (f"https://dev.azure.com/{E.AZURE_ORG}/{self.project}"
                       f"/_workitems/edit/{_tc_id}") if _tc_id else None
            review_items.append(ft.Container(
                ft.Column([
                    ft.Row([badge("Review", "amber", ft.Icons.WARNING_AMBER_ROUNDED),
                            ft.Text(f"#{a['id']}", size=11, color=T.INK_3, weight=ft.FontWeight.BOLD,
                                    font_family=T.F_MONO),
                            ft.Container(expand=True),
                            ft.Icon(ft.Icons.OPEN_IN_NEW, size=13, color=T.INK_3)], spacing=7),
                    ft.Text(a.get("title", ""), size=12.5, weight=ft.FontWeight.BOLD, color=T.INK,
                            font_family=T.F_AR, text_align=ft.TextAlign.RIGHT),
                    ft.Text(a.get("reason", ""), size=11, color=T.INK_2, weight=ft.FontWeight.W_500),
                ], spacing=4),
                padding=ft.Padding.symmetric(vertical=12, horizontal=11), border=ft.Border.all(1, T.BORDER),
                border_radius=T.R, bgcolor=T.CARD_2, margin=ft.Margin.only(bottom=9),
                tooltip=(f"Open test case #{_tc_id} in Azure DevOps" if _tc_url else None),
                on_click=(lambda e, u=_tc_url: self._open_url(u)) if _tc_url else None,
                ink=bool(_tc_url)))
        if not review_items:
            review_items = [ft.Text("Nothing flagged — all good.", size=12, color=T.INK_3,
                                    weight=ft.FontWeight.W_500)]

        # email confirmation chip (if a report was emailed)
        email_chip = None
        emailed_to = getattr(self, "_emailed_to", None)
        if emailed_to:
            email_chip = ft.Container(
                ft.Row([ft.Icon(ft.Icons.MAIL_OUTLINED, size=15, color=T.GREEN),
                        ft.Text(f"Report emailed to {emailed_to}", size=12,
                                color=T.GREEN, weight=ft.FontWeight.BOLD, expand=True)],
                       spacing=8),
                padding=ft.Padding.symmetric(vertical=11, horizontal=13),
                bgcolor=T.GREEN_SOFT, border_radius=T.R,
                border=ft.Border.all(1, "#CFEAD9"), margin=ft.Margin.only(top=10))

        # Header (+ optional subtitle), then scrollable list that expands,
        # then the email chip pinned at the bottom of the card.
        review_header = [
            ft.Row([ft.Text("Needs your review", size=13, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Container(expand=True),
                    ft.Text(str(len(action_items)), size=12, color=T.INK_3, weight=ft.FontWeight.BOLD)]),
        ]
        if action_items:
            review_header.append(
                ft.Text("Existing steps were judged inadequate and regenerated.",
                        size=11.5, color=T.INK_2, weight=ft.FontWeight.W_500))
        review_body = ft.Column([
            *review_header,
            ft.Container(height=10),
            ft.Container(
                ft.Column(review_items, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True),
                expand=True),
            *([email_chip] if email_chip else []),
        ], spacing=0, expand=True)

        right = ft.Column([
            ft.Container(card(review_body, expand=True), expand=True),
            primary_btn("New run", icon=ft.Icons.ARROW_FORWARD, expand=True,
                        on_click=lambda e: self._new_run()),
            ghost_btn("Open plan in Azure", icon=ft.Icons.FOLDER_OUTLINED, expand=True,
                      on_click=lambda e: self._open_azure()),
        ], spacing=14, expand=True)
        body = ft.Row([ft.Container(left, expand=True),
                       ft.Container(right, width=340)], spacing=22,
                      vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
        tag = ft.Container(
            ft.Row([ft.Icon(ft.Icons.CHECK, size=13, color=T.GREEN),
                    ft.Text("Completed", size=11, color=T.GREEN, weight=ft.FontWeight.BOLD)], spacing=5, tight=True),
            padding=ft.Padding.symmetric(vertical=10, horizontal=5), bgcolor=T.GREEN_SOFT, border_radius=20,
            border=ft.Border.all(1, "#CFEAD9"))
        return self.shell("Report", self._relative_time(), body, tag)

    def _new_run(self):
        self.active = "setup"
        self.nav_state = {"setup": "active"}
        # clear the finished-report markers so the Report nav goes back to "03"
        self._run_finished = False
        self.last_report = None
        self.render()

    def _open_url(self, url):
        """Open a URL reliably. In Flet 0.90 launch_url is async, so we just use
        the OS browser directly which always works on desktop and web."""
        opened = False
        # Preferred on desktop: OS browser (synchronous, no coroutine warnings)
        try:
            import webbrowser
            opened = webbrowser.open(url)
        except Exception:
            opened = False
        if opened:
            return
        # Fallback: schedule Flet's async launcher on the event loop
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
        if self.project and self.plan_id:
            url = (f"https://dev.azure.com/{E.AZURE_ORG}/{self.project}"
                   f"/_testPlans/define?planId={self.plan_id}")
            self._open_url(url)
        else:
            self._toast("No test plan selected.")

    # ═══════════════════════════════════════════════════════════════════════════
    #  AUTOMATION SCREEN — Selenium DOM scrape → TestNG/POM project → Git push
    # ═══════════════════════════════════════════════════════════════════════════
    def _auto_field(self, label, attr, hint, password=False, req=False,
                    info=None, on_info=None):
        tf = ft.TextField(
            value=getattr(self, attr, "") or "", hint_text=hint, password=password,
            can_reveal_password=password,
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=11, horizontal=12),
            text_size=13, expand=True,
            on_change=lambda e, a=attr: setattr(self, a, e.control.value))
        return ft.Column([field_label(label, req=req, info=info, on_info=on_info),
                          ft.Container(tf, padding=ft.Padding.only(top=4))],
                         spacing=0)

    def automation_screen(self):
        if not (self.connected and self.project and self.plan_id and self.story_ids):
            return regression.locked_state(
                self, "Automation",
                "Generate self-healing Selenium tests from your stories",
                "Connect your account, then pick a project, test plan, and user "
                "stories on the Setup screen — automation runs on that same "
                "selection.")
        # ── left: config form ──
        ready = bool(self.story_ids and self.project and self.plan_id)
        setup_hint = None
        if not ready:
            setup_hint = ft.Container(
                ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=T.AMBER),
                        ft.Text("Select a project, test plan, and user stories on the Setup "
                                "screen first — automation uses the same selection.",
                                size=12, color=T.AMBER, weight=ft.FontWeight.W_500, expand=True)],
                       spacing=8),
                padding=12, bgcolor=T.AMBER_SOFT, border_radius=T.R,
                border=ft.Border.all(1, "#EAD9A8"), margin=ft.Margin.only(bottom=14))

        site_card = card(ft.Column([
            sec_head("A", "Target site"),
            ft.Container(height=10),
            self._auto_field("Site URL", "auto_site_url",
                             "https://your-app.example.com/page", req=True),
            ft.Container(height=12),
            ft.Text("LOGIN (required to reach the pages)", size=10.5,
                    weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=8),
            self._auto_field("Login page URL", "auto_login_url",
                             "https://your-app.example.com/login (defaults to site URL)"),
            ft.Container(height=10),
            ft.Row([
                ft.Container(self._auto_field("Username", "auto_login_user", "user@example.com"), expand=1),
                ft.Container(self._auto_field("Password", "auto_login_pass", "••••••••", password=True), expand=1),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ], spacing=0))

        git_card = card(ft.Column([
            sec_head("B", "Git destination (IntelliJ syncs this)"),
            ft.Container(height=10),
            self._auto_field("Repository URL", "auto_git_url",
                             "https://github.com/you/automation-tests.git", req=True),
            ft.Container(height=10),
            ft.Row([
                ft.Container(self._auto_field("Branch", "auto_git_branch", "main"), expand=1),
                ft.Container(self._auto_field("Access token (PAT)", "auto_git_token",
                                 "ghp_… or Azure PAT", password=True, req=True,
                                 info="How to create a Git access token (PAT)",
                                 on_info=lambda e: self._show_help("git_pat")), expand=1),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Container(height=4),
            ft.Text("The token is used only to push and is stored locally like your other "
                    "credentials. It is scrubbed from logs.",
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

        local_card = card(ft.Column([
            sec_head("C", "Local copy (optional)"),
            ft.Container(height=10),
            self._auto_field("Save project to folder", "auto_local_path",
                             r"e.g. C:\Users\you\IdeaProjects\automation-tests"),
            ft.Container(height=4),
            ft.Text("If set, the generated Maven project is also copied here so you can "
                    "open it directly in IntelliJ. Leave blank to use a temp folder.",
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

        gen_disabled = self._auto_running or not ready
        if self._auto_running:
            # While running, show Stop + Pause/Resume side by side (matching shadow)
            _stop_btn = ft.FilledButton(
                content=ft.Row(
                    [ft.Icon(ft.Icons.STOP_CIRCLE, size=18, color="#FFFFFF"),
                     ft.Text("Stop", size=14, weight=ft.FontWeight.BOLD,
                             color="#FFFFFF")],
                    spacing=8, tight=True,
                    alignment=ft.MainAxisAlignment.CENTER),
                height=46, expand=True, on_click=lambda e: self._stop_automation(),
                style=ft.ButtonStyle(
                    bgcolor={"": T.RED}, color={"": "#FFFFFF"}, elevation=0,
                    shape=ft.RoundedRectangleBorder(radius=T.R),
                    padding=ft.Padding.symmetric(horizontal=14, vertical=0)))
            _paused = bool(getattr(self, "_auto_paused", False))
            if _paused:
                _pr_label, _pr_icon, _pr_col = "Resume", ft.Icons.PLAY_ARROW, T.GREEN
                _pr_click = lambda e: self._resume_automation()
            else:
                _pr_label, _pr_icon, _pr_col = "Pause", ft.Icons.PAUSE_CIRCLE, T.AMBER
                _pr_click = lambda e: self._pause_automation()
            _pr_btn = ft.FilledButton(
                content=ft.Row(
                    [ft.Icon(_pr_icon, size=18, color="#FFFFFF"),
                     ft.Text(_pr_label, size=14, weight=ft.FontWeight.BOLD,
                             color="#FFFFFF")],
                    spacing=8, tight=True,
                    alignment=ft.MainAxisAlignment.CENTER),
                height=46, expand=True, on_click=_pr_click,
                style=ft.ButtonStyle(
                    bgcolor={"": _pr_col}, color={"": "#FFFFFF"}, elevation=0,
                    shape=ft.RoundedRectangleBorder(radius=T.R),
                    padding=ft.Padding.symmetric(horizontal=14, vertical=0)))
            _stop_w = ft.Container(_stop_btn, border_radius=T.R,
                                   shadow=_btn_shadow(T.RED, 0.55), expand=True)
            _pr_w = ft.Container(_pr_btn, border_radius=T.R,
                                 shadow=_btn_shadow(_pr_col, 0.55), expand=True)
            gen_btn = ft.Row([_stop_w, _pr_w], spacing=10)
        else:
            gen_btn = primary_btn(
                "Generate automation scripts",
                icon=ft.Icons.AUTO_AWESOME, expand=True, disabled=gen_disabled,
                on_click=lambda e: self._start_automation())

        push_disabled = self._auto_running or not self._auto_built
        push_btn = green_btn("Push to Git", icon=ft.Icons.CLOUD_UPLOAD_OUTLINED,
                             expand=True, on_click=lambda e: self._push_automation())
        # grey it out visually when disabled
        if push_disabled:
            push_btn = ft.Row([ft.OutlinedButton(
                "Push to Git", icon=ft.Icons.CLOUD_UPLOAD_OUTLINED, height=42,
                disabled=True, expand=True,
                style=ft.ButtonStyle(color=T.INK_3, side=ft.BorderSide(1, T.BORDER),
                    shape=ft.RoundedRectangleBorder(radius=T.R)))], spacing=0)

        left = ft.Column([
            *([setup_hint] if setup_hint else []),
            site_card,
            git_card,
            local_card,
            ft.Row([gen_btn], spacing=0),
            ft.Row([push_btn], spacing=0),
        ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)

        # ── right: live counters + clean log ──
        log_lines = [self._auto_log_line(ln.get("msg", ""), ln.get("tone", "dim"))
                     for ln in self._auto_log]
        if not log_lines:
            log_lines = [ft.Text("Configure the site + Git, then Generate. Activity shows here.",
                                 size=12, color=T.INK_3, weight=ft.FontWeight.W_500)]
        self._auto_log_col = ft.Column(log_lines, spacing=3, scroll=ft.ScrollMode.AUTO,
                                       expand=True, auto_scroll=True)

        spinner = (ft.ProgressRing(width=15, height=15, stroke_width=2, color=T.VIOLET)
                   if self._auto_running else ft.Icon(ft.Icons.TERMINAL, size=15, color=T.INK_3))
        right = ft.Column([
            card(ft.Column([
                ft.Row([spinner, ft.Text("ACTIVITY", size=11, weight=ft.FontWeight.BOLD,
                                         color=T.INK_3)], spacing=8),
                ft.Container(height=12),
                self._auto_counts_header(),
                ft.Container(height=12),
                ft.Container(ft.SelectionArea(content=self._auto_log_col), expand=True, bgcolor="#FCFCFE",
                             border=ft.Border.all(1, T.BORDER), border_radius=T.R, padding=12),
            ], spacing=0, expand=True), expand=True),
        ], spacing=14, expand=True)

        body = ft.Row([ft.Container(left, expand=True),
                       ft.Container(right, width=384)], spacing=22,
                      vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
        sub = (f"{len(self.story_ids)} stories selected" if self.story_ids else "no stories selected")
        return self.shell("Automation", sub, body)

    # ---- activity panel: live counters + clean, RTL-aware log lines ----
    def _auto_count(self):
        """Derive Live / Snapshot / Guess / TODO tallies from the activity log,
        keyed off the exact outcome markers the explorer emits."""
        live = snap = guess = 0
        for ln in self._auto_log:
            s = (ln.get("msg", "") or "").strip()
            if s.startswith("SNAPSHOT:"):
                snap += 1
            elif s.startswith("GUESS:"):
                guess += 1
            elif s.startswith(("typed into", "clicked", "selected on")) or \
                 (s.startswith("matched ") and "left the page" in s):
                live += 1
        return {"live": live, "snapshot": snap, "guess": guess, "todo": snap + guess}

    def _auto_counts_header(self):
        c = self._auto_count()
        self._auto_count_ctl = {}
        def chip(key, label, color, soft):
            val = ft.Text(str(c[key]), size=14, weight=ft.FontWeight.BOLD, color=color)
            self._auto_count_ctl[key] = val
            return ft.Container(
                ft.Row([ft.Container(width=7, height=7, border_radius=4, bgcolor=color),
                        ft.Text(label, size=10, weight=ft.FontWeight.W_600, color=T.INK_2),
                        val], spacing=5, alignment=ft.MainAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=8, horizontal=6),
                bgcolor=soft, border_radius=T.R, expand=True,
                alignment=ft.Alignment.CENTER)
        return ft.Row([
            chip("live", "Live", T.GREEN, T.GREEN_SOFT),
            chip("snapshot", "Snap", T.AMBER, T.AMBER_SOFT),
            chip("guess", "Guess", T.RED, T.RED_SOFT),
            chip("todo", "TODO", T.VIOLET, T.VIOLET_SOFT),
        ], spacing=7)

    def _auto_log_line(self, msg, tone):
        cmap = {"ok": T.GREEN, "err": T.RED, "warn": T.AMBER, "story": T.VIOLET_INK,
                "dim": T.INK_3, "info": T.INK_2}
        color = cmap.get(tone, T.INK_2)
        stripped = (msg or "").lstrip(" ")
        indent = len(msg or "") - len(stripped)
        pad = min(indent, 8) * 3
        is_ar = any("\u0600" <= ch <= "\u06ff" for ch in stripped)
        weight = ft.FontWeight.BOLD if tone == "story" else ft.FontWeight.W_500
        dot = ft.Container(width=6, height=6, border_radius=3, bgcolor=color,
                           margin=ft.Margin.only(top=5))
        txt = ft.Text(stripped, size=12, color=color, weight=weight,
                      font_family=(T.F_AR if is_ar else (T.F_MONO if tone in ("dim", "info") else None)),
                      text_align=(ft.TextAlign.RIGHT if is_ar else ft.TextAlign.LEFT),
                      expand=True)
        return ft.Container(
            ft.Row([dot, ft.Container(width=8), txt], spacing=0,
                   vertical_alignment=ft.CrossAxisAlignment.START),
            padding=ft.Padding.only(left=pad, top=1, bottom=1))

    def _auto_logmsg(self, msg, tone="dim"):
        # drop consecutive duplicate lines (e.g. repeated "Paused…" notices)
        if self._auto_log and self._auto_log[-1].get("msg") == msg:
            return
        self._auto_log.append({"msg": msg, "tone": tone})
        def upd():
            try:
                col = getattr(self, "_auto_log_col", None)
                if col is not None:
                    real = len(self._auto_log)
                    have = len(col.controls)
                    # render() rebuilds the column from self._auto_log, and this
                    # incremental append can race it at run-end — appending a line
                    # render already added (the duplicate "Stopped." etc.). Only
                    # touch the column when it's actually behind self._auto_log.
                    if real == 1 and have <= 1:
                        # replace the empty-state placeholder with the first line
                        col.controls = [self._auto_log_line(msg, tone)]
                        col.update()
                    elif have < real:
                        col.controls.append(self._auto_log_line(msg, tone))
                        col.update()
                    # have >= real → render already has this line; skip (no dup)
                ctl = getattr(self, "_auto_count_ctl", None)
                if ctl:
                    c = self._auto_count()
                    for k, t in ctl.items():
                        try:
                            t.value = str(c.get(k, 0)); t.update()
                        except Exception:
                            pass
            except Exception:
                pass
        self.ui_safe(upd)

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
        self._auto_logmsg("Resuming…", "info")
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
            title=ft.Row([ft.Icon(ft.Icons.HISTORY, size=20, color=T.VIOLET),
                          ft.Text("Existing tests found", size=15,
                                  weight=ft.FontWeight.BOLD, color=T.INK)], spacing=9),
            content=ft.Container(width=430, content=ft.Text(
                msg, size=12.5, color=T.INK_2, weight=ft.FontWeight.W_500)),
            actions=[
                ghost_btn("Cancel", on_click=lambda e: (on_choice("cancel"), self._close_dialog())),
                green_btn("Keep & add new", on_click=lambda e: (on_choice("keep"), self._close_dialog())),
                danger_btn("Re-evaluate with AI", on_click=lambda e: (on_choice("reeval"), self._close_dialog())),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=T.R_LG))
        self._show_dialog(dlg)

    def _start_automation(self):
        if not (self.story_ids and self.project and self.plan_id):
            self._toast("Select stories on Setup first.")
            return
        if not self.auto_site_url.strip():
            self._toast("Enter the site URL.")
            return
        if not self._auto_project_dir():
            self._toast("Set 'Save project to folder' (Local copy) - it's now the project home.")
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
        self.render()

        def cb(msg, tone="dim"):
            self._auto_logmsg(msg, tone)

        def work():
            try:
                # 1) connect to Azure + fetch stories with their test cases/steps
                cb("Connecting to Azure DevOps...", "dim")
                E.connect_azure_sdk(self.project)
                smap = E.discover_suites_for_stories(self.project, self.plan_id,
                                                     set(self.story_ids), create_missing=False)
                stories = E.fetch_stories(self.story_ids)

                stories_payload = []
                total_tc = 0
                total_steps = 0
                for s in stories:
                    if self._auto_stop:
                        cb("Stopped before scraping.", "warn"); return
                    sid = s.id
                    title = s.fields.get("System.Title", "")
                    criteria = s.fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")
                    suite_id = smap.get(sid)
                    tcs = []
                    if suite_id:
                        try:
                            for tc in E.fetch_test_cases_for_suite(self.project, self.plan_id, suite_id):
                                wi = tc.get("workItem", {})
                                tc_id = wi.get("id")
                                tc_title = wi.get("name", "")
                                steps = E.fetch_test_case_steps(tc_id) if tc_id else []
                                total_steps += len(steps)
                                tcs.append({"id": tc_id, "title": tc_title, "steps": steps})
                        except Exception:
                            pass
                    total_tc += len(tcs)
                    stories_payload.append({
                        "story": {"id": sid, "title": title, "criteria": criteria},
                        "test_cases": tcs,
                    })
                cb(f"Loaded {len(stories_payload)} story/stories - {total_tc} test case(s) - "
                   f"{total_steps} step(s) from Azure.", "ok")

                if self._auto_stop:
                    cb("Stopped before scraping.", "warn"); return

                # 2) decide what needs (re)generating vs what we keep
                project_dir = self._auto_project_dir()
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

                # only stories we will (re)generate need a live walk; for a 'grew'
                # story walk just its NEW test cases. Kept stories are skipped.
                walk_payload = []
                for sp in stories_payload:
                    sid = str(sp["story"]["id"])
                    if sid in new_ids or sid in reeval:
                        walk_payload.append(sp)
                    elif sid in grew_ids:
                        walk_payload.append({"story": sp["story"],
                                             "test_cases": new_tcs.get(sid, [])})

                # 2) Generate a SELF-HEALING project from the stories — no browser.
                #    Locators are seeded (stable where known, // TODO otherwise) and
                #    resolved at RUNTIME by the generated framework via the Anthropic
                #    API when a seed fails. Cases are validated + ordered into a
                #    logical sequence (logged-out negatives/validation/login-page →
                #    successful login → app cases) so we never log out to re-test.
                login = None
                if self.auto_login_user.strip() and self.auto_login_pass:
                    login = {"url": self.auto_login_url.strip() or self.auto_site_url.strip(),
                             "user": self.auto_login_user.strip(),
                             "password": self.auto_login_pass}
                cb("Generating self-healing automation (no browser)…", "info")
                E.generate_and_push_selfhealing(
                    project_dir, stories_payload, self.auto_site_url.strip(),
                    login=login, cb=cb, should_stop=lambda: self._auto_stop,
                    on_error=self._auto_on_ai_error, gate=self._auto_gate)

                self._auto_out_dir = project_dir
                self.creds["auto_local_path"] = (self.auto_local_path or "").strip()
                try:
                    store.save(self.creds)
                except Exception:
                    pass
                if self._auto_stop:
                    cb("Stopped.", "warn"); return
                self._auto_built = True
                cb("Done — review the activity, then Push to Git. Before `mvn test` in "
                   "IntelliJ, set ANTHROPIC_API_KEY, APP_USER and APP_PASS so the "
                   "generated tests can self-heal locators at runtime.", "ok")
            except Exception as ex:
                cb(f"Automation failed: {str(ex)[:200]}", "err")
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

    def _push_automation(self):
        import os as _os
        proj = self._auto_project_dir()
        if not proj or not _os.path.isdir(proj):
            self._toast("Generate scripts to the local folder first.")
            return
        if not self.auto_git_url.strip() or not self.auto_git_token.strip():
            self._toast("Enter the Git repo URL and access token.")
            return
        _ok_url, _url_msg = E._validate_remote_url(self.auto_git_url.strip())
        if not _ok_url:
            self._toast(_url_msg)
            self._auto_logmsg(_url_msg, "err")
            return
        self._save_git_creds()
        self._auto_running = True
        self.render()

        def cb(msg, tone="dim"):
            self._auto_logmsg(msg, tone)

        def work():
            try:
                cb(f"Pushing from {proj}", "dim")
                ok, msg = E.push_to_git(proj, self.auto_git_url.strip(),
                                        self.auto_git_token.strip(),
                                        branch=(self.auto_git_branch.strip() or "main"),
                                        cb=cb)
                if ok:
                    cb("Pushed. Open/refresh the repo in IntelliJ to sync.", "ok")
                else:
                    cb(f"Push failed - {msg}", "err")
            except Exception as ex:
                cb(f"Push error: {str(ex)[:200]}", "err")
            finally:
                self._auto_running = False
                self.ui_safe(self.render)

        self._bg(work)



# ═══════════════════════════════════════════════════════════════════════════════
def main(page: ft.Page):
    page.fonts = {
        T.F_UI: "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800",
        T.F_MONO: "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700",
        T.F_AR: "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700",
    }
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
        # Default: native DESKTOP window. Requires the flet desktop client binary.
        # If it is missing/blocked, install it from PyPI:  pip install flet-desktop
        try:
            _launch()
        except Exception as _e:
            print("\n" + "="*64)
            print("Desktop client could not start:")
            print(f"  {_e}")
            print("\nFix (run once, on a network that allows PyPI):")
            print("  pip install flet-desktop")
            print("\nThen run again:  python main.py")
            print("\nOr run in the browser instead:")
            print("  set WEB_MODE=1   &&   python main.py")
            print("="*64)