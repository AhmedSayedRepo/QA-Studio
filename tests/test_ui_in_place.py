"""UI controls that must update IN PLACE (never a full app.render()).

A full re-render rebuilds every control: the screen jumps back to the top and a
running Activity log loses its live handle. The framework switch regressed to a
full render twice, so these tests drive the real controls and fail if anything
calls app.render()."""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import flet  # noqa: F401
    import automation
except Exception as exc:  # pragma: no cover - flet is installed on dev PCs
    automation = None
    _why = str(exc)


class _NoRender(types.SimpleNamespace):
    def render(self):
        raise AssertionError("app.render() was called - this control must update in place")


@unittest.skipIf(automation is None, "flet/automation not importable")
class FrameworkSwitchInPlace(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self._old_store = sys.modules.get("store")
        sys.modules["store"] = types.SimpleNamespace(save=lambda creds: self.saved.append(dict(creds)))

    def tearDown(self):
        if self._old_store is None:
            sys.modules.pop("store", None)
        else:
            sys.modules["store"] = self._old_store

    def test_picking_a_framework_restyles_in_place_and_saves(self):
        app = _NoRender(creds={}, _auto_target="selenium")
        cards = automation._target_cards(app)
        self.assertEqual(len(cards), 3)
        self.assertEqual(cards[0].border.top.width, 2)        # Selenium active
        cards[1].on_click(None)                               # pick Playwright
        self.assertEqual(app._auto_target, "playwright")
        self.assertEqual(app.creds["auto_target"], "playwright")
        self.assertEqual(self.saved[-1]["auto_target"], "playwright")
        self.assertEqual(cards[1].border.top.width, 2)
        self.assertEqual(cards[0].border.top.width, 1)
        cards[2].on_click(None)                               # pick Cypress
        self.assertEqual(app._auto_target, "cypress")
        self.assertEqual(cards[1].border.top.width, 1)

    def test_picking_the_active_framework_does_nothing(self):
        app = _NoRender(creds={}, _auto_target="cypress")
        cards = automation._target_cards(app)
        cards[2].on_click(None)
        self.assertEqual(self.saved, [])


class NoFullRenderInInPlaceHandlers(unittest.TestCase):
    """Source-level guard for handlers that are hard to drive without a page."""

    def _segment(self, text, start, end):
        a = text.index(start)
        return text[a:text.index(end, a + len(start))]

    def test_framework_cards_never_render(self):
        src = open(os.path.join(ROOT, "automation.py"), encoding="utf-8").read()
        seg = self._segment(src, "def _target_cards(app):", "\ndef ")
        self.assertNotIn("app.render()", seg.replace("never app.render()", ""))

    def test_pause_resume_never_render(self):
        src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
        seg = self._segment(src, "    def _pause_automation(self):", "    def _auto_gate(self):")
        self.assertNotIn("self.render()", seg)

    def test_browser_checkboxes_never_render(self):
        src = open(os.path.join(ROOT, "automation.py"), encoding="utf-8").read()
        seg = self._segment(src, "        def _browser_box(key, label):", "        def _source_option(")
        self.assertNotIn("render()", seg)


if __name__ == "__main__":
    unittest.main()
