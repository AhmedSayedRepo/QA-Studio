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
    assert 'Config.need("APP_USER")' in src and 'Config.need("APP_PASS")' in src


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


def test_bucket2_login_case_keeps_its_actions_when_verified():
    c = _case()
    c["bucket"] = 2
    src, _ = E.generate_selfhealing_test_class({"id": "s1", "title": "t"}, [c], "com.x")
    assert "performLogin();" not in src and 'Config.need("APP_USER")' in src
    pw = AT._pw_spec({"id": "s1", "title": "t"}, [c], E._seed_locator_for_intent, {})
    pw = pw[0] if isinstance(pw, tuple) else pw
    assert "Config" not in pw and "username" in pw


def test_replay_runs_verified_login_steps_as_written():
    import inspect
    src = inspect.getsource(E)
    assert "as_authored = _login_steps_as_authored(case, _is_login_flow_action)" in src
    assert 'bucket == 2 and not as_authored and intent.get("role") == "action"' in src


def test_missing_credentials_fail_with_a_clear_message():
    assert "public static String need(String name)" in E._sh_config("com.x", "https://x.test", "")
    assert "is not set" in AT._pw_env("APP_USER") and "throw new Error" in AT._cy_env("APP_PASS")
