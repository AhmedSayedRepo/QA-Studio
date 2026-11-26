"""Settings: every selection updates IN PLACE (no app.render()), the Test style
toggle is in Settings, Settings' selections are the defaults (Setup's toggles
are per-run overrides), and Restore preferences goes back to those defaults."""
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
        def update(self): pass
    fl = mock.MagicMock()
    for n in ("Column", "Row", "Container", "TextField", "Text", "Icon", "IconButton", "Stack",
              "ProgressRing", "ProgressBar", "FilledButton", "AlertDialog", "ListView", "Switch",
              "GestureDetector", "Dropdown", "DropdownOption", "Checkbox", "TextButton", "Image"):
        setattr(fl, n, type(n, (Ctl,), {}))
    sys.modules['flet'] = fl
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
    import settings, store, strings
    saved = []
    store.save = lambda c: saved.append(dict(c))
    Q = main.QAStudio
    app = Q.__new__(Q)
    renders = []
    app.render = lambda: renders.append(1)
    app.creds = {}
    app.readonly = False
    app.active = 'settings'
    app.tool, app.lang, app.run_lang, app.ui_lang = 'titles', 'ar', 'ar', 'en'
    app.case_style = 'automation'            # a Setup per-run override
    app.user = {'name': 'A'}
    app._identity_visuals = {}
    app._toast = lambda *a, **k: None
    app._fetch_estimate = lambda: None
    app._open_profile_picture_editor = lambda e=None: None
    app.shell = lambda title, sub, body, **k: body
    app.page = mock.MagicMock()
    def walk(c, seen=None):
        seen = seen if seen is not None else set()
        if not isinstance(c, Ctl) or id(c) in seen: return
        seen.add(id(c)); yield c
        for k in ('content', 'controls'):
            v = c.__dict__.get(k)
            for ch in (v if isinstance(v, list) else [v]):
                yield from walk(ch, seen)
    def sel(c): return c.__dict__.get('bgcolor') not in (None,)
    body = settings.screen(app)
    texts = [c.value for c in walk(body) if type(c).__name__ == 'Text']
    assert strings.t('s_test_style') in texts, 'Test style row in Settings'

    # Nothing saved yet -> Settings shows the DEFAULTS, not Setup's overrides.
    D = Q.PREF_DEFAULTS
    ts = app._settings_tool_segments
    assert sel(ts[D['tool']]) and not sel(ts['titles']), 'tool default shown'
    cs = app._case_style_segs_settings
    assert sel(cs[D['case_style']]) and not sel(cs['automation']), 'style default shown'

    # Generator toggle: in place, saved as default.
    ts['titles'].on_click(None)
    assert renders == [], 'tool toggle must not render'
    assert app.creds['tool'] == 'titles' and sel(ts['titles']) and not sel(ts['steps'])

    # Test style: in place, saved as default, hint follows.
    cs['automation'].on_click(None)
    assert renders == [] and app.creds['case_style'] == 'automation'
    assert app._case_style_hint_settings.value == strings.t('s_style_hint_automation')
    assert sel(cs['automation']) and not sel(cs['manual'])

    # Output language: in place.
    app._set_lang('fr', persist=True)
    assert renders == [] and app.creds['lang'] == 'fr'

    # Idle logout: in place.
    opts = [c for c in walk(body) if type(c).__name__ == 'Container'
            and c.__dict__.get('height') == 34 and callable(c.__dict__.get('on_click'))
            and getattr(c.__dict__.get('content'), 'value', None) == '60m']
    assert opts, 'idle 60m option'
    opts[0].on_click(None)
    assert renders == [] and app.creds['idle_minutes'] == 60

    # Setup's Test style toggle is a per-run override: never rewrites the default.
    app.active = 'setup'
    app._case_style_segment()                    # Setup call site
    app._case_style_segs['manual'].on_click(None)
    assert app.case_style == 'manual' and app.creds['case_style'] == 'automation'

    # Restore preferences -> every default, and Settings shows them.
    app.active = 'settings'
    app._reset_prefs()
    for k in ('lang', 'ui_lang', 'tool', 'case_style', 'perf', 'theme'):
        assert app.creds[k] == D[k], (k, app.creds[k])
    assert app.creds['idle_minutes'] == Q.IDLE_MINUTES_DEFAULT
    assert (app.tool, app.lang, app.case_style) == (D['tool'], D['lang'], D['case_style'])
    settings.screen(app)
    assert sel(app._settings_tool_segments[D['tool']])
    assert sel(app._case_style_segs_settings[D['case_style']])
    print('SIM-OK')
""") % ROOT


class SettingsDefaultsInPlace(unittest.TestCase):
    def test_settings_in_place_and_defaults(self):
        r = subprocess.run([sys.executable, "-c", _SIM], capture_output=True, text=True,
                           encoding="utf-8", timeout=120, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
