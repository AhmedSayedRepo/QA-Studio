"""Shared activity-log pieces (log_view.py + log_panel.py): the Generate log
gets the Automation log's All / Issues filter (with an issue count), every log
copies and saves the same 'HH:MM:SS  ✓ [id] message' text, and Save writes a
.log file to Downloads."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import log_view as V  # noqa: E402
import strings  # noqa: E402


def L(msg, tone="dim", **kw):
    d = {"msg": msg, "tone": tone}
    d.update(kw)
    return d


class SharedLogic(unittest.TestCase):
    LINES = [L("Story 12 · Login", "story", ico="▸"), L("Steps written", "ok", id=101),
             L("Skipped — duplicate", "warn", id=102), L("Failed: timeout", "err", id=103),
             L("HTTP 504", "dim", indent=True), L("Next", "ok"), L("note", "dim", indent=True)]

    def test_issue_filter_keeps_details_under_an_issue(self):
        self.assertEqual(V.flat_visibility(self.LINES, True), [False, False, True, True, True, False, False])
        self.assertEqual(V.flat_visibility(self.LINES, False), [True] * 7)
        self.assertEqual(V.issue_count(self.LINES), 2)
        self.assertEqual([l["msg"] for l in V.flat_lines_to_copy(self.LINES, True)],
                         ["Skipped — duplicate", "Failed: timeout", "HTTP 504"])

    def test_copy_text_has_time_glyph_and_id(self):
        lines = [V.stamp(dict(l), 0) for l in self.LINES[:5]] + [L("", "dim"), L("a\x1fb", "case")]
        out = V.format_lines(lines)
        self.assertEqual(len(out), 6)                       # the empty line is dropped
        self.assertRegex(out[0], r"^\d\d:\d\d:\d\d  ▸ Story 12")
        self.assertTrue(out[2].endswith("⚠ [102] Skipped — duplicate"))
        self.assertIn("     HTTP 504", out[4])
        self.assertTrue(out[5].startswith("          ") and out[5].endswith("a b"))   # no time yet; \x1f -> space

    def test_stamp_is_set_once(self):
        line = V.stamp({"msg": "x"}, 5)
        V.stamp(line, 9)
        self.assertEqual(line["ts"], 5)

    def test_save_writes_a_log_file(self):
        d = tempfile.mkdtemp()
        path = V.save_lines(self.LINES, "Generate", folder=d)
        self.assertTrue(os.path.basename(path).startswith("qa-studio-generate-"))
        self.assertTrue(path.endswith(".log"))
        with open(path, encoding="utf-8") as f:
            self.assertEqual(len(f.read().strip().splitlines()), 7)
        self.assertEqual(V.save_lines([L("  ")], "x", folder=d), "")

    def test_strings(self):
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            for k in ("log_save_tip", "log_saved", "log_save_failed", "log_nothing_save", "upd_note_shared_logs"):
                self.assertTrue(strings._STRINGS[lang].get(k), lang + " " + k)
            self.assertIn("{path}", strings._STRINGS[lang]["log_saved"])
            self.assertIn("{err}", strings._STRINGS[lang]["log_save_failed"])


class Wiring(unittest.TestCase):
    def test_both_logs_use_the_shared_toolbar(self):
        run_src = open(os.path.join(ROOT, "run.py"), encoding="utf-8").read()
        auto_src = open(os.path.join(ROOT, "automation.py"), encoding="utf-8").read()
        for src in (run_src, auto_src):
            self.assertIn("log_panel.tool_btn", src)
            self.assertIn("log_panel.filter_chips(", src)
            self.assertIn("log_panel.save_btn(", src)
            self.assertNotIn("def _log_tool_btn", src)


_SIM = textwrap.dedent("""
    import os, sys, tempfile, threading, types
    sys.path.insert(0, %r)
    from unittest import mock
    class Ctl:
        def __init__(self, *a, **k):
            self.__dict__.update(k)
            self.args = a
            if a and 'controls' not in k and isinstance(a[0], list): self.controls = a[0]
            elif a and 'content' not in k and not isinstance(a[0], str): self.content = a[0]
            if a and isinstance(a[0], str): self.value = a[0]
        def update(self): pass
    fl = mock.MagicMock()
    for n in ("Column", "Row", "Container", "TextField", "Text", "Icon", "GestureDetector", "ListView",
              "IconButton", "ProgressRing", "ProgressBar", "FilledButton"):
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
    import run, log_view
    Q = main.QAStudio
    app = Q.__new__(Q)
    toasts, clips = [], []
    app._toast = lambda m: toasts.append(m)
    app._snack = lambda m, *a: toasts.append(m)
    app._copy_text_to_clipboard = lambda text, *a, **k: clips.append(text)
    app.ui_safe = lambda fn: fn()
    app.page = mock.MagicMock()
    app.active, app.tool = 'run', 'steps'
    app.shell = lambda *a, **k: a
    app._build_story_cards = lambda: []
    app._run_meta_line = lambda: ''
    app.goto = lambda *a: None
    app._run_active = app._run_started = True
    app._run_finished = False
    app._stats = {'total': 3, 'stories_done': 0, 'total_stories': 1, 'done': 1, 'skipped': 1,
                  'errors': 1, 'created': 0}
    app._progress = {'pct': 30, 'label': '30%%'}
    app._run_log_ui_lock = threading.Lock()
    app._log_lines = [{'tone': 'story', 'ico': '>', 'msg': 'Story 12'},
                      {'tone': 'ok', 'id': 101, 'msg': 'written'},
                      {'tone': 'warn', 'id': 102, 'msg': 'skipped'},
                      {'tone': 'err', 'id': 103, 'msg': 'failed'},
                      {'tone': 'dim', 'indent': True, 'msg': 'HTTP 504'}]
    run.screen(app)
    col = app._log_col
    assert app._run_issue_n == 2, app._run_issue_n
    app._set_run_log_filter(True)
    assert [c.visible for c in col.controls] == [False, False, True, True, True]
    app._copy_run_log()
    assert clips[-1].count('\\n') == 2 and '[103] failed' in clips[-1], clips[-1]
    app._log_lines += [{'tone': 'ok', 'msg': 'more'}, {'tone': 'err', 'msg': 'again'}]
    app._refresh_run()
    assert [c.visible for c in col.controls] == [False, False, True, True, True, False, True]
    assert app._run_issue_n == 3
    chip = app._run_log_filter_ctr.content.controls[1].content.value
    assert chip.endswith(' 3'), chip
    app._set_run_log_filter(False)
    assert all(c.visible for c in col.controls)
    app._rendered_count = -1                 # a removed spinner line -> full rebuild
    app._refresh_run()
    assert len(col.controls) == 7
    home = tempfile.mkdtemp()
    os.makedirs(os.path.join(home, 'Downloads'))
    with mock.patch.dict(os.environ, {'HOME': home, 'USERPROFILE': home}):
        app._save_run_log()
    saved = os.listdir(os.path.join(home, 'Downloads'))
    assert len(saved) == 1 and saved[0].startswith('qa-studio-generate-'), saved
    path = os.path.join(home, 'Downloads', saved[0])
    assert saved[0] in toasts[-1], toasts[-1]
    assert len(open(path, encoding='utf-8').read().splitlines()) == 7
    app._clear_run_log()
    assert app._run_issue_n == 0
    # Automation: the Issues chip refreshes only when the count changes.
    app._auto_log = [log_view.stamp({'msg': m, 'tone': t}) for m, t in [
        ('Launching', 'dim'), ("  walking 'Login'  [x]", 'case'), ('ok', 'ok'), ('missing', 'warn')]]
    app._auto_running = True
    app._auto_log_col = fl.ListView(controls=[app._auto_log_line(e['msg'], e['tone']) for e in app._auto_log])
    calls = []
    app._auto_refresh_log_filter = lambda: calls.append(1)
    app._apply_log_view(app._auto_log_col)
    app._apply_log_view(app._auto_log_col)
    assert calls == [1] and app._auto_issue_n == 1, calls
    app._auto_log_issues = True
    app._copy_auto_log()
    assert 'missing' in clips[-1] and 'Launching' not in clips[-1], clips[-1]
    print('SIM-OK')
""") % ROOT


class Behaviour(unittest.TestCase):
    def test_generate_and_automation_logs(self):
        r = subprocess.run([sys.executable, "-c", _SIM], capture_output=True, text=True,
                           encoding="utf-8", timeout=300, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
