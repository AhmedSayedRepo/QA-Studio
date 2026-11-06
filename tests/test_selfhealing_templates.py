"""Generated Selenium/TestNG runtime templates: wait strategy and hover."""
import re
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

PKG = "com.qastudio"


def _method(src, signature):
    """Return the body of the Java method whose declaration contains `signature`."""
    start = src.index(signature)
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError("unbalanced braces after %r" % signature)


class SelfHealingWaitStrategyTests(unittest.TestCase):
    def setUp(self):
        self.base = engine._sh_base_test(PKG)
        self.healer = engine._sh_healer(PKG)

    def test_base_test_turns_implicit_wait_off(self):
        # Mixing an implicit wait with the Healer's explicit WebDriverWait makes
        # every miss cost implicit x polls; the generated suite must not do it.
        self.assertIn("implicitlyWait(Duration.ZERO)", self.base)
        self.assertNotRegex(self.base, r"implicitlyWait\(Duration\.ofSeconds\(\s*[1-9]")

    def test_find_waits_for_page_ready_before_any_lookup(self):
        find = _method(self.healer, "public WebElement find(")
        self.assertIn("settle();", find)
        self.assertLess(find.index("settle();"), find.index("store.get(key)"))
        settle = _method(self.healer, "private void settle(")
        self.assertIn("document.readyState", settle)
        # aria-busy is deliberately NOT awaited: apps that leave it set on a
        # container permanently would add the full timeout to every step.
        self.assertNotIn("aria-busy", settle)

    def test_stale_cached_locator_is_probed_briefly_when_a_seed_exists(self):
        find = _method(self.healer, "public WebElement find(")
        self.assertRegex(find, r"tryFind\(cached,\s*seed != null \? probe : wait\)")
        self.assertRegex(self.healer, r"probe = new WebDriverWait\(driver, Duration\.ofSeconds\(2\)\)")

    def test_seed_and_healed_locators_keep_the_full_wait(self):
        find = _method(self.healer, "public WebElement find(")
        self.assertIn("tryFind(seed, wait)", find)
        self.assertIn("tryFind(by, wait)", find)


class SelfHealingHoverTests(unittest.TestCase):
    def test_hover_moves_the_pointer_instead_of_clicking(self):
        healer = engine._sh_healer(PKG)
        hover = re.search(r'case "hover":(.*?)break;', healer, re.S).group(1)
        self.assertIn("new Actions(driver).moveToElement(el).perform()", hover)
        self.assertNotIn("click", hover)
        self.assertIn("import org.openqa.selenium.interactions.Actions;", healer)


class AppHomeStartTests(unittest.TestCase):
    """App cases must not assume the site URL is an app page: on sites whose
    root IS the login form (e.g. SauceDemo), they log in and land on the app."""

    def test_base_test_open_app_home_logs_in_when_the_start_url_shows_the_login_form(self):
        base = engine._sh_base_test(PKG)
        body = _method(base, "protected void openAppHome(")
        self.assertIn("driver.get(Config.BASE_URL);", body)
        self.assertIn("loginFieldPresent(", body)
        self.assertIn("performLogin();", body)

    def test_app_cases_start_with_open_app_home(self):
        case = {"title": "Add to cart", "bucket": 3, "ctype": "interaction",
                "page_context": "app", "intents": []}
        java, _ = engine.generate_selfhealing_test_class({"id": 7}, [case], PKG)
        self.assertIn("openAppHome();", java)
        self.assertNotIn("driver.get(Config.BASE_URL);", java)


class LoginFormVisibleTests(unittest.TestCase):
    def test_detects_a_displayed_password_field_only(self):
        class E:
            def __init__(self, shown): self.shown = shown
            def is_displayed(self): return self.shown
        class D:
            def __init__(self, els): self.els = els
            def find_elements(self, by, value): return self.els
        self.assertTrue(engine._login_form_visible(D([E(False), E(True)])))
        self.assertFalse(engine._login_form_visible(D([E(False)])))
        self.assertFalse(engine._login_form_visible(D([])))


class LoginSeedTests(unittest.TestCase):
    """The generated login must find username fields named like 'user-name'
    (SauceDemo) without AI healing — same breadth as the explorer's login."""
    USER = "input[id*=user i]"

    def test_generated_login_seed_matches_the_explorer_breadth(self):
        self.assertIn(self.USER, engine._sh_base_test(PKG))

    def test_replay_login_seed_matches_the_explorer_breadth(self):
        self.assertIn(self.USER, engine._REPLAY_USER)


if __name__ == "__main__":
    unittest.main()
