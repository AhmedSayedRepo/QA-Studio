"""Safety net: every generated project must at least PARSE.

Templates hold Java and JavaScript inside Python strings, so an escape (\\" or
\\+) can silently turn into invalid code - a broken ReportMailer.java line once
reached a user. These tests generate each framework and run the real
compiler / parser on the output (skipped when javac / node is not installed)."""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402
import automation_targets as A  # noqa: E402

CFG = {"base_url": "https://example.com", "login_url": "https://example.com/login"}

# One case using every step and check type, with quotes and backslashes in the
# values - the inputs most likely to break string escaping in the templates.
KITCHEN_SINK = [
    {"role": "action", "verb": "navigate", "value": "https://example.com/a", "target": "a"},
    {"role": "action", "verb": "type", "target": "Name", "value": 'He said "hi" \\ ok', "kind": "input"},
    {"role": "action", "verb": "clear", "target": "Name", "kind": "input", "value": ""},
    {"role": "action", "verb": "key_press", "target": "Name", "kind": "input", "value": "CONTROL+A"},
    {"role": "action", "verb": "select", "target": "Country", "kind": "select", "value": "Egypt"},
    {"role": "action", "verb": "double_click", "target": "Btn", "value": ""},
    {"role": "action", "verb": "right_click", "target": "Box", "value": ""},
    {"role": "action", "verb": "set_value", "target": "Date", "value": "2026-01-31"},
    {"role": "action", "verb": "drag", "target": "A", "value": "B"},
    {"role": "action", "verb": "autocomplete", "target": "Country", "value": "Egy=>Egypt"},
    {"role": "action", "verb": "scroll_to", "target": "x", "value": "Footer"},
    {"role": "action", "verb": "hover", "target": "Menu", "value": ""},
    {"role": "action", "verb": "upload", "target": "File", "value": "fixtures/qastudio-upload.txt"},
    {"role": "action", "verb": "go_back", "target": "back", "value": ""},
    {"role": "action", "verb": "go_forward", "target": "fwd", "value": ""},
    {"role": "action", "verb": "refresh", "target": "r", "value": ""},
    {"role": "action", "verb": "dismiss_if_present", "target": "x", "value": 'Close "now"'},
    {"role": "assertion", "check": "attr", "value": 'Login|type|is|sub"mit', "target": "t"},
    {"role": "assertion", "check": "pattern", "value": "\\$\\d+", "target": "t"},
    {"role": "assertion", "check": "url", "value": "contains|/a", "target": "t"},
    {"role": "assertion", "check": "value", "value": "x", "target": "Name"},
    {"role": "assertion", "check": "checked", "target": "Agree"},
    {"role": "assertion", "check": "enabled", "target": "Go"},
    {"role": "assertion", "check": "invalid", "target": "Email"},
    {"role": "assertion", "check": "table", "value": "Jason|Due|$100.00", "target": "t"},
    {"role": "assertion", "check": "download", "value": "f.txt", "target": "t"},
    {"role": "assertion", "check": "", "target": 'Says "hello"', "keywords": ['Says "hello"'], "kind": "text"},
    {"role": "action", "verb": "dialog_accept", "value": "hello", "target": "d"},
    {"role": "action", "verb": "dialog_dismiss", "target": "d"},
    {"role": "action", "verb": "window_switch", "target": "w"},
    {"role": "action", "verb": "window_close", "target": "w"},
    {"role": "action", "verb": "frame", "target": "f"},
    {"role": "action", "verb": "frame_exit", "target": "f"},
    {"role": "action", "verb": "remember", "value": "ORD-\\d+", "remember_as": "order", "target": "r"},
    {"role": "action", "verb": "type", "target": "Search", "value": "{{order}}", "kind": "input"},
    # Batch 6-8: pagination, cookies / storage, basic & digest auth.
    {"role": "action", "verb": "next_page", "target": "n", "value": 'Ja"son'},
    {"role": "action", "verb": "next_page", "target": "n", "value": ""},
    {"role": "action", "verb": "go_to_page", "target": "p", "value": "3"},
    {"role": "action", "verb": "set_cookie", "target": "c", "value": 'theme=da"rk'},
    {"role": "action", "verb": "delete_cookies", "target": "c", "value": ""},
    {"role": "action", "verb": "set_storage", "target": "s", "value": "local|token|a\\b"},
    {"role": "action", "verb": "clear_storage", "target": "s", "value": "all"},
    {"role": "action", "verb": "auth_open", "target": "a", "value": "/basic-auth|{{APP_USER}}|{{APP_PASS}}"},
    {"role": "assertion", "check": "page", "value": "3", "target": "t"},
    {"role": "assertion", "check": "cookie", "value": "theme|is|dark", "target": "t"},
    {"role": "assertion", "check": "storage", "value": "local|token|contains|ab", "target": "t"},
]
SEED = lambda *a, **k: ("css", "#x", True)  # noqa: E731


@unittest.skipUnless(shutil.which("javac"), "javac not installed")
class SeleniumJavaParses(unittest.TestCase):
    def test_generated_java_has_no_syntax_errors(self):
        with tempfile.TemporaryDirectory() as d:
            E.build_selfhealing_project(d, [], "https://example.com",
                                        login={"url": "https://example.com/login", "env_user": "u",
                                               "env_pass": "p", "report_to": "a@b.c"},
                                        cb=lambda *a, **k: None)
            case = {"bucket": 3, "priority": 300, "title": 'Every "step"', "ctype": "interaction",
                    "page_context": "app", "intents": KITCHEN_SINK}
            src, cls = E.generate_selfhealing_test_class({"id": "k1", "title": 'All "steps"'}, [case],
                                                         "com.qastudio")
            tdir = os.path.join(d, "src", "test", "java", "com", "qastudio", "tests")
            os.makedirs(tdir, exist_ok=True)
            with open(os.path.join(tdir, cls + ".java"), "w", encoding="utf-8") as f:
                f.write(src)
            files = glob.glob(os.path.join(d, "**", "*.java"), recursive=True)
            self.assertTrue(files)
            out = os.path.join(d, "_out")
            r = subprocess.run(["javac", "-proc:none", "-d", out] + files,
                               capture_output=True, text=True, timeout=180)
            errors = [ln for ln in (r.stderr or "").splitlines()
                      if "error:" in ln and not any(s in ln for s in ("cannot find symbol",
                                                                       "does not exist",
                                                                       "cannot access"))]
            self.assertEqual(errors, [], "generated Java does not parse:\n" + "\n".join(errors[:10]))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class JavaScriptParses(unittest.TestCase):
    def _check(self, build):
        with tempfile.TemporaryDirectory() as d:
            build(d, [], CFG, lambda *a, **k: None, "return [];")
            files = [f for f in glob.glob(os.path.join(d, "**", "*.js"), recursive=True)
                     if "node_modules" not in f]
            self.assertTrue(files)
            bad = []
            for f in files:
                r = subprocess.run(["node", "--check", f], capture_output=True, text=True, timeout=60)
                if r.returncode:
                    bad.append(os.path.relpath(f, d) + ": " + (r.stderr or "").strip().splitlines()[-1])
            self.assertEqual(bad, [])

    def test_playwright_project(self):
        self._check(A.build_playwright_project)

    def test_cypress_project(self):
        self._check(A.build_cypress_project)

    def test_every_step_type_emits_valid_js(self):
        pw, cy = [], []
        for i, it in enumerate(KITCHEN_SINK):
            A._emit_pw_intent(pw, "k%d" % i, dict(it), SEED, {})
            A._emit_cy_intent(cy, "k%d" % i, dict(it), SEED, {})
        opened = sum(ln.count("cy.then(() => {") for ln in cy)
        with tempfile.TemporaryDirectory() as d:
            for name, body in (("pw.js", "async function t(heal, page, expect) {\n%s\n}\n" % "\n".join(pw)),
                               ("cy.js", "function t() {\n%s\n%s}\n" % ("\n".join(cy), "  });\n" * opened))):
                p = os.path.join(d, name)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(body)
                r = subprocess.run(["node", "--check", p], capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, name + ": " + r.stderr)

    def test_shared_page_scripts_parse(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "fns.js")
            with open(p, "w", encoding="utf-8") as f:
                for n in ("_PAGE_CHECK_FN", "_REMEMBER_FN", "_DISMISS_FN", "_PICK_OPTION_FN",
                          "_DROP_TARGET_FN", "_TABLE_CELL_FN"):
                    f.write("const %s = %s;\n" % (n.strip("_"), getattr(E, n)))
            r = subprocess.run(["node", "--check", p], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
