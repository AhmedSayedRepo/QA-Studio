"""Playwright + Cypress get what the Selenium target got after the SauceDemo
runs: waited locator probes (no instant miss -> AI), proven fallbacks before
any AI call, broad login selectors (#user-name), app cases that log in when
the start URL is the login form, and (Cypress) a real hover."""
import json
import re
import unittest

import automation_targets as A


def _intent(fallbacks=None, verb="click"):
    it = {"role": "action", "verb": verb, "target": "Save", "keywords": ["Save"],
          "kind": "button", "value": "", "from_steps": [1], "inspection_source": "live",
          "live_locator": {"by": "id", "value": "save", "verified": True,
                           "fallbacks": fallbacks or []}}
    return it


SEED = lambda it, app_page=False: ("id", "save", True)
FB = [{"by": "css", "value": '[data-test="save"]'}]


class PlaywrightParityTests(unittest.TestCase):
    def test_login_selector_finds_user_name_style_fields(self):
        self.assertIn("input[id*=user i]", A._PW_AUTH_JS)

    def test_open_app_home_logs_in_when_the_start_url_is_the_login_form(self):
        body = A._PW_AUTH_JS[A._PW_AUTH_JS.index("async function openAppHome"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("page.goto(", body)
        self.assertIn("input[type=password]", body)
        self.assertIn("performLogin(page)", body)
        self.assertRegex(A._PW_AUTH_JS, r"module\.exports = \{[^}]*openAppHome")

    def test_app_cases_start_with_open_app_home(self):
        case = {"title": "Add", "bucket": 3, "intents": []}
        spec, _ = A._pw_spec({"id": 7, "title": "S"}, [case], SEED, {})
        self.assertIn("await openAppHome(page);", spec)
        self.assertIn("openAppHome", spec.split("\n", 5)[0] + "\n".join(spec.split("\n")[:12]))

    def test_healer_waits_and_tries_fallbacks_before_ai(self):
        h = A._PW_HEALER_JS
        res = h[h.index("async resolve("):h.index("async act(")]
        self.assertIn("waitForLoadState", res)
        self.assertIn("waitFor(", h)
        self.assertIn("seed.fallbacks", res)
        self.assertLess(res.index("seed.fallbacks"), res.index("if (HEAL)"))

    def test_proven_fallbacks_are_emitted_with_the_seed(self):
        lines = []
        A._emit_pw_intent(lines, "k", _intent(FB), SEED, {})
        self.assertIn('"fallbacks": [{"by": "css", "value": "[data-test=\\"save\\"]"}]', "\n".join(lines))


class CypressParityTests(unittest.TestCase):
    def test_login_selector_finds_user_name_style_fields(self):
        self.assertIn("input[id*=user]", A._CY_HEALER_JS)

    def test_open_app_home_command_logs_in_when_needed(self):
        h = A._CY_HEALER_JS
        body = h[h.index("Cypress.Commands.add('openAppHome'"):]
        body = body[:body.index("\n});\n")]
        self.assertIn("cy.visit(", body)
        self.assertIn("input[type=password]", body)
        self.assertIn("cy.performLogin()", body)

    def test_app_cases_start_with_open_app_home(self):
        case = {"title": "Add", "bucket": 3, "intents": []}
        spec, _ = A._cy_spec({"id": 7, "title": "S"}, [case], SEED, {})
        self.assertIn("cy.openAppHome();", spec)
        self.assertNotIn("cy.visit(Cypress.env('APP_BASE_URL') || '/');", spec)

    def test_selector_resolution_polls_and_tries_fallbacks_before_ai(self):
        h = A._CY_HEALER_JS
        sel = h[h.index("Cypress.Commands.add('healSelector'"):h.index("Cypress.Commands.add('healAct'")]
        self.assertIn("_poll(", sel)
        self.assertIn("seed.fallbacks", sel)
        self.assertLess(sel.index("seed.fallbacks"), sel.index("pickLocator"))
        self.assertIn("cy.wait(", h[h.index("function _poll"):h.index("function _poll") + 400])

    def test_hover_is_a_hover_not_a_click(self):
        act = A._CY_HEALER_JS[A._CY_HEALER_JS.index("Cypress.Commands.add('healAct'"):]
        act = act[:act.index("\n});\n")]
        self.assertRegex(act, r"verb === 'hover'[\s\S]*?trigger\('mouseover'\)")

    def test_proven_fallbacks_are_emitted_with_the_seed(self):
        lines = []
        A._emit_cy_intent(lines, "k", _intent(FB), SEED, {})
        self.assertIn('"fallbacks": [{"by": "css", "value": "[data-test=\\"save\\"]"}]', "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
