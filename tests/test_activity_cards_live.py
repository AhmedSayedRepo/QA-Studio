"""The Automation activity cards (Proven / Replay / Review / Skipped) update in
place while a run is going, not only at the next full render."""
import ast
import os
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _methods(*names):
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    src = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            src[node.name] = textwrap.dedent(ast.get_source_segment(open(os.path.join(ROOT, "main.py"),
                                                                         encoding="utf-8").read(), node))
    ns = {}
    for name in names:
        exec(src[name], ns)
    return ns


class _Num:
    def __init__(self):
        self.value, self.updates = "", 0

    def update(self):
        self.updates += 1


class LiveCards(unittest.TestCase):
    def setUp(self):
        ns = _methods("_auto_count", "_auto_push_counts", "_auto_live_stat")
        App = type("App", (), {k: ns[k] for k in ("_auto_count", "_auto_push_counts", "_auto_live_stat")})
        self.app = App()
        self.app.ui_safe = lambda fn: fn()
        self.app._auto_stats, self.app._auto_todo, self.app._auto_skipped = {}, 0, 0
        self.app._auto_count_ctl = {k: _Num() for k in ("proven", "replay", "todo", "skipped")}

    def test_replay_moves_with_each_case(self):
        a = self.app
        a._auto_live_stat("Replaying every case with its saved locators only (no healing)…")
        a._auto_live_stat("    ✓ replay: Login page looks the same")
        self.assertEqual(a._auto_count_ctl["replay"].value, "1/1")
        a._auto_live_stat("    ✗ replay: Checkboxes — step 2 click (not found)")
        self.assertEqual(a._auto_count_ctl["replay"].value, "1/2")

    def test_proven_from_the_locator_proof_line(self):
        self.app._auto_live_stat("Locator proof — 6 proven unique on the live page, 0 not proven.")
        self.assertEqual(self.app._auto_count_ctl["proven"].value, "6")

    def test_other_lines_change_nothing(self):
        self.assertFalse(self.app._auto_live_stat("walking 'Login'"))
        self.assertEqual(self.app._auto_count_ctl["replay"].updates, 0)

    def test_phase_totals_are_pushed(self):
        with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertGreaterEqual(src.count("self._auto_push_counts()"), 4)
        self.assertIn("self._auto_live_stat(msg)", src)


if __name__ == "__main__":
    unittest.main()
