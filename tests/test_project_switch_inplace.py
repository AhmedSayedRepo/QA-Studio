"""Connect lands on a sensible project (last used / Zephyr-enabled), and a
project switch patches Setup in place: no full render, previous plan and
stories cleared, plans reloaded in the background, a Zephyr "not enabled"
error shown plainly."""
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
from tracker.jira_zephyr import JiraZephyrBackend  # noqa: E402


class PreferredProject(unittest.TestCase):
    def pick(self, projects, creds, enabled=None):
        be = types.SimpleNamespace(zephyr_enabled_project_keys=lambda: enabled)
        app = types.SimpleNamespace(creds=creds)
        with mock.patch.object(B, "get_backend", return_value=be):
            return B.preferred_project(app, projects)

    def test_zephyr_enabled_project_wins_over_the_first(self):
        self.assertEqual(self.pick(["DA", "QA", "SS"], {"backend": "jira_zephyr"}, ["SS", "ZZ"]), "SS")

    def test_last_used_kept_when_enabled(self):
        creds = {"backend": "jira_zephyr", "last_project_jira_zephyr": "QA"}
        self.assertEqual(self.pick(["DA", "QA", "SS"], creds, ["QA", "SS"]), "QA")

    def test_last_used_not_enabled_moves_to_an_enabled_one(self):
        creds = {"backend": "jira_zephyr", "last_project_jira_zephyr": "DA"}
        self.assertEqual(self.pick(["DA", "QA", "SS"], creds, ["SS"]), "SS")

    def test_unknown_zephyr_state_uses_last_then_first(self):
        creds = {"backend": "jira_zephyr", "last_project_jira_zephyr": "QA"}
        self.assertEqual(self.pick(["DA", "QA"], creds, None), "QA")
        self.assertEqual(self.pick(["DA", "QA"], {"backend": "jira_zephyr"}, None), "DA")

    def test_other_backends_use_last_used(self):
        creds = {"backend": "xray", "last_project_xray": "QA"}
        self.assertEqual(self.pick(["DA", "QA"], creds), "QA")
        self.assertEqual(self.pick(["DA", "QA"], {"backend": "azure"}), "DA")
        self.assertIsNone(self.pick([], {"backend": "azure"}))

    def test_zephyr_projects_endpoint(self):
        class Z:
            def paginate(self, path, **kw):
                assert path == "projects"
                return iter([{"key": "SS", "enabled": True}, {"key": "DA", "enabled": False}, {"key": "QA"}])
        be = JiraZephyrBackend(site="https://acme.atlassian.net", jira=object(), zephyr=Z())
        self.assertEqual(be.zephyr_enabled_project_keys(), ["SS", "QA"])

        class Broken:
            def paginate(self, *a, **k):
                raise RuntimeError("401")
        be = JiraZephyrBackend(site="https://acme.atlassian.net", jira=object(), zephyr=Broken())
        self.assertIsNone(be.zephyr_enabled_project_keys())


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
    errs, renders, saved = [], [], []
    app._err = lambda m: errs.append(str(m))
    app.render = lambda *a, **k: renders.append(1)
    app.ui_safe = lambda fn: fn()
    app._bg = lambda fn: fn()
    app.can = lambda perm: True
    app.active = 'setup'
    app.creds = {'backend': 'jira_zephyr'}
    app.project, app.plan_id, app.plan_name = 'DA', 'F1', 'Old plan'
    app._plans = [{'id': 'F1', 'name': 'Old plan'}]
    app.story_ids = ['DA-1', 'DA-2']
    app._invalid = set()
    app._fetch_estimate = lambda: None
    app.project_dd = fl.Dropdown(value='DA')
    app.plan_dd = app._build_plan_dd()
    app._plan_cell = fl.Container(app.plan_dd)
    app.plan_id_field = fl.TextField(value='F1')
    app._open_plan_btn = fl.IconButton(None)
    app._summary_btn = fl.FilledButton('Summary')
    app._summary_shadow = fl.Container(None)
    app._sum_project = fl.Text('DA'); app._sum_plan = fl.Text('#F1')
    app.story_field = fl.TextField(value='DA-1, DA-2')
    app._chip_row = fl.Row([]); app._chip_wrap = fl.Container(None)
    chips = []
    app._setup_rebuild_chips = lambda: chips.append(list(app.story_ids))
    app._setup_update_summary = lambda: None
    synced = []
    app._sync_setup_story_cell = lambda: synced.append(1)
    store.save = lambda c: saved.append(dict(c))
    calls = []
    def fetch_plans(a, project):
        calls.append(project)
        if project == 'DA':
            raise backend_setup_err
        return [{'id': 'F9', 'name': 'Sprint 3'}]
    from tracker.errors import NotFound
    backend_setup_err = NotFound("Zephyr isn't enabled for the Jira project DA. Ask a project admin.")
    with mock.patch.object(backend_setup, 'fetch_plans', side_effect=fetch_plans):
        app._on_project_change(types.SimpleNamespace(data='SS', control=None))
        assert renders == [], renders
        assert app.project == 'SS' and app.plan_id is None and app.story_ids == [], (app.project, app.story_ids)
        assert calls == ['SS'], calls
        assert app._plans == [{'id': 'F9', 'name': 'Sprint 3'}]
        dd = app._plan_cell.content
        assert dd is not None and app._plan_cell.updates >= 2
        assert app.plan_id_field.value == '' and app._sum_project.value == 'SS' and app._sum_plan.value == '\\u2014'
        # Jira + Zephyr: the folder is cleared (open-plan disabled), but Sprint
        # Summary needs only project + sprint, so it stays enabled.
        assert app._summary_btn.disabled is False and app._open_plan_btn.disabled is True
        assert chips == [[]] and synced, (chips, synced)
        assert errs == []
        assert app.creds['last_project_jira_zephyr'] == 'SS'
        # Same project again: nothing happens.
        app._on_project_change(types.SimpleNamespace(data='SS', control=None))
        assert calls == ['SS']
        # Switch to a project without Zephyr: plain error, empty plan list, still no render.
        app._on_project_change(types.SimpleNamespace(data='DA', control=None))
        assert renders == [] and app._plans == []
        assert errs and "Zephyr isn't enabled for the Jira project DA" in errs[-1], errs
    import time; time.sleep(0.2)
    assert saved and saved[-1].get('last_project_jira_zephyr') in ('SS', 'DA'), saved
    # Not on Setup: falls back to load + render.
    app.active = 'run'
    with mock.patch.object(backend_setup, 'fetch_plans', return_value=[]):
        app._on_project_change(types.SimpleNamespace(data='QA', control=None))
    assert renders == [1]
    print('SIM-OK')
""") % ROOT


class InPlaceSwitch(unittest.TestCase):
    def test_project_switch(self):
        r = subprocess.run([sys.executable, "-c", _SIM], capture_output=True, text=True,
                           encoding="utf-8", timeout=300, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
