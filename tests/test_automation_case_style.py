"""Generator 'Automation' test style: stricter prompts, automatability check,
and an automation tag/label on new cases for every backend."""
import pathlib
import sys
import types
import unittest
from unittest.mock import MagicMock

import engine as E
import release_notes
import strings
from tracker.fake import FakeBackend
from tracker.jira_zephyr import JiraZephyrBackend
from tracker.models import Ref, Suite
from tracker.testrail import TestRailBackend as _TR

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANGS = ("en", "ar", "fr", "tr", "es", "de", "nl")


class StyleTests(unittest.TestCase):
    def tearDown(self):
        E.set_case_style("manual")

    def test_default_is_manual_without_labels(self):
        E.set_case_style(None)
        self.assertFalse(E.is_automation_style())
        self.assertEqual(E.case_labels(), [])

    def test_automation_has_label(self):
        E.set_case_style("Automation")
        self.assertEqual(E.case_labels(), ["automation"])

    def test_prompts_append_rules_only_in_automation(self):
        src = (ROOT / "engine.py").read_text(encoding="utf-8")
        self.assertIn("text += _AUTOMATION_STEP_RULES", src)
        self.assertIn("prompt += _AUTOMATION_TITLE_RULES", src)
        self.assertIn("{{APP_USER}}", E._AUTOMATION_STEP_RULES)
        self.assertIn("“", E._AUTOMATION_STEP_RULES)

    def test_steps_prompt_contains_rules(self):
        seen = {}
        orig, orig_sleep = E.ai_complete, E.time.sleep
        try:
            def fake(text, **kw):
                seen["text"] = text
                return '{"steps":[{"precondition":"","action":"a","expected":"b"}]}'
            E.ai_complete = fake
            E.time.sleep = lambda *_: None
            E.set_case_style("automation")
            E.generate_steps("Login works", "AC1 user can log in")
            self.assertIn("AUTOMATION MODE", seen["text"])
            E.set_case_style("manual")
            E.generate_steps("Login works", "AC1 user can log in")
            self.assertNotIn("AUTOMATION MODE", seen["text"])
        finally:
            E.ai_complete, E.time.sleep = orig, orig_sleep


class IssuesTests(unittest.TestCase):
    def test_clean_case_has_no_issues(self):
        steps = [{"precondition": "Logged out, on the login page",
                  "action": "Click the “Login” button",
                  "expected": "“Products” page is shown"}]
        self.assertEqual(E.automation_issues(steps), [])

    def test_flags_multi_action_vague_and_not_automatable(self):
        steps = [{"action": "Type the username and then click Login",
                  "expected": "Login works correctly"},
                 {"action": "Enter the OTP from the SMS", "expected": ""}]
        issues = " | ".join(E.automation_issues(steps))
        self.assertIn("step 1: more than one action", issues)
        self.assertIn("step 1: expected result is vague", issues)
        self.assertIn("step 2: no expected result", issues)
        self.assertIn("step 2: not automatable", issues)

    def test_whole_words_only(self):
        steps = [{"precondition": "On the home page", "action": "Open the hotpot recipe",
                  "expected": "“Recipe” shown"}]
        self.assertEqual(E.automation_issues(steps), [])

    def test_flags_missing_start_unquoted_text_and_long_flow(self):
        steps = [{"action": "Click the “Cart” icon", "expected": "the product name"}]
        issues = " | ".join(E.automation_issues(steps))
        self.assertIn("start state not stated", issues)
        self.assertIn("step 1: no exact on-screen text", issues)
        long = [{"precondition": "x", "action": "Click “A”",
                 "expected": "“B” is shown"}] * 9
        self.assertIn("9 steps — split into smaller cases", E.automation_issues(long))

    def test_state_check_needs_no_quotes(self):
        steps = [{"precondition": "On the cart page", "action": "Click “Remove”",
                  "expected": "The cart badge disappears"}]
        self.assertEqual(E.automation_issues(steps), [])

    def test_empty(self):
        self.assertEqual(E.automation_issues([]), ["no steps"])


class AzureTagTests(unittest.TestCase):
    def setUp(self):
        mods = {"azure": types.ModuleType("azure"),
                "azure.devops": types.ModuleType("azure.devops"),
                "azure.devops.v7_0": types.ModuleType("azure.devops.v7_0"),
                "azure.devops.v7_0.work_item_tracking": types.ModuleType("w"),
                "azure.devops.v7_0.work_item_tracking.models": types.ModuleType("m")}
        mods["azure.devops.v7_0.work_item_tracking.models"].JsonPatchOperation = (
            lambda **kw: kw)
        self._saved = {k: sys.modules.get(k) for k in mods}
        sys.modules.update(mods)
        self.wit, self.test = MagicMock(), MagicMock()
        self.wit.create_work_item.return_value = types.SimpleNamespace(id=7)
        self._clients = (E._wit_client, E._test_client)
        E._wit_client, E._test_client = self.wit, self.test

    def tearDown(self):
        E._wit_client, E._test_client = self._clients
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        E.set_case_style("manual")

    def _paths(self):
        patch = self.wit.create_work_item.call_args[0][0]
        return {p["path"]: p.get("value") for p in patch}

    def test_automation_adds_tag(self):
        E.set_case_style("automation")
        E.create_test_case("P", 1, 2, "Login", None)
        self.assertEqual(self._paths().get("/fields/System.Tags"), "automation")

    def test_manual_has_no_tag(self):
        E.set_case_style("manual")
        E.create_test_case("P", 1, 2, "Login", None)
        self.assertNotIn("/fields/System.Tags", self._paths())


class AdapterLabelTests(unittest.TestCase):
    def _suite(self, sid="5"):
        return Suite(ref=Ref(id=sid), name="S")

    def test_fake_records_labels(self):
        b = FakeBackend()
        case = b.create_test_case("P", None, self._suite(), "T", labels=["automation"])
        self.assertEqual(b.labels[case.ref.id], ["automation"])

    def test_zephyr_sends_labels(self):
        b = JiraZephyrBackend()
        z = MagicMock()
        z.post.return_value = {"id": 1, "key": "P-T1"}
        b._need_zephyr = lambda: z
        b.create_test_case("P", None, self._suite("9"), "T", labels=["automation"])
        self.assertEqual(z.post.call_args_list[0].kwargs["json"]["labels"], ["automation"])
        z.post.reset_mock()
        b.create_test_case("P", None, self._suite("9"), "T")
        self.assertNotIn("labels", z.post.call_args_list[0].kwargs["json"])

    def test_testrail_uses_automated_type(self):
        b = _TR(session=MagicMock())
        posted = {}
        b._get = lambda m: ([{"id": 3, "name": "Automated"}] if m == "get_case_types"
                            else [])
        b._post = lambda m, body: posted.update(body) or {"id": 11}
        b._steps_template_id = lambda project: None
        b.create_test_case(types.SimpleNamespace(ref=Ref(id="1")), None, self._suite(),
                           "T", labels=["automation"])
        self.assertEqual(posted.get("type_id"), 3)


class WiringTests(unittest.TestCase):
    def test_ui_and_runs_set_style(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        setup = (ROOT / "setup.py").read_text(encoding="utf-8")
        worker = (ROOT / "run_worker.py").read_text(encoding="utf-8")
        bset = (ROOT / "backend_setup.py").read_text(encoding="utf-8")
        self.assertIn('E.set_case_style(getattr(self, "case_style", "manual"))', main)
        self.assertIn("app._case_style_segment()", setup)
        self.assertIn('E.set_case_style(run.get("case_style") or "manual")', worker)
        self.assertIn("labels=labels", bset)

    def test_strings_and_release_note(self):
        for lang in LANGS:
            for key in ("s_test_style", "s_style_manual", "s_style_automation",
                        "s_style_hint_manual", "s_style_hint_automation",
                        "upd_note_automation_cases"):
                self.assertIn(key, strings._STRINGS[lang], (lang, key))
        self.assertIn("upd_note_automation_cases", release_notes.PENDING_RELEASE_NOTE_KEYS)


class LabelFilterTests(unittest.TestCase):
    def test_filter_keeps_only_tagged_int_or_str_keys(self):
        story_tcs = {"S1": [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}],
                     "S2": [{"id": "P-T3", "title": "c"}]}
        labels = {1: ["Automation"], 2: [], "P-T3": ["automation", "smoke"]}
        out, kept, total = E.filter_cases_by_label(story_tcs, labels)
        self.assertEqual((kept, total), (2, 3))
        self.assertEqual([c["id"] for c in out["S1"]], [1])
        self.assertEqual([c["id"] for c in out["S2"]], ["P-T3"])

    def test_azure_fetch_parses_tags(self):
        wit = MagicMock()
        wit.get_work_items.return_value = [
            types.SimpleNamespace(id=5, fields={"System.Tags": "automation; smoke"}),
            types.SimpleNamespace(id=6, fields={})]
        saved = E._wit_client
        E._wit_client = wit
        try:
            self.assertEqual(E.fetch_case_labels(["5", 6, "x"]),
                             {5: ["automation", "smoke"], 6: []})
        finally:
            E._wit_client = saved

    def test_fake_roundtrip(self):
        b = FakeBackend()
        s = Suite(ref=Ref(id="5"), name="S")
        tagged = b.create_test_case("P", None, s, "T1", labels=["automation"])
        plain = b.create_test_case("P", None, s, "T2")
        got = b.fetch_case_labels([tagged.ref, plain.ref])
        self.assertEqual(sorted(map(tuple, got.values())), [(), ("automation",)])

    def test_testrail_without_automated_type_is_unsupported(self):
        b = _TR(session=MagicMock())
        b._get = lambda m: []
        self.assertIsNone(b.fetch_case_labels([Ref(id="1")]))

    def test_zephyr_reads_labels(self):
        b = JiraZephyrBackend()
        z = MagicMock()
        z.get.return_value = {"labels": ["automation"]}
        b._need_zephyr = lambda: z
        self.assertEqual(b.fetch_case_labels([Ref(id="1", key="P-T1")]),
                         {"P-T1": ["automation"]})


class RemoteAndLayoutTests(unittest.TestCase):
    def test_remote_runs_carry_style(self):
        auth = (ROOT / "auth_supabase.py").read_text(encoding="utf-8")
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('row["case_style"] = "automation"', auth)
        self.assertIn('case_style=getattr(self, "case_style", "manual")', main)
        sql = (ROOT / "supabase" / "migrations"
               / "20261106120000_remote_runs_case_style.sql").read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS case_style", sql)

    def test_style_sits_between_generator_and_language(self):
        setup = (ROOT / "setup.py").read_text(encoding="utf-8")
        i_tool = setup.index("app._tool_segment(persist=False)")
        i_style = setup.index("app._case_style_segment()")
        i_lang = setup.index("app._lang_segment(persist=False)")
        self.assertTrue(i_tool < i_style < i_lang)

    def test_only_tagged_wiring_and_strings(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        auto = (ROOT / "automation.py").read_text(encoding="utf-8")
        self.assertIn('getattr(self, "auto_only_automation", False) and not _file_mode', main)
        self.assertIn("_flip_only_automation", auto)
        for lang in LANGS:
            for key in ("auto_only_tagged", "auto_only_tagged_log",
                        "auto_only_tagged_unsupported"):
                self.assertIn(key, strings._STRINGS[lang], (lang, key))


class HelpTopicTests(unittest.TestCase):
    def test_help_topics_localized_with_matching_step_counts(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"auto_cases": {', main)
        self.assertIn('"auto_scripts": {', main)
        for lang in LANGS:
            d = strings._STRINGS[lang]
            for key, n in (("auto_cases", 8), ("auto_scripts", 7)):
                self.assertIn(f"helpm_{key}_title", d, (lang, key))
                for i in range(n):
                    self.assertIn(f"helpm_{key}_s{i}", d, (lang, key, i))
                self.assertNotIn(f"helpm_{key}_s{n}", d)
            self.assertIn("s_style_tips", d)
            self.assertIn("auto_tips_link", d)

    def test_links_open_the_topics(self):
        self.assertIn('app._show_help("auto_cases")',
                      (ROOT / "setup.py").read_text(encoding="utf-8"))
        self.assertIn('app._show_help("auto_scripts")',
                      (ROOT / "automation.py").read_text(encoding="utf-8"))

    def test_prompt_carries_the_rules(self):
        r = E._AUTOMATION_STEP_RULES
        for phrase in ("Quote EVERY on-screen text", "One fact per expected result",
                       "At most 8 steps", "No environment URLs"):
            self.assertIn(phrase, r)
        self.assertIn("split a long multi-page journey", E._AUTOMATION_TITLE_RULES)


if __name__ == "__main__":
    unittest.main()
