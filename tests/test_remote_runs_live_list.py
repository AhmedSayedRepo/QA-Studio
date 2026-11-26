"""Remote Runs list refreshes by itself and shows each in-progress run's
progress on its row (no need to open the run) — updated in place, no render."""
import os
import subprocess
import sys
import textwrap
import unittest

import strings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SIM = textwrap.dedent("""
    import sys
    sys.path.insert(0, %r)
    from unittest import mock
    class Ctl:
        def __init__(self, *a, **k):
            self.__dict__.update(k); self.args = a
            if a and 'controls' not in k and isinstance(a[0], list): self.controls = a[0]
            elif a and 'content' not in k and not isinstance(a[0], str): self.content = a[0]
            if a and isinstance(a[0], str): self.value = a[0]
        def update(self): self.updates = getattr(self, 'updates', 0) + 1
    fl = mock.MagicMock()
    for n in ("Column", "Row", "Container", "Text", "Icon", "ProgressBar", "ProgressRing"):
        setattr(fl, n, type(n, (Ctl,), {}))
    sys.modules['flet'] = fl
    for _ in range(40):
        try:
            import remote_runs_screen as RR
            break
        except ModuleNotFoundError as e:
            sys.modules[e.name] = mock.MagicMock()
            sys.modules.pop('remote_runs_screen', None)
    import strings
    RR.card = lambda c, **k: Ctl(c); RR.empty_state = lambda *a, **k: Ctl()
    RR.ghost_btn = lambda *a, **k: Ctl(); RR._status_badge = lambda s: Ctl(s)
    RR._start_list_poll = lambda app: None          # driven by hand below
    runs = [{'id': 'r1', 'status': 'running', 'kind': 'steps', 'story_ids': ['SS-1', 'SS-2'],
             'project': 'SS', 'plan_id': 'F1', 'created_at': None},
            {'id': 'r2', 'status': 'done', 'kind': 'titles', 'story_ids': ['SS-3'],
             'project': 'SS', 'plan_id': 'F1', 'created_at': None}]
    events = {'r1': [{'seq': 1, 'kind': 'stat', 'payload': {'stories_done': 1, 'total_stories': 2}},
                     {'seq': 2, 'kind': 'progress', 'payload': {'pct': 40, 'label': '40%% · 4 of 10'}}]}
    asked = []
    def get_events(rid, after_seq=0, limit=500):
        asked.append((rid, after_seq))
        return [e for e in events.get(rid, []) if e['seq'] > after_seq]
    RR.auth = mock.MagicMock()
    RR.auth.list_remote_runs = lambda limit=50: [dict(r) for r in runs]
    RR.auth.get_remote_run_events = get_events
    class App: pass
    app = App()
    app.active = 'remote_runs'; app._rr_view_id = None
    app._rr_list = [dict(r) for r in runs]; app._rr_list_loading = False
    renders = []
    app.render = lambda: renders.append(1)
    app.ui_safe = lambda fn: fn()
    app.shell = lambda t, s, body, **k: body
    RR._list_screen(app)
    bar, txt = app._rr_row_cells['r1']
    assert 'r2' not in app._rr_row_cells, 'finished runs show no progress bar'
    assert txt.value == strings.t('rr_row_waiting') and bar.value is None
    live = RR._poll_list_once(app)
    assert live is True and renders == []
    assert abs(bar.value - 0.4) < 1e-9, bar.value
    assert txt.value == '40%% · 4 of 10  ·  ' + strings.t('rr_row_stories', done=1, total=2), txt.value
    RR._poll_list_once(app)
    assert asked[-1] == ('r1', 2), asked          # incremental: only NEW events
    runs[0]['status'] = 'done'                     # status change -> rows rebuilt in place
    live = RR._poll_list_once(app)
    assert live is False and renders == []
    assert 'r1' not in app._rr_row_cells and app._rr_list_col.updates >= 1
    print('SIM-OK')
""") % ROOT


class RemoteRunsLiveList(unittest.TestCase):
    def test_list_shows_progress_in_place(self):
        r = subprocess.run([sys.executable, "-c", _SIM], capture_output=True, text=True,
                           encoding="utf-8", timeout=120, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])

    def test_strings(self):
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            S = strings._STRINGS[lang]
            self.assertIn("{done}", S["rr_row_stories"], lang)
            self.assertIn("{total}", S["rr_row_stories"], lang)
            self.assertTrue(S.get("rr_row_waiting"), lang)
            self.assertTrue(S.get("upd_note_remote_runs_live"), lang)


if __name__ == "__main__":
    unittest.main()
