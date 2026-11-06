"""Live run (SauceDemo) replay failures on the negative-login cases:
A. an explicit username/password/Login sequence in a NEGATIVE login case was
   treated as a login flow -> real login first, own steps skipped, error looked
   for on the products page;
B. the saved plan blanked every login typed value, so authored test data
   ('wrong_password', 'locked_out_user') was lost — only the real credentials
   must be kept out (as runtime placeholders);
C. data-test / data-cy attributes are stable locators too."""
import sys
import types
import unittest

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    _requests = types.ModuleType("requests")
    _adapters = types.ModuleType("requests.adapters")
    _adapters.HTTPAdapter = object
    _requests.adapters = _adapters
    sys.modules["requests"] = _requests
    sys.modules["requests.adapters"] = _adapters

import engine
import automation_targets

LOGIN = {"user": "standard_user", "password": "secret_sauce"}


def _act(target, value, verb="type", kind="input"):
    return {"role": "action", "verb": verb, "target": target, "keywords": [target],
            "kind": kind, "value": value, "from_steps": [1], "inspection_source": "live",
            "live_locator": {"by": "id", "value": target.split()[0].lower(), "verified": True}}


def _neg_case():
    intents = [_act("Username field", "standard_user"), _act("Password field", "wrong_password"),
               _act("Login button", "", verb="click", kind="button"),
               {"role": "assertion", "target": "error", "keywords": ["do not match"],
                "kind": "text", "inspection_source": "live",
                "live_locator": {"by": "css", "value": '[data-test="error"]', "verified": True}}]
    return [{"story": {"id": 7}, "test_cases": [{
        "id": 2, "title": "Login with wrong password shows an error", "steps": [{}],
        "inspection_plan": {"intents": intents, "case_type": "negative_login",
                            "page_context": "login", "explicit_login_flow": True}}]}]


class NegativeLoginSequencingTests(unittest.TestCase):
    def test_negative_login_case_is_never_a_login_flow(self):
        case = engine.sequence_verified_inspection_plans(_neg_case())[0]["cases"][0]
        self.assertEqual(case["bucket"], 0)
        self.assertFalse(case["explicit_login_flow"])

    def test_generated_java_types_its_own_values_without_logging_in(self):
        case = engine.sequence_verified_inspection_plans(_neg_case())[0]["cases"][0]
        java, _ = engine.generate_selfhealing_test_class({"id": 7}, [case], "com.qastudio")
        self.assertNotIn("performLogin();", java)
        self.assertIn('"wrong_password"', java)


class PlanValueTests(unittest.TestCase):
    def test_real_credentials_become_runtime_placeholders(self):
        self.assertEqual(engine._plan_safe_value("standard_user", LOGIN), engine.APP_USER_TOKEN)
        self.assertEqual(engine._plan_safe_value("secret_sauce", LOGIN), engine.APP_PASS_TOKEN)

    def test_authored_test_data_is_kept(self):
        self.assertEqual(engine._plan_safe_value("wrong_password", LOGIN), "wrong_password")
        self.assertEqual(engine._plan_safe_value("locked_out_user", LOGIN), "locked_out_user")
        self.assertEqual(engine._plan_safe_value("", LOGIN), "")

    def test_without_known_credentials_nothing_is_replaced(self):
        self.assertEqual(engine._plan_safe_value("standard_user", {}), "standard_user")


class PlaceholderEmissionTests(unittest.TestCase):
    def _case(self, value):
        return {"title": "Neg", "bucket": 0, "ctype": "negative_login",
                "page_context": "login", "explicit_login_flow": False,
                "intents": [_act("Username field", value)]}

    def test_java_reads_placeholders_from_config_never_a_literal(self):
        java, _ = engine.generate_selfhealing_test_class(
            {"id": 7}, [self._case(engine.APP_USER_TOKEN)], "com.qastudio")
        self.assertIn("Config.USER);", java)
        self.assertNotIn(engine.APP_USER_TOKEN, java)

    def test_playwright_and_cypress_read_placeholders_from_env(self):
        for emit, expect in ((automation_targets._emit_pw_intent, "process.env.APP_PASS"),
                             (automation_targets._emit_cy_intent, "Cypress.env('APP_PASS')")):
            lines = []
            emit(lines, "k", _act("Password field", engine.APP_PASS_TOKEN),
                 lambda it, app_page=False: ("id", "password", True), {})
            text = "\n".join(lines)
            self.assertIn(expect, text, emit.__name__)
            self.assertNotIn(engine.APP_PASS_TOKEN, text)

    def test_replay_types_the_real_credential_for_a_placeholder(self):
        self.assertEqual(engine._replay_value(engine.APP_USER_TOKEN, LOGIN), "standard_user")
        self.assertEqual(engine._replay_value("wrong_password", LOGIN), "wrong_password")


class DataTestAttributeTests(unittest.TestCase):
    def test_candidates_cover_data_test_and_data_cy(self):
        cands = engine._locator_candidates({"tag": "h3", "testid": "error", "text": ""})
        values = [c["value"] for c in cands]
        for sel in ('[data-testid="error"]', '[data-test="error"]', '[data-cy="error"]'):
            self.assertIn(sel, values)


class PlaceholderContractTests(unittest.TestCase):
    def test_engine_and_js_targets_use_the_same_placeholders(self):
        self.assertEqual(engine.APP_USER_TOKEN, automation_targets._APP_USER_TOKEN)
        self.assertEqual(engine.APP_PASS_TOKEN, automation_targets._APP_PASS_TOKEN)


if __name__ == "__main__":
    unittest.main()
