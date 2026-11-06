"""Two live-run findings (SauceDemo):
1. A page title that keeps its position but changes text ("Checkout: Your
   Information" -> "Checkout: Overview") must count as NEW for DOM-diff
   assertion binding.
2. "Click Continue without filling the form" compiled to an empty 'type First
   Name' step; the walk typed 'test' because only the compiled target was
   checked for leave-empty wording, not the authored step."""
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


def _el(text, css="span.title:nth-of-type(1)", **kw):
    e = {"id": "", "name": "", "css": css, "xpath": "/html/body/span[1]", "text": text}
    e.update(kw)
    return e


class DomDiffTests(unittest.TestCase):
    def test_same_element_with_changed_text_counts_as_new(self):
        before = [_el("Checkout: Your Information"), _el("Cancel", css="#cancel")]
        after = [_el("Checkout: Overview"), _el("Cancel", css="#cancel")]
        new = engine._new_since(engine._diff_keys(before), after)
        self.assertEqual([e["text"] for e in new], ["Checkout: Overview"])

    def test_unchanged_elements_are_not_new(self):
        page = [_el("Products"), _el("Cart", css="#cart")]
        self.assertEqual(engine._new_since(engine._diff_keys(page), list(page)), [])

    def test_newly_appeared_element_is_new(self):
        before = [_el("Products")]
        after = [_el("Products"), _el("1", css="span.badge")]
        self.assertEqual([e["text"] for e in engine._new_since(engine._diff_keys(before), after)],
                         ["1"])


class LeaveEmptyTests(unittest.TestCase):
    STEPS = [{"action": 'Click the shopping cart icon'},
             {"action": 'Click the "Checkout" button'},
             {"action": 'Click the "Continue" button without filling the form'}]

    def _it(self, value="", steps=(3,), target="First Name field"):
        return {"verb": "type", "target": target, "keywords": ["First Name"],
                "value": value, "from_steps": list(steps)}

    def test_authored_step_wording_means_leave_the_field_empty(self):
        self.assertTrue(engine._type_leaves_empty(self._it(), self.STEPS))

    def test_a_typed_value_is_never_left_empty(self):
        self.assertFalse(engine._type_leaves_empty(self._it(value="Ahmed"), self.STEPS))

    def test_plain_type_step_without_a_value_is_not_left_empty(self):
        steps = [{"action": 'Enter a first name'}]
        self.assertFalse(engine._type_leaves_empty(self._it(steps=(1,)), steps))

    def test_target_wording_still_counts(self):
        self.assertTrue(engine._type_leaves_empty(
            self._it(target="empty password field", steps=(9,)), self.STEPS))


class QuotedExpectedSplitTests(unittest.TestCase):
    """'The "Your Cart" page shows "Sauce Labs Fleece Jacket"' states two facts
    held by two elements (title + product). When no single element matches,
    the walk splits on the authored quoted phrases."""

    def _it(self, expected):
        return {"role": "assertion", "target": "product listed on Your Cart page",
                "keywords": ["Your Cart", "Sauce Labs", "Fleece Jacket"], "kind": "text",
                "check": "text_visible", "expected": expected, "from_steps": [2],
                "inspection_source": "guess", "inspection_unresolved": "guess"}

    def test_two_quoted_phrases_become_two_exact_checks(self):
        parts = engine._split_by_quoted_expected(
            self._it('The "Your Cart" page shows "Sauce Labs Fleece Jacket"'))
        self.assertEqual([p["keywords"] for p in parts],
                         [["Your Cart"], ["Sauce Labs Fleece Jacket"]])
        self.assertEqual([p["target"] for p in parts], ["Your Cart", "Sauce Labs Fleece Jacket"])
        for p in parts:
            self.assertEqual(p["from_steps"], [2])
            self.assertNotIn("inspection_unresolved", p)
            self.assertNotIn("inspection_source", p)

    def test_arabic_and_curly_quotes_count(self):
        parts = engine._split_by_quoted_expected(self._it("تظهر «سلة التسوق» وتعرض “Fleece Jacket”"))
        self.assertEqual([p["keywords"] for p in parts], [["سلة التسوق"], ["Fleece Jacket"]])

    def test_one_quoted_phrase_becomes_one_exact_check(self):
        parts = engine._split_by_quoted_expected(self._it('The "Products" title shows'))
        self.assertEqual([p["target"] for p in parts], ["Products"])
        self.assertEqual(engine._split_by_quoted_expected(self._it("The cart page lists it")), [])

    def test_phrase_equal_to_target_is_not_split(self):
        it = self._it('The "Products" title shows')
        it["target"] = "Products"
        self.assertEqual(engine._split_by_quoted_expected(it), [])

    def test_compiler_dropped_quotes_uses_authored_expected(self):
        # Seen live (Playwright run): compiled expected lost its quotes.
        it = self._it("Sauce Labs Fleece Jacket on Your Cart page")
        steps = [{"action": 'Click "Add to cart" for "Sauce Labs Fleece Jacket"', "expected": ""},
                 {"action": "Click the shopping cart icon",
                  "expected": 'The "Your Cart" page shows "Sauce Labs Fleece Jacket"'}]
        parts = engine._split_by_quoted_expected(it, steps)
        self.assertEqual([p["target"] for p in parts], ["Your Cart", "Sauce Labs Fleece Jacket"])
        # Without the source steps there is nothing to split on.
        self.assertEqual(engine._split_by_quoted_expected(it), [])

    def test_action_quotes_are_not_used(self):
        it = self._it("product is listed")
        steps = [{"action": 'Click "Add to cart"', "expected": ""},
                 {"action": "Open cart", "expected": "the product is listed"}]
        self.assertEqual(engine._split_by_quoted_expected(it, steps), [])

    def test_negative_assertions_are_never_split_this_way(self):
        it = self._it('"Remove" disappears and "Add to cart" shows')
        it["check"] = "hidden"
        self.assertEqual(engine._split_by_quoted_expected(it), [])

    def test_real_cart_page_binds_both_parts(self):
        pool = [{"tag": "span", "cls": "title", "text": "Your Cart", "id": "", "css": "span.title"},
                {"tag": "div", "cls": "inventory_item_name", "text": "Sauce Labs Backpack", "id": "", "css": "a"},
                {"tag": "div", "cls": "inventory_item_name", "text": "Sauce Labs Fleece Jacket", "id": "", "css": "b"}]
        whole = self._it('The "Your Cart" page shows "Sauce Labs Fleece Jacket"')
        self.assertEqual(engine._rank_candidates(whole, pool), [])
        texts = [engine._rank_candidates(p, pool)[0][1]["text"]
                 for p in engine._split_by_quoted_expected(whole)]
        self.assertEqual(texts, ["Your Cart", "Sauce Labs Fleece Jacket"])


if __name__ == "__main__":
    unittest.main()
