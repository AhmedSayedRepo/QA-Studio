"""Phase A: every captured locator is PROVEN on the live page — it must match
exactly one element, and that element must be the one the explorer bound.
Fake driver, no browser."""
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

By = types.SimpleNamespace(ID="id", NAME="name", CSS_SELECTOR="css selector", XPATH="xpath")
TARGET_XPATH = "/html[1]/body[1]/form[1]/button[2]"


class FakeEl:
    def __init__(self, key, shown=True):
        self.key, self.shown = key, shown

    def is_displayed(self):
        return self.shown

    def __eq__(self, other):
        return isinstance(other, FakeEl) and other.key == self.key

    def __hash__(self):
        return hash(self.key)


class FakeDriver:
    """matches: {(by, value): [FakeEl, ...]}"""
    def __init__(self, matches):
        self.matches = matches
        self.calls = []

    def find_elements(self, by, value):
        self.calls.append((by, value))
        if (by, value) == ("raise", value):
            raise RuntimeError("invalid selector")
        return list(self.matches.get((by, value), []))


TARGET, OTHER = FakeEl("save"), FakeEl("other")


def _el(**over):
    el = {"tag": "button", "id": "", "name": "", "testid": "", "svgicon": "",
          "aria": "", "text": "Save", "css": "form > button:nth-of-type(2)",
          "xpath": TARGET_XPATH, "selectorCandidates": []}
    el.update(over)
    return el


class LocatorCandidateTests(unittest.TestCase):
    def test_stable_attributes_come_first_and_positional_xpath_is_never_a_candidate(self):
        cands = engine._locator_candidates(_el(testid="save-btn", id="saveBtn", name="save",
                                               aria="Save changes"))
        self.assertEqual(cands[0], {"by": "css", "value": '[data-testid="save-btn"]'})
        self.assertIn({"by": "id", "value": "saveBtn"}, cands)
        self.assertIn({"by": "name", "value": "save"}, cands)
        self.assertNotIn(TARGET_XPATH, [c["value"] for c in cands])

    def test_framework_generated_ids_are_skipped(self):
        cands = engine._locator_candidates(_el(id="pn_id_42"))
        self.assertNotIn("pn_id_42", [c["value"] for c in cands])


class LocatorProofTests(unittest.TestCase):
    def _drv(self, extra):
        base = {(By.XPATH, TARGET_XPATH): [TARGET]}
        base.update(extra)
        return FakeDriver(base)

    def test_skips_a_locator_that_matches_two_elements(self):
        el = _el(name="save", testid="save-btn")
        drv = self._drv({(By.NAME, "save"): [OTHER, TARGET],
                         (By.CSS_SELECTOR, '[data-testid="save-btn"]'): [TARGET]})
        loc = engine._prove_locator(drv, el, By)
        self.assertEqual((loc["by"], loc["value"]), ("css", '[data-testid="save-btn"]'))
        self.assertTrue(loc["verified"])
        self.assertNotIn({"by": "name", "value": "save"}, loc["fallbacks"])

    def test_rejects_a_unique_locator_that_points_at_a_different_element(self):
        el = _el(id="saveBtn", aria="Save")
        drv = self._drv({(By.ID, "saveBtn"): [OTHER],
                         (By.XPATH, '//*[@aria-label="Save"]'): [TARGET]})
        loc = engine._prove_locator(drv, el, By)
        self.assertEqual(loc["value"], '//*[@aria-label="Save"]')
        self.assertTrue(loc["verified"])

    def test_keeps_primary_plus_at_most_two_proven_fallbacks(self):
        el = _el(testid="s", id="saveBtn", name="save", aria="Save")
        drv = self._drv({(By.CSS_SELECTOR, '[data-testid="s"]'): [TARGET],
                         (By.ID, "saveBtn"): [TARGET], (By.NAME, "save"): [TARGET],
                         (By.XPATH, '//*[@aria-label="Save"]'): [TARGET]})
        loc = engine._prove_locator(drv, el, By)
        self.assertEqual(loc["value"], '[data-testid="s"]')
        self.assertEqual(loc["fallbacks"], [{"by": "id", "value": "saveBtn"},
                                            {"by": "name", "value": "save"}])

    def test_nothing_unique_keeps_todays_locator_but_marks_it_unverified(self):
        el = _el(name="save")
        drv = self._drv({(By.NAME, "save"): [TARGET, OTHER]})
        loc = engine._prove_locator(drv, el, By)
        legacy = engine._to_locator(el)
        self.assertEqual((loc["by"], loc["value"]), (legacy["by"], legacy["value"]))
        self.assertFalse(loc["verified"])
        self.assertEqual(loc["reason"], "not-unique")
        self.assertEqual(loc["fallbacks"], [])

    def test_target_that_cannot_be_located_is_unverified(self):
        el = _el(testid="s")
        drv = FakeDriver({(By.CSS_SELECTOR, '[data-testid="s"]'): [TARGET]})  # no xpath hit
        loc = engine._prove_locator(drv, el, By)
        self.assertFalse(loc["verified"])
        self.assertEqual(loc["reason"], "target-not-found")

    def test_target_without_xpath_falls_back_to_its_verified_css(self):
        el = _el(xpath="", css="#result", text="")
        drv = FakeDriver({(By.CSS_SELECTOR, "#result"): [TARGET]})
        loc = engine._prove_locator(drv, el, By)
        self.assertTrue(loc["verified"])
        self.assertEqual(loc["value"], "#result")

    def test_a_selector_the_browser_rejects_is_just_skipped(self):
        el = _el(testid="s", selectorCandidates=["::bad("])
        drv = self._drv({(By.CSS_SELECTOR, '[data-testid="s"]'): [TARGET]})
        drv.find_elements = (lambda by, value, _f=drv.find_elements:
                             (_ for _ in ()).throw(RuntimeError("bad")) if value == "::bad("
                             else _f(by, value))
        loc = engine._prove_locator(drv, el, By)
        self.assertTrue(loc["verified"])

    def test_without_a_driver_nothing_is_claimed(self):
        loc = engine._prove_locator(None, _el(testid="s"), By)
        self.assertFalse(loc["verified"])
        self.assertEqual(loc["reason"], "no-driver")


class ProofSummaryTests(unittest.TestCase):
    def test_counts_verified_and_lists_unverified_steps_with_reasons(self):
        payload = [{"story": {"id": 7}, "test_cases": [{"title": "Save works", "steps": [
            {"locator": {"by": "id", "value": "a", "verified": True}},
            {"locator": {"by": "name", "value": "b", "verified": False, "reason": "not-unique"},
             "assert_locator": {"by": "id", "value": "c", "verified": True}},
            {"locator": None},
            {"locator": {"by": "id", "value": "legacy"}},
        ]}]}]
        summary = engine._proof_summary(payload)
        self.assertEqual(summary["verified"], 2)
        self.assertEqual(summary["unverified"], [
            {"story": 7, "case": "Save works", "step": 2, "kind": "action",
             "reason": "not-unique"},
            {"story": 7, "case": "Save works", "step": 4, "kind": "action",
             "reason": "not-proven"},
        ])


if __name__ == "__main__":
    unittest.main()
