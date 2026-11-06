"""'Copy AI prompt': the prompt asks an external AI for exactly the CSV that
QA Studio imports, and a reply in that format loads and maps correctly."""
import os
import pathlib
import tempfile
import unittest

import automation_file_source as FS
import engine as E
import strings

ROOT = pathlib.Path(__file__).resolve().parents[1]
HEADER = ("Test Case ID,Test Case Title,Step,Action,Expected Result,Element Type,"
          "Preferred Locator,Frame Context,Automation Note")

# What a model following the prompt returns.
REPLY = HEADER + '''
TC01,Valid login,1,Open https://app.example.com/login,"The ""Login"" heading is shown",,,,
TC01,Valid login,2,"Enter ""{{APP_USER}}"" in the ""Email"" field",,,,,
TC01,Valid login,3,"Enter ""{{APP_PASS}}"" in the ""Password"" field",,,,,
TC01,Valid login,4,"Click the ""Login"" button","The ""My account"" heading is shown",,,,
TC02,Confirm delete,1,Open https://app.example.com/items,"The ""Items"" heading is shown",,,,
TC02,Confirm delete,2,"Click the ""Delete"" button",,,,,
TC02,Confirm delete,3,Accept the browser confirm,"""Item deleted"" is shown",browser dialog,,,
TC03,Upload avatar,1,Open https://app.example.com/profile,"The ""Profile"" heading is shown",,,,
TC03,Upload avatar,2,"Upload a file in the ""Avatar"" input",,file input,#avatar,,
TC03,Upload avatar,3,"Press Enter in the ""Search"" field","""1 result"" is shown",keyboard,,,wait up to 10 seconds
'''


class PromptTests(unittest.TestCase):
    def test_prompt_names_the_import_columns_and_rules(self):
        p = E.external_case_prompt()
        self.assertIn(HEADER, p)
        for phrase in ("{{APP_USER}}", "browser dialog", "window context", "file input",
                       "keyboard", "At most 8 steps", "Open <full URL of the page>"):
            self.assertIn(phrase, p)

    def test_known_urls_are_filled_in(self):
        p = E.external_case_prompt(site_url="https://a.test", login_url="https://a.test/login")
        self.assertIn("Site URL: https://a.test\n", p)
        self.assertIn("Login URL (if any): https://a.test/login", p)
        self.assertIn("[https://your-app.example.com]", E.external_case_prompt())

    def test_reply_in_that_format_imports_and_maps(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "reply.csv")
            with open(path, "w", encoding="utf-8") as f:
                f.write(REPLY)
            cases = FS.load_test_cases(path)
        self.assertEqual([len(c["steps"]) for c in cases], [4, 3, 3])
        self.assertIsNone(E._source_operation_contract(cases[0]["steps"][0]))   # Open URL
        self.assertEqual(E._source_operation_contract(cases[1]["steps"][2])["verb"], "dialog_accept")
        up = E._source_operation_contract(cases[2]["steps"][1])
        self.assertEqual((up["verb"], up["preferred_locator"]), ("upload", "#avatar"))
        self.assertEqual(E._source_operation_contract(cases[2]["steps"][2])["value"], "ENTER")

    def test_placeholder_types_the_test_account(self):
        self.assertEqual(E._replay_value("{{APP_USER}}", {"user": "u1"}), "u1")
        src = (ROOT / "engine.py").read_text(encoding="utf-8")
        self.assertIn('typed_value = _replay_value(it.get("value", ""), login)', src)


class WiringTests(unittest.TestCase):
    def test_buttons_and_strings(self):
        self.assertIn("app._copy_external_case_prompt",
                      (ROOT / "automation.py").read_text(encoding="utf-8"))
        self.assertIn("app._copy_external_case_prompt",
                      (ROOT / "setup.py").read_text(encoding="utf-8"))
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("def _copy_external_case_prompt(self, e=None):", main)
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            for key in ("auto_copy_prompt", "auto_copy_prompt_done", "auto_copy_prompt_tip",
                        "upd_note_copy_ai_prompt"):
                self.assertIn(key, strings._STRINGS[lang], (lang, key))


if __name__ == "__main__":
    unittest.main()
