"""SuperAdmin 'Sender organization': browsing / saving another organization's
sender must never change this user's own sender (creds file + the engine's
live sender used by every report email)."""
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SIM = textwrap.dedent("""
    import sys, threading
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
    import store, engine as E
    saved = []
    store.save = lambda c: saved.append(dict(c))
    live = {}
    E.set_credentials = lambda **kw: live.update({k: v for k, v in kw.items()})
    auth = main.auth
    auth.configured = lambda: True
    auth.is_super_admin = lambda u: True
    auth.org_id_of = lambda u: 'OWN'
    auth.can_manage_org_settings = lambda u: True
    auth.caps_for = lambda u: ['act.run']
    calls = []
    def get_org_settings(org_id=None, include_audit=False):
        calls.append(org_id)
        org = org_id or 'OWN'
        em = {'sender': org.lower() + '@x.com', 'sender_name': org, 'app_password': 'pw-' + org}
        return True, ({'settings': {'email': em}, 'audit': []} if include_audit else {'email': em})
    auth.get_org_settings = get_org_settings
    pushed = []
    auth.admin_set_org_email = lambda s, n, p, org_id=None: (pushed.append((s, n, p, org_id)), (True, ''))[1]
    Q = main.QAStudio
    app = Q.__new__(Q)
    app.user = {'email': 'boss@x.com'}
    app.active = 'other'
    app.creds = {'gmail': 'pw-OWN', 'gmail_sender': 'own@x.com', 'gmail_sender_name': 'OWN'}
    own = dict(app.creds)
    app.ui_safe = lambda fn: fn()
    app._toast = lambda *a, **k: None
    app._refresh_cred_field = lambda *a: None
    app.render = lambda: None
    class Now:
        def __init__(self, target=None, daemon=None, **k): self.t = target
        def start(self): self.t()
    main.threading.Thread = Now
    # 1. pick another organization
    app._on_org_email_target_change(mock.Mock(control=mock.Mock(value='PF')))
    assert app.creds == own, ('own creds changed', app.creds)
    assert live == {}, ('live sender touched', live)
    assert app._sender_creds() == {'gmail': 'pw-PF', 'gmail_sender': 'pf@x.com', 'gmail_sender_name': 'PF'}
    # 2. save that org's sender: server only, never the local file / live sender
    app.sender_field = mock.Mock(value='new-pf@x.com')
    app._save_sender()
    assert saved == [] and live == {}, (saved, live)
    assert app.creds == own
    assert pushed[-1] == ('new-pf@x.com', 'PF', 'pw-PF', 'PF'), pushed
    # 3. send-time refresh uses THIS user's own organization
    calls.clear()
    app._ensure_org_email_fresh()
    assert calls == [None], calls
    assert live.get('gmail_sender') == 'own@x.com', live
    # 4. picking their own org again shows + uses their own values
    live.clear()
    app._on_org_email_target_change(mock.Mock(control=mock.Mock(value='OWN')))
    assert app._sender_creds() is app.creds
    assert live.get('gmail_sender') == 'own@x.com', live
    print('SIM-OK')
""") % ROOT


class OrgSenderIsolation(unittest.TestCase):
    def test_other_org_never_leaks_into_own_sender(self):
        r = subprocess.run([sys.executable, "-c", _SIM], capture_output=True, text=True,
                           encoding="utf-8", timeout=120, cwd=ROOT)
        self.assertIn("SIM-OK", r.stdout, r.stdout[-2000:] + r.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
