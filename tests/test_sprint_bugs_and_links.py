"""Sprint Summary Bugs card, Sprint Report translation skip/retry + status
colours, Jira summary bug fetch, and links opening in front of QA Studio."""
import asyncio
import os
import subprocess
import sys
import textwrap
import types
import unittest
from unittest import mock

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
    WITH_BUGS = %r
    stories = [{'id': 'ST-1', 'title': 't', 'state': 'Done', 'test_cases': 1, 'assigned_to': 'A'}]
    bugs = ([{'id': 'SS-%%d' %% (100 + i), 'title': 'Bug %%d' %% i, 'state': 'Done',
              'tags': 'regression' if i == 0 else '',
              'url': 'https://x.atlassian.net/browse/SS-%%d' %% (100 + i)} for i in range(4)] +
            [{'id': 'SS-200', 'title': 'Open bug', 'state': 'New', 'tags': '',
              'url': 'https://x.atlassian.net/browse/SS-200'}])
    data = {'plan_name': 'P', 'iteration': 'Sprint 1', 'stories': stories, 'url_map': {}}
    if WITH_BUGS:
        data['bugs'] = bugs
    class App:
        def __getattr__(self, n): return mock.MagicMock()
    app = App()
    app.creds = {'backend': 'jira_zephyr'}
    app.project, app.plan_id, app.emails, app.readonly = 'SS', 'F1', '', False
    app.can = lambda p: True
    app.ui_safe = lambda fn: fn()
    opened = []
    app._open_url = lambda u: opened.append(u)
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
         mock.patch.object(backend_setup, 'sprint_summary', return_value=data):
        modals.open_sprint_summary(app)
        for t in threads: t.join(3)
    dlg = dlgs[-1]
    title = strings.t('sum_bugs')
    if not WITH_BUGS:
        assert title not in texts(dlg), 'Azure (no bug data) must not show the Bugs card'
        print('SIM-OK'); sys.exit(0)
    assert texts(dlg).count(title) >= 2, 'Bugs tile + Bugs card'
    assert strings.t('sum_bugs_split', reg=1, sprint=4) in texts(dlg)
    def chips(root):
        return [c for c in walk(root) if type(c).__name__ == 'Container'
                and callable(c.__dict__.get('on_click'))
                and str(c.__dict__.get('tooltip', '')).endswith(
                    strings.t('sum_bugs_chip_tip', status='', n='').split('—')[-1].strip())]
    cs = chips(dlg)
    assert len(cs) == 2, len(cs)
    def keys(root): return [v for v in texts(root) if str(v).startswith('SS-1') or v == 'SS-200']
    def click(c):
        # chip clicks show a spinner, then rebuild the Bugs card in the background
        c.on_click(None)
        for t in list(threads): t.join(3)
    assert keys(dlg) == [], 'bug list closed until a chip is clicked'
    click([c for c in cs if c.tooltip.startswith('Done')][0])
    assert keys(dlg) == ['SS-100', 'SS-101', 'SS-102', 'SS-103'], keys(dlg)
    row = [c for c in walk(dlg) if type(c).__name__ == 'Container'
           and c.__dict__.get('tooltip') == 'SS-102'][0]
    row.on_click(None)
    assert opened == ['https://x.atlassian.net/browse/SS-102'], opened
    click([c for c in chips(dlg) if c.tooltip.startswith('New')][0])
    assert keys(dlg) == ['SS-200'], keys(dlg)
    click([c for c in chips(dlg) if c.tooltip.startswith('New')][0])
    assert keys(dlg) == [], 'second click closes the list'
    print('SIM-OK')
""")


def _sprint_titles():
    """Import sprint_titles with Flet and the AI call stubbed."""
    for name in ("flet", "image_assets"):
        sys.modules.setdefault(name, mock.MagicMock())
    import sprint_titles
    return sprint_titles


class SprintSummaryBugs(unittest.TestCase):
    def _run(self, with_bugs):
        r = subprocess.run([sys.executable, "-c", _SIM % (ROOT, with_bugs)],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=120, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])

    def test_bugs_card_chips_list_and_links(self):
        self._run(True)

    def test_no_card_without_bug_data(self):
        self._run(False)

    def test_jira_summary_fetches_sprint_bugs(self):
        import backend_setup
        sp = types.SimpleNamespace(path="1561", name="Sprint 1", state="active")
        story = types.SimpleNamespace(ref=types.SimpleNamespace(key="SS-1", id="1"),
                                      title="t", state="Done", assignee=None)
        be = mock.MagicMock()
        be.fetch_sprints.return_value = [sp]
        be.fetch_story_summaries_in_sprint.return_value = [story]
        be.linked_test_case_count.return_value = 2
        be.fetch_bugs_in_sprint.return_value = [{"id": "SS-9", "state": "New"},
                                                {"id": "SS-9", "state": "New"}]
        app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS",
                                    plan_name="P", setup_sprint="1561")
        with mock.patch.object(backend_setup, "get_backend", return_value=be), \
             mock.patch.object(backend_setup, "_as_project", return_value="SS"), \
             mock.patch.object(backend_setup, "is_azure", return_value=False):
            d = backend_setup.sprint_summary(app, "F1")
        self.assertEqual([b["id"] for b in d["bugs"]], ["SS-9"])   # de-duplicated

    def test_strings_all_languages(self):
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            S = strings._STRINGS[lang]
            for k in ("sum_bugs", "sum_p_bugs", "sum_bugs_split", "sum_bugs_chip_tip",
                      "sum_bugs_none", "sum_bugs_more", "spr_p_fetch", "spr_p_translate",
                      "spr_p_retry", "spr_ok_no_translation_needed", "spr_ok_report_ready_n",
                      "spr_ok_translated_n", "spr_ok_retranslate_none_needed",
                      "spr_warn_untranslated", "spr_bug_show_more", "spr_bug_open_tip"):
                self.assertTrue(S.get(k), lang + " " + k)
        # {key} would collide with strings.t(key, **kw)
        self.assertEqual(strings.t("spr_bug_open_tip", issue="SS-1"), "Open SS-1")


class SprintReportTranslation(unittest.TestCase):
    def setUp(self):
        self.S = _sprint_titles()
        self.calls = []

        def fake_ai(prompt, max_tokens=0):
            import json
            arr = json.loads(prompt[prompt.index("["):])
            self.calls.append(len(arr))
            ar = "Arabic" in prompt
            # first big batch: the model echoes every 3rd item untranslated
            return json.dumps([t if (len(arr) > 5 and i % 3 == 0)
                               else (("ترجمة " + t) if ar else "x") for i, t in enumerate(arr)],
                              ensure_ascii=False)
        p = mock.patch.object(self.S.E, "ai_complete", side_effect=fake_ai)
        p.start()
        self.addCleanup(p.stop)

    def test_same_language_makes_no_ai_call(self):
        titles = ["TAMM - Story %d" % i for i in range(266)]
        out, err, need, done = self.S._translate(titles, "en")
        self.assertEqual((self.calls, need, out), ([], 0, titles))

    def test_missed_titles_are_retried(self):
        titles = ["Story %d" % i for i in range(266)]
        out, err, need, done = self.S._translate(titles, "ar")
        self.assertEqual((need, done), (266, 266))

    def test_status_colours_distinct(self):
        sts = ["Done", "Released", "Dropped", "Waiting", "New", "In QA", "In UAT"]
        cols = [self.S.status_dot_color(s) for s in sts]
        self.assertEqual(len(set(cols)), len(cols), dict(zip(sts, cols)))
        self.assertEqual(self.S.status_dot_color("Mystery"),
                         self.S.status_dot_color("mystery"))   # stable


class LinksOpenInFront(unittest.TestCase):
    def _launcher(self, launch):
        import mobile_url_launcher as M
        page = types.SimpleNamespace(run_task=lambda fn, *a: asyncio.run(fn(*a)))
        lau = types.SimpleNamespace(launch_url=launch)
        return M, mock.patch.multiple(M, _launcher=lau, _page=page)

    def test_opens_through_window_process(self):
        seen = []

        async def ok(url):
            seen.append(url)
        M, p = self._launcher(ok)
        with p:
            fb = mock.Mock()
            self.assertTrue(M.open_front("https://a", fallback=fb))
        self.assertEqual(seen, ["https://a"])
        fb.assert_not_called()

    def test_falls_back_when_launch_fails_or_hangs(self):
        async def boom(url):
            raise RuntimeError("no client")

        async def hang(url):
            await asyncio.sleep(5)
        for launch in (boom, hang):
            M, p = self._launcher(launch)
            with p:
                fb = mock.Mock()
                M.open_front("https://b", fallback=fb, timeout=0.2)
            fb.assert_called_once_with("https://b")

    def test_not_attached_returns_false(self):
        import mobile_url_launcher as M
        with mock.patch.multiple(M, _launcher=None, _page=None):
            self.assertFalse(M.open_front("https://c"))


_READY_SIM = textwrap.dedent("""
    import sys
    sys.path.insert(0, %r)
    from unittest import mock
    sys.modules['flet'] = mock.MagicMock()
    for name in ('ai_usage_screen', 'identity_editor', 'login', 'orgs_screen', 'perf',
                 'perf.models', 'perf.ports', 'report', 'sprint_titles', 'task_manager',
                 'useful_links', 'users_screen', 'window_chrome'):
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
    app.can = lambda p: True
    app.project, app.plan_id, app.setup_sprint = 'SS', None, '1561'
    app.creds = {'backend': 'jira_zephyr'}
    assert app._summary_ready(), 'Jira: sprint picked, no folder -> enabled'
    app.setup_sprint = ''
    assert app._summary_ready(), 'Jira: all active sprints -> enabled'
    app.setup_sprint = '__backlog__'
    assert not app._summary_ready(), 'Jira: backlog -> disabled'
    app.creds = {'backend': 'azure'}
    app.setup_sprint = '1561'
    assert not app._summary_ready(), 'Azure without a plan -> disabled'
    app.plan_id = 'F1'
    assert app._summary_ready(), 'Azure with a plan -> enabled'
    app.can = lambda p: False
    assert not app._summary_ready(), 'no permission -> disabled'
    print('SIM-OK')
""")


class SummaryButtonReady(unittest.TestCase):
    def test_enabled_by_sprint_on_jira_plan_on_azure(self):
        r = subprocess.run([sys.executable, "-c", _READY_SIM % ROOT], capture_output=True,
                           text=True, encoding="utf-8", timeout=120, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])


class CollapseSpinners(unittest.TestCase):
    """Clicking a big group shows a spinner FIRST, then rebuilds off the UI thread."""

    def _deferred_app(self):
        queued = []
        return types.SimpleNamespace(ui_safe=queued.append, _open_url=lambda u: None,
                                     _st_bug_open=set()), queued

    def test_plan_table_feature_header(self):
        sys.modules.setdefault("flet", mock.MagicMock())
        import regression
        Ctl = type("Ctl", (), {"__init__": lambda self, *a, **k: (self.__dict__.update(k),
                                                                  setattr(self, "args", a),
                                                                  setattr(self, "content", a[0] if a else k.get("content")))[0],
                               "update": lambda self: None})
        fl = mock.MagicMock()
        for n in ("Container", "ProgressRing", "Icon", "Row", "Text"):
            setattr(fl, n, type(n, (Ctl,), {}))
        app, queued = self._deferred_app()
        toggled = []
        with mock.patch.object(regression, "ft", fl):
            hdr = regression._feature_header(app, "Feature", 3, True,
                                             lambda e: toggled.append(1))
            slot = hdr.content.args[0][-1]
            hdr.on_click(None)
            self.assertEqual(type(slot.content).__name__, "ProgressRing")
            self.assertEqual(toggled, [])           # rebuild not run yet
            hdr.on_click(None)                      # double click ignored
            self.assertEqual(len(queued), 1)
            queued.pop()()
        self.assertEqual(toggled, [1])


_PICKER_SIM = textwrap.dedent("""
    import sys, threading
    sys.path.insert(0, %r)
    from unittest import mock
    class Ctl:
        def __init__(self, *a, **k):
            self.__dict__.update(k); self.args = a
            if a and 'controls' not in k and isinstance(a[0], list): self.controls = a[0]
            elif a and 'content' not in k and not isinstance(a[0], str): self.content = a[0]
        def update(self): self.updates = getattr(self, 'updates', 0) + 1
    fl = mock.MagicMock()
    for n in ("Column", "Row", "Container", "TextField", "Text", "Icon", "IconButton",
              "Checkbox", "GestureDetector", "ListView", "Stack"):
        setattr(fl, n, type(n, (Ctl,), {}))
    sys.modules['flet'] = fl
    import regression, backend_setup
    gate = threading.Event()
    def members(app, proj):
        gate.wait(5)
        return [{'email': 'a@x.com', 'name': 'A'}]
    class App:
        def __getattr__(self, n): return mock.MagicMock()
    app = App()
    app.project = 'SS'; app.emails = ''; app._members_cache = None
    app._members_loading = False; app._email_open = False
    renders = []
    app.render = lambda: renders.append(1)
    app.ui_safe = lambda fn: fn()
    with mock.patch.object(backend_setup, 'fetch_project_members', side_effect=members):
        holder = regression.email_recipient_picker(app, 'emails', is_open_key='_email_open',
                                                   sync_key='setup_emails')
        first = list(holder.controls)
        gate.set()
        for t in threading.enumerate():
            if t is not threading.current_thread() and t.daemon: t.join(3)
    assert renders == [], 'members loading must not redraw the whole screen'
    assert holder.controls != first and getattr(holder, 'updates', 0) == 1
    assert app._members_cache == [{'email': 'a@x.com', 'name': 'A'}]
    print('SIM-OK')
""")


class EmailPickerInPlace(unittest.TestCase):
    """Second draw on Connect: Setup's email picker loaded members and then
    called app.render(); it must only rebuild its own cell."""

    def test_members_loaded_updates_picker_only(self):
        r = subprocess.run([sys.executable, "-c", _PICKER_SIM % ROOT], capture_output=True,
                           text=True, encoding="utf-8", timeout=120, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
