"""Jira sprints: read from the project's OWN board(s) (not just the first
board whose filter includes the project), ordered active → future → closed,
shown with their full names; and the rail's connection card refreshes on a
preserved render."""
import os
import types
import unittest
from datetime import date
from unittest import mock

import backend_setup as B
import strings
from tracker.jira_zephyr import JiraZephyrBackend
from tracker.models import Project, Ref

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeJira:
    def __init__(self, boards, sprints):
        self.boards, self.sprints = boards, sprints

    def get(self, path, params=None, **kw):
        if path == "agile/1.0/board":
            if params.get("type") == "scrum":
                return {"values": self.boards}
            return {"values": []}
        return {}

    def paginate(self, path, extract=None, **kw):
        bid = int(path.split("/")[3])
        return iter(self.sprints.get(bid, []))


BOARDS = [
    {"id": 7, "name": "Old Pensions Board", "location": {"projectKey": "PEN"}},
    {"id": 31, "name": "Smart Services Board", "location": {"projectKey": "SS"}},
    {"id": 40, "name": "SS Support Board", "location": {"projectKey": "ss"}},
]
SPRINTS = {
    7: [{"id": 423, "name": "Sprint 1", "state": "closed", "endDate": "2023-01-10T00:00:00.000Z"}],
    31: [{"id": 900, "name": "SS Sprint 13", "state": "closed", "endDate": "2026-10-30T00:00:00.000Z"},
         {"id": 950, "name": "SS Sprint 14 (Pensions)", "state": "active", "startDate": "2026-11-15T00:00:00.000Z"},
         {"id": 990, "name": "SS Sprint 15", "state": "future"},
         {"id": 880, "name": "SS Sprint 12", "state": "closed", "endDate": "2026-10-10T00:00:00.000Z"}],
    40: [{"id": 950, "name": "SS Sprint 14 (Pensions)", "state": "active"},
         {"id": 961, "name": "Support Sprint 3", "state": "closed", "endDate": "2026-11-01T00:00:00.000Z"}],
}


class Sprints(unittest.TestCase):
    def setUp(self):
        self.be = JiraZephyrBackend(site="https://acme.atlassian.net",
                                    jira=FakeJira(BOARDS, SPRINTS), zephyr=object())

    def test_only_the_projects_own_boards(self):
        self.assertEqual(self.be._boards_for("SS"), [(31, "Smart Services Board"), (40, "SS Support Board")])

    def test_sprints_ordered_and_deduplicated(self):
        sp = self.be.fetch_sprints("SS")
        self.assertEqual([s.path for s in sp], ["950", "990", "961", "900", "880"])
        self.assertEqual(sp[0].state, "active")
        self.assertEqual(sp[0].start, date(2026, 11, 15))
        self.assertNotIn("423", [s.path for s in sp])        # another project's board
        self.assertEqual(self.be.sprint_board("961"), "SS Support Board")

    def test_no_own_board_keeps_the_first(self):
        be = JiraZephyrBackend(site="https://acme.atlassian.net",
                               jira=FakeJira(BOARDS[:1], SPRINTS), zephyr=object())
        self.assertEqual([s.path for s in be.fetch_sprints("SS")], ["423"])

    def test_labels_full_names_board_and_active(self):
        app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS")
        with mock.patch.object(B, "get_backend", return_value=self.be), \
                mock.patch.object(B, "_as_project", return_value=Project(ref=Ref(id="1", key="SS"), name="SS")):
            rows = B.fetch_sprints(app, "SS")
        first = rows[0]
        self.assertEqual(first["name"], "SS Sprint 14 (Pensions)")
        self.assertEqual(first["label"], "SS Sprint 14 (Pensions)  ·  Smart Services Board  ·  "
                         + strings.t("sprint_state_active"))
        self.assertEqual(first["state"], "active")
        self.assertTrue(all(r["label"] for r in rows))

    def test_sprint_ids_are_not_quoted_in_jql(self):
        # Live site: `sprint = 526` found 200 issues, `sprint = "526"` found 0.
        self.assertEqual(JiraZephyrBackend._sprint_clause("526"), "sprint = 526")
        self.assertEqual(JiraZephyrBackend._sprint_clause("Sprint 4"), 'sprint = "Sprint 4"')

    def test_strings(self):
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            self.assertTrue(strings._STRINGS[lang].get("sprint_state_active"), lang)


class Wiring(unittest.TestCase):
    def test_pickers_use_the_label(self):
        reg = open(os.path.join(ROOT, "regression.py"), encoding="utf-8").read()
        tm = open(os.path.join(ROOT, "task_manager.py"), encoding="utf-8").read()
        md = open(os.path.join(ROOT, "modals.py"), encoding="utf-8").read()
        self.assertIn('it.get("label") or', reg)
        self.assertIn('it.get("label") or _sprint_num', tm)
        self.assertIn('text=it.get("label") or it["path"]', md)

    def test_rail_conn_card_refreshes_on_preserved_render(self):
        src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
        i = src.index('self._rail_scroll.controls = nav_items')
        self.assertIn("card.content = self._rail_conn_row()", src[i:i + 600])
        self.assertIn("def _rail_conn_row(self):", src)


if __name__ == "__main__":
    unittest.main()
