"""Setup's Sprint picker (Jira backends): stories come from the chosen
sprint, all active sprints (default, each story labelled with its sprint) or
the backlog; changing it reloads the stories in place and keeps only the
selected stories still listed."""
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
import strings  # noqa: E402
from tracker.models import Project, Ref, Sprint, Story  # noqa: E402


def story(k):
    return Story(ref=Ref(id=k, key=k), title="T " + k)


class StorySource(unittest.TestCase):
    SPRINTS = [Sprint(ref=Ref(id="526"), name="TAMM Sprint 1 - 2025", path="526", state="active"),
               Sprint(ref=Ref(id="423"), name="BCM Sprint 1", path="423", state="active"),
               Sprint(ref=Ref(id="900"), name="Old", path="900", state="closed")]
    IN = {"526": [story("SS-1"), story("SS-2")], "423": [story("SS-3")], "900": [story("SS-9")]}

    def run_with(self, pick):
        be = types.SimpleNamespace(fetch_sprints=lambda p: self.SPRINTS,
                                   fetch_stories_in_sprint=lambda p, path: self.IN.get(path, []),
                                   fetch_backlog_stories=lambda p, n: [story("SS-50")])
        app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS", setup_sprint=pick)
        with mock.patch.object(B, "reads_stories_from_azure", return_value=False), \
                mock.patch.object(B, "get_backend", return_value=be), \
                mock.patch.object(B, "_as_project", return_value=Project(ref=Ref(id="1", key="SS"), name="SS")):
            return B.fetch_stories_for_plan(app, "F1"), app._stories_source_note

    def test_all_active_labels_each_story_with_its_sprint(self):
        got, note = self.run_with("")
        self.assertEqual([(g["id"], g["sprint"]) for g in got],
                         [("SS-1", "TAMM Sprint 1 - 2025"), ("SS-2", "TAMM Sprint 1 - 2025"), ("SS-3", "BCM Sprint 1")])
        self.assertEqual(note, "")

    def test_one_sprint(self):
        got, _ = self.run_with("900")
        self.assertEqual([g["id"] for g in got], ["SS-9"])

    def test_backlog(self):
        got, _ = self.run_with("__backlog__")
        self.assertEqual([g["id"] for g in got], ["SS-50"])

    def test_empty_sprint_says_so(self):
        got, note = self.run_with("777")
        self.assertEqual(got, [])
        self.assertEqual(note, strings.t("stories_note_sprint_empty"))

    def test_strings(self):
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            for k in ("setup_sprint_label", "setup_sprint_hint", "setup_sprint_all_active",
                      "setup_sprint_backlog", "stories_note_sprint_empty"):
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
    import backend_setup
    Q = main.QAStudio
    app = Q.__new__(Q)
    app.ui_safe = lambda fn: fn()
    app._bg = lambda fn: fn()
    app.render = lambda *a, **k: (_ for _ in ()).throw(AssertionError("full render"))
    app.creds = {'backend': 'jira_zephyr'}
    app.project, app.plan_id, app.connected, app.active = 'SS', 'F1', True, 'setup'
    app._fetch_estimate = lambda: None
    app.story_ids = ['SS-1', 'SS-3']
    app.story_field = fl.TextField(value='SS-1, SS-3')
    app._chip_row = fl.Row([]); app._chip_wrap = fl.Container(None)
    app._setup_rebuild_chips = lambda: None
    app._setup_update_summary = lambda: None
    app._sync_setup_story_cell = lambda: None
    rows = [{'name': 'TAMM Sprint 1 - 2025', 'path': '526', 'label': 'TAMM Sprint 1 - 2025  \u00b7  active', 'state': 'active'},
            {'name': 'BCM Sprint 1', 'path': '423', 'label': 'BCM Sprint 1  \u00b7  active', 'state': 'active'}]
    with mock.patch.object(backend_setup, 'fetch_sprints', return_value=rows):
        app._sprint_cell = fl.Container(None)
        app._load_setup_sprints()
    dd = app._sprint_cell.content
    keys = [o.key for o in dd.options]
    assert keys == ['__active__', '526', '423', '__backlog__'], keys
    assert dd.value == '__active__'
    loaded = []
    def fake_stories(a, pid):
        loaded.append(a.setup_sprint)
        return [{'id': 'SS-1', 'title': 'x'}] if a.setup_sprint == '526' else []
    with mock.patch.object(backend_setup, 'fetch_stories_for_plan', side_effect=fake_stories):
        app._on_setup_sprint_change(types.SimpleNamespace(data='526', control=None))
        assert app.setup_sprint == '526' and loaded == ['526'], loaded
        assert app.story_ids == ['SS-1'], app.story_ids        # SS-3 isn't in that sprint
        # Editable dropdown handing back the TEXT works too.
        app._on_setup_sprint_change(types.SimpleNamespace(data='BCM Sprint 1  \u00b7  active', control=None))
        assert app.setup_sprint == '423', app.setup_sprint
        app._on_setup_sprint_change(types.SimpleNamespace(data='__backlog__', control=None))
        assert app.setup_sprint == '__backlog__'
        app._on_setup_sprint_change(types.SimpleNamespace(data='__active__', control=None))
        assert app.setup_sprint == ''
    print('SIM-OK')
""") % ROOT


class PickerUI(unittest.TestCase):
    def test_picker(self):
        r = subprocess.run([sys.executable, "-c", _SIM], capture_output=True, text=True,
                           encoding="utf-8", timeout=300, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
