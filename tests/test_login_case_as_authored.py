"""A login case whose login steps were verified live is generated as written
(its own 'Open .../login'), not through performLogin() on the base URL."""
import json
import engine as E
import automation_targets as AT

_LL = lambda by, v: {"by": by, "value": v, "verified": True, "fallbacks": []}
_INTENTS = [
    {"role": "action", "verb": "navigate", "target": "login page", "keywords": ["Login Page"],
     "kind": "link", "value": "https://the-internet.herokuapp.com/login"},
    {"role": "action", "verb": "type", "target": "username field", "keywords": ["Username"],
     "kind": "input", "value": "{{APP_USER}}", "live_locator": _LL("id", "username")},
    {"role": "action", "verb": "type", "target": "password field", "keywords": ["Password"],
     "kind": "input", "value": "{{APP_PASS}}", "live_locator": _LL("id", "password")},
    {"role": "action", "verb": "click", "target": "login button", "keywords": ["Login"],
     "kind": "button", "value": "", "live_locator": _LL("xpath", "//button[normalize-space()=\"Login\"]")},
    {"role": "assertion", "verb": "", "target": "secure area confirmation", "keywords": ["secure area"],
     "kind": "text", "check": "text_visible", "expected": "You logged into a secure area!",
     "live_locator": _LL("id", "flash")},
]


def _case(verified=True):
    intents = json.loads(json.dumps(_INTENTS))
    if not verified:
        intents[1].pop("live_locator")
    return {"title": "Valid login opens the secure area", "bucket": 3, "priority": 201,
            "ctype": "interaction", "page_context": "app", "explicit_login_flow": True,
            "intents": intents}


def test_java_emits_the_case_own_login_steps():
    src, _ = E.generate_selfhealing_test_class({"id": "s1", "title": "t"}, [_case()], "com.x")
    assert "performLogin();" not in src
    assert 'driver.get("https://the-internet.herokuapp.com/login");' in src
    assert "resetContext();" in src
    assert "Config.USER" in src and "Config.PASS" in src


def test_java_keeps_helper_when_login_steps_unverified():
    src, _ = E.generate_selfhealing_test_class({"id": "s1", "title": "t"}, [_case(False)], "com.x")
    assert "performLogin();" in src


def test_playwright_and_cypress_emit_case_own_login_steps():
    pw = AT._pw_spec({"id": "s1", "title": "t"}, [_case()], E._seed_locator_for_intent, {})
    cy = AT._cy_spec({"id": "s1", "title": "t"}, [_case()], E._seed_locator_for_intent, {})
    pw = pw[0] if isinstance(pw, tuple) else pw
    cy = cy[0] if isinstance(cy, tuple) else cy
    assert "performLogin(page)" not in pw and "the-internet.herokuapp.com/login" in pw
    assert "cy.performLogin()" not in cy and "the-internet.herokuapp.com/login" in cy


def test_selenium_report_screenshots_passed_tests():
    j = E._sh_report_mailer("com.qastudio")
    assert "public void onTestSuccess(ITestResult r)" in j
    assert 'Reporter.log("<a href=\\"../qa-screenshots/' in j
    assert 'String src = "qa-screenshots/"' in j
