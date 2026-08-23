"""Focused regression tests for the local AI Usage estimate catalogue."""
import unittest

import engine


class UsagePricingTests(unittest.TestCase):
    def test_new_direct_provider_entries_have_documented_rates(self):
        self.assertEqual(engine.price_for("openai", "gpt-4.1"), {"in": 2.0, "out": 8.0})
        self.assertEqual(engine.price_for("gemini", "gemini-2.5-flash"), {"in": 0.3, "out": 2.5})
        self.assertEqual(engine.price_for("minimax", "MiniMax-M2"), {"in": 0.3, "out": 1.2})
        self.assertEqual(engine.price_for("openai", "gpt-5.6-terra"), {"in": 2.0, "out": 12.0})

    def test_explicitly_free_models_report_zero_not_unknown(self):
        self.assertEqual(engine.price_for("ollama", "any-local-model"), {"in": 0.0, "out": 0.0})
        self.assertEqual(
            engine.price_for("openrouter", "meta-llama/llama-3.3-70b-instruct:free"),
            {"in": 0.0, "out": 0.0},
        )

    def test_unknown_model_stays_unpriced(self):
        self.assertIsNone(engine.price_for("openai", "gpt-unpublished-example"))
        self.assertIsNone(engine._call_cost("openai", "gpt-unpublished-example", 1000, 1000))

    def test_cost_uses_exact_token_counts_and_catalogue_rate(self):
        self.assertEqual(engine._call_cost("openai", "gpt-4.1", 1_000_000, 1_000_000), 10.0)


if __name__ == "__main__":
    unittest.main()
