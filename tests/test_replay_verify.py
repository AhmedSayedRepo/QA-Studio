"""Phase B: replay each sequenced case using ONLY its saved locators (primary,
then proven fallbacks) — no scoring, no AI — exactly as the generated test
would run. Fake browser, no Chrome."""
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

By = types.SimpleNamespace(ID="id", NAME="name", CSS_SELECTOR="css selector",
                           XPATH="xpath", TAG_NAME="tag name")
LOGIN_FIELDS = "input[type=password],#username,input[name=username]"


class El:
    def __init__(self, drv, key, shown=True, selected=False, enabled=True):
        self.drv, self.key, self.shown = drv, key, shown
        self.selected, self.enabled = selected, enabled

    def is_displayed(self):
        return self.shown

    def is_selected(self):
        return self.selected

    def is_enabled(self):
        return self.enabled

    def click(self):
        self.drv.log.append(("click", self.key))
        self.drv.on_click(self.key)

    def clear(self):
        self.drv.log.append(("clear", self.key))

    def send_keys(self, value):
        self.drv.log.append(("type", self.key, value))


class Drv:
    def __init__(self, dom=None, body="", url="https://app.test/login"):
        self.dom = dict(dom or {})      # (by, value) -> [El]
        self.body, self.current_url = body, url
        self.log, self.quit_called = [], False
        self.after_click = {}           # key -> callable(drv)

    def el(self, key, **kw):
        return El(self, key, **kw)

    def put(self, by, value, *els):
        self.dom[(by, value)] = list(els)

    def on_click(self, key):
        fn = self.after_click.get(key)
        if fn:
            fn(self)

    def get(self, url):
        self.log.append(("get", url))
        self.current_url = url

    def delete_all_cookies(self):
        self.log.append(("cookies",))

    def find_elements(self, by, value):
        return list(self.dom.get((by, value), []))

    def execute_script(self, script, *args):
        if "innerText" in script:
            return self.body
        if args:
            self.log.append(("jsclick", getattr(args[0], "key", None)))
        return None

    def quit(self):
        self.quit_called = True


def _intent(role, verb="click", target="Save", locator=None, fallbacks=None,
            steps=(1,), **extra):
    it = {"role": role, "verb": verb, "target": target, "keywords": [target],
          "kind": extra.pop("kind", "button"), "value": extra.pop("value", ""),
          "from_steps": list(steps), "inspection_source": "live"}
    if locator:
        it["live_locator"] = dict(locator, verified=True, fallbacks=list(fallbacks or []))
    it.update(extra)
    return it


def _seq(*cases, story_id=7):
    out = []
    for i, (title, bucket, intents) in enumerate(cases):
        out.append({"title": title, "bucket": bucket, "priority": bucket * 100 + i,
                    "intents": intents, "explicit_login_flow": False,
                    "page_context": "app" if bucket == 3 else "login"})
    return [{"story": {"id": story_id}, "cases": out}]


SAVE = {"by": "id", "value": "save"}
LOGIN = {"url": "https://app.test/login", "user": "u1", "password": "p1"}


def _run(drv, sequenced, **kw):
    return engine.replay_sequenced(sequenced, "https://app.test/", LOGIN,
                                   make_driver=lambda: drv, By=By, wait=0, **kw)


class ReplayTests(unittest.TestCase):
    def test_case_passes_using_only_its_saved_locator(self):
        drv = Drv()
        drv.put(By.ID, "save", drv.el("save"))
        report = _run(drv, _seq(("Save works", 3, [_intent("action", locator=SAVE)])))
        self.assertEqual((report["passed"], report["failed"]), (1, 0))
        self.assertIn(("get", "https://app.test/"), drv.log)
        self.assertIn(("click", "save"), drv.log)
        self.assertTrue(drv.quit_called)

    def test_falls_back_to_a_proven_fallback_and_says_so(self):
        drv = Drv()
        drv.put(By.CSS_SELECTOR, '[data-testid="save"]', drv.el("save"))
        it = _intent("action", locator=SAVE, fallbacks=[{"by": "css", "value": '[data-testid="save"]'}])
        report = _run(drv, _seq(("Save works", 3, [it])))
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["cases"][0]["fallbacks_used"], 1)

    def test_missing_element_fails_that_case_only_and_names_the_step(self):
        drv = Drv()
        drv.put(By.ID, "next", drv.el("next"))
        bad = _intent("action", target="Delete", locator={"by": "id", "value": "gone"}, steps=(2, 3))
        after = _intent("action", target="Next", locator={"by": "id", "value": "next"}, steps=(4,))
        report = _run(drv, _seq(("Broken", 3, [bad, after]),
                                ("Fine", 3, [_intent("action", target="Next",
                                                     locator={"by": "id", "value": "next"})])))
        self.assertEqual((report["passed"], report["failed"]), (1, 1))
        failed = report["cases"][0]
        self.assertEqual((failed["passed"], failed["steps"], failed["reason"]),
                         (False, [2, 3], "not-found"))
        self.assertEqual(drv.log.count(("click", "next")), 1)   # rest of broken case skipped
        self.assertEqual(bad["replay_failed"], "not-found")

    def test_hidden_element_fails_a_visibility_assertion(self):
        drv = Drv()
        drv.put(By.ID, "msg", drv.el("msg", shown=False))
        report = _run(drv, _seq(("Msg", 3, [_intent("assertion", target="Saved",
                                                      locator={"by": "id", "value": "msg"})])))
        self.assertEqual(report["cases"][0]["reason"], "not-visible")

    def test_disappear_assertion_passes_when_nothing_is_displayed(self):
        drv = Drv()
        drv.put(By.ID, "row", drv.el("row", shown=False))
        it = _intent("assertion", target="Row", locator={"by": "id", "value": "row"},
                     assert_before_action=True)
        self.assertEqual(_run(drv, _seq(("Gone", 3, [it])))["passed"], 1)

    def test_text_assertion_without_a_locator_checks_page_text(self):
        drv = Drv(body="Changes saved successfully")
        it = _intent("assertion", target="saved", kind="message",
                     inspection_source="context")
        it["keywords"] = ["saved"]
        self.assertEqual(_run(drv, _seq(("Msg", 3, [it])))["passed"], 1)
        drv2 = Drv(body="Nothing here")
        self.assertEqual(_run(drv2, _seq(("Msg", 3, [dict(it)])))["failed"], 1)

    def test_login_case_logs_in_with_the_generated_login_steps_and_skips_its_actions(self):
        drv = Drv()
        drv.put(By.CSS_SELECTOR, LOGIN_FIELDS, drv.el("pw"))
        drv.put(By.CSS_SELECTOR, engine._REPLAY_USER, drv.el("user"))
        drv.put(By.CSS_SELECTOR, "#password,input[type=password]", drv.el("pw"))
        drv.put(By.CSS_SELECTOR, "#kc-login,button[type=submit],input[type=submit]", drv.el("submit"))
        drv.after_click["submit"] = lambda d: setattr(d, "current_url", "https://app.test/home")
        typed_action = _intent("action", verb="type", target="username", kind="input",
                               locator={"by": "id", "value": "never-used"})
        report = _run(drv, _seq(("Valid login", 2, [typed_action])))
        self.assertEqual(report["passed"], 1)
        self.assertIn(("type", "user", "u1"), drv.log)
        self.assertIn(("type", "pw", "p1"), drv.log)
        self.assertIn(("click", "submit"), drv.log)

    def test_unresolved_step_fails_without_searching(self):
        drv = Drv()
        it = _intent("action", target="Mystery", kind="any")
        it["inspection_unresolved"] = "guess"
        report = _run(drv, _seq(("Unknown", 3, [it])))
        self.assertEqual(report["cases"][0]["reason"], "unresolved")

    def test_stop_request_ends_the_replay(self):
        drv = Drv()
        drv.put(By.ID, "save", drv.el("save"))
        report = _run(drv, _seq(("A", 3, [_intent("action", locator=SAVE)])),
                      should_stop=lambda: True)
        self.assertTrue(report["stopped"])
        self.assertEqual(report["passed"] + report["failed"], 0)


class AppHomeReplayTests(unittest.TestCase):
    def test_app_case_logs_in_when_the_site_url_is_the_login_page(self):
        drv = Drv()
        pw = drv.el("pw")
        drv.put(By.CSS_SELECTOR, LOGIN_FIELDS, pw)
        drv.put(By.CSS_SELECTOR, engine._REPLAY_USER, drv.el("user"))
        drv.put(By.CSS_SELECTOR, "#password,input[type=password]", pw)
        drv.put(By.CSS_SELECTOR, "#kc-login,button[type=submit],input[type=submit]", drv.el("submit"))

        def _logged_in(d):
            d.current_url = "https://app.test/inventory"
            d.dom.pop((By.CSS_SELECTOR, LOGIN_FIELDS), None)
            d.put(By.ID, "save", d.el("save"))
        drv.after_click["submit"] = _logged_in
        report = _run(drv, _seq(("Save works", 3, [_intent("action", locator=SAVE)])))
        self.assertEqual(report["passed"], 1, report)
        self.assertIn(("click", "submit"), drv.log)
        self.assertIn(("click", "save"), drv.log)


class ReplaySkipMessageTests(unittest.TestCase):
    def test_incomplete_plan_says_why_the_replay_did_not_run(self):
        msgs = []
        payload = [{"story": {"id": 7}, "test_cases": [{"title": "T", "steps": [{}],
                     "inspection_plan": {"intents": [{"role": "action", "target": "X",
                                                      "inspection_source": "guess"}]}}]}]
        out = engine.replay_verify(payload, {}, "https://app.test/",
                                   cb=lambda m, *a: msgs.append(m))
        self.assertIsNone(out)
        self.assertTrue(any("Replay skipped" in m for m in msgs), msgs)


if __name__ == "__main__":
    unittest.main()
