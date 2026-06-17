"""main.py — QA Studio (Flet desktop app).
Run:  pip install flet pillow anthropic openai azure-devops requests
      flet run main.py        (or)   python main.py
"""
import threading, traceback
import flet as ft

import theme as T
import store
import engine as E

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
    return ft.Row(parts, spacing=4, tight=True,
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
    label_row = [ft.Text(label, size=11, color=T.INK_2, weight=ft.FontWeight.BOLD,
                         expand=True)]
    if tone:  # colored status dot (matches design)
        label_row.append(ft.Container(width=8, height=8, bgcolor=numc, border_radius=5))
    children = [
        ft.Row(label_row, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Row([
            ft.Text(str(num), size=22, weight=ft.FontWeight.BOLD, color=numc),
            ft.Text(sub or "", size=12, color=T.INK_3, weight=ft.FontWeight.BOLD),
        ], spacing=2, vertical_alignment=ft.CrossAxisAlignment.END),
    ]
    return ft.Container(ft.Column(children, spacing=3), padding=ft.Padding.symmetric(vertical=14, horizontal=14),
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
        self.emails = ""
        self.existing_mode = "evaluate"

        # run state
        self.stop_flag = False
        self.last_report = None

        # cached azure lookups
        self._projects = []
        self._plans = []
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
        self.auto_site_url = ""
        self.auto_login_url = ""
        self.auto_login_user = ""
        self.auto_login_pass = ""
        self.auto_git_url = self.creds.get("git_url", "")
        self.auto_git_branch = self.creds.get("git_branch", "") or "main"
        self.auto_git_token = self.creds.get("git_token", "")
        self.auto_headless = True
        self._auto_log = []
        self._auto_running = False
        self._auto_out_dir = None
        self._auto_built = False
        self._run_active = False

        # update-check state
        self._update_info = None     # set by background check_for_update
        self._updating = False
        self._update_dismissed = False

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
                         or (n["id"] == "run" and (getattr(self, "_run_active", False)
                                                   or st == "active"
                                                   or self.last_report is not None)))
            # active indicator bar on the far left
            indicator = ft.Container(width=3, height=22,
                                     bgcolor=(T.VIOLET if is_active else ft.Colors.TRANSPARENT),
                                     border_radius=4)
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
                    bgcolor=bg, border_radius=9,
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
                        ft.Container(ft.Icon(ft.Icons.SCIENCE_OUTLINED, color="#FFFFFF", size=20),
                                     width=38, height=38, bgcolor=T.VIOLET, border_radius=11,
                                     alignment=ft.Alignment.CENTER),
                        ft.Column([
                            ft.Text("QA Studio", size=15, weight=ft.FontWeight.BOLD, color=T.RAIL_INK),
                            ft.Text("Azure DevOps · AI", size=11, color=T.RAIL_DIM, weight=ft.FontWeight.BOLD),
                        ], spacing=1),
                    ], spacing=11), padding=ft.Padding.symmetric(vertical=16, horizontal=6)),
                ft.Container(ft.Text("PIPELINE", size=10, weight=ft.FontWeight.BOLD,
                                     color="#615E6E"), padding=ft.Padding.only(left=18, top=14, bottom=6)),
                ft.Container(ft.Column(nav_items, spacing=3), padding=ft.Padding.symmetric(vertical=10, horizontal=0)),
                ft.Container(expand=True),
                ft.Container(
                    ft.Row([
                        ft.Container(
                            ft.Icon(ft.Icons.AUTO_AWESOME, size=15, color="#FFFFFF") if self.connected
                            else ft.Container(width=10, height=10, bgcolor=conn_color, border_radius=5),
                            width=30, height=30,
                            bgcolor=(T.VIOLET if self.connected else T.RAIL_2),
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

    def topbar(self, title, sub=None, right=None):
        row = [ft.Text(title, size=15, weight=ft.FontWeight.BOLD, color=T.INK)]
        if sub:
            row.append(ft.Text(f"·  {sub}", size=12, color=T.INK_2, weight=ft.FontWeight.BOLD))
        row.append(ft.Container(expand=True))
        if right:
            row.append(right)
        return ft.Container(ft.Row(row, spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            padding=ft.Padding.symmetric(vertical=0, horizontal=24), height=58,
                            alignment=ft.Alignment.CENTER_LEFT,
                            border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER)),
                            bgcolor=ft.Colors.with_opacity(0.7, "#FFFFFF"))

    def shell(self, title, sub, body, right=None):
        return ft.Row([
            self.rail(),
            ft.Container(
                ft.Column([
                    self.topbar(title, sub, right),
                    ft.Container(body, padding=22, expand=True),
                ], spacing=0, expand=True),
                expand=True, bgcolor=T.BG),
        ], spacing=0, expand=True)

    # ---- navigation ----
    def goto(self, screen):
        self.active = screen
        self.render()

    def render(self):
        try:
            if self.active == "setup":
                view = self.setup_screen()
            elif self.active == "run":
                view = self.run_screen()
            elif self.active == "automation":
                view = self.automation_screen()
            else:
                view = self.report_screen()
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
            if self.active == "setup":
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
        # Window icon (taskbar + title bar) — points to the bundled app.ico
        try:
            import os as _os
            _icon = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "app.ico")
            if _os.path.exists(_icon):
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
                    # Only intercept while a run is in progress; otherwise leave
                    # the default close behavior intact.
                    self.page.window.prevent_close = False
                    self.page.window.on_event = self._on_window_event
            except Exception:
                pass
        self.render()
        # Check for a newer version in the background (never blocks startup)
        self._kickoff_update_check()

    def _set_run_active(self, active):
        """Track whether a run is in progress, and toggle the OS window's
        prevent_close so the X triggers a confirm dialog only during a run."""
        self._run_active = bool(active)
        try:
            if hasattr(self.page, "window") and self.page.window is not None:
                self.page.window.prevent_close = bool(active)
                self.page.update()
        except Exception:
            pass

    def _on_window_event(self, e):
        """Best-effort confirm-on-close while a run is active. If a run is NOT
        active, we never block the close, so the X button always works."""
        try:
            etype = getattr(e, "data", None) or getattr(e, "type", None)
        except Exception:
            etype = None
        if etype != "close":
            return
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
        def work():
            info = E.check_for_update()
            self._update_info = info
            if info.get("update"):
                self.ui_safe(self.render)
        try:
            self._bg(work)
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
            inner = ft.Row([
                ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, size=18, color="#FFFFFF"),
                ft.Text(f"A new version of QA Studio is available "
                        f"(v{remote} · you have v{local}).",
                        size=12.5, color="#FFFFFF", weight=ft.FontWeight.BOLD, expand=True),
                ft.FilledButton("Update now", icon=ft.Icons.DOWNLOAD,
                                on_click=lambda e: self._do_update(),
                                style=ft.ButtonStyle(
                                    bgcolor={"": "#FFFFFF"}, color={"": T.VIOLET_INK},
                                    shape=ft.RoundedRectangleBorder(radius=T.R_SM),
                                    padding=ft.Padding.symmetric(horizontal=14, vertical=6))),
                ft.IconButton(ft.Icons.CLOSE, icon_size=16, icon_color="#FFFFFF",
                              tooltip="Dismiss",
                              on_click=lambda e: self._dismiss_update()),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        return ft.Container(inner, bgcolor=T.VIOLET,
                            padding=ft.Padding.symmetric(horizontal=18, vertical=10))

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
                    try:
                        self.page.snack_bar = ft.SnackBar(ft.Text(msg), bgcolor=T.RED, duration=8000)
                        self.page.snack_bar.open = True
                        self.page.update()
                    except Exception:
                        pass
            self.ui_safe(finish)
        self._bg(work)

    def _show_restart_dialog(self, msg):
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE, color=T.GREEN, size=20),
                          ft.Text("Update complete", weight=ft.FontWeight.BOLD, size=16)],
                         spacing=8, tight=True),
            content=ft.Container(
                ft.Text(msg, size=13, color=T.INK_2, weight=ft.FontWeight.W_500),
                width=420),
            actions=[
                green_btn("Restart now", on_click=lambda e: self._restart_app()),
                ghost_btn("Later", on_click=lambda e: self._close_dialog()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dialog(dlg)

    def _restart_app(self):
        """Relaunch the app process, then fully exit the current one
        (including the flet client window) so no orphan taskbar entry remains."""
        self._close_dialog()
        try:
            import sys, os, subprocess
            main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
            pyw = sys.executable
            try:
                cand = os.path.join(os.path.dirname(pyw), "pythonw.exe")
                if os.path.exists(cand):
                    pyw = cand
            except Exception:
                pass
            flags = 0x08000000 if os.name == "nt" else 0
            subprocess.Popen([pyw, main_py], cwd=os.path.dirname(main_py),
                             creationflags=flags)
        except Exception:
            pass
        # Close THIS instance completely (window + python), reusing the robust
        # close path so the old flet.exe window can't linger in the taskbar.
        self._run_active = False
        self._auto_running = False
        self._force_close()

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
            [connection_card, tool_card, task_card, self.err_text],
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
    }

    def _show_help(self, key):
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
            field_label("Azure Organization", req=True,
                        info="How to find your Azure organization name",
                        on_info=lambda e: self._show_help("org")),
            ft.Container(ft.Row([self.org_field, self.org_btn], spacing=8),
                        padding=ft.Padding.only(top=4, bottom=12)),
            field_label("API Key", req=True, info="How to get your AI provider API key",
                        on_info=lambda e: self._show_help("api_key")),
            ft.Container(ft.Row([self.api_key_field, self.api_btn], spacing=8),
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
        return ft.Column([
            self._cred_saved_row(ft.Icons.AUTO_AWESOME, "AI Provider", T.disp_name(name),
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
        self._provider_choice = self.prov_dd.value
        name = self._provider_choice
        active = self._provider_active(name)
        self.api_key_field.value = self._saved_key(name)
        self.api_key_field.read_only = active
        self.api_key_field.bgcolor = T.CARD_2 if active else T.CARD
        self.api_key_field.hint_text = f"Paste key for {T.disp_name(name)}"
        # rebuild the button
        self.render()

    def _save_key(self, e=None):
        name = self._provider_choice
        val = (self.api_key_field.value or "").strip()
        if not val:
            self._err("API Key is required."); return
        self.creds["keys"][name] = val; store.save(self.creds)
        self._key_unlocked = False
        self._toast("API key saved."); self.render()

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

    def _err(self, msg):
        self._err_msg = msg
        try:
            self.err_text.value = msg
            self.page.update()
        except Exception:
            pass
        # Also show as a snackbar so it is visible even if the inline label is gone
        if msg:
            try:
                self.page.snack_bar = ft.SnackBar(ft.Text(msg), bgcolor=T.RED, duration=6000)
                self.page.snack_bar.open = True
                self.page.update()
            except Exception:
                pass

    def _toast(self, msg):
        self.page.snack_bar = ft.SnackBar(ft.Text(msg), bgcolor=T.GREEN)
        self.page.snack_bar.open = True
        self.page.update()

    # ---- task card (connected) ----
    def _task_card(self):
        self.project_dd = ft.Dropdown(
            value=self.project, hint_text="Select project",
            options=[ft.DropdownOption(p) for p in self._projects],
            on_select=self._on_project_change,
            tooltip=(self.project or None),
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=8), text_size=13, filled=True,
            bgcolor=T.CARD, expand=True)

        _plan_tip = next((f"[{p['id']}] {p['name']}" for p in self._plans if p["id"] == self.plan_id), None)
        self.plan_dd = ft.Dropdown(
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

        def _commit_stories(e=None):
            raw = (self.story_field.value or "").strip().strip("()[]")
            ids = []
            for x in raw.replace(" ", ",").split(","):
                x = x.strip()
                if x.isdigit() and int(x) not in ids:
                    ids.append(int(x))
            self.story_ids = ids
            if ids:
                self._err_msg = ""  # clear any "add a story" error
            self.story_field.value = ", ".join(str(s) for s in ids)
            _build_chips()
            self._estimated_tc = None
            try:
                self.story_field.update(); self._chip_row.update(); self._chip_wrap.update()
            except Exception:
                pass
            _update_summary_inplace()
            self._fetch_estimate()

        self.story_field = ft.TextField(
            value=", ".join(str(s) for s in self.story_ids),
            hint_text="e.g. 166730, 166731, 166732  (comma-separated)",
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
            text_size=13, expand=True, on_submit=_commit_stories, on_blur=_commit_stories)
        _build_chips()
        story_box = ft.Column([self.story_field, self._chip_wrap], spacing=0, tight=True)

        self.email_field = ft.TextField(
            value=self.emails, hint_text="qa-leads@wss.com  (optional)",
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=12), text_size=13, expand=True,
            on_change=lambda e: setattr(self, "emails", self.email_field.value))

        # Sprint summary button — green (like Create) when a plan is selected,
        # grey/disabled when no plan is chosen yet.
        _sum_enabled = bool(self.plan_id)
        self._summary_btn = ft.FilledButton(
            "Selected Sprint Summary report",
            icon=ft.Icons.SUMMARIZE_OUTLINED, height=46,
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
        _summary_row = ft.Row([self._summary_btn], spacing=0)

        rows = [
            sec_head("3", "Task",
                     ft.Row([ft.Icon(ft.Icons.ARROW_FORWARD, size=13, color=T.INK_3),
                             ft.Text("from your connection", size=11, color=T.INK_3, weight=ft.FontWeight.BOLD)],
                            spacing=4, tight=True)),
            ft.Container(height=12),
            # Row 1 — Project (full width)
            field_label("Project", req=True),
            ft.Container(self.project_dd, padding=ft.Padding.only(top=4, bottom=12)),
            # Row 2 — Test Plan (40%) · Test Plan ID (30%) · Create (30%)
            ft.Row([
                ft.Column([field_label("Test Plan", req=True),
                           ft.Container(self.plan_dd, padding=ft.Padding.only(top=4))],
                          expand=4, spacing=0),
                ft.Column([field_label("Test Plan ID", hint="auto"),
                           ft.Container(self.plan_id_field, padding=ft.Padding.only(top=4))],
                          expand=3, spacing=0),
                ft.Column([ft.Container(height=18),
                           ft.Container(
                               green_btn("Create", icon=ft.Icons.ADD, expand=True,
                                         on_click=lambda e: self._open_create_plan()),
                               padding=ft.Padding.only(top=4))],
                          expand=3, spacing=0),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Container(height=12),
            # Row 3 — Sprint summary (full width)
            ft.Container(_summary_row, padding=ft.Padding.only(bottom=14)),
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
            if p["id"] == self.plan_id: self.plan_name = p["name"]
        self.render()

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
                          org=org, gmail_sender=sender)
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
                    if kmsg == "auth":
                        self._err(f"AI provider key rejected. Check your {E.AI_PROVIDER.upper()} "
                                  f"API key is correct and active.")
                    elif kmsg == "network":
                        self._err("Cannot reach the AI provider — check your network/firewall.")
                    else:
                        self._err(f"AI provider key check failed: {kmsg}")
                    return
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
        # scroll_to is async in Flet 0.85 and warns when called sync; we rely on
        # in-place updates to preserve scroll instead, so this is now a no-op.
        return

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

        body_col = ft.Column(
            [ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2, color=T.VIOLET),
                     ft.Text("Loading sprint summary…", size=13, color=T.INK_2,
                             weight=ft.FontWeight.BOLD)], spacing=10)],
            spacing=12, tight=True, scroll=ft.ScrollMode.AUTO)

        # email recipients field (asked each time) + status text
        self._sum_data = None
        email_field = ft.TextField(
            hint_text="recipient@example.com, another@example.com",
            value=(self.emails or ""),
            bgcolor=T.CARD, filled=True,
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=10, horizontal=12),
            text_size=12.5, dense=True, expand=True)
        email_status = ft.Text("", size=11.5, weight=ft.FontWeight.BOLD)

        def do_email(e=None):
            if not self._sum_data:
                return
            if not E.GMAIL_APP_PASS:
                email_status.value = "Set a Gmail App Password in Setup → Connection first."
                email_status.color = T.AMBER
                try: email_status.update()
                except Exception: self.render()
                return
            to = [x.strip() for x in (email_field.value or "").split(",") if x.strip()]
            if not to:
                email_status.value = "Enter at least one recipient."
                email_status.color = T.RED
                try: email_status.update()
                except Exception: self.render()
                return
            email_status.value = "Sending…"; email_status.color = T.INK_2
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
            ft.Container(email_status, padding=ft.Padding.only(top=4)),
        ], spacing=0, tight=True)
        email_bar.visible = False  # shown only after data loads

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([ft.Icon(ft.Icons.SUMMARIZE_OUTLINED, color=T.VIOLET_INK, size=20),
                          ft.Text("Sprint Summary", weight=ft.FontWeight.BOLD, size=16)],
                         spacing=8, tight=True),
            content=ft.Container(
                ft.Column([ft.Container(body_col, expand=True), email_bar],
                          spacing=6, tight=False),
                width=560, height=540),
            actions=[close_btn],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dialog(dlg)

        def load():
            try:
                data = E.sprint_summary(self.project, self.plan_id)
            except Exception as ex:
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
                total = data["total_stories"]
                by_state = data["by_state"]
                total_tc = data["total_test_cases"]

                # Header line
                header = ft.Column([
                    ft.Text(data["plan_name"], size=15, weight=ft.FontWeight.BOLD, color=T.INK),
                    ft.Text(data["iteration"] or "—", size=11, color=T.INK_3,
                            weight=ft.FontWeight.BOLD, font_family=T.F_MONO),
                ], spacing=2)

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
                    ft.Container(height=6),
                    ft.Text("STATUS BREAKDOWN", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
                    status_row,
                    ft.Container(height=6),
                    ft.Text("STORIES", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
                    ft.Container(ft.Column(story_rows, spacing=0, scroll=ft.ScrollMode.AUTO),
                                 bgcolor="#FCFCFE", border=ft.Border.all(1, T.BORDER),
                                 border_radius=T.R, padding=ft.Padding.symmetric(vertical=2, horizontal=4),
                                 height=200),
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
                html = E.build_report_email(tool_name, rpt.get("summary",""), stats,
                                            rpt.get("action_items",[]),
                                            rpt.get("skipped_items",[]),
                                            per_story=rpt.get("per_story", []),
                                            plan_url=plan_url,
                                            total_secs=_secs)
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
    def _auto_field(self, label, attr, hint, password=False, req=False):
        tf = ft.TextField(
            value=getattr(self, attr, "") or "", hint_text=hint, password=password,
            can_reveal_password=password,
            border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=11, horizontal=12),
            text_size=13, expand=True,
            on_change=lambda e, a=attr: setattr(self, a, e.control.value))
        return ft.Column([field_label(label, req=req), ft.Container(tf, padding=ft.Padding.only(top=4))],
                         spacing=0)

    def automation_screen(self):
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
                self._auto_field("Username", "auto_login_user", "user@example.com"),
                self._auto_field("Password", "auto_login_pass", "••••••••", password=True),
            ], spacing=12),
        ], spacing=0))

        git_card = card(ft.Column([
            sec_head("B", "Git destination (IntelliJ syncs this)"),
            ft.Container(height=10),
            self._auto_field("Repository URL", "auto_git_url",
                             "https://github.com/you/automation-tests.git", req=True),
            ft.Container(height=10),
            ft.Row([
                self._auto_field("Branch", "auto_git_branch", "main"),
                self._auto_field("Access token (PAT)", "auto_git_token",
                                 "ghp_… or Azure PAT", password=True, req=True),
            ], spacing=12),
            ft.Container(height=4),
            ft.Text("The token is used only to push and is stored locally like your other "
                    "credentials. It is scrubbed from logs.",
                    size=11, color=T.INK_3, weight=ft.FontWeight.W_500),
        ], spacing=0))

        gen_disabled = self._auto_running or not ready
        gen_btn = primary_btn(
            "Generate automation scripts" if not self._auto_running else "Working…",
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
            ft.Row([gen_btn], spacing=0),
            ft.Row([push_btn], spacing=0),
        ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)

        # ── right: live log + info ──
        log_lines = []
        for ln in self._auto_log:
            tone = ln.get("tone", "dim")
            cmap = {"ok": T.GREEN, "err": T.RED, "warn": T.AMBER, "story": T.VIOLET_INK,
                    "dim": T.INK_3, "info": T.INK_2}
            log_lines.append(ft.Text(ln.get("msg", ""), size=12,
                                     color=cmap.get(tone, T.INK_2),
                                     weight=ft.FontWeight.W_500,
                                     font_family=(T.F_MONO if tone in ("dim", "info") else None)))
        if not log_lines:
            log_lines = [ft.Text("Configure the site + Git, then Generate. Activity shows here.",
                                 size=12, color=T.INK_3, weight=ft.FontWeight.W_500)]
        self._auto_log_col = ft.Column(log_lines, spacing=4, scroll=ft.ScrollMode.AUTO,
                                       expand=True, auto_scroll=True)

        spinner = (ft.ProgressRing(width=15, height=15, stroke_width=2, color=T.VIOLET)
                   if self._auto_running else ft.Icon(ft.Icons.TERMINAL, size=15, color=T.INK_3))
        right = ft.Column([
            card(ft.Column([
                ft.Row([spinner, ft.Text("ACTIVITY", size=11, weight=ft.FontWeight.BOLD,
                                         color=T.INK_3)], spacing=8),
                ft.Container(height=10),
                ft.Container(self._auto_log_col, expand=True, bgcolor="#FCFCFE",
                             border=ft.Border.all(1, T.BORDER), border_radius=T.R, padding=12),
            ], spacing=0, expand=True), expand=True),
        ], spacing=14, expand=True)

        body = ft.Row([ft.Container(left, expand=True),
                       ft.Container(right, width=360)], spacing=22,
                      vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
        sub = (f"{len(self.story_ids)} stories selected" if self.story_ids else "no stories selected")
        return self.shell("Automation", sub, body)

    def _auto_logmsg(self, msg, tone="dim"):
        self._auto_log.append({"msg": msg, "tone": tone})
        def upd():
            try:
                if hasattr(self, "_auto_log_col"):
                    cmap = {"ok": T.GREEN, "err": T.RED, "warn": T.AMBER, "story": T.VIOLET_INK,
                            "dim": T.INK_3, "info": T.INK_2}
                    self._auto_log_col.controls.append(
                        ft.Text(msg, size=12, color=cmap.get(tone, T.INK_2),
                                weight=ft.FontWeight.W_500,
                                font_family=(T.F_MONO if tone in ("dim", "info") else None)))
                    self._auto_log_col.update()
            except Exception:
                pass
        self.ui_safe(upd)

    def _save_git_creds(self):
        try:
            self.creds["git_url"] = self.auto_git_url
            self.creds["git_branch"] = self.auto_git_branch
            self.creds["git_token"] = self.auto_git_token
            store.save(self.creds)
        except Exception:
            pass

    def _start_automation(self):
        if not (self.story_ids and self.project and self.plan_id):
            self._toast("Select stories on Setup first.")
            return
        if not self.auto_site_url.strip():
            self._toast("Enter the site URL.")
            return
        self._save_git_creds()
        self._auto_running = True
        self._auto_built = False
        self._auto_log = []
        self._set_run_active(True)
        self.render()

        def cb(msg, tone="dim"):
            self._auto_logmsg(msg, tone)

        def work():
            import tempfile, os as _os
            try:
                # 1) connect to Azure + fetch stories with their test cases/steps
                cb("Connecting to Azure DevOps…", "dim")
                E.connect_azure_sdk(self.project)
                smap = E.discover_suites_for_stories(self.project, self.plan_id,
                                                     set(self.story_ids), create_missing=False)
                stories = E.fetch_stories(self.story_ids)

                stories_payload = []
                total_tc = 0
                total_steps = 0
                for s in stories:
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
                                # pull the actual steps written by the Steps script
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
                cb(f"Loaded {len(stories_payload)} story/stories · {total_tc} test case(s) · "
                   f"{total_steps} step(s) from Azure.", "ok")

                # 2) scrape the live DOM
                login = None
                if self.auto_login_user.strip() and self.auto_login_pass:
                    login = {"url": self.auto_login_url.strip() or self.auto_site_url.strip(),
                             "user": self.auto_login_user.strip(),
                             "password": self.auto_login_pass}
                dom = E.scrape_dom(self.auto_site_url.strip(), login=login, cb=cb,
                                   headless=self.auto_headless)

                # 3) build the Maven project
                out_dir = tempfile.mkdtemp(prefix="qastudio_automation_")
                self._auto_out_dir = out_dir
                cb(f"Generating project at {out_dir}", "dim")
                E.build_automation_project(out_dir, stories_payload, dom,
                                           self.auto_site_url.strip(),
                                           cb=cb, should_stop=lambda: False)
                self._auto_built = True
                cb("Done. Review the activity, then Push to Git.", "ok")
            except Exception as ex:
                cb(f"Automation failed: {str(ex)[:200]}", "err")
            finally:
                self._auto_running = False
                self._set_run_active(False)
                self.ui_safe(self.render)

        self._bg(work)

    def _push_automation(self):
        if not (self._auto_built and self._auto_out_dir):
            self._toast("Generate scripts first.")
            return
        if not self.auto_git_url.strip() or not self.auto_git_token.strip():
            self._toast("Enter the Git repo URL and access token.")
            return
        self._save_git_creds()
        self._auto_running = True
        self._set_run_active(True)
        self.render()

        def cb(msg, tone="dim"):
            self._auto_logmsg(msg, tone)

        def work():
            try:
                ok, msg = E.push_to_git(self._auto_out_dir, self.auto_git_url.strip(),
                                        self.auto_git_token.strip(),
                                        branch=(self.auto_git_branch.strip() or "main"),
                                        cb=cb)
                if ok:
                    cb("Pushed. Open/refresh the repo in IntelliJ to sync.", "ok")
                else:
                    cb(f"Push failed — {msg}", "err")
            except Exception as ex:
                cb(f"Push error: {str(ex)[:200]}", "err")
            finally:
                self._auto_running = False
                self._set_run_active(False)
                self.ui_safe(self.render)

        self._bg(work)



# ═══════════════════════════════════════════════════════════════════════════════
def main(page: ft.Page):
    page.fonts = {
        T.F_UI: "https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800",
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