"""Regression Plan on Jira + Zephyr: finding each story's test-case folder uses
ONE folder listing (it re-downloaded every folder per story), matches folders
whose names had / or \\ replaced, and Generate reports progress in two phases."""
import sys
import types
import unittest
from unittest import mock

from tracker.jira_zephyr import JiraZephyrBackend
from tracker.models import Plan, Ref, Story


class FakeZephyr:
    def __init__(self, folders):
        self.folders = folders
        self.listings = 0

    def paginate(self, path, **kw):
        assert path == "folders"
        self.listings += 1
        return iter(self.folders)


def _story(key, title):
    return Story(ref=Ref(id=key, key=key), title=title)


class BatchFolderLookup(unittest.TestCase):
    def setUp(self):
        self.z = FakeZephyr([
            {"id": 11, "name": "SS-1 Login", "parentId": 100},
            {"id": 12, "name": "SS-2 Upload a-b file", "parentId": 100},   # title had "/"
            {"id": 13, "name": "SS-3 Other plan", "parentId": 200},
        ])
        self.be = JiraZephyrBackend(site="https://acme.atlassian.net", jira=object(), zephyr=self.z)
        self.plan = Plan(ref=Ref(id="100"), name="Sprint")

    def test_one_listing_for_all_stories(self):
        got = self.be.find_suites_for_stories("SS", self.plan, [
            _story("SS-1", "Login"), _story("SS-2", "Upload a/b file"),
            _story("SS-3", "Other plan"), _story("SS-4", "No folder")])
        self.assertEqual(self.z.listings, 1)
        self.assertEqual({k: v.ref.id for k, v in got.items()}, {"SS-1": "11", "SS-2": "12"})

    def test_single_lookup_matches_slash_titles(self):
        row = self.be._find_folder("SS", "SS-2 Upload a/b file", parent_id=100)
        self.assertEqual(row["id"], 12)       # used to miss -> duplicate folder


class SuiteMapUsesBatch(unittest.TestCase):
    def test_backend_setup_prefers_batch(self):
        import backend_setup as B
        be = mock.MagicMock()
        be.fetch_stories.return_value = [_story("SS-1", "Login")]
        be.find_suites_for_stories.return_value = {
            "SS-1": types.SimpleNamespace(ref=Ref(id="11"))}
        app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS")
        with mock.patch.object(B, "is_azure", return_value=False), \
             mock.patch.object(B, "get_backend", return_value=be), \
             mock.patch.object(B, "_as_project", return_value="SS"), \
             mock.patch.object(B, "_plan_dto", return_value=Plan(ref=Ref(id="100"), name="P")):
            smap = B.suite_map_for_stories(app, "100", ["SS-1"])
        self.assertEqual(smap["SS-1"], "11")
        be.find_suite_for_story.assert_not_called()


class TwoPhaseProgress(unittest.TestCase):
    def _regression(self):
        try:
            import flet  # noqa: F401
            stub = {}
        except Exception:
            stub = {"flet": mock.MagicMock()}
        return stub

    def test_phases_in_order_and_old_callbacks(self):
        with mock.patch.dict(sys.modules, self._regression()):
            import regression
            import backend_setup
            app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS",
                                        _reg_suite_cache={}, _reg_case_count_cache={})
            sel = [{"id": "SS-%d" % i, "plan_id": "100"} for i in range(1, 11)]
            smap = {"SS-%d" % i: str(10 + i) for i in range(1, 11)}
            calls = []
            # Folder path: a backend without link lookups (see FairEstimate
            # for the linked path).
            with mock.patch.object(regression, "_discover_suite_map", return_value=smap), \
                 mock.patch.object(regression, "_count_linked_cases", return_value=None), \
                 mock.patch.object(backend_setup, "count_cases_in_suite", return_value=3):
                counts = regression._count_cases(app, sel,
                                                 progress=lambda d, t, ph: calls.append((ph, d, t)))
                self.assertEqual(sum(counts.values()), 30)
                self.assertEqual(calls[0], ("find", 0, 10))
                self.assertEqual(calls[1], ("find", 10, 10))
                self.assertEqual(calls[2], ("count", 0, 10))
                self.assertEqual(calls[-1], ("count", 10, 10))
                old = []
                app._reg_case_count_cache = {}
                regression._count_cases(app, sel, progress=lambda d, t: old.append((d, t)))
                self.assertEqual(old[-1], (10, 10))


class FairEstimate(unittest.TestCase):
    """15 test cases for 266 stories gave a 2 h plan: count every LINKED test,
    and estimate stories that still have none from their size."""

    def _env(self):
        try:
            import flet  # noqa: F401
            return {}
        except Exception:
            return {"flet": mock.MagicMock()}

    def test_linked_first_then_folder_fallback(self):
        with mock.patch.dict(sys.modules, self._env()):
            import regression
            import backend_setup
            be = mock.MagicMock()
            be.linked_test_case_count.side_effect = lambda k: {"SS-1": 4, "SS-2": 0, "SS-3": 0}[k]
            app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS",
                                        _reg_suite_cache={}, _reg_case_count_cache={})
            sel = [{"id": k, "plan_id": "100"} for k in ("SS-1", "SS-2", "SS-3")]
            folder_seen = []

            def folder(app_, rest, progress=None):
                folder_seen.append([r["id"] for r in rest])
                return {"SS-2": 2, "SS-3": 0}
            with mock.patch.object(backend_setup, "is_azure", return_value=False), \
                 mock.patch.object(backend_setup, "get_backend", return_value=be), \
                 mock.patch.object(regression, "_count_folder_cases", side_effect=folder):
                counts = regression._count_cases(app, sel)
                self.assertEqual(counts, {"SS-1": 4, "SS-2": 2, "SS-3": 0})
                self.assertEqual(folder_seen, [["SS-2", "SS-3"]])     # only the zeros
                be.linked_test_case_count.reset_mock()
                regression._count_cases(app, sel)                    # cached now
                be.linked_test_case_count.assert_not_called()

    def test_azure_keeps_suite_counting(self):
        with mock.patch.dict(sys.modules, self._env()):
            import regression
            import backend_setup
            app = types.SimpleNamespace(creds={"backend": "azure"}, project="P")
            with mock.patch.object(backend_setup, "is_azure", return_value=True), \
                 mock.patch.object(regression, "_count_folder_cases", return_value={"1": 7}) as f:
                self.assertEqual(regression._count_cases(app, [{"id": "1", "plan_id": "9"}]), {"1": 7})
                f.assert_called_once()

    def test_size_estimate_and_rows(self):
        with mock.patch.dict(sys.modules, self._env()):
            import regression
            app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS",
                                        _reg_effort_mode=True, _reg_story_features={},
                                        _reg_feature_name_cache={})
            with mock.patch.object(regression, "_fetch_cp_complexity",
                                   return_value=({"SS-2": 150.0, "SS-3": 1.0, "SS-4": 5000.0}, {})):
                est = regression._estimate_cases(app, ["SS-2", "SS-3", "SS-4"])
            self.assertEqual(est, {"SS-2": 5, "SS-3": regression.EST_MIN_CASES,
                                   "SS-4": regression.EST_MAX_CASES})
            sel = [{"id": "SS-1", "plan_id": "100"}, {"id": "SS-2", "plan_id": "100"}]
            meta = {"SS-1": {"priority": 3}, "SS-2": {"priority": 1}}
            with mock.patch.object(regression, "_cache_load"), \
                 mock.patch.object(regression, "_cache_save"), \
                 mock.patch.object(regression, "_fetch_meta", return_value=meta), \
                 mock.patch.object(regression, "_count_cases", return_value={"SS-1": 3, "SS-2": 0}), \
                 mock.patch.object(regression, "_estimate_cases", return_value={"SS-2": 5}):
                rows = {r["id"]: r for r in regression.build_rows(app, sel)}
            self.assertEqual((rows["SS-1"]["cases"], rows["SS-1"]["cases_est"]), (3, False))
            self.assertEqual((rows["SS-2"]["cases"], rows["SS-2"]["cases_est"]), (5, True))
            self.assertGreater(rows["SS-2"]["hours"], 0)             # not 0 h any more
            self.assertEqual(regression._cases_txt(rows["SS-2"]), "~5")
            app._reg_selected_rows = list(rows.values())
            app._reg_res_names, app._reg_res_count = ["ahmed"], 1
            app._reg_plans_selected, app.plan_name = [], ""
            d = regression.plan_payload(app)
            self.assertEqual((d["total_cases"], d["est_cases"], d["est_stories"]), (8, 5, 1))


class SprintPlanAISpeed(unittest.TestCase):
    def _env(self):
        try:
            import flet  # noqa: F401
            return {}
        except Exception:
            return {"flet": mock.MagicMock()}

    def test_workers_by_provider(self):
        with mock.patch.dict(sys.modules, self._env()):
            import regression
            with mock.patch.object(regression.E, "AI_PROVIDER", "openai"):
                self.assertEqual(regression._cp_ai_workers(), regression._CP_AI_WORKERS_PAID)
            with mock.patch.object(regression.E, "AI_PROVIDER", "ollama"):
                self.assertEqual(regression._cp_ai_workers(), regression._CP_AI_WORKERS)

    def test_ai_progress_reported(self):
        with mock.patch.dict(sys.modules, self._env()):
            import regression
            texts = {"SS-%d" % i: {"title": "t", "desc": "", "crit": ""} for i in range(25)}
            seen = []
            with mock.patch.object(regression.E, "ai_complete", return_value="[]"), \
                 mock.patch.object(regression.E, "parse_json_robust", return_value=[]):
                regression._fetch_cp_ai_complexity(types.SimpleNamespace(), texts,
                                                   progress=lambda d, t: seen.append((d, t)))
            self.assertEqual(seen[0], (0, 25))
            self.assertEqual(seen[-1], (25, 25))

    def test_banner_count_and_bar_update_in_place(self):
        with mock.patch.dict(sys.modules, self._env()):
            import regression
            cnt = types.SimpleNamespace(value="…", update=lambda: None)
            bar = types.SimpleNamespace(value=None, update=lambda: None)
            app = types.SimpleNamespace(_cp_prov_parts=(cnt, bar), ui_safe=lambda fn: fn())
            texts = {"SS-%d" % i: {"title": "t", "desc": "", "crit": ""} for i in range(20)}
            with mock.patch.object(regression.E, "ai_complete", return_value="[]"), \
                 mock.patch.object(regression.E, "parse_json_robust", return_value=[]):
                regression._cp_ai_scores(app, texts)
            self.assertEqual(cnt.value, "20 / 20")
            self.assertEqual(bar.value, 1.0)
            self.assertEqual(app._cp_ai_progress, (20, 20))

    def test_meta_and_text_share_one_fetch(self):
        import threading
        with mock.patch.dict(sys.modules, self._env()):
            import regression
            import backend_setup
            calls = []
            gate = threading.Event()

            def fetch(refs):
                calls.append(len(refs))
                gate.wait(2)
                return [_story(r.key, "T " + r.key) for r in refs]
            be = mock.MagicMock()
            be.fetch_stories.side_effect = fetch
            app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS")
            ids = ["SS-%d" % i for i in range(1, 6)]
            out = {}
            with mock.patch.object(backend_setup, "get_backend", return_value=be):
                t1 = threading.Thread(target=lambda: out.setdefault("a", regression._story_dtos(app, ids)))
                t2 = threading.Thread(target=lambda: out.setdefault("b", regression._story_dtos(app, ids)))
                t1.start(); t2.start()
                gate.set(); t1.join(3); t2.join(3)
            self.assertEqual(calls, [5])                     # one fetch for both
            self.assertEqual(len(out["a"]), 5)
            self.assertEqual(len(out["b"]), 5)


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
