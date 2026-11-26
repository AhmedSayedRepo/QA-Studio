"""Sprint Summary: a loading bar + "may take time" note while it loads, and
clickable status cards that filter the story list (click again / chip clears)."""
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import strings  # noqa: E402

_SIM = textwrap.dedent("""
    import sys, threading, time
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
    for n in ("Column", "Row", "Container", "TextField", "Text", "Icon", "IconButton", "Stack",
              "ProgressRing", "ProgressBar", "FilledButton", "AlertDialog", "ListView",
              "GestureDetector", "Dropdown", "DropdownOption", "Checkbox", "TextButton"):
        setattr(fl, n, type(n, (Ctl,), {}))
    sys.modules['flet'] = fl
    for _ in range(80):
        try:
            import modals
            break
        except ModuleNotFoundError as e:
            sys.modules[e.name] = mock.MagicMock()
            sys.modules.pop('modals', None)
    import backend_setup, strings
    stories = ([{'id': 'SS-%%d' %% i, 'title': 't', 'state': 'Released', 'test_cases': 1,
                 'assigned_to': 'A'} for i in range(5)] +
               [{'id': 'SS-9%%d' %% i, 'title': 't', 'state': 'In QA', 'test_cases': 2,
                 'assigned_to': 'B'} for i in range(2)])
    data = {'plan_name': 'P', 'iteration': 'Sprint 1', 'stories': stories, 'url_map': {}}
    gate = threading.Event()
    def fake_summary(app, pid, progress=None):
        gate.wait(5)
        return data
    class App:
        def __getattr__(self, n): return mock.MagicMock()
    app = App()
    app.creds = {'backend': 'jira_zephyr'}
    app.project, app.plan_id, app.emails, app.readonly = 'SS', 'F1', '', False
    app.can = lambda p: True
    app.ui_safe = lambda fn: fn()
    threads = []
    def bg(fn):
        t = threading.Thread(target=fn, daemon=True); t.start(); threads.append(t)
    app._bg = bg
    dlgs = []
    app._show_dialog = lambda d: dlgs.append(d)
    def walk(c, seen=None):
        seen = seen if seen is not None else set()
        if not isinstance(c, Ctl) or id(c) in seen: return
        seen.add(id(c)); yield c
        for k in ('content', 'controls', 'actions'):
            v = c.__dict__.get(k)
            for ch in (v if isinstance(v, list) else [v]):
                yield from walk(ch, seen)
    def texts(root): return [getattr(c, 'value', '') for c in walk(root) if type(c).__name__ == 'Text']
    with mock.patch.object(backend_setup, 'sprint_summary_available', return_value=True), \\
         mock.patch.object(backend_setup, 'sprint_summary', side_effect=fake_summary):
        modals.open_sprint_summary(app)
        time.sleep(0.3)
        dlg = dlgs[-1]
        # Loading: moving bar + the "may take time" note.
        bars = [c for c in walk(dlg) if type(c).__name__ == 'ProgressBar']
        assert bars and bars[0].value is None, bars
        assert strings.t('sum_may_take_time') in texts(dlg)
        gate.set()
        for t in threads: t.join(3)
    def cards(root):
        return [c for c in walk(root) if type(c).__name__ == 'Container'
                and callable(c.__dict__.get('on_click')) and c.__dict__.get('ink')
                and str(c.__dict__.get('tooltip', '')).startswith(('Released', 'In QA'))]
    def ids(root): return [v for v in texts(root) if str(v).startswith('SS-')]
    cs = cards(dlg)
    assert len(cs) == 2, len(cs)
    assert len([c for c in walk(dlg) if type(c).__name__ == 'IconButton']) >= 7
    qa = [c for c in cs if c.tooltip.startswith('In QA')][0]
    qa.on_click(None)
    show = strings.t('sum_filter_showing', status='In QA', n=2, total=7)
    assert show in texts(dlg), texts(dlg)
    rows = [c for c in walk(dlg) if type(c).__name__ == 'IconButton']
    assert len(rows) == 2, len(rows)
    cs = cards(dlg)
    on = [c for c in cs if c.tooltip.startswith('In QA')][0]
    off = [c for c in cs if c.tooltip.startswith('Released')][0]
    assert on.opacity == 1.0 and off.opacity < 1.0
    # Clear chip resets to all stories.
    chip = [c for c in walk(dlg) if type(c).__name__ == 'Container'
            and c.__dict__.get('tooltip') == strings.t('sum_filter_clear')][0]
    chip.on_click(None)
    assert show not in texts(dlg)
    assert len([c for c in walk(dlg) if type(c).__name__ == 'IconButton']) == 7
    # Clicking the same card twice toggles off.
    [c for c in cards(dlg) if c.tooltip.startswith('Released')][0].on_click(None)
    assert len([c for c in walk(dlg) if type(c).__name__ == 'IconButton']) == 5
    [c for c in cards(dlg) if c.tooltip.startswith('Released')][0].on_click(None)
    assert len([c for c in walk(dlg) if type(c).__name__ == 'IconButton']) == 7
    print('SIM-OK')
""") % ROOT


class SprintSummaryFilter(unittest.TestCase):
    def test_strings(self):
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            S = strings._STRINGS[lang]
            for k in ("sum_may_take_time", "sum_filter_tip", "sum_filter_showing", "sum_filter_clear"):
                self.assertTrue(S.get(k), lang + " " + k)
            for ph in ("{status}", "{n}", "{total}"):
                self.assertIn(ph, S["sum_filter_showing"], lang)
            self.assertIn("{status}", S["sum_filter_tip"], lang)

    def test_modal_filter_and_loading(self):
        r = subprocess.run([sys.executable, "-c", _SIM], capture_output=True, text=True,
                           encoding="utf-8", timeout=120, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
