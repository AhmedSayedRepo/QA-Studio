"""Batch 3: drag and drop, table-cell checks and file downloads — contracts,
inspection/replay helpers and generated Selenium / Playwright / Cypress code."""
import os
import pathlib
import subprocess
import tempfile
import unittest

import automation_targets as AT
import engine as E
import strings

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_drag(self):
        r = E._source_operation_contract(
            {"action": 'Drag "Drag me to my target" onto "Drop here"', "element_type": "drag and drop"})
        self.assertEqual((r["verb"], r["value"]), ("drag", "Drop here"))
        self.assertIn("drag", E._VALID_ACTION_VERBS)

    def test_table_and_download_parsing(self):
        self.assertEqual(E._table_parts("Jason|Due|$100.00"), ("Jason", "Due", "$100.00"))
        src = (ROOT / "engine.py").read_text(encoding="utf-8")
        self.assertIn('intent["check"] = "table"', src)
        self.assertIn('intent["check"] = "download"', src)

    def test_find_download(self):
        import time
        d = E._download_dir()
        p = os.path.join(d, "qa_batch3_probe.txt")
        open(p, "w").close()
        try:
            self.assertTrue(E._find_download("qa_batch3_probe", time.time() - 5, timeout=1))
            self.assertFalse(E._find_download("no_such_file_zz", time.time() - 5, timeout=0.5))
        finally:
            os.remove(p)


class GeneratedCodeTests(unittest.TestCase):
    def test_java(self):
        h = E._sh_healer("com.x")
        for m in ('case "drag": {', "public boolean assertTableCell(", "public boolean assertDownloaded(",
                  "DROP_TARGET_JS =", "HTML5_DRAG_JS =", "TABLE_CELL_JS ="):
            self.assertIn(m, h)
        self.assertIn("download.default_directory", E._driver_factory("com.x"))
        lines = []
        E._emit_intent(lines, "k", {"role": "assertion", "check": "table", "value": "Jason|Due|$100.00"})
        self.assertIn('heal.assertTableCell("Jason", "Due", "$100.00")', lines[0])
        lines = []
        E._emit_intent(lines, "k", {"role": "assertion", "check": "download", "value": "file_1.txt"})
        self.assertIn('heal.assertDownloaded("file_1.txt")', lines[0])

    def _node_check(self, src):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.js")
            with open(p, "w", encoding="utf-8") as f:
                f.write(src)
            r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_playwright(self):
        src = AT._with_batch2_js(AT._PW_HEALER_JS)
        for m in ("case 'drag': {", "async assertTableCell(", "prepareDownload()", "async assertDownloaded("):
            self.assertIn(m, src)
        self.assertNotIn("__TABLE_CELL_FN__", src)
        self._node_check(src)
        story = {"id": "1", "title": "s"}
        case = {"title": "dl", "bucket": 3, "intents": [
            {"role": "action", "verb": "click", "target": "File 1", "kind": "link"},
            {"role": "assertion", "check": "download", "value": "file_1.txt"}]}
        js = AT._pw_spec(story, [case], E._seed_locator_for_intent, {})[0] \
            if hasattr(AT, "_pw_spec") else ""
        if js:
            self.assertLess(js.index("heal.prepareDownload();"), js.index("heal.act("))

    def test_cypress(self):
        src = AT._with_batch2_js(AT._CY_HEALER_JS)
        for m in ("verb === 'drag'", "'healAssertTableCell'", "'healAssertDownloaded'"):
            self.assertIn(m, src)
        self._node_check(src)
        self.assertIn("findDownload(name)", (ROOT / "automation_targets.py").read_text(encoding="utf-8"))


class TextTests(unittest.TestCase):
    def test_prompt_and_note(self):
        for t in ("drag and drop", "The row with", "is downloaded"):
            self.assertIn(t, E.EXTERNAL_CASE_PROMPT)
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            self.assertIn("upd_note_batch3", strings._STRINGS[lang])


if __name__ == "__main__":
    unittest.main()


class SplitLoopTests(unittest.TestCase):
    def test_split_parts_never_split_again(self):
        steps = [{"action": "x", "expected": 'The "Tags" field shows "JavaScript"'}]
        it = {"role": "assertion", "target": "Tags", "expected": steps[0]["expected"], "from_steps": [1]}
        parts = E._split_by_quoted_expected(it, steps)
        self.assertEqual(len(parts), 2)
        for p in parts:
            self.assertEqual(E._split_by_quoted_expected(p, steps), [])

    def test_value_check_is_not_split(self):
        steps = [{"action": "x", "expected": 'The "Tags" field shows "JavaScript"'}]
        it = {"role": "assertion", "check": "value", "target": "Tags",
              "expected": steps[0]["expected"], "from_steps": [1]}
        self.assertEqual(E._split_by_quoted_expected(it, steps), [])


class ValueOnCustomDropdownTests(unittest.TestCase):
    def test_value_check_reads_text_and_binds_any_kind(self):
        src = (ROOT / "engine.py").read_text(encoding="utf-8")
        self.assertIn('intent["kind"] = "any"', src)
        self.assertIn('or el.text or ""', src)
        self.assertIn("shown.isEmpty() ? el.getText() : shown", E._sh_healer("com.x"))
        self.assertIn("shown = await el.innerText()", AT._PW_HEALER_JS)
        self.assertIn("$el.is('input,textarea') ? $el.val() : $el.text()", AT._CY_HEALER_JS)


class RepeatDownloadTests(unittest.TestCase):
    def test_renamed_copy_is_found(self):
        import time
        p = os.path.join(E._download_dir(), "qa_probe_dl (1).txt")
        open(p, "w").close()
        try:
            self.assertTrue(E._find_download("qa_probe_dl.txt", time.time() - 5, timeout=1))
            self.assertFalse(E._find_download("qa_probe_dl.pdf", time.time() - 5, timeout=0.5))
        finally:
            os.remove(p)
        self.assertIn("for (File f : old) f.delete();", E._driver_factory("com.x"))


class RunFromIdeTests(unittest.TestCase):
    def test_js_report_shows_the_ide(self):
        m = AT._REPORT_MAILER_JS
        for t in ("function detectIde()", "['Run from', e.ide]", "' · run from <b>'",
                  "(ide ? ' from ' + ide : '')", "detectIde };"):
            self.assertIn(t, m)
        src = (ROOT / "automation_targets.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("ide: detectIde() } };"), 2)   # Playwright + Cypress
        self.assertIn("# QA_IDE=IntelliJ IDEA", AT._PW_ENV_EXAMPLE)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "node_modules", "dotenv"))
            with open(os.path.join(d, "node_modules", "dotenv", "index.js"), "w") as f:
                f.write("module.exports={config(){}}")
            with open(os.path.join(d, "report-mailer.js"), "w", encoding="utf-8") as f:
                f.write(m)
            env = dict(os.environ, QA_IDE="", TERM_PROGRAM="vscode")
            env.pop("CI", None)
            r = subprocess.run(["node", "-e", "console.log(require('./report-mailer').detectIde())"],
                               cwd=d, capture_output=True, text=True, env=env)
            self.assertIn(r.stdout.strip(), ("VS Code", "IntelliJ IDEA", "Cursor"))

    def test_java_report_shows_the_ide(self):
        j = E._sh_report_mailer("com.x")
        for t in ("static String ide()", '{"Run from", ide()},', '"QA Studio — Selenium run from " + ide()',
                  "ProcessHandle.current().parent()"):
            self.assertIn(t, j)


class ReportSitesTests(unittest.TestCase):
    def test_js_lists_every_site(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "node_modules", "dotenv"))
            with open(os.path.join(d, "node_modules", "dotenv", "index.js"), "w") as f:
                f.write("module.exports={config(){}}")
            with open(os.path.join(d, "report-mailer.js"), "w", encoding="utf-8") as f:
                f.write(AT._REPORT_MAILER_JS)
            with open(os.path.join(d, "a.spec.js"), "w") as f:
                f.write('await page.goto("https://jqueryui.com/x");\\ncy.visit("https://the-internet.herokuapp.com/t");'
                        '\\nawait page.goto("https://jqueryui.com/y");')
            r = subprocess.run(["node", "-e", "console.log(require('./report-mailer').siteOf('a.spec.js'))"],
                               cwd=d, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "https://jqueryui.com, https://the-internet.herokuapp.com")

    def test_java_lists_every_site(self):
        j = E._sh_report_mailer("com.x")
        self.assertIn('{"Sites", sites()},', j)
        self.assertIn("static String sites()", j)


class ReportVideoTests(unittest.TestCase):
    def test_videos(self):
        src = (ROOT / "automation_targets.py").read_text(encoding="utf-8")
        self.assertIn("video: process.env.VIDEO || 'on',", src)
        self.assertIn("path.join('cypress', 'reports', 'videos', path.dirname(r.spec.relative)", src)
        self.assertIn("VIDEO=on", AT._PW_ENV_EXAMPLE)


class NamedStepsTests(unittest.TestCase):
    CASE = {"title": "t", "bucket": 3, "ctype": "interaction", "page_context": "app", "intents": [
        {"role": "action", "verb": "select", "value": "Fast", "target": "speed dropdown", "kind": "select",
         "from_steps": [2]},
        {"role": "action", "verb": "type", "value": "{{APP_PASS}}", "target": "Password", "kind": "input",
         "from_steps": [3]},
        {"role": "assertion", "check": "download", "value": "file_1.txt",
         "expected": 'The file "file_1.txt" is downloaded', "from_steps": [4]}]}

    def test_labels(self):
        L = E._intent_step_label
        self.assertEqual(L(self.CASE["intents"][0]), 'Step 2 · Select "Fast" in "speed dropdown"')
        self.assertEqual(L(self.CASE["intents"][1]), 'Step 3 · Type into "Password"')   # no secret
        self.assertEqual(L(self.CASE["intents"][2]), 'Step 4 · Verify: The file "file_1.txt" is downloaded')

    def test_all_frameworks(self):
        js, _ = AT._pw_spec({"id": "1", "title": "s"}, [self.CASE], E._seed_locator_for_intent, {})
        self.assertIn('await test.step("Step 2 · Select \\"Fast\\" in \\"speed dropdown\\"", async () => {', js)
        self.assertNotIn("APP_PASS}}", js.split("test.step")[2][:60])
        cy, _ = AT._cy_spec({"id": "1", "title": "s"}, [self.CASE], E._seed_locator_for_intent, {})
        self.assertIn('cy.log("**Step 3 · Type into \\"Password\\"**");', cy)
        java, _ = E.generate_selfhealing_test_class({"id": "1", "title": "s"}, [self.CASE], "com.x")
        self.assertIn('org.testng.Reporter.log("Step 4 - Verify: The file \\"file_1.txt\\" is downloaded", true);', java)
