"""Generated projects: visible browser, report opens after every run and is
emailed; plus the two Playwright failures from the first the-internet run
(key name "ENTER", "Hello World!" read before it became visible)."""
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

import automation_targets as AT
import engine as E

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = {"base_url": "https://x.test", "login_url": "https://x.test/login", "ui_locale": "en",
       "report_email_to": "qa@x.test", "smtp_user": "sender@gmail.com"}


def _node_check(src):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.js")
        with open(p, "w", encoding="utf-8") as f:
            f.write(src)
        return subprocess.run(["node", "--check", p], capture_output=True, text=True)


class PlaywrightFixTests(unittest.TestCase):
    def test_key_names_are_translated(self):
        h = AT._PW_HEALER_JS
        self.assertIn("await el.press(pwKey(value))", h)
        self.assertIn("ENTER: 'Enter'", h)
        r = subprocess.run(["node", "-e", h[h.index("const _PW_KEYS"):h.index("class Healer")]
                            + "console.log([pwKey('ENTER'), pwKey('arrow_up'), pwKey('a')].join(','))"],
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "Enter,ArrowUp,a")

    def test_visible_assertion_waits(self):
        self.assertIn("await el.waitFor({ state: 'visible', timeout: 10000 })", AT._PW_HEALER_JS)
        self.assertIn("ExpectedConditions.visibilityOf(el)", E._sh_healer("com.q"))


class ProjectTests(unittest.TestCase):
    def test_playwright_headed_report_and_mail(self):
        self.assertIn("process.env.HEADLESS", AT._PW_CONFIG)
        self.assertNotIn("headless: true,", AT._PW_CONFIG)
        self.assertIn("['./support/after-run.js']", AT._PW_CONFIG)
        with tempfile.TemporaryDirectory() as out:
            AT.build_playwright_project(out, [], CFG, lambda *a, **k: None, "return [];")
            for f in ("report-mailer.js", "after-run.js"):
                self.assertTrue(os.path.isfile(os.path.join(out, "support", f)))
            pkg = json.load(open(os.path.join(out, "package.json")))
            self.assertIn("nodemailer", pkg["dependencies"])
            env = open(os.path.join(out, ".env")).read()
            self.assertIn("REPORT_EMAIL_TO=qa@x.test", env)
            self.assertIn("SMTP_USER=sender@gmail.com", env)
            self.assertIn("SMTP_PASS=", env)

    def test_cypress_headed_report_and_mail(self):
        with tempfile.TemporaryDirectory() as out:
            AT.build_cypress_project(out, [], CFG, lambda *a, **k: None, "return [];")
            pkg = json.load(open(os.path.join(out, "package.json")))
            self.assertEqual(pkg["scripts"]["test"], "cypress run --headed")
            self.assertTrue(os.path.isfile(os.path.join(out, "cypress", "report-mailer.js")))
            cfg = open(os.path.join(out, "cypress.config.js")).read()
            self.assertIn("afterRunHook", cfg)
            self.assertIn("mailReport", cfg)
            self.assertEqual(_node_check(cfg).returncode, 0)

    def test_js_files_parse(self):
        for src in (AT._REPORT_MAILER_JS, AT._PW_AFTER_RUN_JS,
                    AT._PW_HEALER_JS.replace("__UI_LOCALE__", "en")):
            self.assertEqual(_node_check(src).returncode, 0)

    def test_selenium_listener(self):
        j = E._sh_report_mailer("com.q")
        for m in ("implements ISuiteListener, IExecutionListener", "Desktop.getDesktop().browse",
                  "Transport.send(m)", "emailable-report.html"):
            self.assertIn(m, j)
        self.assertIn("angus-mail", E._sh_pom("com.q", "x"))
        with tempfile.TemporaryDirectory() as out:
            story = {"id": "9", "title": "t"}
            case = {"bucket": 3, "title": "c", "ctype": "x", "page_context": "app",
                    "priority": 300, "tc_id": "1", "intents": []}
            E.build_selfhealing_project(out, [{"story": story, "cases": [case]}], "https://x.test",
                                        login={"report_to": "qa@x.test", "smtp_user": "s@g.com"},
                                        cb=lambda *a, **k: None)
            self.assertIn("ReportMailer", open(os.path.join(out, "testng.xml")).read())
            props = open(os.path.join(out, "config.properties")).read()
            self.assertIn("report.email.to=qa@x.test", props)
            self.assertNotIn("smtp.pass", props)

    def test_main_passes_recipients(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('login["report_to"] = (getattr(self, "_auto_email_to", "") or "").strip()', main)


if __name__ == "__main__":
    unittest.main()
