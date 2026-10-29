"""Default/offered AI models must be ones the providers still serve, and a
retired model id saved in a user's settings is upgraded on load."""
import copy
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

RETIRED = ("deepseek-chat", "deepseek-reasoner", "gemini-1.5-pro",
           "gemini-1.5-flash", "gemini-2.0-flash")


class DefaultModelTests(unittest.TestCase):
    def test_defaults_are_current_models(self):
        self.assertEqual(engine.AI_CONFIG["deepseek"]["model"], "deepseek-flash")
        self.assertEqual(engine.AI_CONFIG["gemini"]["model"], "gemini-3.8-flash")

    def test_offered_fallback_lists_contain_no_retired_models(self):
        for provider in ("deepseek", "gemini"):
            for mid in engine.STATIC_MODELS[provider]:
                self.assertNotIn(mid, RETIRED, provider)
        self.assertEqual(engine.STATIC_MODELS["deepseek"][0], "deepseek-flash")
        self.assertEqual(engine.STATIC_MODELS["gemini"][0], "gemini-3.8-flash")


class RetiredSavedModelTests(unittest.TestCase):
    def setUp(self):
        self._cfg = copy.deepcopy(engine.AI_CONFIG)
        self._provider = engine.AI_PROVIDER

    def tearDown(self):
        engine.AI_CONFIG.clear()
        engine.AI_CONFIG.update(self._cfg)
        engine.AI_PROVIDER = self._provider

    def _load(self, provider, model):
        engine.set_credentials(provider=provider, model=model)
        return engine.AI_CONFIG[provider]["model"]

    def test_saved_deepseek_aliases_are_upgraded(self):
        self.assertEqual(self._load("deepseek", "deepseek-chat"), "deepseek-flash")
        self.assertEqual(self._load("deepseek", "deepseek-reasoner"), "deepseek-v4-pro")

    def test_saved_retired_gemini_models_are_upgraded(self):
        self.assertEqual(self._load("gemini", "gemini-1.5-pro"), "gemini-3.8-flash")
        self.assertEqual(self._load("gemini", "gemini-1.5-flash"), "gemini-3.5-flash-lite")
        self.assertEqual(self._load("gemini", " gemini-2.0-flash "), "gemini-3.6-flash")

    def test_current_and_unknown_models_are_kept_as_saved(self):
        self.assertEqual(self._load("gemini", "gemini-2.5-pro"), "gemini-2.5-pro")
        self.assertEqual(self._load("deepseek", "deepseek-v4-pro"), "deepseek-v4-pro")
        self.assertEqual(self._load("openai", "deepseek-chat"), "deepseek-chat")


class PricingTests(unittest.TestCase):
    def test_new_gemini_defaults_have_published_prices(self):
        self.assertEqual(engine.price_for("gemini", "gemini-3.8-flash"), {"in": 0.75, "out": 3.75})
        self.assertEqual(engine.price_for("gemini", "gemini-3.5-flash-lite"), {"in": 0.3, "out": 2.5})

    def test_unpublished_deepseek_flash_price_is_not_guessed(self):
        self.assertIsNone(engine.price_for("deepseek", "deepseek-flash"))


if __name__ == "__main__":
    unittest.main()
