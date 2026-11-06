"""Frames, JavaScript dialogs, new windows, uploads and key presses work in the
replay check and in all three generated frameworks (Selenium, Playwright,
Cypress) — not only in live inspection."""
import os
import pathlib
import subprocess
import tempfile
import unittest

import automation_file_source as FS
import automation_targets as AT
import engine as E
try:
    from test_replay_verify import By, Drv, _intent, _run, _seq
except ImportError:  # run as a package
    from tests.test_replay_verify import By, Drv, _intent, _run, _seq

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOC = {"live_locator": {"by": "css", "value": "#x"}}


def _act(verb, target, kind="button", value=""):
    return dict({"role": "action", "verb": verb, "target": target, "kind": kind,
                 "keywords": [target], "value": value}, **LOC)


CASE = {"bucket": 3, "title": "ctx", "ctype": "x", "page_context": "app", "priority": 300,
        "intents": [_act("frame", "editor iframe", "frame"),
                    _act("click", "Click for JS Confirm"), _act("dialog_dismiss", "", "context"),
                    _act("click", "Click Here", "link"), _act("window_switch", "", "context"),
                    _act("upload", "file", "input", "fixtures/qastudio-upload.txt"),
                    _act("key_press", "field", "input", "ENTER")]}
STORY = {"id": "9", "title": "t"}


class SeleniumTests(unittest.TestCase):
    def test_java_steps_in_order(self):
        java, _ = E.generate_selfhealing_test_class(STORY, [CASE], "com.q")
        lines = [l.strip() for l in java.splitlines() if l.strip().startswith("heal.")]
        self.assertTrue(lines[0].startswith('heal.enterFrame("9.0.0"'))
        self.assertIn("heal.handleDialog(false);", lines)
        i = lines.index("heal.prepareWindow();")
        self.assertTrue(lines[i + 1].startswith('heal.act("9.0.3", "click"'))
        self.assertEqual(lines[i + 2], "heal.switchWindow();")

    def test_healer_and_basetest_support(self):
        healer, base = E._sh_healer("com.q"), E._sh_base_test("com.q")
        for m in ("public void enterFrame(", "public void handleDialog(boolean accept)",
                  "public void prepareWindow()", "public void switchWindow()",
                  'case "upload":', 'case "key_press":'):
            self.assertIn(m, healer)
        self.assertIn("protected void resetContext()", base)
        self.assertEqual(base.count("resetContext();"), 2)

    def test_project_has_upload_fixture(self):
        with tempfile.TemporaryDirectory() as out:
            E.build_selfhealing_project(out, [{"story": STORY, "cases": [dict(CASE, tc_id="1")]}],
                                        "https://the-internet.herokuapp.com",
                                        cb=lambda *a, **k: None)
            self.assertTrue(os.path.isfile(os.path.join(out, "fixtures", "qastudio-upload.txt")))


class CypressTests(unittest.TestCase):
    def test_prepare_before_the_triggering_click(self):
        js, _ = AT._cy_spec(STORY, [CASE], E._seed_locator_for_intent, {})
        lines = [l.strip() for l in js.splitlines() if l.strip().startswith("cy.")]
        self.assertTrue(any(l.startswith('cy.healEnterFrame("9.0.0"') for l in lines))
        i = lines.index("cy.prepareDialog(false);")
        self.assertIn('"Click for JS Confirm"', lines[i + 1])
        j = lines.index("cy.prepareSameTabWindow();")
        self.assertIn('"Click Here"', lines[j + 1])

    def test_healer_commands_and_syntax(self):
        h = AT._CY_HEALER_JS
        for m in ("'healEnterFrame'", "'prepareDialog'", "'prepareSameTabWindow'",
                  "verb === 'upload'", "verb === 'key_press'", "beforeEach(() => { __frame = null; })"):
            self.assertIn(m, h)
        self.assertNotIn("cy.get(sel)", h)   # every lookup is frame-aware
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.js")
            with open(p, "w", encoding="utf-8") as f:
                f.write(h.replace("require('./harvest')", "{ harvest: () => [] }"))
            r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_project_has_upload_fixture(self):
        with tempfile.TemporaryDirectory() as out:
            AT.build_cypress_project(out, [], {"base_url": "https://x"},
                                     lambda *a, **k: None, "return [];")
            self.assertTrue(os.path.isfile(os.path.join(out, "fixtures", "qastudio-upload.txt")))


class Alert:
    def __init__(self, drv):
        self.drv = drv

    def accept(self):
        self.drv.log.append(("alert", "accept")); self.drv.alert_open = False

    def dismiss(self):
        self.drv.log.append(("alert", "dismiss")); self.drv.alert_open = False

    def send_keys(self, text):
        self.drv.log.append(("alert", "keys", text))


class SwitchTo:
    def __init__(self, drv):
        self.drv = drv

    @property
    def alert(self):
        if not self.drv.alert_open:
            raise RuntimeError("no alert")
        return Alert(self.drv)

    def window(self, h):
        self.drv.log.append(("window", h)); self.drv.current_window_handle = h

    def frame(self, el):
        self.drv.log.append(("frame", el.key))

    def default_content(self):
        self.drv.log.append(("default",))


class CtxDrv(Drv):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.alert_open, self.window_handles, self.current_window_handle = False, ["main"], "main"
        self.switch_to = SwitchTo(self)

    def close(self):
        self.window_handles.remove(self.current_window_handle)


class ReplayContextTests(unittest.TestCase):
    def test_dialog_window_and_frame_steps_replay(self):
        drv = CtxDrv()
        drv.put(By.ID, "confirm", drv.el("confirm"))
        drv.put(By.ID, "link", drv.el("link"))
        drv.put(By.ID, "ifr", drv.el("ifr"))
        drv.after_click["confirm"] = lambda d: setattr(d, "alert_open", True)
        drv.after_click["link"] = lambda d: d.window_handles.append("popup")
        steps = [_intent("action", target="confirm", locator={"by": "id", "value": "confirm"}),
                 _intent("action", verb="dialog_dismiss", target="", kind="context"),
                 _intent("action", target="link", locator={"by": "id", "value": "link"}),
                 _intent("action", verb="window_switch", target="", kind="context"),
                 _intent("action", verb="frame", target="iframe", kind="frame",
                         locator={"by": "id", "value": "ifr"})]
        report = _run(drv, _seq(("ctx", 3, steps)))
        self.assertEqual(report["passed"], 1, report["cases"])
        self.assertIn(("alert", "dismiss"), drv.log)
        self.assertIn(("window", "popup"), drv.log)
        self.assertIn(("frame", "ifr"), drv.log)

    def test_missing_dialog_fails_clearly(self):
        drv = CtxDrv()
        report = _run(drv, _seq(("ctx", 3, [_intent("action", verb="dialog_accept",
                                                     target="", kind="context")])))
        self.assertEqual(report["cases"][0]["reason"], "no-dialog")


class SampleFileTests(unittest.TestCase):
    def test_contracts_from_the_sample_columns(self):
        rows = [{"action": 'Select "Option 2" from the dropdown', "element_type": "native select"},
                {"action": 'Enter "tomsmith" in the "Username" field', "element_type": "text input"}]
        self.assertEqual(E._source_operation_contract(rows[0])["value"], "Option 2")
        self.assertEqual(E._source_operation_contract(rows[1])["value"], "tomsmith")

    def test_empty_contract_value_keeps_compiled_value(self):
        src = ROOT / "engine.py"
        self.assertIn('(key == "value" and value)', src.read_text(encoding="utf-8"))


class LiveRunFixTests(unittest.TestCase):
    """Fixes from the first live run of samples/the-internet-test-cases.csv."""

    def test_same_title_words_different_outcome_are_not_duplicates(self):
        orig = E._ai_duplicate_clusters
        E._ai_duplicate_clusters = lambda *a, **k: []
        try:
            cases = [{"id": "4", "title": "Cancel a JavaScript confirm",
                      "steps": [{"action": "x", "expected": 'Result "You clicked: Cancel" is shown'}]},
                     {"id": "5", "title": "Confirm a JavaScript confirm",
                      "steps": [{"action": "x", "expected": 'Result "You clicked: Ok" is shown'}]}]
            self.assertEqual([c["id"] for c in E.dedupe_case_list(cases)], ["4", "5"])
            same = [dict(cases[0], id="6"), cases[0]]
            self.assertEqual(len(E.dedupe_case_list(same)), 1)
        finally:
            E._ai_duplicate_clusters = orig

    def test_url_ending_in_upload_is_navigation(self):
        self.assertIsNone(E._source_operation_contract(
            {"action": "Open https://the-internet.herokuapp.com/upload"}))
        self.assertEqual(E._source_operation_contract(
            {"action": "Upload the report file"})["verb"], "upload")

    def test_quoted_label_binds_when_compiler_paraphrased_it(self):
        pool = [{"idx": 0, "tag": "h3", "text": "Opening a new window", "css": "h3", "visible": True},
                {"idx": 1, "tag": "a", "text": "Click Here", "css": "a1", "visible": True}]
        it = {"role": "action", "verb": "click", "target": "new window link",
              "keywords": ["new window"], "kind": "link"}
        E._anchor_quoted_keywords(it, {"action": 'Click the "Click Here" link'})
        ranked = E._rank_candidates(it, pool)
        self.assertEqual(ranked[0][1]["text"], "Click Here")

    def test_anchor_needs_every_quoted_label(self):
        pool = [{"idx": 0, "tag": "button", "text": "Add to cart", "id": "add-to-cart-bike-light",
                 "css": "#b", "visible": True},
                {"idx": 1, "tag": "button", "text": "Add to cart", "id": "add-to-cart-sauce-labs-backpack",
                 "css": "#p", "visible": True}]
        it = {"role": "action", "verb": "click", "target": "add to cart", "keywords": [],
              "kind": "button"}
        E._anchor_quoted_keywords(it, {"action": 'Click "Add to cart" for "Sauce Labs Backpack"'})
        ids = [e["id"] for _s, e in E._rank_candidates(it, pool)]
        self.assertEqual(ids[0], "add-to-cart-sauce-labs-backpack")
        self.assertNotIn("add-to-cart-bike-light", ids)

    def test_typed_value_is_not_an_anchor(self):
        it = {"role": "action", "verb": "type", "target": "username", "keywords": ["username"],
              "value": "tomsmith"}
        E._anchor_quoted_keywords(it, {"action": 'Enter "tomsmith" in the "Username" field'})
        self.assertNotIn("tomsmith", [k.lower() for k in it["keywords"]])

    def test_walk_waits_for_late_content(self):
        src = (ROOT / "engine.py").read_text(encoding="utf-8")
        self.assertIn("_deadline = time.time() + 10", src)


class SecondRunFixTests(unittest.TestCase):
    """Second live run: the Upload submit button and blocked-run emails."""

    def test_submit_input_label_is_harvested(self):
        self.assertIn("input[type=submit],input[type=button],input[type=reset]", E._HARVEST_JS)

    def test_input_button_binds_and_gets_value_xpath(self):
        pool = [{"idx": 0, "tag": "a", "text": "", "css": "fork", "visible": True},
                {"idx": 1, "tag": "input", "type": "file", "text": "", "id": "file-upload",
                 "css": "#file-upload", "visible": True},
                {"idx": 2, "tag": "input", "type": "submit", "text": "Upload", "id": "file-submit",
                 "css": "#file-submit", "visible": True}]
        it = {"role": "action", "verb": "click", "target": "upload button",
              "keywords": ["Upload"], "kind": "button"}
        E._anchor_quoted_keywords(it, {"action": 'Click the "Upload" button'})
        self.assertEqual(E._rank_candidates(it, pool)[0][1]["id"], "file-submit")
        self.assertIn({"by": "xpath", "value": '//input[@value="Upload"]'},
                      E._locator_candidates(pool[2]))

    def test_blocked_run_sends_a_blocked_report(self):
        html = E.build_automation_report_email("selenium", "Generation blocked", {"Stories": 1},
                                               blocked=True)
        self.assertIn("BLOCKED", html)
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("_send_auto_report(blocked=True)", main)
        self.assertIn("failed=failed, stopped=stopped, blocked=blocked,", main)


class UploadFixtureTests(unittest.TestCase):
    """Third live run: 'upload failed: File not found : C:\\Use…'. The fixture now
    lives in ~/.qastudio (outside AppData\\Local, which Store-installed Python
    redirects so ChromeDriver can't see the file) and is re-created on send."""

    def test_fixture_prefers_home_folder_and_recreates(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as home:
            with patch("os.path.expanduser", return_value=home):
                p = E._upload_fixture_path()
                self.assertEqual(os.path.dirname(p), os.path.realpath(os.path.join(home, ".qastudio")))
                os.remove(p)
                self.assertTrue(os.path.isfile(E._upload_fixture_path()))

    def test_walk_resolves_fixture_at_send_time(self):
        src = (ROOT / "engine.py").read_text(encoding="utf-8")
        self.assertIn("path = _upload_fixture_path()", src)
        self.assertEqual(E._replay_upload_file(), E._upload_fixture_path())
