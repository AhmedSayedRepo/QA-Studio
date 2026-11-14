"""Batch 2: custom dropdowns, autocomplete, shadow DOM, infinite scroll, and the
field-value check — in the step contracts, the explorer helpers, replay, and
the generated Selenium / Playwright / Cypress code."""
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


class ContractTests(unittest.TestCase):
    def c(self, action, etype):
        return E._source_operation_contract({"action": action, "element_type": etype})

    def test_custom_dropdown(self):
        r = self.c('Select "Fast" from the "Select a speed" dropdown', "custom dropdown")
        self.assertEqual((r["verb"], r["kind"], r["value"]), ("select", "select", "Fast"))

    def test_autocomplete(self):
        r = self.c('Type "Ja" in the "Tags" field and pick "JavaScript"', "autocomplete")
        self.assertEqual((r["verb"], r["value"]), ("autocomplete", "Ja=>JavaScript"))
        self.assertEqual(E._autocomplete_parts("Ja=>JavaScript"), ("Ja", "JavaScript"))
        self.assertEqual(E._autocomplete_parts("Python"), ("Python", "Python"))

    def test_scroll(self):
        r = self.c('Scroll down until "3a - Logo Pizza delivered" appears', "infinite scroll")
        self.assertEqual((r["verb"], r["value"]), ("scroll_to", "3a - Logo Pizza delivered"))

    def test_verbs_registered(self):
        for v in ("autocomplete", "scroll_to"):
            self.assertIn(v, E._VALID_ACTION_VERBS)


class ShadowLocatorTests(unittest.TestCase):
    EL = {"tag": "input", "type": "checkbox", "shadow": "custom-checkbox-element",
          "css": "div:nth-of-type(1) > input:nth-of-type(1)", "xpath": "",
          "selectorCandidates": ["div:nth-of-type(1) > input:nth-of-type(1)"]}

    def test_locators_use_the_host_chain(self):
        self.assertEqual(E._to_locator(self.EL)["by"], "shadow")
        cands = E._locator_candidates(self.EL)
        self.assertTrue(cands and all(c["by"] == "shadow" for c in cands))
        self.assertTrue(cands[0]["value"].startswith("custom-checkbox-element >>> "))

    def test_harvest_and_finders(self):
        self.assertIn("shadow: hostChain(el)", E._HARVEST_JS)
        self.assertIn("[role=combobox]", E._HARVEST_JS)
        self.assertIn("split(' >>> ')", E._SHADOW_FIND_FN)
        healer = E._sh_healer("com.x")
        for m in ('case "shadow":      return new ByShadow(value);', "public void pickOption(String text)",
                  "public void scrollTo(String key", 'case "autocomplete":',
                  "public boolean assertValue(", "import java.util.List;"):
            self.assertIn(m, healer)
        self.assertNotIn("__PICK_OPTION_JS__", healer)


class GeneratedJsTests(unittest.TestCase):
    def _check(self, src):
        self.assertNotIn("__PICK_OPTION_FN__", src)
        self.assertNotIn("__SCROLL_MAX__", src)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.js")
            with open(p, "w", encoding="utf-8") as f:
                f.write(src)
            r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_playwright(self):
        src = AT._with_batch2_js(AT._PW_HEALER_JS)
        for m in ("async pickOption(text)", "async scrollTo(key, seed, intent)",
                  "case 'autocomplete':", "async assertValue(", "pressSequentially"):
            self.assertIn(m, src)
        self._check(src)

    def test_cypress(self):
        src = AT._with_batch2_js(AT._CY_HEALER_JS)
        for m in ("function _pickOption(text)", "function _scrollTo(key, seed, intent)",
                  "verb === 'autocomplete'", "'healAssertValue'", "case 'shadow':",
                  "if (!__frame) return cy.get(selector);"):
            self.assertIn(m, src)
        self.assertNotIn("if (!__frame) return _q(", src)   # no self-recursion
        self._check(src)

    def test_ids_are_attribute_selectors(self):
        # '#3a-logo' is invalid CSS; [id="3a-logo"] is not.
        src = (ROOT / "automation_targets.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("""return '[id="' + v + '"]';"""), 2)
        self.assertNotIn("""case 'id':          return '#' + v;""", src)


class ValueCheckTests(unittest.TestCase):
    def test_wired_everywhere(self):
        eng = (ROOT / "engine.py").read_text(encoding="utf-8")
        tgt = (ROOT / "automation_targets.py").read_text(encoding="utf-8")
        self.assertIn('intent["check"] = "value"', eng)
        self.assertIn('return "wrong-value", idx > 0', eng)
        self.assertIn("heal.assertValue(", eng)
        self.assertIn("heal.assertValue(%s", tgt)
        self.assertIn("cy.healAssertValue(%s", tgt)


class SampleAndTextTests(unittest.TestCase):
    def test_sample_loads(self):
        cases = F.load_test_cases(str(ROOT / "samples" / "components-test-cases.csv"))
        self.assertEqual(len(cases), 7)
        types = {st.get("element_type") for c in cases for st in c["steps"]}
        self.assertTrue({"custom dropdown", "autocomplete", "infinite scroll"} <= types)

    def test_prompt_and_note(self):
        for t in ("custom dropdown", "autocomplete", "infinite scroll", "shadow DOM"):
            self.assertIn(t, E.EXTERNAL_CASE_PROMPT)
        import release_notes
        shipped = {k for keys in release_notes.RELEASE_NOTE_KEYS.values() for k in keys}
        self.assertIn("upd_note_components", set(release_notes.PENDING_RELEASE_NOTE_KEYS) | shipped)
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            self.assertIn("upd_note_components", strings._STRINGS[lang])


if __name__ == "__main__":
    unittest.main()


class SeleniumReadyToRunTests(unittest.TestCase):
    def test_maven_run_configs(self):
        import xml.etree.ElementTree as ET
        x = E._idea_maven_config("Run tests", ["test"])
        root = ET.fromstring(x)
        cfg = root.find("configuration")
        self.assertEqual(cfg.get("type"), "MavenRunConfiguration")
        self.assertEqual([o.get("value") for o in root.iter("option") if o.get("value") == "test"], ["test"])
        src = (ROOT / "engine.py").read_text(encoding="utf-8")
        self.assertIn('("Download dependencies", ["dependency:resolve", "test-compile"], "")', src)
        self.assertIn("maven_prefetch(out_dir, cb=cb, should_stop=should_stop)", src)

    def test_prefetch_without_maven_explains(self):
        from unittest import mock
        msgs = []
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(E.maven_prefetch("/tmp", cb=lambda m, *a: msgs.append(m)))
        self.assertIn("Run tests", msgs[0])


class CypressRunFixTests(unittest.TestCase):
    def test_spec_starts_on_its_own_url(self):
        case = {"title": "t", "bucket": 3, "intents": [
            {"role": "action", "verb": "navigate", "value": "https://x.dev/form.html", "target": "page"}]}
        js, _ = AT._cy_spec({"id": "1", "title": "s"}, [case], E._seed_locator_for_intent, {})
        self.assertIn('cy.visit("https://x.dev/form.html");', js)
        self.assertNotIn("cy.openAppHome();", js)
        h = AT._CY_HEALER_JS
        self.assertIn("if (!Cypress.env('APP_USER')) return;", h)
        self.assertIn("if (!toSelector(steps[i][0])) steps.splice(i, 1);", h)


class ExistingChoiceStringsTests(unittest.TestCase):
    def test_all_languages(self):
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            d = strings._STRINGS[lang]
            for key in ("auto_existing_title", "auto_existing_update", "auto_existing_update_hint",
                        "auto_existing_recheck", "auto_existing_recheck_hint", "auto_existing_keep",
                        "auto_existing_keep_hint", "auto_keep_log", "upd_note_existing_choice"):
                self.assertIn(key, d, (lang, key))
            self.assertIn("{n}", d["auto_keep_log"])


class PruneKeepsReportMailerTests(unittest.TestCase):
    def test_report_mailer_survives_cleanup(self):
        d = tempfile.mkdtemp()
        t = os.path.join(d, "src", "test", "java", "com", "qastudio", "tests")
        os.makedirs(t)
        for n in ("BaseTest", "ReportMailer", "OldStoryTests"):
            open(os.path.join(t, n + ".java"), "w").close()
        E._prune_generated_orphans(d, "com.qastudio", {"stories": {}})
        self.assertEqual(sorted(os.listdir(t)), ["BaseTest.java", "ReportMailer.java"])


class CypressFrameBodyTests(unittest.TestCase):
    def test_body_target_and_multi_site(self):
        h = AT._CY_HEALER_JS
        self.assertIn("Cypress.$(body).is(selector)", h)
        src = (ROOT / "automation_targets.py").read_text(encoding="utf-8")
        self.assertIn("chromeWebSecurity: false,", src)
