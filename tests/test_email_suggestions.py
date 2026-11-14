"""Emails used before are suggested while typing in the recipients field and
every address used is remembered (also directory picks, for offline use)."""
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_STUB = textwrap.dedent("""
    import sys, types
    sys.path.insert(0, %r)
    from unittest import mock
    class Ctl:
        def __init__(self, *a, **k):
            self.__dict__.update(k)
            if a and 'controls' not in k and isinstance(a[0], list): self.controls = a[0]
            elif a and 'content' not in k: self.content = a[0]
            if a and isinstance(a[0], str): self.value = a[0]
        def update(self): pass
    fl = mock.MagicMock()
    for n in ("Column", "Row", "Container", "TextField", "Text", "Icon", "GestureDetector"):
        setattr(fl, n, type(n, (Ctl,), {}))
    sys.modules['flet'] = fl
    saved = []
    sys.modules['store'] = types.SimpleNamespace(save=lambda d: saved.append(dict(d)))
    for _ in range(40):
        try:
            import regression
            break
        except ModuleNotFoundError as e:
            sys.modules[e.name] = mock.MagicMock()
            sys.modules.pop("regression", None)
    regression.hover_field = lambda c, *a, **k: c
    regression._checkbox_multiselect = lambda *a, **k: Ctl()
""") % ROOT


class EmailSuggestions(unittest.TestCase):
    def test_typing_suggests_used_emails_and_click_adds(self):
        body = textwrap.dedent("""
            app = types.SimpleNamespace(creds={"email_recent": ["pat@x.com", "zed@y.org"]},
                project=None, page=None, _to="", _open=False)
            col = regression.email_recipient_picker(app, "_to", is_open_key="_open", sync_key="k")
            tf = col.controls[1].content
            wrap = col.controls[2]
            tf.value = "p"; tf.on_change(None)
            shown = [r.content.controls[1].value for r in wrap.content.controls]
            first = wrap.content.controls[0]
            first.on_click(None)
            print("R", shown, wrap.visible, app._to, tf.value)
            tf.value = "new@z.io"; tf.on_submit(None)
            tf.value = "typed@q.net"; regression.flush_typed_emails(app, "k")   # Setup run / summary send
            print("F", app._to)
            print("S", app.creds["email_recent"][:3], len(saved) > 0)
        """)
        r = subprocess.run([sys.executable, "-c", _STUB + body], capture_output=True, text=True,
                           encoding="utf-8", timeout=120, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = [x for x in r.stdout.splitlines() if x.startswith("R ")][0]
        self.assertIn("['pat@x.com']", line)          # only the match for "p"
        self.assertIn("False pat@x.com", line)         # list hidden, address added, field cleared
        f = [x for x in r.stdout.splitlines() if x.startswith("F ")][0]
        self.assertTrue(f.endswith("typed@q.net"))
        for src, call in (("main.py", 'flush_typed_emails(self, "setup_emails")'),
                          ("modals.py", 'flush_typed_emails(app, "sum_emails")')):
            self.assertIn(call, open(os.path.join(ROOT, src), encoding="utf-8").read())
        s = [x for x in r.stdout.splitlines() if x.startswith("S ")][0]
        self.assertIn("['typed@q.net', 'new@z.io', 'pat@x.com']", s)
        self.assertTrue(s.endswith("True"))


if __name__ == "__main__":
    unittest.main()
