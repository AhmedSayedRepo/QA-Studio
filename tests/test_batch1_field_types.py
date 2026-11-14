"""Batch 1 field types: radio, double/right-click, leaving frames, back to the
original window / closing a tab, JavaScript prompt answers, date/time/range/
color values, built-in form validation and multi-select — in the step
contracts, the replay check and all three generated frameworks."""
import os
import pathlib
import subprocess
import tempfile
import unittest

import automation_file_source as FS
import automation_targets as AT
import engine as E

try:
    from test_browser_context_ops import CtxDrv, STORY
    from test_replay_verify import By, _intent, _run, _seq
except ImportError:  # run as a package
    from tests.test_browser_context_ops import CtxDrv, STORY
    from tests.test_replay_verify import By, _intent, _run, _seq

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOC = {"live_locator": {"by": "css", "value": "#x"}}


def _a(verb, target="t", kind="any", value=""):
    return dict({"role": "action", "verb": verb, "target": target, "kind": kind,
                 "keywords": [target], "value": value}, **LOC)


CASE = {"bucket": 3, "title": "b1", "ctype": "x", "page_context": "app", "priority": 300,
        "intents": [_a("double_click", "Clickable"), _a("right_click", "Hot spot"),
                    _a("set_value", "Date", "input", "2026-01-31"),
                    _a("select", "Cars", "select", "Volvo; Audi"),
                    _a("frame", "editor", "frame"), _a("frame_exit", "", "context"),
                    _a("click", "Click for JS Prompt", "button"),
                    _a("dialog_accept", "", "context", "hello"),
                    _a("click", "Click Here", "link"), _a("window_switch", "", "context"),
                    _a("window_close", "", "context"),
                    dict({"role": "assertion", "verb": "", "target": "Email", "kind": "input",
                          "keywords": ["Email"], "check": "invalid", "value": ""}, **LOC)]}


class ContractTests(unittest.TestCase):
    def c(self, action, et=""):
        k = E._source_operation_contract({"action": action, "element_type": et})
        return k and (k["verb"], k["kind"], k["value"])

    def test_prose_and_element_types(self):
        self.assertEqual(self.c("Switch back to the main page"), ("frame_exit", "context", ""))
        self.assertEqual(self.c("Close the new tab"), ("window_close", "context", ""))
        self.assertEqual(self.c("Switch back to the original window"), ("window_back", "context", ""))
        self.assertEqual(self.c('Enter "hello" in the browser prompt and accept it', "browser dialog"),
                         ("dialog_accept", "context", "hello"))
        self.assertEqual(self.c('Double-click the "Copy" button')[0], "double_click")
        self.assertEqual(self.c("Right-click the hot spot")[0], "right_click")
        self.assertEqual(self.c('Set the "Date" field to "2026-01-31"', "date input"),
                         ("set_value", "input", "2026-01-31"))
        self.assertEqual(self.c('Select "Volvo" and "Audi" from the "Cars" list', "multi select"),
                         ("select", "select", "Volvo; Audi"))
        self.assertEqual(self.c('Click the "Yes" radio button', "radio"), ("click", "radio", ""))
        self.assertEqual(self.c('Hover over the "Hoverable" button', "button"), ("hover", "button", ""))

    def test_radio_kind_and_verbs(self):
        self.assertTrue(E._kind_matches("radio", {"tag": "input", "type": "radio"}))
        self.assertFalse(E._kind_matches("checkbox", {"tag": "input", "type": "radio"}))
        for v in ("double_click", "right_click", "set_value", "frame_exit", "window_back",
                  "window_close"):
            self.assertIn(v, E._VALID_ACTION_VERBS)
        self.assertEqual(E._VERB_SYNONYMS["double-click"], "double_click")

    def test_samples_load(self):
        for name, n in (("the-internet-test-cases.csv", 19), ("selenium-web-form-test-cases.csv", 6)):
            self.assertEqual(len(FS.load_test_cases(str(ROOT / "samples" / name))), n)


class GeneratorTests(unittest.TestCase):
    def test_selenium(self):
        java, _ = E.generate_selfhealing_test_class(STORY, [CASE], "com.q")
        for line in ('"double_click"', '"right_click"', '"set_value"', "heal.exitFrame();",
                     'heal.handleDialog(true, "hello");', "heal.backToOriginalWindow(true);",
                     "heal.assertInvalid("):
            self.assertIn(line, java)
        h = E._sh_healer("com.q")
        for m in ("doubleClick(VideoRecorder.raw(el))", "executeScript(RIGHT_CLICK_JS, el)", 'case "set_value":', "deselectAll()",
                  "public void exitFrame()", "public void backToOriginalWindow(boolean closeCurrent)",
                  "public boolean assertInvalid(", "a.sendKeys(text)"):
            self.assertIn(m, h)

    def test_playwright(self):
        js, _ = AT._pw_spec(STORY, [CASE], E._seed_locator_for_intent, {})
        for line in ("heal.exitFrame();", "heal.prepareDialog('accept', \"hello\");",
                     "await heal.backToOriginalWindow(true);", "heal.assertInvalid("):
            self.assertIn(line, js)
        for m in ("el.dblclick()", "new MouseEvent('contextmenu', o)", "case 'set_value':", "backToOriginalWindow",
                  "dialog.accept(text == null"):
            self.assertIn(m, AT._PW_HEALER_JS)

    def test_cypress(self):
        js, _ = AT._cy_spec(STORY, [CASE], E._seed_locator_for_intent, {})
        for line in ("cy.exitFrame();", 'cy.prepareDialog(true, "hello");',
                     "cy.backToOriginalWindow();", "cy.healAssertInvalid("):
            self.assertIn(line, js)
        h = AT._CY_HEALER_JS
        for m in (".dblclick(", ".rightclick(", "verb === 'set_value'", "select(opts)",
                  "'exitFrame'", "'backToOriginalWindow'", "cy.stub(w, 'prompt')"):
            self.assertIn(m, h)

    def test_js_syntax(self):
        with tempfile.TemporaryDirectory() as d:
            for name, src in (("cy.js", AT._CY_HEALER_JS.replace("require('./harvest')", "{ harvest: () => [] }")),
                              ("pw.js", AT._PW_HEALER_JS.replace("__UI_LOCALE__", "en"))):
                p = os.path.join(d, name)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(src)
                r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, name + r.stderr)


class JSDrv(CtxDrv):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.valid = True

    def execute_script(self, script, *args):
        if "checkValidity" in script:
            return self.valid
        if "dispatchEvent" in script and args:
            self.log.append(("set_value", args[0].key, args[1]))
            return None
        return super().execute_script(script, *args)


class ReplayTests(unittest.TestCase):
    def test_frame_exit_window_close_set_value_and_prompt(self):
        drv = JSDrv()
        for key in ("ifr", "date", "link", "prompt"):
            drv.put(By.ID, key, drv.el(key))
        drv.after_click["link"] = lambda d: d.window_handles.append("popup")
        drv.after_click["prompt"] = lambda d: setattr(d, "alert_open", True)
        steps = [_intent("action", verb="frame", target="ifr", kind="frame",
                         locator={"by": "id", "value": "ifr"}),
                 _intent("action", verb="frame_exit", target="", kind="context"),
                 _intent("action", verb="set_value", target="date", kind="input", value="2026-01-31",
                         locator={"by": "id", "value": "date"}),
                 _intent("action", target="prompt", locator={"by": "id", "value": "prompt"}),
                 _intent("action", verb="dialog_accept", target="", kind="context", value="hello"),
                 _intent("action", target="link", locator={"by": "id", "value": "link"}),
                 _intent("action", verb="window_switch", target="", kind="context"),
                 _intent("action", verb="window_close", target="", kind="context")]
        report = _run(drv, _seq(("b1", 3, steps)))
        self.assertEqual(report["passed"], 1, report["cases"])
        self.assertIn(("default",), drv.log)
        self.assertIn(("set_value", "date", "2026-01-31"), drv.log)
        self.assertIn(("alert", "keys", "hello"), drv.log)
        self.assertEqual(drv.current_window_handle, "main")
        self.assertNotIn("popup", drv.window_handles)

    def test_invalid_check(self):
        drv = JSDrv()
        drv.put(By.ID, "email", drv.el("email"))
        a = _intent("assertion", target="email", kind="input", check="invalid",
                    locator={"by": "id", "value": "email"})
        self.assertEqual(_run(drv, _seq(("v", 3, [a])))["cases"][0]["reason"], "not-invalid")
        drv.valid = False
        self.assertEqual(_run(drv, _seq(("v", 3, [a])))["passed"], 1)


if __name__ == "__main__":
    unittest.main()


class SplitStepTests(unittest.TestCase):
    """Live run: the compiler split 'Enter "hello" in the browser prompt and
    accept it' into type + click; the click had no element and blocked."""

    def test_one_reviewed_step_stays_one_action(self):
        raw = [{"action": 'Click the "Click for JS Prompt" button', "element_type": "button"},
               {"action": 'Enter "hello" in the browser prompt and accept it',
                "expected": 'Result "You entered: hello" is shown', "element_type": "browser dialog"}]
        compiled = [{"role": "action", "verb": "click", "target": "JS Prompt", "from_steps": [1]},
                    {"role": "action", "verb": "type", "target": "prompt", "value": "hello",
                     "from_steps": [2]},
                    {"role": "action", "verb": "click", "target": "accept browser prompt",
                     "from_steps": [2]},
                    {"role": "assertion", "verb": "", "target": "result", "from_steps": [2]}]
        out = E._apply_source_operation_contracts(compiled, raw)
        acts = [(i["verb"], i.get("value")) for i in out if i["role"] == "action"]
        self.assertEqual(acts, [("click", None), ("dialog_accept", "hello")])
        self.assertEqual(sum(1 for i in out if i["role"] == "assertion"), 1)

    def test_every_sample_contract_step_collapses_to_one_action(self):
        for name in ("the-internet-test-cases.csv", "selenium-web-form-test-cases.csv"):
            for case in FS.load_test_cases(str(ROOT / "samples" / name)):
                steps = case["steps"]
                # Worst case: the compiler emits TWO actions for every step.
                compiled = [dict(role="action", verb=v, target="x", from_steps=[n])
                            for n in range(1, len(steps) + 1) for v in ("click", "type")]
                out = E._apply_source_operation_contracts(compiled, steps)
                for n, st in enumerate(steps, start=1):
                    if E._source_operation_contract(st):
                        acts = [i for i in out if i["role"] == "action" and i["from_steps"] == [n]]
                        self.assertEqual(len(acts), 1, (name, case["title"], n))


class WalkContextResetTests(unittest.TestCase):
    """Live run: TC07 left its popup open, so TC17's window switch landed on
    the stale main window and recorded the wrong heading (replay caught it)."""

    def test_walk_resets_windows_per_case_and_prefers_new_ones(self):
        src = (ROOT / "engine.py").read_text(encoding="utf-8")
        self.assertIn("def _walk_reset_context():", src)
        i = src.index("walking '{tc.get('title','')}'")
        self.assertIn("_walk_reset_context()", src[i:i + 600])
        self.assertIn('known = walk_windows.get("known") or {old_handle}', src)
