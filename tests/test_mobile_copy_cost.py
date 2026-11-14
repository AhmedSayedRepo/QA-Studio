"""Copy follows the log tab, OpenAI calls are priced, phone dialogs fit the
screen and the Performance screen stacks its Activity card on a phone."""
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import log_view  # noqa: E402
import engine as E  # noqa: E402


class CopyFollowsTab(unittest.TestCase):
    LINES = [{"msg": "Start", "tone": "info"},
             {"msg": "  walking 'A'", "tone": "case"}, {"msg": "ok step", "tone": "ok"},
             {"msg": "bad", "tone": "err"},
             {"msg": "  walking 'B'", "tone": "case"}, {"msg": "fine", "tone": "ok"},
             {"msg": "slow site", "tone": "warn"}]

    def test_issues_tab_copies_only_issues(self):
        got = [l["msg"] for l in log_view.lines_to_copy(self.LINES, issues_only=True)]
        self.assertEqual(got, ["  walking 'A'", "bad", "  walking 'B'", "slow site"])

    def test_all_tab_copies_everything(self):
        self.assertEqual(len(log_view.lines_to_copy(self.LINES, issues_only=False)), len(self.LINES))

    def test_copy_button_uses_the_tab(self):
        src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
        self.assertIn('log_view.lines_to_copy(getattr(self, "_auto_log", [])', src)


class OpenAiCost(unittest.TestCase):
    def test_current_and_dated_openai_models_are_priced(self):
        for model in ("gpt-5", "gpt-5-mini", "gpt-5.4", "gpt-6.1-sol", "gpt-4o-2024-08-06",
                      "gpt-4.1-mini-2025-04-14", "GPT-4o", "o4-mini", "openai/gpt-5-nano"):
            self.assertIsNotNone(E.price_for("openai", model), model)
        self.assertAlmostEqual(E._call_cost("openai", "gpt-5", 1_000_000, 100_000), 2.25)

    def test_unknown_models_stay_unpriced(self):
        self.assertIsNone(E.price_for("openai", "gpt-unknown"))
        self.assertIsNone(E.price_for("openrouter", "openai/gpt-5"))
        self.assertIsNotNone(E.price_for("anthropic", "claude-haiku-4-5-20251001"))


class InspectionWindow(unittest.TestCase):
    """QA Studio's visible inspection Chrome fills the screen instead of a fixed
    1440x900 window that ran under the taskbar on scaled displays."""

    def test_visible_browser_is_maximized_headless_keeps_its_size(self):
        self.assertEqual(E._inspection_window_args(False), ["--start-maximized"])
        self.assertEqual(E._inspection_window_args(True), ["--window-size=1440,900"])
        calls = []
        drv = type("D", (), {"maximize_window": lambda self: calls.append("max")})()
        E._fit_inspection_window(drv, headless=False)
        E._fit_inspection_window(drv, headless=True)
        self.assertEqual(calls, ["max"])
        src = open(os.path.join(ROOT, "engine.py"), encoding="utf-8").read()
        self.assertNotIn('"--window-size=1440,900",', src)       # no fixed size left inline


# Runs in a separate Python with a stand-in Flet, so it works with or without
# Flet installed and never leaks the stand-in into other tests.
_STUB = textwrap.dedent("""
    import sys, types
    sys.path.insert(0, %r)
    from unittest import mock
    class Ctl:
        def __init__(self, *a, **k):
            self.__dict__.update(k)
            if a and 'controls' not in k and isinstance(a[0], list): self.controls = a[0]
            elif a and 'content' not in k: self.content = a[0]
        def update(self): pass
    fl = mock.MagicMock()
    for n in ("Column", "Row", "Container"): setattr(fl, n, type(n, (Ctl,), {}))
    sys.modules['flet'] = fl
    for _ in range(40):
        try:
            import platform_caps, dialogs, updater_ui, performance
            break
        except ModuleNotFoundError as e:
            sys.modules[e.name] = mock.MagicMock()
            for m in ("performance", "regression", "dialogs", "updater_ui", "ui", "engine", "main"):
                sys.modules.pop(m, None)
""") % ROOT


def _run(body):
    r = subprocess.run([sys.executable, "-c", _STUB + textwrap.dedent(body)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    return r.returncode, r.stdout, r.stderr


class PhoneLayouts(unittest.TestCase):
    def test_dialog_content_is_narrowed_to_the_phone(self):
        code, out, err = _run("""
            platform_caps._FLET_PLATFORM = "android"
            page = types.SimpleNamespace(width=360, height=740, show_dialog=lambda d: None)
            app = types.SimpleNamespace(page=page, _close_dropdowns=None)
            c = types.SimpleNamespace(width=480); d = types.SimpleNamespace(content=c, actions=[1])
            dialogs.show_dialog(app, d)
            w, h = updater_ui._mobile_dialog_box(app, 3000)
            platform_caps._FLET_PLATFORM = "windows"
            c2 = types.SimpleNamespace(width=480); d2 = types.SimpleNamespace(content=c2, actions=[1])
            dialogs.show_dialog(app, d2)
            print("R", c.width, hasattr(d, "inset_padding"), w, h, c2.width)
        """)
        self.assertEqual(code, 0, err)
        r = out.split("R ")[1].split()
        self.assertEqual(r[0], "304")          # 360 - 2*12 margin - 2*16 padding
        self.assertEqual(r[1], "True")
        self.assertEqual(r[2], "304")
        self.assertLessEqual(int(r[3]), 444)   # at most 60% of the screen height, then scrolls
        self.assertEqual(r[4], "480")          # desktop unchanged

    def test_performance_stacks_activity_on_a_phone(self):
        code, out, err = _run("""
            app = mock.MagicMock(); app.active = "performance"
            app._perf_result = None; app._perf_last_result = None
            cap = {}
            app.shell = lambda t, s, body, **k: cap.__setitem__('body', body)
            platform_caps._FLET_PLATFORM = "android"
            performance.screen(app); b = cap['body']
            mob = (type(b).__name__, getattr(b.controls[-1], 'height', None))
            app._perf_refresh_mounted()
            again = getattr(b.controls[-1], 'height', None)
            platform_caps._FLET_PLATFORM = "windows"
            performance.screen(app); d = cap['body']
            print("R", mob[0], mob[1], again, type(d).__name__, getattr(d.controls[1], 'width', None))
        """)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.split("R ")[1].split(), ["Column", "420", "420", "Row", "384"])


if __name__ == "__main__":
    unittest.main()


class AutomationEmailPreview(unittest.TestCase):
    """The phone/inbox preview reads "9/9 replay passed · 4 proven locators · 0
    need review", not the metric table as one run-on line ("Needs review 0
    Proven locators 4" read as "0 proven locators")."""

    def test_hidden_first_line_summarises_the_run(self):
        st = {"Stories": 1, "Test cases": 9, "Skipped": 0, "Needs review": 0,
              "Proven locators": 4, "Replay passed": "9/9", "Time": "4m 58s"}
        html = E.build_automation_report_email("cypress", "1 story generated", st)
        body = html[html.index("<body"):]
        first = body[body.index(">") + 1:].lstrip()
        self.assertTrue(first.startswith("<div style='display:none"))
        self.assertIn("Completed · 9/9 replay passed · 4 proven locators · 0 need review", first[:400])
        self.assertIn("1 proven locator · 1 needs review",
                      E._automation_preheader("Blocked", {"Proven locators": 1, "Needs review": 1}))
