"""Jira backends: the story picker uses the project's own story-level issue
types, and falls back to the open backlog (saying so) when the board has no
active sprint or the sprint holds no stories."""
import types
import unittest
from unittest import mock

import backend_setup as B
import strings
from tracker.jira_zephyr import JiraZephyrBackend
from tracker.models import Project, Ref, Sprint, Story


class FakeJira:
    def __init__(self, issue_types=None, issues=None):
        self.issue_types = issue_types
        self.issues = issues or []
        self.jqls = []

    def get(self, path, params=None, **kw):
        if path.startswith("api/3/project/"):
            if self.issue_types is None:
                raise RuntimeError("no access")
            return {"issueTypes": self.issue_types}
        if path == "api/3/search/jql":
            self.jqls.append(params["jql"])
            return {"issues": self.issues, "isLast": True}
        return {}


def issue(key, summary="S"):
    return {"id": "1" + key[-1], "key": key, "fields": {"summary": summary, "status": {"name": "To Do"}}}


class IssueTypes(unittest.TestCase):
    def test_project_types_used_in_jql(self):
        jira = FakeJira(issue_types=[
            {"name": "Feature", "hierarchyLevel": 0}, {"name": "Task", "hierarchyLevel": 0},
            {"name": "Bug", "hierarchyLevel": 0}, {"name": "Epic", "hierarchyLevel": 1},
            {"name": "Sub-task", "subtask": True, "hierarchyLevel": -1}, {"name": "Test", "hierarchyLevel": 0}],
            issues=[issue("SS-1")])
        be = JiraZephyrBackend(site="https://acme.atlassian.net", jira=jira, zephyr=object())
        self.assertEqual(be._story_issue_types("SS"), ("Feature", "Task"))
        be.fetch_stories_in_sprint("SS", "42")
        self.assertIn('issuetype in ("Feature", "Task")', jira.jqls[-1])
        self.assertIn('sprint = 42 ', jira.jqls[-1])

    def test_falls_back_to_classic_types(self):
        be = JiraZephyrBackend(site="https://acme.atlassian.net", jira=FakeJira(issue_types=None), zephyr=object())
        self.assertEqual(be._story_issue_types("SS"), ("Story", "User Story", "Task"))

    def test_backlog_query(self):
        jira = FakeJira(issue_types=[{"name": "Story", "hierarchyLevel": 0}], issues=[issue("SS-7", "Login")])
        be = JiraZephyrBackend(site="https://acme.atlassian.net", jira=jira, zephyr=object())
        got = be.fetch_backlog_stories("SS", 50)
        self.assertEqual([s.ref.key for s in got], ["SS-7"])
        self.assertIn("statusCategory != Done", jira.jqls[-1])
        self.assertIn('issuetype in ("Story")', jira.jqls[-1])


class StoryPickerSource(unittest.TestCase):
    def _run(self, sprints, sprint_stories, backlog):
        be = types.SimpleNamespace(
            fetch_sprints=lambda p: sprints,
            fetch_stories_in_sprint=lambda p, path: sprint_stories,
            fetch_backlog_stories=lambda p, n: backlog)
        app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS")
        proj = Project(ref=Ref(id="1", key="SS"), name="SS")
        with mock.patch.object(B, "reads_stories_from_azure", return_value=False), \
                mock.patch.object(B, "get_backend", return_value=be), \
                mock.patch.object(B, "_as_project", return_value=proj):
            got = B.fetch_stories_for_plan(app, "27313685")
        return got, app._stories_source_note

    def story(self, key):
        return Story(ref=Ref(id=key, key=key), title="T " + key)

    def test_sprint_stories_win(self):
        got, note = self._run([Sprint(ref=Ref(id="1"), name="S1", path="1", state="active")],
                              [self.story("SS-1")], [self.story("SS-9")])
        self.assertEqual([g["id"] for g in got], ["SS-1"])
        self.assertEqual(note, "")

    def test_no_sprint_uses_backlog(self):
        got, note = self._run([], [], [self.story("SS-9")])
        self.assertEqual([g["id"] for g in got], ["SS-9"])
        self.assertEqual(note, strings.t("stories_note_backlog_no_sprint"))

    def test_empty_sprint_uses_backlog(self):
        got, note = self._run([Sprint(ref=Ref(id="1"), name="S1", path="1", state="active")], [],
                              [self.story("SS-9")])
        self.assertEqual(note, strings.t("stories_note_backlog_empty_sprint"))

    def test_nothing_anywhere(self):
        got, note = self._run([], [], [])
        self.assertEqual(got, [])
        self.assertEqual(note, strings.t("stories_note_none", project="SS"))

    def test_strings_in_every_language(self):
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            for k in ("stories_note_backlog_no_sprint", "stories_note_backlog_empty_sprint", "stories_note_none"):
                self.assertTrue(strings._STRINGS[lang].get(k), lang + " " + k)
            self.assertIn("{project}", strings._STRINGS[lang]["stories_note_none"])


if __name__ == "__main__":
    unittest.main()
