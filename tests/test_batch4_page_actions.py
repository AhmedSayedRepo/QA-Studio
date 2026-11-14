"""Batch 4: browser back / forward / refresh, "close X if it is shown",
clear a field, key combinations and attribute checks - parsed from the authored
wording and emitted the same way for Selenium, Playwright and Cypress."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402
import automation_targets as A  # noqa: E402

SEED = lambda *a, **k: ("css", "#x", True)  # noqa: E731


def _pack(*actions, expected=""):
    steps = [{"action": a} for a in actions]
    if expected:
        steps.append({"action": "Check it", "expected": expected})
    return E._apply_assertion_pack([], steps)


class PageActionParsing(unittest.TestCase):
    def test_back_forward_refresh(self):
        verbs = [it["verb"] for it in _pack("Go back", "Click the browser Back button", "Go forward",
                                            "Refresh the page", "Reload")]
        self.assertEqual(verbs, ["go_back", "go_back", "go_forward", "refresh", "refresh"])

    def test_window_and_frame_steps_are_not_browser_back(self):
        self.assertEqual(_pack("Go back to the original window", "Switch back to the main page outside the iframe"), [])

    def test_close_if_shown(self):
        named, unnamed = _pack('Close "Close" if it is shown', "Dismiss the cookie banner if present")
        self.assertEqual((named["verb"], named["value"]), ("dismiss_if_present", "Close"))
        self.assertEqual((unnamed["verb"], unnamed["value"]), ("dismiss_if_present", ""))

    def test_plain_click_is_not_a_page_action(self):
        self.assertIsNone(E._page_action_in('Click the "Close" button'))
        self.assertIsNone(E._page_action_in('Click the "Back to list" link'))


class AttributeChecks(unittest.TestCase):
    def test_attribute_phrases(self):
        out = _pack(expected='The "Login" button has "type" equal to "submit", the "Username" field has no '
                             '"disabled" attribute and the "Home" link has attribute "href" containing "/"')
        self.assertEqual([(i["check"], i["value"]) for i in out], [
            ("attr", "Login|type|is|submit"), ("attr", "Username|disabled|absent|"),
            ("attr", "Home|href|contains|/")])

    def test_attr_is_a_page_check_everywhere(self):
        self.assertIn("attr", E._PAGE_CHECKS)
        self.assertIn("if (check === 'attr')", E._PAGE_CHECK_FN)
        self.assertEqual(E._page_check_label("attr", "Login|type|is|submit"), '"Login" attribute "type" is "submit"')


class ClearAndKeys(unittest.TestCase):
    def test_clear_contract_but_not_the_clear_button(self):
        self.assertEqual(E._source_operation_contract(
            {"action": 'Clear the "Input: Text" field', "element_type": "text input"})["verb"], "clear")
        self.assertEqual(E._source_operation_contract(
            {"action": 'Click the "Clear Inputs" button', "element_type": "button"})["verb"], "click")

    def test_key_values(self):
        self.assertEqual(E._key_value("Press Ctrl+A in the field"), "CONTROL+A")
        self.assertEqual(E._key_value("press shift + tab"), "SHIFT+TAB")
        self.assertEqual(E._key_value("Press the Escape key"), "ESCAPE")
        self.assertEqual(E._key_value("Press Backspace"), "BACKSPACE")
        self.assertIn("clear", E._VALID_ACTION_VERBS)


class EmittedForEveryFramework(unittest.TestCase):
    def _all(self, intent):
        j, p, c = [], [], []
        E._emit_intent(j, "k1", intent)
        A._emit_pw_intent(p, "k1", intent, SEED, {})
        A._emit_cy_intent(c, "k1", intent, SEED, {})
        return "\n".join(j), "\n".join(p), "\n".join(c)

    def test_page_actions(self):
        for verb in ("go_back", "go_forward", "refresh", "dismiss_if_present"):
            j, p, c = self._all({"role": "action", "verb": verb, "target": verb, "value": ""})
            self.assertIn('heal.pageAction("%s"' % verb, j)
            self.assertIn('await heal.pageAction("%s"' % verb, p)
            self.assertIn('cy.healPageAction("%s"' % verb, c)

    def test_attr_check(self):
        j, p, c = self._all({"role": "assertion", "check": "attr", "value": "Login|type|is|submit", "target": "x"})
        self.assertIn('heal.assertPage("attr", "Login|type|is|submit")', j)
        self.assertIn('heal.assertPage("attr", "Login|type|is|submit")', p)
        self.assertIn('cy.healAssertPage("attr", "Login|type|is|submit")', c)

    def test_runtimes_implement_them(self):
        healer = E._sh_healer("com.qastudio")
        self.assertIn("public void pageAction(String verb, String value)", healer)
        self.assertIn('case "clear":', healer)
        self.assertIn("static CharSequence keyChord(String value)", healer)
        support = "".join(getattr(A, n) for n in dir(A) if n.startswith("_") and isinstance(getattr(A, n), str))
        self.assertIn("async pageAction(verb, value)", support)
        self.assertIn("Cypress.Commands.add('healPageAction'", support)
        self.assertIn("case 'clear': await el.fill('')", support)
        self.assertIn("verb === 'clear'", support)
        self.assertNotIn("__DISMISS_FN__", A._with_batch2_js("const DISMISS_FN = __DISMISS_FN__;"))

    def test_page_actions_need_no_locator(self):
        for verb in ("go_back", "dismiss_if_present"):
            self.assertIn(verb, E._PAGE_ACTIONS)


if __name__ == "__main__":
    unittest.main()
