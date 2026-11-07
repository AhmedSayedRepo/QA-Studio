"""Data-driven test cases: {{placeholders}}, data rows from a Data sheet /
companion CSV / Azure DevOps parameters, inspection with the first row, and
one generated test per row in Selenium, Playwright and Cypress."""
import os
import pathlib
import subprocess
import tempfile
import unittest

import automation_file_source as F
import automation_targets as AT
import engine as E
import strings

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROWS = [{"name": "JavaScript", "values": {"prefix": "Ja", "language": "JavaScript"}},
        {"name": "Python", "values": {"prefix": "Py", "language": "Python"}}]


def _case():
    return {"title": "Pick a suggestion", "bucket": 3, "ctype": "interaction", "page_context": "app",
            "data": ROWS, "intents": [
                {"role": "action", "verb": "autocomplete", "value": "{{prefix}}=>{{language}}",
                 "target": "Tags", "kind": "input", "from_steps": [2],
                 "live_locator": {"by": "id", "value": "tags"}},
                {"role": "assertion", "check": "value", "value": "{{language}}", "target": "Tags",
                 "expected": 'The "Tags" field shows "{{language}}"', "from_steps": [2],
                 "live_locator": {"by": "id", "value": "tags"}}]}


class FillTests(unittest.TestCase):
    def test_fill(self):
        cache = {}
        out = E._data_fill("{{Prefix}}|{{first name}}|{{x}}|{{random.email}}|{{random.email}}",
                           {"prefix": "Ja", "First_Name": "Jason"}, cache)
        parts = out.split("|")
        self.assertEqual(parts[:3], ["Ja", "Jason", "{{x}}"])
        self.assertEqual(parts[3], parts[4])          # same value within one run
        self.assertRegex(E._data_fill("{{today+2d}}"), r"^\d{4}-\d{2}-\d{2}$")
        self.assertFalse(E._has_data_tokens("{{APP_USER}}"))

    def test_walk_fill_and_restore(self):
        intents = _case()["intents"]
        E._intents_fill_data(intents, ROWS[0]["values"], {})
        self.assertEqual(intents[0]["value"], "Ja=>JavaScript")
        E._intents_restore_data(intents)
        self.assertEqual(intents[0]["value"], "{{prefix}}=>{{language}}")
        self.assertNotIn("_data_orig", intents[0])
        view = E._data_view(intents[1], ROWS[1]["values"], {})
        self.assertEqual(view["value"], "Python")
        self.assertEqual(intents[1]["value"], "{{language}}")


class SourceTests(unittest.TestCase):
    def test_sample_data_csv(self):
        cases = F.load_test_cases(str(ROOT / "samples" / "data-driven-test-cases.csv"))
        self.assertEqual([len(c["data"]) for c in cases], [3, 3])
        self.assertEqual(cases[0]["data"][1], {"name": "Python", "values": {"prefix": "Py", "language": "Python"}})

    def test_azure_parameters(self):
        xml = ('<NewDataSet><xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" id="NewDataSet"/>'
               '<Table1><username>tom</username></Table1><Table1><username>bob</username></Table1></NewDataSet>')
        self.assertEqual(E._azure_local_data_rows(xml), [{"username": "tom"}, {"username": "bob"}])
        steps = [{"action": "Type @username, mail a@b.com", "expected": "Hi @username"}]
        E.azure_params_to_placeholders(steps, [{"values": {"username": "tom"}}])
        self.assertEqual(steps[0], {"action": "Type {{username}}, mail a@b.com", "expected": "Hi {{username}}"})


class GeneratorTests(unittest.TestCase):
    def _node_check(self, src):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.js")
            with open(p, "w", encoding="utf-8") as f:
                f.write(src)
            r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_playwright(self):
        js, _ = AT._pw_spec({"id": "1", "title": "s"}, [_case()], E._seed_locator_for_intent, {})
        self.assertIn("for (const row of [", js)
        self.assertIn('test("Pick a suggestion · " + row.name, async ({ page, heal }) => {', js)
        self.assertIn("heal.useData(row.values);", js)
        self.assertIn('heal.fill("{{prefix}}=>{{language}}")', js)
        self._node_check(js)
        self.assertIn("useData(values)", AT._PW_HEALER_JS)

    def test_cypress(self):
        js, _ = AT._cy_spec({"id": "1", "title": "s"}, [_case()], E._seed_locator_for_intent, {})
        self.assertIn("qaUseData(row.values);", js)
        self.assertIn('qaFill("{{language}}")', js)
        self._node_check(js)
        self.assertIn("globalThis.qaFill", AT._CY_HEALER_JS)

    def test_selenium(self):
        java, _ = E.generate_selfhealing_test_class({"id": "1", "title": "s"}, [_case()], "com.x")
        self.assertIn('@DataProvider(name = "data_tc_300_PickASuggestion")', java)
        self.assertIn('{"Python", Healer.row("prefix", "Py", "language", "Python")},', java)
        self.assertIn("public void tc_300_PickASuggestion(String dataSet, java.util.Map<String, String> data) {", java)
        self.assertIn('heal.fill("{{language}}")', java)
        h = E._sh_healer("com.x")
        for m in ("public void useData(Map<String, String> values)", "public String fill(String text)",
                  "public static Map<String, String> row(String... kv)"):
            self.assertIn(m, h)


class TextTests(unittest.TestCase):
    def test_prompt_and_note(self):
        self.assertIn("Data-driven cases", E.EXTERNAL_CASE_PROMPT)
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            self.assertIn("upd_note_data_driven", strings._STRINGS[lang])


if __name__ == "__main__":
    unittest.main()


def test_local_file_run_passes_data_rows_to_the_engine():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert "def case_data(self, case_id):" in src
    assert '_case_data(tcid) if callable(_case_data)' in src
