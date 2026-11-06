"""Task 7: generated Selenium tests carry the proven fallbacks (no AI needed
when the primary breaks), and generation is blocked when a case failed the
no-healing replay — unless the user explicitly allows it."""
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


def _intent(**extra):
    it = {"role": "action", "verb": "click", "target": "Save", "keywords": ["Save"],
          "kind": "button", "value": "", "from_steps": [1], "inspection_source": "live",
          "live_locator": {"by": "id", "value": "save", "verified": True,
                           "fallbacks": [{"by": "css", "value": '[data-testid="save"]'}]}}
    it.update(extra)
    return it


def _payload(intent):
    return [{"story": {"id": 7, "title": "S"}, "test_cases": [{
        "id": 1, "title": "Save works", "steps": [{"action": "click Save"}],
        "inspection_plan": {"intents": [intent], "case_type": "interaction",
                            "page_context": "app"}}]}]


class FallbackEmissionTests(unittest.TestCase):
    def test_proven_fallbacks_are_baked_into_the_java_seed(self):
        java, _ = engine.generate_selfhealing_test_class(
            {"id": 7}, [{"title": "Save works", "bucket": 3, "intents": [_intent()],
                         "ctype": "interaction", "page_context": "app"}], "com.qastudio")
        self.assertIn('Healer.anyOf(Healer.toBy("id", "save"), '
                      'Healer.toBy("cssSelector", "[data-testid=\\"save\\"]"))', java)

    def test_without_fallbacks_the_seed_is_unchanged(self):
        it = _intent()
        it["live_locator"] = {"by": "id", "value": "save"}
        java, _ = engine.generate_selfhealing_test_class(
            {"id": 7}, [{"title": "Save works", "bucket": 3, "intents": [it],
                         "ctype": "interaction", "page_context": "app"}], "com.qastudio")
        self.assertIn('Healer.toBy("id", "save")', java)
        self.assertNotIn("anyOf", java)

    def test_healer_provides_anyOf_backed_by_selenium_ByAll(self):
        healer = engine._sh_healer("com.qastudio")
        self.assertIn("import org.openqa.selenium.support.pagefactory.ByAll;", healer)
        self.assertIn("public static By anyOf(By... bys)", healer)


class ReplayGateTests(unittest.TestCase):
    def test_a_step_that_failed_replay_blocks_generation(self):
        self.assertIsNone(engine.sequence_verified_inspection_plans(
            _payload(_intent(replay_failed="not-found"))))

    def test_user_can_explicitly_allow_it(self):
        out = engine.sequence_verified_inspection_plans(
            _payload(_intent(replay_failed="not-found")), allow_unverified=True)
        self.assertEqual(len(out), 1)

    def test_clean_replay_is_not_blocked(self):
        self.assertIsNotNone(engine.sequence_verified_inspection_plans(_payload(_intent())))


if __name__ == "__main__":
    unittest.main()
