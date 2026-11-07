"""Regenerate re-inspects only new/changed/failed cases; passed cases reuse the
plan saved by the earlier run."""
import pathlib
import tempfile
import unittest

import engine as E
import strings

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE, LOGIN = "https://the-internet.herokuapp.com", "https://the-internet.herokuapp.com/login"


def _plan(failed=False, exact=True):
    loc = {"by": "css", "value": "#ok"} if exact else {}
    it = {"role": "action", "inspection_source": "live", "live_locator": loc, "target": "Ok"}
    if failed:
        it["replay_failed"] = "not found"
    return {"schema_version": 1, "intents": [
        {"role": "action", "inspection_source": "start_state"}, it]}


def _case(title, action="Click \"Ok\"", plan=None):
    tc = {"id": title, "title": title,
          "steps": [{"action": action, "expected": "\"Done\" is shown"}]}
    if plan is not None:
        tc["inspection_plan"] = plan
    return tc


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()

    def _payload(self, *cases):
        return [{"story": {"id": "1"}, "test_cases": list(cases)}]

    def test_passed_reused_failed_and_changed_reinspected(self):
        first = self._payload(_case("A", plan=_plan()), _case("B", plan=_plan(failed=True)),
                              _case("C", plan=_plan()))
        self.assertEqual(E.save_verified_cases(first, self.out, SITE, LOGIN), 2)
        nxt = self._payload(_case("A"), _case("B"), _case("C", action="Click \"Other\""),
                            _case("D"))
        to_walk, reused = E.split_cached_cases(nxt, self.out, SITE, LOGIN)
        self.assertEqual(reused, 1)
        self.assertEqual([tc["title"] for tc in to_walk[0]["test_cases"]], ["B", "C", "D"])
        a = nxt[0]["test_cases"][0]
        self.assertTrue(a["inspection_reused"])
        self.assertEqual(a["inspection_plan"]["intents"][1]["live_locator"]["value"], "#ok")
        # Same dicts: results written on to_walk land on the full payload.
        self.assertIs(to_walk[0]["test_cases"][0], nxt[0]["test_cases"][1])

    def test_failed_now_is_forgotten(self):
        E.save_verified_cases(self._payload(_case("A", plan=_plan())), self.out, SITE, LOGIN)
        E.save_verified_cases(self._payload(_case("A", plan=_plan(failed=True))),
                              self.out, SITE, LOGIN)
        _, reused = E.split_cached_cases(self._payload(_case("A")), self.out, SITE, LOGIN)
        self.assertEqual(reused, 0)

    def test_other_site_or_force_reinspects(self):
        E.save_verified_cases(self._payload(_case("A", plan=_plan())), self.out, SITE, LOGIN)
        self.assertEqual(E.split_cached_cases(self._payload(_case("A")), self.out,
                                              "https://staging.example.com", LOGIN)[1], 0)
        self.assertEqual(E.split_cached_cases(self._payload(_case("A")), self.out,
                                              SITE, LOGIN, force=True)[1], 0)
        self.assertEqual(E.split_cached_cases(self._payload(_case("A")), self.out,
                                              SITE, LOGIN)[1], 1)

    def test_unverified_plan_not_saved(self):
        self.assertEqual(E.save_verified_cases(
            self._payload(_case("A", plan=_plan(exact=False)), _case("B")),
            self.out, SITE, LOGIN), 0)


class WiringTests(unittest.TestCase):
    def test_worker_and_ui(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("E.split_cached_cases(", main)
        self.assertIn("E.save_verified_cases(walk_payload, project_dir", main)
        self.assertIn("if _replay:\n                        E.save_verified_cases(", main)
        self.assertIn("existing_option(\"recheck\"", (ROOT / "automation.py").read_text(encoding="utf-8"))
        self.assertIn(".qastudio/verified-cases.json", E._sh_gitignore())

    def test_strings(self):
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            for key in ("auto_reinspect_all", "auto_reinspect_hint", "auto_reuse_log",
                        "upd_note_reuse_verified"):
                self.assertIn(key, strings._STRINGS[lang], (lang, key))


if __name__ == "__main__":
    unittest.main()


class LoginFormDetectionTests(unittest.TestCase):
    """A page with only a password input (Selenium web form) is not a login."""

    def test_all_runners_need_a_username_field(self):
        eng = (ROOT / "engine.py").read_text(encoding="utf-8")
        tgt = (ROOT / "automation_targets.py").read_text(encoding="utf-8")
        self.assertIn("if (loginFormPresent(2000)) performLogin();", eng)
        self.assertIn('and find_all("css", _REPLAY_USER), min(wait, 2)):', eng)
        self.assertIn("loginForm = (await page.locator('#username, #user-name", tgt)
        self.assertIn("&& $body.find('#username, #user-name", tgt)


class LoginFailureBlocksTests(unittest.TestCase):
    def test_login_failure_flags_first_step(self):
        eng = (ROOT / "engine.py").read_text(encoding="utf-8")
        self.assertIn('first["replay_failed"] = row["reason"] or "failed"', eng)


class OptionalTestAccountTests(unittest.TestCase):
    def test_credentials_optional(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertNotIn('_mark("auto_login_user", strings.t("auto_err_test_user"))', main)
        self.assertIn('if login.get("user") and poll(', (ROOT / "engine.py").read_text(encoding="utf-8"))
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            self.assertIn("auto_creds_optional", strings._STRINGS[lang])


class HelpAndReleaseNoteTests(unittest.TestCase):
    def test_help_and_note(self):
        import release_notes
        shipped = {k for keys in release_notes.RELEASE_NOTE_KEYS.values() for k in keys}
        keys = set(release_notes.PENDING_RELEASE_NOTE_KEYS) | shipped
        for key in ("upd_note_reuse_verified", "upd_note_optional_account"):
            self.assertIn(key, keys)
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            self.assertIn("upd_note_optional_account", strings._STRINGS[lang])
        self.assertIn("re-check every case", strings._STRINGS["en"]["helpm_auto_scripts_s6"])


class ListModeNoMailTests(unittest.TestCase):
    def test_pw_after_run_skips_list(self):
        import automation_targets as A
        self.assertIn("process.argv.includes('--list') || !this.ran", A._PW_AFTER_RUN_JS)


class MultiSelectAssertTests(unittest.TestCase):
    def test_all_selected_options_checked(self):
        import automation_targets as A
        src = (ROOT / "automation_targets.py").read_text(encoding="utf-8")
        self.assertIn("locator('option:checked').allTextContents()", src)
        self.assertIn("getAllSelectedOptions()", (ROOT / "engine.py").read_text(encoding="utf-8"))


class BrandedRunReportTests(unittest.TestCase):
    def test_js_report(self):
        import automation_targets as A
        js = A._REPORT_MAILER_JS
        for s in ("function buildHtml(run, mode, detailName)", "qa-studio-report.html",
                  "cid: x._cid", "Results by story", "Environment"):
            self.assertIn(s, js)
        self.assertIn("writeReport(run, dir, detailed ? 'qa-studio-detailed.html' : 'index.html')", A._PW_AFTER_RUN_JS)
        self.assertIn("writeReport(run, dir, detailed ? 'qa-studio-detailed.html' : 'index.html')", A._CY_CONFIG)
        self.assertIn("browserInfo.isHeadless", A._CY_CONFIG)

    def test_java_report(self):
        j = E._sh_report_mailer("com.x")
        for s in ("implements ISuiteListener, IExecutionListener, ITestListener",
                  "target/qa-studio-report.html", "getScreenshotAs(OutputType.BYTES)",
                  "new MimeMultipart(\"related\")", "package com.x.tests;"):
            self.assertIn(s, j)
        self.assertNotIn("__PKG__", j)

    def test_release_note(self):
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            self.assertIn("upd_note_branded_report", strings._STRINGS[lang])
