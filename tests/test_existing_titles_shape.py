"""Non-Azure generation must see the story's existing test cases.

engine reads each existing item as `it.get("title")`; adapters return plain
strings, which used to make that raise (swallowed) - so every run asked the AI
for a full new set. Jira + Zephyr also counts cases linked to the story outside
its folder."""
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import backend_setup as B  # noqa: E402
import engine as E  # noqa: E402
from tracker.jira_zephyr import JiraZephyrBackend  # noqa: E402
from tracker.models import Ref, Story, Suite  # noqa: E402


def _engine_read(items):
    """The exact loop engine.run_titles uses (kept in sync by test_engine_loop)."""
    out = []
    for it in items:
        nm = (it.get("title") or "").strip()
        if nm:
            out.append(nm)
    return out


class _Be:
    def __init__(self, name, linked=None):
        self.name = name
        self.linked_calls = []
        self._linked = linked or []

    def fetch_stories(self, refs):
        return [Story(ref=Ref(id="10501", key="SS-30552"), title="Debt certificate")]

    def ensure_suite_for_story(self, proj, plan, story):
        return Suite(ref=Ref(id="77"), name="SS-30552 Debt certificate")

    def fetch_existing_titles_for_suite(self, proj, plan, suite):
        return ["Verify A", "verify b", ""]

    def linked_test_case_titles(self, key):
        self.linked_calls.append(key)
        return list(self._linked)


def _ops(be, backend="jira_zephyr"):
    app = types.SimpleNamespace(creds={"backend": backend}, project="SS")
    with mock.patch.object(B, "is_azure", return_value=False), \
            mock.patch.object(B, "get_backend", return_value=be), \
            mock.patch.object(B, "_as_project", side_effect=lambda b, p: p), \
            mock.patch.object(B, "plan_name_for", return_value="P"):
        return B.generation_ops(app)


class ExistingTitles(unittest.TestCase):
    def test_engine_loop(self):
        src = open(os.path.join(ROOT, "engine.py"), encoding="utf-8").read()
        self.assertIn('for it in _o.existing_titles(project, plan_id, suite_id):\n'
                      '                nm = (it.get("title") or "").strip()', src)

    def test_dict_shape_and_linked_cases(self):
        be = _Be("jira_zephyr", linked=["VERIFY A", "Linked elsewhere"])
        ops = _ops(be)
        self.assertEqual(ops.discover_suites("SS", "F1", ["SS-30552"]).get("SS-30552"), "77")
        got = _engine_read(ops.existing_titles("SS", "F1", "77"))
        self.assertEqual(got, ["Verify A", "verify b", "Linked elsewhere"])
        self.assertEqual(be.linked_calls, ["SS-30552"])

    def test_linked_lookup_failure_keeps_folder_titles(self):
        be = _Be("jira_zephyr")
        be.linked_test_case_titles = mock.Mock(side_effect=RuntimeError("boom"))
        ops = _ops(be)
        ops.discover_suites("SS", "F1", ["SS-30552"])
        self.assertEqual(_engine_read(ops.existing_titles("SS", "F1", "77")), ["Verify A", "verify b"])

    def test_other_backends_folder_only(self):
        be = _Be("testrail", linked=["X"])
        ops = _ops(be, backend="testrail")
        ops.discover_suites("SS", "F1", ["SS-30552"])
        self.assertEqual(_engine_read(ops.existing_titles("SS", "F1", "77")), ["Verify A", "verify b"])
        self.assertEqual(be.linked_calls, [])

    def test_zephyr_linked_titles(self):
        be = JiraZephyrBackend.__new__(JiraZephyrBackend)
        z = mock.Mock()
        z.get.side_effect = lambda path, **k: (
            [{"key": "SS-T1", "version": 1}, {"key": "SS-T2", "version": 1}]
            if path.startswith("issuelinks/") else {"name": " Title of %s " % path.split("/")[-1]})
        be._need_zephyr = lambda: z
        self.assertEqual(be.linked_test_case_keys("SS-30552"), ["SS-T1", "SS-T2"])
        self.assertEqual(sorted(be.linked_test_case_titles("SS-30552")),
                         ["Title of SS-T1", "Title of SS-T2"])


class LinkCheckScript(unittest.TestCase):
    def test_reports_link(self):
        import io
        import contextlib
        import zephyr_link_check as Z
        be = mock.Mock()
        z, jira = mock.Mock(), mock.Mock()
        z.get.side_effect = lambda path, **k: (
            {"issues": [{"issueId": 10501, "type": "COVERAGE"}]} if path.endswith("/links")
            else {"name": "Verify A", "folder": {"id": 77}})
        jira.get.return_value = {"key": "SS-30552"}
        be._need_zephyr.return_value, be._need_jira.return_value = z, jira
        be.fetch_stories.return_value = [Story(ref=Ref(id="10501", key="SS-30552"), title="t")]
        be.linked_test_case_keys.return_value = ["SS-T1167"]
        buf = io.StringIO()
        with mock.patch.object(Z, "_saved_creds", return_value={"backend": "jira_zephyr"}), \
                mock.patch.object(Z.B, "active", return_value="jira_zephyr"), \
                mock.patch.object(Z.B, "get_backend", return_value=be), \
                contextlib.redirect_stdout(buf):
            Z.main("SS-T1167", "SS-30552")
        out = buf.getvalue()
        self.assertIn("issueId=10501  ->  SS-30552", out)
        self.assertIn("numeric id QA Studio links with: 10501", out)
        self.assertIn("SS-T1167 IS linked to SS-30552", out)


if __name__ == "__main__":
    unittest.main()
