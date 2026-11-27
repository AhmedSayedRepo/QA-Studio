"""Android Back (beta) navigation history + desktop Windows Hello gate."""
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PRE = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, %r)
    from unittest import mock
    class Ctl:
        def __init__(self, *a, **k):
            self.__dict__.update(k)
            self.args = a
        def update(self): pass
    fl = mock.MagicMock()
    for n in ("Column", "Row", "Container", "TextField", "Text", "Icon", "IconButton", "Stack",
              "ProgressRing", "ProgressBar", "FilledButton", "AlertDialog", "ListView", "Switch",
              "GestureDetector", "Dropdown", "DropdownOption", "Checkbox", "TextButton", "Image"):
        setattr(fl, n, type(n, (Ctl,), {}))
    sys.modules['flet'] = fl
    for name in ('ai_usage_screen', 'identity_editor', 'login', 'orgs_screen', 'perf',
                 'perf.models', 'perf.ports', 'report', 'sprint_titles', 'task_manager',
                 'useful_links', 'users_screen', 'window_chrome'):
        sys.modules[name] = mock.MagicMock()
    for _ in range(80):
        try:
            import main
            break
        except ModuleNotFoundError as e:
            sys.modules[e.name] = mock.MagicMock()
            sys.modules.pop('main', None)
    import platform_caps, strings
    Q = main.QAStudio
    app = Q.__new__(Q)
    app.user = {'id': 'u1'}
    app.active = 'setup'
    app._nav_history, app._nav_last, app._nav_popping = [], None, False
    app._back_beta, app._back_exit_until = False, 0.0
    app._onboarding_open = False
    app._rr_view_id = None
    app.can_open_screen = lambda s: s != 'forbidden'
    toasts = []
    app._toast = lambda m: toasts.append(m)
    app.ui_safe = lambda fn: fn()
    view = Ctl(can_pop=True)
    app.page = mock.MagicMock()
    app.page.views = [view]
    def visit(s):
        app.active = s
        app._track_nav()
    def goto(s):
        visit(s)
    app.goto = goto
""" % ROOT)


def _run(body):
    code = _PRE + textwrap.dedent(body)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT, timeout=120)
    if r.returncode != 0:
        raise AssertionError(r.stdout + r.stderr)
    return r.stdout


class BackBeta(unittest.TestCase):
    def test_history_and_back_to_previous_then_exit_window(self):
        out = _run("""
            visit('setup'); visit('regression'); visit('settings')
            assert app._nav_history == ['setup', 'regression'], app._nav_history
            k, fn = app._back_action(); fn()
            assert k == 'screen' and app.active == 'regression', (k, app.active)
            k, fn = app._back_action(); fn()
            assert k == 'screen' and app.active == 'setup'
            assert app._nav_history == []
            platform_caps.is_mobile = lambda: True
            app.set_back_beta(True)
            assert view.can_pop is False and view.on_confirm_pop
            k, fn = app._back_action()
            assert k == 'arm_exit'
            fn()
            assert view.can_pop is True and toasts == [strings.t('main_back_exit')]
            k, fn = app._back_action()
            assert k == 'exit' and fn is None
            print('OK')
        """)
        self.assertIn("OK", out)

    def test_history_does_not_grow_on_ping_pong_and_skips_blocked(self):
        out = _run("""
            for _ in range(10):
                visit('setup'); visit('regression')
            assert app._nav_history == ['setup'], app._nav_history
            app.active = 'settings'
            app._nav_history = ['setup', 'forbidden']
            k, fn = app._back_action(); fn()
            assert app.active == 'setup', app.active
            print('OK')
        """)
        self.assertIn("OK", out)

    def test_onboarding_closes_first_and_not_home_goes_home(self):
        out = _run("""
            closed = []
            app._close_dialog = lambda: closed.append(1)
            app._onboarding_open = True
            k, fn = app._back_action(); fn()
            assert k == 'dialog' and closed and not app._onboarding_open
            app._home_screen = lambda: 'setup'
            app.active = 'report'; app._nav_history = []
            k, fn = app._back_action(); fn()
            assert k == 'screen' and app.active == 'setup'
            print('OK')
        """)
        self.assertIn("OK", out)

    def test_beta_off_never_touches_root_view(self):
        out = _run("""
            platform_caps.is_mobile = lambda: True
            view.can_pop = 'untouched'
            app._apply_back_nav(update=True)
            assert view.can_pop == 'untouched'
            app.set_back_beta(True); app.set_back_beta(False)
            assert view.can_pop is True and view.on_confirm_pop is None
            print('OK')
        """)
        self.assertIn("OK", out)

    def test_signed_out_clears_history(self):
        out = _run("""
            visit('setup'); visit('regression')
            app.user = None
            app._track_nav()
            assert app._nav_history == []
            print('OK')
        """)
        self.assertIn("OK", out)


    def test_back_nav_always_on_and_no_settings_switch(self):
        with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("self._back_beta = True", src)
        with open(os.path.join(ROOT, "settings.py"), encoding="utf-8") as f:
            self.assertNotIn("set_back_beta", f.read())

    def test_left_swipe_drawer_is_filled_on_render(self):
        with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("_d.controls = [self._nav_drawer_content()]", src)

    def test_mobile_long_hint_wraps_below_label_and_header_badge_moves(self):
        out = _run("""
            import ui, backend_setup, theme as T
            platform_caps.is_mobile = lambda: True
            c = ui.field_label('Sender name', hint='shown to recipients instead of the address')
            assert isinstance(c, fl.Column), type(c)
            short = ui.field_label('Email Sender', hint='optional')
            assert not isinstance(short, fl.Column)
            h = backend_setup._section_header(fl, T, 'Jira + Zephyr Scale')
            row = h.args[0]
            assert len(row.args[0]) == 2, len(row.args[0])   # icon + texts only
            platform_caps.is_mobile = lambda: False
            assert not isinstance(ui.field_label('A', hint='shown to recipients instead'), fl.Column)
            h = backend_setup._section_header(fl, T, 'Jira')
            assert len(h.args[0].args[0]) == 3
            print('OK')
        """)
        self.assertIn("OK", out)


class WindowsHello(unittest.TestCase):
    def test_gate_only_on_desktop_when_enabled_and_not_passed(self):
        out = _run("""
            import desktop_hello
            platform_caps.is_mobile = lambda: False
            desktop_hello.enabled = lambda: True
            assert app._hello_gate_active() is True
            app._hello_passed = True
            assert app._hello_gate_active() is False
            app._hello_passed = False
            desktop_hello.enabled = lambda: False
            assert app._hello_gate_active() is False
            platform_caps.is_mobile = lambda: True
            desktop_hello.enabled = lambda: True
            assert app._hello_gate_active() is False
            print('OK')
        """)
        self.assertIn("OK", out)

    def test_no_prompt_at_launch_and_no_auto_restore(self):
        out = _run("""
            import desktop_hello
            platform_caps.is_mobile = lambda: False
            desktop_hello.enabled = lambda: True
            desktop_hello.warm = lambda: None
            asked = []
            desktop_hello.verify = lambda msg: asked.append(msg) or (True, 'ok')
            main.auth.configured = lambda: True
            restored = []
            main.auth.acquire_silent = lambda: restored.append(1) or {'id': 'u'}
            app.user = None
            app._bg = lambda fn: fn()
            app._start_hello_gate()
            assert not asked and not restored and app.user is None
            assert app._hello_gate_active() is True   # silent restore skipped
            print('OK')
        """)
        self.assertIn("OK", out)

    def test_saved_account_password_waits_for_hello(self):
        with open(os.path.join(ROOT, "login.py"), encoding="utf-8") as f:
            src = f.read()
        sel = src[src.index("def _select(em, pw):"):src.index("def _render_accounts(")]
        self.assertIn('_fill(em, "" if _hello else pw)', sel)
        self.assertIn('_hello.verify(strings.t("set_hello_prompt"),', sel)
        self.assertIn("hwnd=_hwnd", sel)
        self.assertIn("_hwnd = _hello.foreground_window()", sel)
        self.assertIn("if ok:", sel)
        self.assertIn("threading.Thread(target=_verify", sel)

    def test_module_is_inert_off_windows(self):
        out = _run("""
            import desktop_hello
            desktop_hello.supported_platform = lambda: False
            assert desktop_hello.available() is False
            assert desktop_hello.enabled() is False
            assert desktop_hello.verify('x') == (False, 'unavailable')
            print('OK')
        """)
        self.assertIn("OK", out)

    def test_check_works_from_inside_a_running_event_loop(self):
        out = _run("""
            import asyncio, desktop_hello
            desktop_hello.supported_platform = lambda: True
            class U:
                @staticmethod
                async def check_availability_async(): return 0
                @staticmethod
                async def request_verification_async(m): return 0
            desktop_hello._ucv = lambda: U
            desktop_hello._verify_for_window = lambda h, m, t: 0
            async def main_loop():          # like a Flet switch handler
                return (desktop_hello.available(refresh=True),
                        desktop_hello.verify('x'))
            got = asyncio.run(main_loop())
            assert got == (True, (True, 'ok')), (got, desktop_hello.last_reason)
            print('OK')
        """)
        self.assertIn("OK", out)

    def test_window_owned_prompt_iid_and_result_mapping(self):
        out = _run("""
            import desktop_hello as d
            assert d.async_result_iid() == 'fd596ffd-2318-558f-9dbe-d21df43764a5'
            d.available = lambda refresh=False: True
            seen = []
            d._verify_for_window = lambda h, m, t: seen.append(h) or 6
            assert d.verify('x', hwnd=4242) == (False, 'canceled') and seen == [4242]
            d._verify_for_window = lambda h, m, t: 0
            assert d.verify('x', hwnd=1) == (True, 'ok')
            def boom(h, m, t): raise OSError('E_FAIL')
            d._verify_for_window = boom
            assert d.verify('x', hwnd=1) == (False, 'failed') and 'E_FAIL' in d.last_reason
            print('OK')
        """)
        self.assertIn("OK", out)

    def test_requirements_pin_winrt_to_windows_only(self):
        with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.lower().startswith("winrt")]
        self.assertTrue(lines)
        for l in lines:
            self.assertIn('sys_platform == "win32"', l)

    def test_strings_in_all_languages(self):
        import importlib
        sys.path.insert(0, ROOT)
        S = importlib.import_module("strings")
        keys = ["set_hello_title", "set_hello_desc", "set_hello_unavailable",
                "set_hello_on", "set_hello_off", "set_hello_prompt",
                "main_hello_fallback", "set_beta", "set_back_beta_title",
                "set_back_beta_desc", "set_back_beta_on", "set_back_beta_off"]
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            for k in keys:
                self.assertIn(k, S._STRINGS[lang], (lang, k))


if __name__ == "__main__":
    unittest.main()
