"""Automation and Performance open without a tracker connection: a local
test-case file needs none. Only the tracker source needs Setup's connection."""
import types
import unittest

import backend_setup as B


def _app(connected=False, project=None, mode="backend", readonly=False):
    return types.SimpleNamespace(connected=connected, project=project,
                                 auto_source_mode=mode, readonly=readonly)


class TrackerAvailabilityTests(unittest.TestCase):
    def test_connected_with_a_project_has_the_tracker(self):
        self.assertTrue(B.tracker_available(_app(True, "Proj")))

    def test_not_connected_or_no_project_has_no_tracker(self):
        self.assertFalse(B.tracker_available(_app(False, "Proj")))
        self.assertFalse(B.tracker_available(_app(True, None)))

    def test_read_only_viewer_keeps_its_previous_access(self):
        self.assertTrue(B.tracker_available(_app(readonly=True)))


class AutomationSourceTests(unittest.TestCase):
    def test_without_a_tracker_the_file_source_is_used_even_if_backend_was_saved(self):
        self.assertTrue(B.automation_uses_file_source(_app(mode="backend")))

    def test_connected_respects_the_saved_choice(self):
        self.assertFalse(B.automation_uses_file_source(_app(True, "P", mode="backend")))
        self.assertTrue(B.automation_uses_file_source(_app(True, "P", mode="file")))


class SingleSourceDecisionTests(unittest.TestCase):
    """The form and the background worker must decide the source the same way;
    a worker reading the saved choice directly ran Azure while the form showed
    Local file (seen live: 'Connecting to Azure DevOps…' with no connection)."""

    def test_no_screen_or_worker_reads_the_saved_source_choice_directly(self):
        import os
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pattern = re.compile(r'auto_source_mode["\'],\s*["\'][a-z]+["\']\)\s*==\s*["\']file')
        for name in ("main.py", "automation.py", "regression.py"):
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                hits = [i for i, line in enumerate(fh, 1) if pattern.search(line)]
            self.assertEqual(hits, [], "%s reads auto_source_mode directly at lines %s; "
                                       "use backend_setup.automation_uses_file_source" % (name, hits))


if __name__ == "__main__":
    unittest.main()
