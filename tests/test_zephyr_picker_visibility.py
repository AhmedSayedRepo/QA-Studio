"""Jira + Zephyr Scale stays hidden from the backend picker (its auth isn't
proven yet) except when it is already selected, or for a developer who sets
QA_SHOW_ZEPHYR=1 to test it."""
import os
import unittest
from unittest.mock import patch

import backend_setup as B


class ZephyrPickerVisibilityTests(unittest.TestCase):
    def test_hidden_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QA_SHOW_ZEPHYR", None)
            self.assertIn(B.JIRA_ZEPHYR, B._picker_hidden(B.AZURE))

    def test_shown_while_it_is_the_selected_backend(self):
        os.environ.pop("QA_SHOW_ZEPHYR", None)
        self.assertNotIn(B.JIRA_ZEPHYR, B._picker_hidden(B.JIRA_ZEPHYR))

    def test_developer_switch_shows_it(self):
        with patch.dict(os.environ, {"QA_SHOW_ZEPHYR": "1"}):
            self.assertNotIn(B.JIRA_ZEPHYR, B._picker_hidden(B.AZURE))

    def test_standalone_testrail_stays_hidden_either_way(self):
        with patch.dict(os.environ, {"QA_SHOW_ZEPHYR": "1"}):
            self.assertIn(B.TESTRAIL, B._picker_hidden(B.AZURE))


if __name__ == "__main__":
    unittest.main()
