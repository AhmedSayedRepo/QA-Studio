"""Refined Performance screen: every field explains itself, advanced options
fold away, Browse sits beside its field, and the load summary reads plainly."""
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_STUB = textwrap.dedent("""
    import sys, json
    sys.path.insert(0, %r)
    from unittest import mock
    class Ctl:
        def __init__(self, *a, **k):
            self.__dict__.update(k)
            if a and 'controls' not in k and isinstance(a[0], list): self.controls = a[0]
            elif a and 'content' not in k: self.content = a[0]
            if a and type(self).__name__ == 'Text': self.value = a[0]
        def update(self): pass
    fl = mock.MagicMock()
    for n in ("Column", "Row", "Container", "Text", "TextField", "Dropdown",
              "DropdownOption", "ExpansionTile", "ListView", "IconButton", "Icon",
              "ProgressRing"):
        setattr(fl, n, type(n, (Ctl,), {}))
    sys.modules['flet'] = fl
    for _ in range(40):
        try:
            import platform_caps, performance, ui, strings
            break
        except ModuleNotFoundError as e:
            sys.modules[e.name] = mock.MagicMock()
            for m in ("performance", "ui", "engine", "main"):
                sys.modules.pop(m, None)
    platform_caps._FLET_PLATFORM = "windows"

    def walk(c, out, d=0):
        if c is None or d > 40: return
        out.append(c)
        for a in ("content", "controls", "title", "subtitle"):
            v = getattr(c, a, None)
            for x in (v if isinstance(v, list) else [v] if isinstance(v, Ctl) else []):
                walk(x, out, d + 1)

    def texts(root):
        nodes = []; walk(root, nodes)
        return [n.value for n in nodes if type(n).__name__ == "Text" and isinstance(getattr(n, "value", None), str)]

    class App:
        active = "performance"
        def __getattr__(self, name):
            if name.startswith("_perf_") or name.startswith("_auto_"):
                return None
            raise AttributeError(name)
        def _auto_field(self, label, attr, hint, info=None, **k):
            tf = fl.TextField(value=getattr(self, attr, "") or "", hint_text=hint, expand=True)
            return fl.Column([ui.field_label(label, info=info),
                              fl.Container(tf), fl.Container(fl.Text("Required."))])
        def shell(self, *a, **k): self.body = a[2] if len(a) > 2 else k.get("body")
        def _queue_performance_refresh(self): pass
""") % ROOT


def _run(body):
    r = subprocess.run([sys.executable, "-c", _STUB + textwrap.dedent(body)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    if r.returncode != 0:
        raise AssertionError(r.stdout + r.stderr)
    return r.stdout


class RefinedPerformance(unittest.TestCase):
    def test_har_fields_explain_themselves_and_advanced_folds(self):
        out = _run("""
            app = App(); app._perf_source = "har"
            left, right = performance.screen(app, _fragment=True)
            t = texts(left)
            for k in ("pfx_sub_source", "pfx_d_har", "pfx_d_domains", "pfx_d_auth",
                      "pfx_sub_load", "pfx_d_users", "pfx_budgets", "pfx_d_csv", "pfx_d_out"):
                assert strings.t(k) in t, k
            assert strings.t("pfx_optional") in t
            nodes = []; walk(left, nodes)
            tiles = [n for n in nodes if type(n).__name__ == "ExpansionTile"]
            titles = [getattr(x.title, "value", "") for x in tiles]
            assert strings.t("pfx_adv") in titles and strings.t("pfx_adv_engines") in titles
            adv = [x for x in tiles if getattr(x.title, "value", "") == strings.t("pfx_adv")][0]
            assert adv.expanded is False
            assert strings.t("pfx_summary", users="20", ramp="15", dur="60") in t
            print("OK")
        """)
        self.assertIn("OK", out)

    def test_advanced_opens_when_it_holds_a_value_and_curl_has_its_hint(self):
        out = _run("""
            app = App(); app._perf_source = "curl"; app._perf_corr = "id = $.id @ /cart"
            left, right = performance.screen(app, _fragment=True)
            t = texts(left)
            assert strings.t("pfx_d_curl") in t
            nodes = []; walk(left, nodes)
            adv = [n for n in nodes if type(n).__name__ == "ExpansionTile"
                   and getattr(n.title, "value", "") == strings.t("pfx_adv")][0]
            assert adv.expanded is True
            print("OK")
        """)
        self.assertIn("OK", out)

    def test_browse_sits_beside_its_field(self):
        out = _run("""
            app = App(); app._perf_source = "har"
            left, right = performance.screen(app, _fragment=True)
            nodes = []; walk(left, nodes)
            # The HAR field's input row holds the input box and the Browse button.
            har_rows = [n for n in nodes if type(n).__name__ == "Row" and len(getattr(n, "controls", [])) == 2
                        and getattr(n.controls[0], "expand", False) is True
                        and type(n.controls[1]).__name__ == "Container"
                        and getattr(n.controls[1], "padding", None) is not None]
            assert len(har_rows) >= 3, len(har_rows)   # HAR, Data CSV, Output folder
            print("OK")
        """)
        self.assertIn("OK", out)

    def test_strings_in_all_languages(self):
        sys.path.insert(0, ROOT)
        import strings as S
        keys = [k for k in S._STRINGS["en"] if k.startswith("pfx_")]
        self.assertGreater(len(keys), 40)
        for lang in ("ar", "fr", "tr", "es", "de", "nl"):
            for k in keys:
                self.assertIn(k, S._STRINGS[lang], (lang, k))


if __name__ == "__main__":
    unittest.main()
