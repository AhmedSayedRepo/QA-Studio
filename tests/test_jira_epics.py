"""Regression Plan on Jira + Zephyr: every story gets its real epic ("No Feature"
only when it truly has none) — the story fetch survives a missing key, and the
classic "Epic Link" field is read when `parent` isn't set."""
import re
import types
import unittest
from unittest import mock

from tracker.jira_zephyr import JiraZephyrBackend
from tracker.models import Ref


class FakeJira:
    """`key IN (...)` search that fails the WHOLE query if any key is missing,
    like Jira Cloud does."""

    def __init__(self, issues, fields=None):
        self.issues = {i["key"]: i for i in issues}
        self.fields = fields or []
        self.queries = 0

    def get(self, path, params=None, **kw):
        if path == "api/3/field":
            return self.fields
        if path == "api/3/search/jql":
            self.queries += 1
            keys = re.findall(r'"([^"]+)"', params["jql"])
            missing = [k for k in keys if k not in self.issues]
            if missing:
                raise RuntimeError("400: The issue key '%s' does not exist" % missing[0])
            return {"issues": [self.issues[k] for k in keys], "isLast": True}
        return {}


def story(key, parent=None, epic_link=None):
    f = {"summary": "Story " + key, "status": {"name": "In QA"}}
    if parent:
        f["parent"] = {"key": parent, "fields": {"summary": "Epic " + parent}}
    if epic_link:
        f["customfield_10014"] = epic_link
    return {"id": key.split("-")[1], "key": key, "fields": f}


def backend(jira):
    return JiraZephyrBackend(site="https://acme.atlassian.net", jira=jira, zephyr=object())


class FetchStoriesSurvivesMissingKeys(unittest.TestCase):
    def test_one_missing_key_does_not_drop_the_rest(self):
        jira = FakeJira([story("SS-%d" % i, parent="SS-900") for i in range(1, 121)])
        refs = [Ref(id=k, key=k) for k in ["SS-%d" % i for i in range(1, 121)] + ["SS-99999"]]
        got = backend(jira).fetch_stories(refs)
        self.assertEqual(len(got), 120)                       # only the bad key is lost
        self.assertTrue(all(s.state == "In QA" for s in got))
        self.assertTrue(all(s.epic_id == "SS-900" for s in got))

    def test_batches_keys(self):
        jira = FakeJira([story("SS-%d" % i) for i in range(1, 121)])
        backend(jira).fetch_stories([Ref(id="SS-%d" % i, key="SS-%d" % i) for i in range(1, 121)])
        self.assertEqual(jira.queries, 3)                     # 50 + 50 + 20


class EpicSources(unittest.TestCase):
    def test_parent_epic(self):
        s = backend(FakeJira([story("SS-1", parent="SS-50")])).fetch_stories([Ref(id="SS-1", key="SS-1")])[0]
        self.assertEqual((s.epic_id, s.epic_name), ("SS-50", "Epic SS-50"))

    def test_classic_epic_link(self):
        jira = FakeJira([story("SS-2", epic_link="SS-60")],
                        fields=[{"id": "customfield_10014", "name": "Epic Link"}])
        s = backend(jira).fetch_stories([Ref(id="SS-2", key="SS-2")])[0]
        self.assertEqual(s.epic_id, "SS-60")

    def test_no_epic_is_no_feature(self):
        jira = FakeJira([story("SS-3")], fields=[{"id": "customfield_10014", "name": "Epic Link"}])
        s = backend(jira).fetch_stories([Ref(id="SS-3", key="SS-3")])[0]
        self.assertEqual(s.epic_id, "")


class RegressionGroupsByEpic(unittest.TestCase):
    def test_meta_carries_epic_and_state(self):
        import sys
        try:
            import flet  # noqa: F401  (installed on dev PCs)
            stub = {}
        except Exception:
            stub = {"flet": mock.MagicMock()}
        # patch.dict restores sys.modules afterwards, so a stubbed flet (and the
        # regression module imported with it) never leaks into other tests.
        with mock.patch.dict(sys.modules, stub):
            self._run()

    def _run(self):
        import regression
        import backend_setup
        jira = FakeJira([story("SS-1", parent="SS-50"), story("SS-2", epic_link="SS-60"),
                         story("SS-3"), story("SS-60")],
                        fields=[{"id": "customfield_10014", "name": "Epic Link"}])
        be = backend(jira)
        app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS")
        with mock.patch.object(backend_setup, "get_backend", return_value=be), \
             mock.patch.object(backend_setup, "reads_stories_from_azure", return_value=False):
            meta = regression._fetch_meta(app, ["SS-1", "SS-2", "SS-3", "SS-GONE"])
            names = regression._fetch_feature_names(app, ["SS-60"])
        self.assertEqual(meta["SS-1"]["parent_id"], "SS-50")
        self.assertEqual(meta["SS-2"]["parent_id"], "SS-60")
        self.assertIsNone(meta["SS-3"]["parent_id"])          # truly no epic
        self.assertEqual(meta["SS-1"]["state"], "In QA")      # not "Unknown"
        self.assertEqual(names["SS-60"], "Story SS-60")       # epic name resolved
        self.assertEqual(app._reg_feature_name_cache["SS-50"], "Epic SS-50")

    def test_sprint_plan_gets_story_text_for_ai(self):
        """Sprint Plan's AI scoring: 'AI scored 0/0' happened because the one
        big story query failed, so no text reached the AI."""
        import sys
        try:
            import flet  # noqa: F401
            stub = {}
        except Exception:
            stub = {"flet": mock.MagicMock()}
        with mock.patch.dict(sys.modules, stub):
            import regression
            import backend_setup
            keys = ["SS-%d" % i for i in range(1, 121)]
            jira = FakeJira([story(k) for k in keys])
            app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS")
            with mock.patch.object(backend_setup, "get_backend", return_value=backend(jira)), \
                 mock.patch.object(backend_setup, "reads_stories_from_azure", return_value=False):
                texts = regression._cp_fetch_fields(app, keys + ["SS-GONE"])
        self.assertEqual(len(texts), 120)
        self.assertEqual(texts["SS-7"]["title"], "Story SS-7")


def setUpModule():
    """Keep fake 'skipped SS-GONE' warnings out of the real diagnostics.log."""
    global _diag_patches
    import diag_log
    _diag_patches = [mock.patch.object(diag_log, n, lambda *a, **k: None)
                     for n in ("log", "log_warn") if hasattr(diag_log, n)]
    for p in _diag_patches:
        p.start()


def tearDownModule():
    for p in _diag_patches:
        p.stop()


if __name__ == "__main__":
    unittest.main()
