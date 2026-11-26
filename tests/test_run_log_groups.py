"""Generate (Run) log grouped by story like the Automation log: a story header
with a status pill + count that folds its lines, and each test case's time
shown inside its own row instead of as a separate "⏱ 0:03" line."""
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import log_view as V  # noqa: E402
import strings  # noqa: E402

LINES = [
    {"tone": "dim", "msg": "Connecting…"},
    {"tone": "story", "ico": "▸", "msg": "Story SS-1 · Login"},
    {"tone": "ok", "id": "SS-T1", "msg": "Verify A", "detail": "⏱ 0:03"},
    {"tone": "ok", "id": "SS-T2", "msg": "Verify B", "detail": "⏱ 0:02"},
    {"tone": "story", "ico": "▸", "msg": "Story SS-2 · Logout"},
    {"tone": "ok", "id": "SS-T3", "msg": "Verify C", "detail": "⏱ 0:01"},
    {"tone": "warn", "msg": "Created SS-T3 but couldn't link it"},
    {"tone": "err", "msg": "Verify D — boom"},
    {"tone": "dim", "indent": True, "msg": "detail of the error"},
]


class RunGroupsModel(unittest.TestCase):
    def test_build(self):
        m = V.run_build(LINES)
        self.assertEqual(m["group"], [None, 0, 0, 0, 1, 1, 1, 1, 1])
        self.assertEqual(m["headers"], {0: 1, 1: 4})
        self.assertEqual(m["status"], {0: "ok", 1: "err"})
        self.assertEqual(m["counts"][0], {"ok": 2, "warn": 0, "err": 0})
        self.assertEqual(m["counts"][1]["ok"], 1)

    def test_visibility(self):
        # Latest story open, earlier one folded; lines outside a story always show.
        self.assertEqual(V.run_visibility(LINES), [True, True, False, False] + [True] * 5)
        self.assertEqual(V.run_visibility(LINES, {0: True, 1: False}),
                         [True, True, True, True, True, False, False, False, False])
        # Issues: headers of stories with an issue + issue lines (+ their detail).
        self.assertEqual(V.run_visibility(LINES, None, True),
                         [False, False, False, False, True, False, True, True, True])

    def test_copy_includes_folded_lines_and_time(self):
        self.assertEqual(len(V.run_lines_to_copy(LINES)), len(LINES))
        text = V.format_lines([dict(LINES[2], ts=0)])[0]
        self.assertTrue(text.endswith("[SS-T1] Verify A  ·  ⏱ 0:03"), text)

    def test_strings(self):
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            for k in ("runlog_cases", "runlog_issues"):
                self.assertIn("{n}", strings._STRINGS[lang].get(k, ""), lang + " " + k)

    def test_detail_not_a_separate_line(self):
        src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
        self.assertNotIn('self._log_lines.append({"tone": "dim", "indent": True, "msg": payload["detail"]})', src)
        self.assertIn("log_view.run_lines_to_copy(", src)


_SIM = textwrap.dedent("""
    import sys
    sys.path.insert(0, %r)
    from unittest import mock
    class Ctl:
        def __init__(self, *a, **k):
            self.__dict__.update(k)
            self.args = a
            if a and 'controls' not in k and isinstance(a[0], list): self.controls = a[0]
            elif a and 'content' not in k and not isinstance(a[0], str): self.content = a[0]
            if a and isinstance(a[0], str): self.value = a[0]
            self.visible = k.get('visible', True)
        def update(self): pass
    fl = mock.MagicMock()
    for n in ("Column", "Row", "Container", "Text", "Icon", "IconButton", "ProgressRing"):
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
    Q = main.QAStudio
    app = Q.__new__(Q)
    app._log_lines = [
        {"tone": "story", "ico": "x", "msg": "Story SS-1 · Login"},
        {"tone": "ok", "id": "SS-T1", "msg": "Verify A", "detail": "\\u23f1 0:03"},
        {"tone": "ok", "id": "SS-T2", "msg": "Verify B", "detail": "\\u23f1 0:02"},
        {"tone": "story", "ico": "x", "msg": "Story SS-2 · Out"},
        {"tone": "warn", "msg": "couldn't link"},
    ]
    app._log_col = fl.Column(app._render_log_lines())
    app._apply_run_log_view(app._log_col)
    c = app._log_col.controls
    assert len(c) == 5
    h0, h1 = c[0], c[3]
    assert h0._qa_count.value == main.strings.t('runlog_cases', n=2), h0._qa_count.value
    assert h1._qa_pill_txt.value == '\\u26a0'
    assert c[1].visible is False and c[2].visible is False and c[4].visible is True
    def texts(x, out):
        if isinstance(x, Ctl):
            if type(x).__name__ == 'Text': out.append(x.value)
            for k in ('content', 'controls'):
                v = x.__dict__.get(k)
                for ch in (v if isinstance(v, list) else [v]): texts(ch, out)
        return out
    assert '\\u23f1 0:03' in texts(c[1], []), texts(c[1], [])
    h0.on_click(None)
    assert c[1].visible is True and c[2].visible is True
    h0.on_click(None)
    assert c[1].visible is False
    app._clear_run_log = None
    print('SIM-OK')
""") % ROOT


class RunGroupsUI(unittest.TestCase):
    def test_render_and_toggle(self):
        r = subprocess.run([sys.executable, "-c", _SIM], capture_output=True, text=True,
                           encoding="utf-8", timeout=300, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
