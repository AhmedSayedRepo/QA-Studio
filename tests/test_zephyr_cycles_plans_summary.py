"""Jira + Zephyr: "folder" wording in Setup, the optional Zephyr test cycle
for a run's test cases (linked to a Zephyr test plan), Create Plan making
story folders, and the Sprint Summary from Zephyr traceability links."""
import os
import subprocess
import sys
import textwrap
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import backend_setup as B  # noqa: E402
import engine as E  # noqa: E402
import strings  # noqa: E402
from tracker.errors import TrackerError  # noqa: E402
from tracker.jira_zephyr import JiraZephyrBackend  # noqa: E402
from tracker.models import Project, Ref, Sprint, Story, User  # noqa: E402


class Z:
    def __init__(self):
        self.posts = []

    def post(self, path, json=None, **kw):
        self.posts.append((path, json))
        if path == "testcycles":
            return {"id": 9, "key": "SS-R4"}
        if path == "testexecutions" and json["testCaseKey"] == "SS-T9":
            raise TrackerError("Request failed (400). Test case has parameters", backend="zephyr", status=400)
        return {}

    def get(self, path, **kw):
        if path == "issuelinks/SS-45/testcases":
            return [{"key": "SS-T1"}, {"key": "SS-T2"}]
        return []

    def paginate(self, path, **kw):
        assert path == "testplans" and kw["params"]["projectKey"] == "SS"
        return iter([{"key": "SS-P2", "name": "Pension Payroll"}, {"key": "SS-P5", "name": "Mobile"}])


class Adapter(unittest.TestCase):
    def setUp(self):
        self.z = Z()
        self.be = JiraZephyrBackend(site="https://acme.atlassian.net", jira=object(), zephyr=self.z)

    def test_cycle_with_cases_linked_to_a_plan(self):
        res = self.be.create_cycle_for_cases("SS", "QA Studio run", ["SS-T1", "SS-T2", "SS-T9", "SS-T1"], plan_key="SS-P2")
        self.assertEqual(res["cycle"], "SS-R4")
        self.assertEqual(res["added"], 2)
        self.assertEqual([k for k, _ in res["failed"]], ["SS-T9"])
        self.assertEqual(res["plan_error"], "")
        paths = [p for p, _ in self.z.posts]
        self.assertEqual(paths[0], "testcycles")
        self.assertEqual(paths.count("testexecutions"), 3)              # duplicates dropped
        self.assertEqual(paths[-1], "testplans/SS-P2/links/testcycles")
        self.assertEqual(self.z.posts[-1][1], {"testCycleIdOrKey": "SS-R4"})
        ex = [j for p, j in self.z.posts if p == "testexecutions"][0]
        self.assertEqual(ex, {"projectKey": "SS", "testCaseKey": "SS-T1", "testCycleKey": "SS-R4",
                              "statusName": "Not Executed"})

    def test_no_plan_no_link(self):
        self.be.create_cycle_for_cases("SS", "x", ["SS-T1"])
        self.assertNotIn("links/testcycles", " ".join(p for p, _ in self.z.posts))

    def test_light_sprint_read(self):
        class J:
            def __init__(self): self.calls = []
            def get(self, path, params=None, **kw):
                if path.startswith("api/3/project/"):
                    return {"issueTypes": [{"name": "Story", "hierarchyLevel": 0}]}
                self.calls.append(params)
                return {"issues": [{"key": "SS-1", "fields": {"summary": "x", "status": {"name": "Done"}}}], "isLast": True}
        j = J()
        be = JiraZephyrBackend(site="https://acme.atlassian.net", jira=j, zephyr=object())
        got = be.fetch_story_summaries_in_sprint("SS", "526")
        self.assertEqual([g.ref.key for g in got], ["SS-1"])
        self.assertEqual(j.calls[0]["fields"], "summary,status,assignee,issuetype")
        self.assertIn("sprint = 526 ", j.calls[0]["jql"])

    def test_plans_and_link_counts(self):
        self.assertEqual(self.be.fetch_zephyr_test_plans("SS"), [("SS-P2", "Pension Payroll"), ("SS-P5", "Mobile")])
        self.assertEqual(self.be.linked_test_case_count("SS-45"), 2)


class Summary(unittest.TestCase):
    def test_zephyr_summary(self):
        sprints = [Sprint(ref=Ref(id="526"), name="TAMM Sprint 1", path="526", state="active"),
                   Sprint(ref=Ref(id="7"), name="Old", path="7", state="closed")]
        st = [Story(ref=Ref(id="10045", key="SS-45"), title="Login", state="In QA",
                    assignee=User(id="u", display_name="Ahmed")),
              Story(ref=Ref(id="10046", key="SS-46"), title="Logout", state="Done")]
        be = types.SimpleNamespace(fetch_sprints=lambda p: sprints,
                                   fetch_stories_in_sprint=lambda p, path: self.fail("heavy read used"),
                                   fetch_story_summaries_in_sprint=lambda p, path: st if path == "526" else [],
                                   linked_test_case_count=lambda k: {"SS-45": 2}.get(k, 0))
        steps = []
        app = types.SimpleNamespace(creds={"backend": "jira_zephyr", "jira_site": "https://acme.atlassian.net"},
                                    project="SS", plan_name="TAMM", setup_sprint="")
        with mock.patch.object(B, "get_backend", return_value=be), \
                mock.patch.object(B, "_as_project", return_value=Project(ref=Ref(id="1", key="SS"), name="SS")):
            d = B.sprint_summary(app, "F1", progress=lambda f, t: steps.append((round(f, 2), t)))
        fracs = [f for f, _ in steps]
        self.assertEqual(fracs, sorted(fracs))                  # never goes backwards
        self.assertEqual(fracs[-1], 1.0)
        self.assertTrue(any("story 2 of 2" in t for _, t in steps), steps)
        self.assertEqual(d["iteration"], "TAMM Sprint 1")
        self.assertEqual(d["total_stories"], 2)
        self.assertEqual(d["total_test_cases"], 2)
        self.assertEqual(d["by_state"], {"In QA": 1, "Done": 1})
        self.assertEqual(d["stories"][0]["assigned_to"], "Ahmed")
        self.assertEqual(d["url_map"]["SS-45"], "https://acme.atlassian.net/browse/SS-45")
        html = E.build_sprint_summary_email(d)
        self.assertIn("https://acme.atlassian.net/browse/SS-45", html)
        self.assertNotIn("dev.azure.com", html)

    def test_azure_unchanged(self):
        app = types.SimpleNamespace(creds={"backend": "azure"}, project="P")
        with mock.patch.object(E, "sprint_summary", return_value={"ok": 1}) as m:
            self.assertEqual(B.sprint_summary(app, 5), {"ok": 1})
        m.assert_called_once_with("P", 5)

    def test_availability(self):
        self.assertTrue(B.sprint_summary_available(types.SimpleNamespace(creds={"backend": "azure"})))
        self.assertTrue(B.sprint_summary_available(types.SimpleNamespace(creds={"backend": "jira_zephyr"})))
        self.assertFalse(B.sprint_summary_available(types.SimpleNamespace(creds={"backend": "xray"})))


class Wiring(unittest.TestCase):
    def test_modals(self):
        src = open(os.path.join(ROOT, "modals.py"), encoding="utf-8").read()
        self.assertIn("_bs.sprint_summary_available(app)", src)
        self.assertIn("data = _bs.sprint_summary(app, app.plan_id, progress=_sum_progress)", src)
        self.assertIn("_be.ensure_suite_for_story(_proj, plan, _st)", src)
        self.assertIn("elif _is_zephyr:", src)
        self.assertIn('strings.t("mdl_connecting_to", name=_sum_backend)', src)
        self.assertNotIn('Text(strings.t("mdl_connecting_azure")', src)
        self.assertIn("progress=_sum_progress", src)

    def test_run_case_keys_recorded(self):
        app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS")
        be = types.SimpleNamespace(fetch_stories=lambda refs: [],
                                   create_test_case=lambda *a, **k: types.SimpleNamespace(ref=Ref(id="5", key="SS-T5")))
        with mock.patch.object(B, "is_azure", return_value=False), mock.patch.object(B, "get_backend", return_value=be), \
                mock.patch.object(B, "_as_project", side_effect=lambda b, p: p), \
                mock.patch.object(E, "case_labels", return_value=()):
            ops = B.generation_ops(app)
            ops.create_case("SS", "F1", "77", "t", "")
        self.assertEqual(app._run_case_keys, ["SS-T5"])

    def test_strings(self):
        keys = ("setup_zephyr_folder", "setup_zephyr_folder_id", "setup_zephyr_create_folder", "setup_zephyr_search_folder",
                "zcycle_label", "zcycle_sub", "zcycle_loading_plans", "zcycle_no_plan", "zcycle_plan_label",
                "zcycle_done", "zcycle_failed", "zcycle_case_failed", "zcycle_plan_failed",
                "mdl_zephyr_story_folders", "mdl_zephyr_story_folders_sub")
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            for k in keys:
                self.assertTrue(strings._STRINGS[lang].get(k), lang + " " + k)
            self.assertIn("{cycle}", strings._STRINGS[lang]["zcycle_done"])
            self.assertIn("{name}", strings._STRINGS[lang]["mdl_connecting_to"])
            for k in ("sum_p_sprints", "sum_p_stories", "sum_p_cases"):
                self.assertTrue(strings._STRINGS[lang].get(k), lang + " " + k)


_SIM = textwrap.dedent("""
    import os, sys, threading, types
    sys.path.insert(0, %r)
    from unittest import mock
    class Ctl:
        def __init__(self, *a, **k):
            self.__dict__.update(k)
            self.args = a
            if a and 'controls' not in k and isinstance(a[0], list): self.controls = a[0]
            elif a and 'content' not in k and not isinstance(a[0], str): self.content = a[0]
            if a and isinstance(a[0], str): self.value = a[0]
            self.updates = 0
        def update(self): self.updates += 1
    fl = mock.MagicMock()
    for n in ("Column", "Row", "Container", "TextField", "Text", "Icon", "GestureDetector", "ListView",
              "IconButton", "ProgressRing", "ProgressBar", "FilledButton", "Dropdown", "DropdownOption"):
        setattr(fl, n, type(n, (Ctl,), {}))
    sys.modules['flet'] = fl
    for name in ('ai_usage_screen', 'identity_editor', 'idle_watch', 'login', 'orgs_screen', 'perf',
                 'perf.models', 'perf.ports', 'report', 'settings', 'setup', 'sprint_titles',
                 'task_manager', 'useful_links', 'users_screen', 'window_chrome'):
        sys.modules[name] = mock.MagicMock()
    for _ in range(80):
        try:
            import main
            break
        except ModuleNotFoundError as e:
            sys.modules[e.name] = mock.MagicMock()
            sys.modules.pop('main', None)
    import backend_setup, store
    Q = main.QAStudio
    app = Q.__new__(Q)
    saved, logs, synced = [], [], []
    store.save = lambda c: saved.append(dict(c))
    app.ui_safe = lambda fn: fn()
    app._bg = lambda fn: fn()
    app._refresh_run = lambda: None
    app._log_lines = []
    app.project, app.plan_id, app.plan_name, app.connected = 'SS', 'F1', 'TAMM', True
    app.creds = {'backend': 'azure'}
    assert app._plan_word('au_test_plan') == main.strings.t('au_test_plan')
    app.creds = {'backend': 'jira_zephyr'}
    assert app._plan_word('au_test_plan') == main.strings.t('setup_zephyr_folder')
    assert app._plan_word('main_create_plan_btn') == main.strings.t('setup_zephyr_create_folder')
    row = app._build_zcycle_row()                       # off: just the checkbox row
    assert type(row).__name__ == 'Row', type(row).__name__
    app._zcycle_cell = fl.Container(None)
    with mock.patch.object(backend_setup, 'zephyr_test_plans', return_value=[('SS-P2', 'Payroll')]):
        app._set_zcycle(on=True)
    assert app.creds['zephyr_cycle_on'] is True
    col = app._zcycle_cell.content
    def find_dd(c, seen=None):
        seen = seen or set()
        if id(c) in seen: return None
        seen.add(id(c))
        if isinstance(c.__dict__.get('options'), list): return c
        for k in ('content', 'controls'):
            v = getattr(c, k, None)
            for ch in (v if isinstance(v, list) else [v]):
                if isinstance(ch, Ctl):
                    r = find_dd(ch, seen)
                    if r is not None: return r
        return None
    dd = find_dd(col)
    assert [o.key for o in dd.options] == ['__none__', 'SS-P2'], [o.key for o in dd.options]
    app._set_zcycle(plan='SS-P2  \u00b7  Payroll')
    assert app.creds['zephyr_cycle_plan'] == 'SS-P2'
    import time; time.sleep(0.2)
    assert saved and saved[-1]['zephyr_cycle_plan'] == 'SS-P2'
    # After a run: the cycle step logs what it did.
    app._run_case_keys = ['SS-T1', 'SS-T2', 'SS-T1']
    calls = []
    def fake_add(a, name, keys, plan_key=None):
        calls.append((name, keys, plan_key))
        return {'cycle': 'SS-R4', 'added': 2, 'failed': [('SS-T9', 'params')], 'plan_error': ''}
    with mock.patch.object(backend_setup, 'add_run_to_zephyr_cycle', side_effect=fake_add):
        app._add_run_to_zephyr_cycle()
    assert calls and calls[0][1] == ['SS-T1', 'SS-T2'] and calls[0][2] == 'SS-P2', calls
    assert 'SS-R4' in app._log_lines[0]['msg'] and app._log_lines[1]['tone'] == 'warn', app._log_lines
    # Off: nothing happens.
    app.creds['zephyr_cycle_on'] = False
    with mock.patch.object(backend_setup, 'add_run_to_zephyr_cycle', side_effect=AssertionError('called')):
        app._add_run_to_zephyr_cycle()
    print('SIM-OK')
""") % ROOT


class SetupUI(unittest.TestCase):
    def test_setup_option(self):
        r = subprocess.run([sys.executable, "-c", _SIM], capture_output=True, text=True,
                           encoding="utf-8", timeout=300, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
