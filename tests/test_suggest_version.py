"""release.bat's version suggestion: next patch after the last PUBLISHED release,
so a VERSION bumped by an aborted run isn't skipped (3.9.3 was, today)."""
import subprocess
import sys
import os
import unittest

import _suggest_version as sv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SuggestVersionTests(unittest.TestCase):
    def test_reuses_the_number_an_aborted_run_bumped_but_never_published(self):
        self.assertEqual(sv.suggest("3.9.3", "v3.9.2"), "3.9.3")

    def test_normal_case_is_next_patch(self):
        self.assertEqual(sv.suggest("3.9.4", "v3.9.4"), "3.9.5")

    def test_patch_rolls_past_nine_numerically(self):
        self.assertEqual(sv.suggest("3.8.9", "v3.8.9"), "3.8.10")

    def test_latest_release_above_version_file_is_ignored(self):
        # stray releases/tags such as v4.6.3 must not drag the suggestion up
        self.assertEqual(sv.suggest("3.9.4", "v4.6.3"), "3.9.5")

    def test_falls_back_to_version_file_without_a_release(self):
        self.assertEqual(sv.suggest("3.9.4", "(none)"), "3.9.5")
        self.assertEqual(sv.suggest("3.9.4", ""), "3.9.5")

    def test_unparseable_input_gives_no_suggestion(self):
        self.assertEqual(sv.suggest("(none)", "(none)"), "")

    def test_cli_prints_only_the_suggestion(self):
        out = subprocess.run([sys.executable, "_suggest_version.py", "3.9.3", "v3.9.2"],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "3.9.3"))


if __name__ == "__main__":
    unittest.main()
