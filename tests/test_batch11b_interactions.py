"""Batch 11b: toast messages, page keyboard shortcuts, drag-and-drop upload areas
and multi-level hover menus - parsing, labels, inspection and the generated
Selenium, Playwright and Cypress code."""
import csv
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
import release_notes  # noqa: E402
import strings  # noqa: E402

NODE = shutil.which("node")
SEED = lambda *a, **k: ("css", "#x", True)  # noqa: E731


class Steps(unittest.TestCase):
    def test_actions(self):
        cases = {
            'Hover over "Products" > "Laptops" and click "Gaming"': ("menu_path", "click|Products|Laptops|Gaming"),
            'Hover "Enabled" > "Downloads"': ("menu_path", "hover|Enabled|Downloads"),
            'Hover "Enabled", then click "Back to JQuery UI"': ("menu_path", "click|Enabled|Back to JQuery UI"),
            "Open the menu Products > Laptops > Gaming": ("menu_path", "click|Products|Laptops|Gaming"),
            'Select "Products > Laptops" from the menu': ("menu_path", "click|Products|Laptops"),
            "Press Ctrl+K": ("shortcut", "CONTROL+K"),
            "Press Escape": ("shortcut", "ESCAPE"),
            "Press the keyboard shortcut Tab on the page": ("shortcut", "TAB"),
            "Use the keyboard shortcut Shift+?": ("shortcut", "SHIFT+?"),
            'Press "/" to search': ("shortcut", "/"),
            'Drop a file onto the "Drag files here" area': ("drop_file", "Drag files here"),
            "Drag and drop a file onto the drop area": ("drop_file", ""),
            "Close the toast message": ("dismiss_if_present", "@toast"),
            "Close the notification": ("dismiss_if_present", "@toast"),
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(E._page_action_in(text), want)

    def test_existing_steps_unchanged(self):
        for text in ('Hover over "Products"', 'Type "a > b" in the "X" field', "Press Enter",
                     'Press Ctrl+A in the "Name" field', "Press Enter in the search field", 'Drag "A" onto "B"',
                     'Hover over the "Avatar" image and check "name"'):
            with self.subTest(text=text):
                self.assertIsNone(E._page_action_in(text))
        self.assertEqual(E._page_action_in('Close "X" if it is shown'), ("dismiss_if_present", "X"))
        self.assertEqual(E._page_action_in("Switch to an iPhone 14 screen"), ("device", "iPhone 14"))

    def test_toast_checks(self):
        cases = {
            'A toast "Saved successfully" appears': "Saved successfully",
            'The notification "You logged into a secure area!" is shown': "You logged into a secure area!",
            'The toast says "Done"': "Done",
            '"Saved" appears in a snackbar': "Saved",
            "A toast message appears": "",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual([(c, v) for c, v, _ in E._page_checks_in(text)], [("toast", want)])
        self.assertEqual(E._page_checks_in('The page shows "Hello"'), [])

    def test_labels(self):
        self.assertEqual(E._page_action_label("shortcut", "CONTROL+K"), "Press the keyboard shortcut Ctrl+K on the page")
        self.assertEqual(E._page_action_label("menu_path", "click|A|B|C"), 'Hover "A" > "B", then click "C"')
        self.assertEqual(E._page_action_label("menu_path", "hover|A|B"), 'Hover "A" > "B"')
        self.assertEqual(E._page_action_label("drop_file", "Zone"), 'Drop the test file onto "Zone"')
        self.assertEqual(E._page_action_label("dismiss_if_present", "@toast"), "Close the toast message")
        self.assertEqual(E._page_check_label("toast", "Saved"), 'toast message "Saved" is shown')

    def test_assertion_pack_places_them(self):
        raw = [{"action": 'Click the "Save" button', "expected": 'A toast "Saved" appears'},
               {"action": 'Hover "A" > "B", then click "C"', "expected": ""}]
        clean = [{"role": "action", "verb": "click", "target": "Save", "from_steps": [1]},
                 {"role": "assertion", "check": "text", "expected": "Saved", "target": "Saved", "from_steps": [1]},
                 {"role": "action", "verb": "hover", "target": "A", "from_steps": [2]}]
        out = E._apply_assertion_pack(clean, raw)
        got = [(i.get("role"), i.get("verb") or i.get("check"), i.get("value")) for i in out]
        self.assertEqual(got, [("action", "click", None), ("assertion", "toast", "Saved"),
                               ("action", "menu_path", "click|A|B|C")])


class Runtimes(unittest.TestCase):
    def test_every_framework_emits_them(self):
        for verb, value in (("shortcut", "CONTROL+K"), ("drop_file", "Zone"), ("menu_path", "click|A|B")):
            it = {"role": "action", "verb": verb, "target": "x", "value": value}
            j, p, c = [], [], []
            E._emit_intent(j, "k1", it)
            A._emit_pw_intent(p, "k1", it, SEED, {})
            A._emit_cy_intent(c, "k1", it, SEED, {})
            self.assertIn('heal.pageAction("%s", "%s")' % (verb, value), "\n".join(j))
            self.assertIn('heal.pageAction("%s"' % verb, "\n".join(p))
            self.assertIn('cy.healPageAction("%s", "%s")' % (verb, value), "\n".join(c))

    def test_runtime_code(self):
        healer = E._sh_healer("com.qastudio")
        for s in ('case "shortcut":', "dropFile(fill(", "menuPath(fill(", "new Actions(driver).moveToElement(el)"):
            self.assertIn(s, healer)
        self.assertNotIn("__DROP_FILE_JS__", healer)
        self.assertNotIn("__MENU_ITEM_JS__", healer)
        pw = A._with_batch2_js(A._PW_HEALER_JS)
        cy = A._with_batch2_js(A._CY_HEALER_JS)
        for text in (pw, cy):
            for verb in ("shortcut", "drop_file", "menu_path"):
                self.assertIn("if (verb === '%s')" % verb, text)
            self.assertNotIn("__DROP_FILE_FN__", text)
            self.assertNotIn("__MENU_ITEM_FN__", text)
        self.assertIn("Input.dispatchMouseEvent", cy)
        for fn in (E._PAGE_CHECK_FN, E._DISMISS_FN):
            self.assertNotIn("__TOAST_SEL__", fn)
        self.assertIn("check === 'toast'", E._PAGE_CHECK_FN)
        self.assertIn("qastudio-upload.txt", E._DROP_FILE_FN)

    @unittest.skipUnless(NODE, "node not installed")
    def test_generated_js_parses(self):
        d = tempfile.mkdtemp()
        items = (("healer.js", A._with_batch2_js(A._PW_HEALER_JS)),
                 ("cy-healer.js", A._with_batch2_js(A._CY_HEALER_JS).replace("__UI_LOCALE__", "en")),
                 ("fns.js", "const a = (%s);\nconst b = (%s);\nconst c = (%s);\nconst e = (%s);\n"
                  % (E._DROP_FILE_FN, E._MENU_ITEM_FN, E._PAGE_CHECK_FN, E._DISMISS_FN)))
        for name, text in items:
            path = os.path.join(d, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            r = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, name + ": " + r.stderr)


class Texts(unittest.TestCase):
    def test_strings_and_notes(self):
        # Pending until release.bat stamps it into a version (4.0.6 and later).
        released = {k for keys in release_notes.RELEASE_NOTE_KEYS.values() for k in keys}
        self.assertIn("upd_note_batch11b_interactions", set(release_notes.PENDING_RELEASE_NOTE_KEYS) | released)
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            for k in ("help_automation_d17", "help_automation_p17", "upd_note_batch11b_interactions"):
                self.assertTrue(strings._STRINGS[lang].get(k), lang + " " + k)

    def test_sample_cases_parse(self):
        with open(os.path.join(ROOT, "samples", "batch11b-interactions-test-cases.csv"), encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        verbs = [a[0] for a in (E._page_action_in(r["Action"]) for r in rows) if a]
        self.assertEqual(sorted(set(verbs)), ["dismiss_if_present", "drop_file", "menu_path", "shortcut"])
        checks = [c for r in rows for c, _, _ in E._page_checks_in(r["Expected Result"])]
        self.assertIn("toast", checks)


if __name__ == "__main__":
    unittest.main()
