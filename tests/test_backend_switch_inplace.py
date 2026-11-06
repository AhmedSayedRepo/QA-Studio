"""Setup's 'Step 1 · Connect' panel describes the SELECTED backend, and picking a
backend updates the screen in place instead of a full render()."""
import types
import unittest
from unittest.mock import patch

import backend_setup as B
import strings

LANGS = ("en", "ar", "fr", "tr", "es", "de", "nl")
TEMPLATE_KEYS = ("bset_conn_desc", "bset_conn_step_validate",
                 "bset_conn_step_projects", "bset_conn_step_plans")


class ConnectPanelTextTests(unittest.TestCase):
    def test_azure_keeps_its_existing_wording(self):
        desc, steps = B.connect_panel_text({"backend": B.AZURE})
        self.assertEqual(desc, strings.t("main_setup_desc"))
        self.assertEqual(steps, [strings.t("au_step_validates_pat"),
                                 strings.t("au_step_loads_projects"),
                                 strings.t("au_step_fetches_plans")])

    def test_legacy_creds_without_backend_are_azure(self):
        self.assertEqual(B.connect_panel_text({}), B.connect_panel_text({"backend": B.AZURE}))

    def test_jira_zephyr_names_its_own_products_not_azure(self):
        desc, steps = B.connect_panel_text({"backend": B.JIRA_ZEPHYR})
        text = " ".join([desc] + steps)
        self.assertIn("Jira + Zephyr Scale", text)
        self.assertIn("Zephyr Scale", steps[2])
        self.assertNotIn("Azure", text)
        self.assertNotIn("PAT", text)

    def test_every_non_azure_backend_gets_three_steps_naming_its_products(self):
        expect = {B.XRAY: ("Jira + Xray", "Jira", "Xray"),
                  B.TESTRAIL: ("TestRail", "TestRail", "TestRail"),
                  B.AZURE_TESTRAIL: ("Azure DevOps + TestRail", "Azure DevOps", "TestRail"),
                  B.JIRA_TESTRAIL: ("Jira + TestRail", "Jira", "TestRail")}
        for name, (creds, source, target) in expect.items():
            desc, steps = B.connect_panel_text({"backend": name})
            self.assertEqual(len(steps), 3, name)
            self.assertIn(creds, desc, name)
            self.assertIn(creds, steps[0], name)
            self.assertIn(source, steps[1], name)
            self.assertIn(target, steps[2], name)
            self.assertNotIn("PAT", " ".join([desc] + steps), name)

    def test_templates_exist_in_all_seven_languages_with_placeholders(self):
        for lang in LANGS:
            for key in TEMPLATE_KEYS:
                value = strings._STRINGS[lang].get(key, "")
                self.assertTrue(value.strip(), (lang, key))
        for lang in LANGS:
            self.assertIn("{creds}", strings._STRINGS[lang]["bset_conn_desc"], lang)
            self.assertIn("{creds}", strings._STRINGS[lang]["bset_conn_step_validate"], lang)
            self.assertIn("{source}", strings._STRINGS[lang]["bset_conn_step_projects"], lang)
            self.assertIn("{target}", strings._STRINGS[lang]["bset_conn_step_plans"], lang)


class PickWithoutFullRenderTests(unittest.TestCase):
    def _app(self, inplace):
        calls = {"render": 0, "inplace": 0}

        def _render():
            calls["render"] += 1

        app = types.SimpleNamespace(
            creds={"backend": B.AZURE},
            backend_dd=types.SimpleNamespace(value=B.XRAY),
            _fetch_estimate=lambda: None, _toast=lambda *a, **k: None,
            render=_render)
        if inplace is not None:
            def _refresh():
                calls["inplace"] += 1
                return inplace
            app._refresh_backend_inplace = _refresh
        return app, calls

    def _pick(self, app):
        with patch.object(B, "backend_allowed_for_user", return_value=True), \
             patch("store.save", lambda *a, **k: None):
            B._on_pick(app, None)

    def test_switch_updates_in_place_without_full_render(self):
        app, calls = self._app(inplace=True)
        self._pick(app)
        self.assertEqual(app.creds["backend"], B.XRAY)
        self.assertEqual(calls, {"render": 0, "inplace": 1})

    def test_falls_back_to_render_when_in_place_update_is_not_possible(self):
        app, calls = self._app(inplace=False)
        self._pick(app)
        self.assertEqual(calls, {"render": 1, "inplace": 1})

    def test_falls_back_to_render_when_the_screen_has_no_in_place_hook(self):
        app, calls = self._app(inplace=None)
        self._pick(app)
        self.assertEqual(calls["render"], 1)


if __name__ == "__main__":
    unittest.main()
