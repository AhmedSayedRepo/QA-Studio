"""Live case counter in the Activity toolbar ("Inspecting 3 / 31"): parsed from
the run's own log lines and shown INSIDE the fixed-height toolbar row, so it
never takes height from the log rail."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import run_progress as R  # noqa: E402


def _feed(*lines):
    st = R.new_state()
    for ln in lines:
        R.update(st, ln)
    return R.text(st)


class Counter(unittest.TestCase):
    LOADED = "Loaded 1 story/stories - 31 test case(s) - 99 step(s) from the local test-case file."

    def test_inspection_of_every_case(self):
        self.assertEqual(_feed(self.LOADED, "  walking 'A'  [x]", "  walking 'B'  [x]"), "Inspecting 2 / 31")

    def test_inspection_of_only_changed_cases(self):
        self.assertEqual(_feed(self.LOADED, "Reusing 28 case(s) verified in an earlier run — inspecting 3 "
                                            "new, changed or failed case(s).", "  walking 'A'  [x]"),
                         "Inspecting 1 / 3")

    def test_sequencing_and_replay(self):
        self.assertEqual(_feed(self.LOADED, "Sequencing case 5/31"), "Sequencing 5 / 31")
        self.assertEqual(_feed(self.LOADED, "  walking 'A'  [x]", "Replaying the 2 fully verified case(s) so",
                               "    ✓ replay: A", "    ✗ replay: B"), "Replaying 2 / 2")

    def test_nothing_before_the_first_case(self):
        self.assertEqual(_feed(self.LOADED, "Launching Chrome…"), "")


class CounterKeepsLogHeight(unittest.TestCase):
    def test_counter_lives_in_the_fixed_toolbar_row(self):
        src = open(os.path.join(ROOT, "automation.py"), encoding="utf-8").read()
        a = src.index("log_toolbar = ft.Container(")
        row = src[a:src.index("border=ft.Border.only(bottom=", a)]
        self.assertIn("app._auto_case_ctr,", row)
        self.assertIn("ft.Container(log_toolbar, height=50)", src)

    def test_updates_in_place(self):
        src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
        a = src.index("    def _auto_refresh_progress(self):")
        seg = src[a:src.index("    def _auto_logmsg(self", a)]
        self.assertNotIn("render()", seg)
        self.assertIn("self._auto_track_progress(msg)", src)


if __name__ == "__main__":
    unittest.main()
