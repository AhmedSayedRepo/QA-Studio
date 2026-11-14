"""Pause holds the live inspection/replay between test cases, and a blocked
run still replays (and remembers) its fully verified cases."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402


class PauseAndPartialReplay(unittest.TestCase):
    def test_walk_and_replay_use_the_pause_gate(self):
        main = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
        self.assertEqual(main.count("should_stop=lambda: not self._auto_gate(), headless=False)"), 2)

    def test_pause_resume_swap_buttons_in_place(self):
        main = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
        a = main.index("    def _pause_automation(self):")
        b = main.index("    def _auto_gate(self):")
        self.assertEqual(main[a:b].count("self._auto_refresh_buttons()"), 2)
        self.assertNotIn("self.render()", main[a:b])

    def test_partial_replay_picks_only_fully_verified_cases(self):
        good = {"intents": [{"role": "action", "inspection_source": "live",
                             "live_locator": {"by": "css", "value": "#a"}}]}
        bad = {"intents": [{"role": "assertion", "inspection_source": "guess"}]}
        self.assertTrue(E._plan_fully_verified(good))
        self.assertFalse(E._plan_fully_verified(bad))
        import inspect
        src = inspect.getsource(E.replay_verify)
        self.assertIn("_plan_fully_verified(tc.get(\"inspection_plan\"))", src)


if __name__ == "__main__":
    unittest.main()
