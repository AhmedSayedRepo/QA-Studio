"""Final 'Done' line only asks for the AI key when TODOs remain; the TODO tally
line is not a warning at 0; Regenerate option skips the dialog; the Generate run
updates the Automation screen in place."""
import pathlib
import unittest

import engine as E
import release_notes
import strings

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANGS = ("en", "ar", "fr", "tr", "es", "de", "nl")


class DoneMessageTests(unittest.TestCase):
    def test_zero_todo_makes_key_optional(self):
        for tgt in ("selenium", "playwright", "cypress"):
            msg = E.automation_done_message(tgt, 0, "Anthropic")
            self.assertIn("optional", msg)
            self.assertNotIn("resolves the", msg)
            self.assertIn("APP_USER", msg)

    def test_todo_left_asks_for_key(self):
        msg = E.automation_done_message("selenium", 3, "Anthropic")
        self.assertIn("QA_AI_API_KEY (your Anthropic key)", msg)
        self.assertIn("3 TODO", msg)
        self.assertIn("mvn test", msg)

    def test_js_targets_keep_setup_commands(self):
        self.assertIn("'Run tests'", E.automation_done_message("playwright", 0))
        self.assertIn("npx playwright show-report", E.automation_done_message("playwright", 0))
        self.assertIn("cypress/reports", E.automation_done_message("cypress", 1))


class SourceWiringTests(unittest.TestCase):
    def setUp(self):
        self.engine = (ROOT / "engine.py").read_text(encoding="utf-8")
        self.main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.auto = (ROOT / "automation.py").read_text(encoding="utf-8")

    def test_todo_line_is_ok_when_zero(self):
        self.assertIn("if _todo_total:", self.engine)
        self.assertIn("none left for runtime self-healing.\", \"ok\")", self.engine)

    def test_regenerate_skips_dialog(self):
        self.assertIn('getattr(self, "auto_regenerate", False)', self.main)
        # One three-way choice now drives the regenerate / re-inspect flags.
        self.assertIn("def set_existing_mode(self, mode, save=True):", self.main)
        self.assertIn('self.auto_regenerate = mode != "keep"', self.main)
        self.assertIn('self.auto_reinspect_all = mode == "recheck"', self.main)
        self.assertIn('getattr(self, "auto_existing_mode", "") == "keep"', self.main)
        for mode in ("update", "recheck", "keep"):
            self.assertIn('existing_option("%s"' % mode, self.auto)

    def test_generate_run_updates_in_place(self):
        self.assertIn("automation.refresh_run_ui(self, starting=True)", self.main)
        self.assertIn("automation.refresh_run_ui(self) or self.render()", self.main)
        self.assertNotIn("self.ui_safe(self.render)\n\n        self._bg(work)", self.main)
        self.assertIn("app._auto_result_ctr", self.auto)


class StringsTests(unittest.TestCase):
    def test_regen_strings_in_every_language(self):
        for lang in LANGS:
            for key in ("auto_regen_label", "auto_regen_hint", "auto_regen_log",
                        "upd_note_regenerate"):
                self.assertIn(key, strings._STRINGS[lang], (lang, key))
        self.assertIn("{n}", strings._STRINGS["en"]["auto_regen_log"])

    def test_release_note_pending(self):
        # Pending until release.bat stamps it onto a version, then registered.
        shipped = {k for keys in release_notes.RELEASE_NOTE_KEYS.values() for k in keys}
        self.assertIn("upd_note_regenerate", set(release_notes.PENDING_RELEASE_NOTE_KEYS) | shipped)


if __name__ == "__main__":
    unittest.main()


class ReportEmailFlushTests(unittest.TestCase):
    """A typed recipient without Enter used to be dropped silently."""

    def test_typed_address_is_committed(self):
        reg = (ROOT / "regression.py").read_text(encoding="utf-8")
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("on_blur=lambda e: _add_custom(quiet=True)", reg)
        self.assertIn("_flushers[sync_key] = lambda: _add_custom(quiet=True)", reg)
        self.assertIn('getattr(self, "_email_flushers", None) or {}).get("auto_emails")', main)
        self.assertIn("No report email — no recipients selected.", main)
