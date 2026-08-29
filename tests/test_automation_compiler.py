"""Regression coverage for the scenario-safe automation intent compiler."""
import json
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# The focused compiler test has no HTTP behavior.  This lets it run with the
# bundled Codex Python too, which intentionally has no project dependencies.
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    _requests = types.ModuleType("requests")
    _adapters = types.ModuleType("requests.adapters")
    _adapters.HTTPAdapter = object
    _requests.adapters = _adapters
    sys.modules["requests"] = _requests
    sys.modules["requests.adapters"] = _adapters

import engine
import automation_targets


def _intent(role, source_step, target, verb="", value="", kind="button"):
    return {
        "role": role,
        "verb": verb,
        "target": target,
        "keywords": [target],
        "kind": kind,
        "value": value,
        "check": "visible" if role == "assertion" else "",
        "expected": target if role == "assertion" else "",
        "from_steps": [source_step],
    }


def _case(*actions):
    return {
        "title": "Scenario ordering",
        "steps": [{"action": action, "expected": ""} for action in actions],
    }


class AutomationCompilerTests(unittest.TestCase):
    def _compile(self, tc, intents):
        response = json.dumps({"page": "app", "intents": intents})
        with patch.object(engine, "ai_complete", return_value=response):
            return engine.compile_test_case(tc)

    def test_normalises_pasted_markdown_browser_url_before_navigation(self):
        actual = engine._normalise_browser_http_url(
            "[Sauce Demo](https://www.saucedemo.com/\u200b)", "Login URL")

        self.assertEqual(actual, "https://www.saucedemo.com/")

    def test_rejects_browser_url_with_embedded_whitespace_without_echoing_it(self):
        with self.assertRaisesRegex(ValueError, "without spaces or line breaks"):
            engine._normalise_browser_http_url("https://example.test/a b", "Login URL")

    def test_executes_intents_in_authored_step_order_not_model_response_order(self):
        tc = _case("Open the account menu", "Choose Settings", "Save changes")
        intents = [
            _intent("action", 3, "Save changes", "click"),
            _intent("action", 2, "Settings", "click"),
            _intent("action", 1, "Account menu", "click"),
        ]

        compiled = self._compile(tc, intents)

        self.assertEqual([item["from_steps"] for item in compiled], [[1], [2], [3]])
        self.assertEqual([item["target"] for item in compiled],
                         ["Account menu", "Settings", "Save changes"])

    def test_preserves_repeated_actions_from_distinct_authored_steps(self):
        tc = _case("Click Next", "Click Next")
        intents = [
            _intent("action", 1, "Next", "click"),
            _intent("action", 2, "Next", "click"),
        ]

        compiled = self._compile(tc, intents)

        self.assertEqual(len(compiled), 2)
        self.assertEqual([item["from_steps"] for item in compiled], [[1], [2]])

    def test_runs_an_action_before_its_same_step_expectation(self):
        tc = _case("Save changes")
        intents = [
            _intent("assertion", 1, "Success message"),
            _intent("action", 1, "Save changes", "click"),
        ]

        compiled = self._compile(tc, intents)

        self.assertEqual([item["role"] for item in compiled], ["action", "assertion"])

    def test_uses_direct_live_capture_as_the_generated_locator_seed(self):
        tc = _case("Save changes")
        tc["steps"][0]["locator"] = {"by": "css", "value": '[data-testid="save"]'}
        tc["steps"][0]["locator_src"] = "live"
        compiled = self._compile(tc, [_intent("action", 1, "Save changes", "click")])

        self.assertEqual(compiled[0]["live_locator"],
                         {"by": "css", "value": '[data-testid="save"]'})
        self.assertEqual(engine._seed_locator_for_intent(compiled[0]),
                         ("cssSelector", '[data-testid="save"]', True))
        seeds, lines = {}, []
        engine._emit_intent(lines, "1.0.0", compiled[0], seed_sink=seeds)
        self.assertEqual(seeds["1.0.0"]["source"], "inspection")

    def test_inspection_seed_upgrades_heuristics_but_preserves_healed_locator(self):
        with tempfile.TemporaryDirectory() as out_dir:
            engine._write_seed_locators(out_dir, {
                "1.0.0": {"by": "cssSelector", "value": "#old", "source": "seed"},
            })
            engine._write_seed_locators(out_dir, {
                "1.0.0": {"by": "cssSelector", "value": '[data-testid="save"]',
                          "source": "inspection"},
            })
            with open(out_dir + "/locators.json", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["1.0.0"]["source"], "inspection")

            # A newer live inspection corrects an earlier live capture; only
            # runtime/manual records are protected from this replacement.
            automation_targets._merge_seed_locators(out_dir, {
                "1.0.0": {"by": "cssSelector", "value": "#fresh-save",
                          "source": "inspection"},
            }, lambda *args: None)
            with open(out_dir + "/locators.json", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["1.0.0"]["value"], "#fresh-save")

            with open(out_dir + "/locators.json", "w", encoding="utf-8") as f:
                json.dump({"1.0.0": {"by": "id", "value": "healed-save",
                                      "source": "runtime"}}, f)
            engine._write_seed_locators(out_dir, {
                "1.0.0": {"by": "cssSelector", "value": "#new", "source": "inspection"},
            })
            with open(out_dir + "/locators.json", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["1.0.0"]["value"], "healed-save")

    def test_writes_local_inspection_screen_report(self):
        with tempfile.TemporaryDirectory() as out_dir:
            path = engine.write_inspection_screens(out_dir, {
                "stats": {"live": 2, "snapshot": 1, "guess": 0},
                "dom_snapshot": [{"tag": "button", "text": "Save"}],
                "screen_captures": [{"url": "https://example.test/settings",
                                     "elements": [{"id": "save"}]}],
            })
            with open(path, encoding="utf-8") as f:
                report = json.load(f)
        self.assertEqual(report["stats"]["live"], 2)
        self.assertEqual(report["screens"][0]["text"], "Save")
        self.assertEqual(report["schema_version"], 4)
        self.assertEqual(report["screen_captures"][0]["elements"][0]["id"], "save")

    def test_screen_json_rehydrates_per_intent_bindings(self):
        payload = [{"story": {"id": "story-1"}, "test_cases": [{
            "id": "case-1", "title": "Sign in", "steps": [{"action": "Enter credentials"}],
        }]}]
        result = {"stories_payload": [{"story": {"id": "story-1"}, "test_cases": [{
            "id": "case-1", "title": "Sign in", "steps": [{
                "locator_bindings": [{"role": "action", "verb": "type",
                                      "target": "username field", "keywords": ["Username"],
                                      "kind": "input", "locator": {"by": "css", "value": "#user"},
                                      "source": "live"}],
            }],
        }]}]}
        with tempfile.TemporaryDirectory() as out_dir:
            path = engine.write_inspection_screens(out_dir, result)
            self.assertEqual(engine.apply_inspection_screens(payload, path), 1)
            # Re-applying a report is safe during resume: no duplicate binding.
            self.assertEqual(engine.apply_inspection_screens(payload, path), 0)
        binding = payload[0]["test_cases"][0]["steps"][0]["locator_bindings"][0]
        self.assertEqual(binding["locator"]["value"], "#user")

    def test_screen_json_rehydrates_verified_execution_plan(self):
        payload = [{"story": {"id": "story-1"}, "test_cases": [{
            "id": "case-1", "title": "Save settings", "steps": [],
        }]}]
        plan = {
            "schema_version": 1, "case_type": "interaction",
            "page_context": "app", "explicit_login_flow": False,
            "intents": [{**_intent("action", 1, "Save settings", "click"),
                         "inspection_source": "live",
                         "live_locator": {"by": "css", "value": "#save"}}],
        }
        result = {"stories_payload": [{"story": {"id": "story-1"}, "test_cases": [{
            "id": "case-1", "title": "Save settings", "inspection_plan": plan,
        }]}]}
        with tempfile.TemporaryDirectory() as out_dir:
            path = engine.write_inspection_screens(out_dir, result)
            self.assertEqual(engine.apply_inspection_screens(payload, path), 1)
        self.assertEqual(payload[0]["test_cases"][0]["inspection_plan"], plan)

    def test_verified_execution_plan_generates_without_second_compilation(self):
        plan = {
            "case_type": "interaction", "page_context": "app",
            "explicit_login_flow": False,
            "intents": [
                {**_intent("action", 1, "Save settings", "click"),
                 "inspection_source": "live",
                 "live_locator": {"by": "css", "value": "#save"}},
                {**_intent("assertion", 1, "Saved message"),
                 "inspection_source": "live",
                 "live_locator": {"by": "css", "value": "[role=status]"}},
            ],
        }
        payload = [{"story": {"id": "story-1"}, "test_cases": [{
            "id": "case-1", "title": "Save settings", "inspection_plan": plan,
        }]}]
        messages = []
        sequenced = engine.sequence_verified_inspection_plans(
            payload, log=lambda message, *_: messages.append(message))
        self.assertEqual(sequenced[0]["cases"][0]["intents"], plan["intents"])
        self.assertIn("no second AI compile", messages[-1])

    def test_verified_execution_plan_blocks_unresolved_locator(self):
        plan = {
            "case_type": "interaction", "page_context": "app", "intents": [
                {**_intent("action", 1, "Save settings", "click"),
                 "inspection_source": "guess", "inspection_unresolved": "guess"},
            ],
        }
        payload = [{"story": {"id": "story-1"}, "test_cases": [{
            "id": "case-1", "title": "Save settings", "inspection_plan": plan,
        }]}]
        messages = []
        self.assertIsNone(engine.sequence_verified_inspection_plans(
            payload, log=lambda message, *_: messages.append(message)))
        self.assertIn("coverage is incomplete", messages[0])

    def test_generation_uses_verified_plan_without_recompiling(self):
        plan = {"case_type": "interaction", "page_context": "app", "intents": [
            {**_intent("action", 1, "Save settings", "click"),
             "inspection_source": "live",
             "live_locator": {"by": "css", "value": "#save"}},
        ]}
        payload = [{"story": {"id": "story-1"}, "test_cases": [{
            "id": "case-1", "title": "Save settings", "inspection_plan": plan,
        }]}]
        with patch.object(engine, "validate_and_sequence_suite") as recompile, \
                patch.object(automation_targets, "build", return_value=["generated"]) as build:
            result = engine.generate_and_push_selfhealing(
                "unused", payload, "https://example.test", target="playwright")
        self.assertEqual(result, ["generated"])
        recompile.assert_not_called()
        self.assertEqual(build.call_args.args[2][0]["cases"][0]["intents"], plan["intents"])

    def test_semantic_binding_rejects_a_different_named_product(self):
        intent = _intent("action", 1, "Add Sauce Labs Backpack to cart", "click")
        intent["keywords"] = ["Sauce Labs Backpack", "add to cart"]
        wrong = {"idx": 0, "tag": "button", "visible": True,
                 "id": "add-to-cart-sauce-labs-bike-light", "text": "Add to cart",
                 "name": "", "role": "", "testid": "", "placeholder": "",
                 "aria": "", "aname": "", "type": "button", "cls": "", "svgicon": ""}
        right = {**wrong, "idx": 1, "id": "add-to-cart-sauce-labs-backpack"}

        ranked = engine._rank_candidates(intent, [wrong, right])

        self.assertEqual([candidate["id"] for _score, candidate in ranked],
                         ["add-to-cart-sauce-labs-backpack"])

    def test_semantic_binding_requires_remove_not_the_product_title(self):
        intent = _intent("action", 1, "Remove button for Sauce Labs Backpack", "click")
        intent["keywords"] = ["Remove", "Sauce Labs Backpack"]
        title = {"idx": 0, "tag": "a", "visible": True,
                 "id": "item_4_title_link", "text": "Sauce Labs Backpack",
                 "name": "", "role": "", "testid": "item-4-title-link",
                 "placeholder": "", "aria": "", "aname": "", "type": "",
                 "cls": "", "svgicon": ""}
        remove = {**title, "idx": 1, "tag": "button",
                  "id": "remove-sauce-labs-backpack", "text": "Remove",
                  "testid": "remove-sauce-labs-backpack"}

        ranked = engine._rank_candidates(intent, [title, remove])

        self.assertEqual([candidate["id"] for _score, candidate in ranked],
                         ["remove-sauce-labs-backpack"])

    def test_semantic_binding_requires_shopping_cart_not_add_to_cart(self):
        intent = _intent("action", 1, "shopping cart", "click", kind="link")
        intent["keywords"] = ["shopping cart"]
        add = {"idx": 0, "tag": "button", "visible": True,
               "id": "add-to-cart-sauce-labs-backpack", "text": "Add to cart",
               "name": "", "role": "", "testid": "add-to-cart-sauce-labs-backpack",
               "placeholder": "", "aria": "", "aname": "", "type": "",
               "cls": "", "svgicon": ""}
        cart = {**add, "idx": 1, "tag": "a", "id": "shopping-cart-link",
                "text": "", "testid": "shopping-cart-link"}

        ranked = engine._rank_candidates(intent, [add, cart])

        self.assertEqual([candidate["id"] for _score, candidate in ranked],
                         ["shopping-cart-link"])

    def test_click_fallback_accepts_a_link_when_prose_wrongly_calls_it_a_button(self):
        # This is the live SauceDemo shape: the authored/compiled action calls
        # the cart a button, while the actual tested DOM exposes a stable link.
        # A safe resolver may cross only the button/link control boundary.
        intent = _intent("action", 4, "shopping cart", "click", kind="button")
        intent["keywords"] = ["Open Cart", "cart"]
        cart_link = {"idx": 0, "tag": "a", "visible": True, "id": "",
                     "text": "1", "name": "", "role": "",
                     "testid": "shopping-cart-link", "placeholder": "",
                     "aria": "", "aname": "", "type": "",
                     "cls": "shopping_cart_link", "svgicon": "",
                     "css": "#shopping_cart_container > a.shopping_cart_link"}

        ranked = engine._rank_candidates(intent, [cart_link])

        self.assertEqual([candidate["testid"] for _score, candidate in ranked],
                         ["shopping-cart-link"])

    def test_assertion_uses_visible_keyword_not_page_context_words(self):
        intent = _intent("assertion", 1, "Products inventory page")
        intent.update({"keywords": ["Products"], "kind": "text"})
        heading = {"idx": 0, "tag": "span", "visible": True, "id": "",
                   "text": "Products", "name": "", "role": "", "testid": "title",
                   "placeholder": "", "aria": "", "aname": "", "type": "",
                   "cls": "title", "svgicon": ""}
        inventory = {**heading, "idx": 1, "text": "Inventory list", "testid": "inventory"}

        ranked = engine._rank_candidates(intent, [heading, inventory])

        self.assertEqual([candidate["text"] for _score, candidate in ranked], ["Products"])

    def test_add_action_rejects_existing_remove_button_for_same_product(self):
        intent = _intent("action", 1, "Add to cart button for Sauce Labs Backpack", "click")
        intent.update({"keywords": ["Add to cart", "Sauce Labs Backpack"], "kind": "button"})
        remove = {"idx": 0, "tag": "button", "visible": True,
                  "id": "remove-sauce-labs-backpack", "text": "Remove",
                  "name": "", "role": "", "testid": "remove-sauce-labs-backpack",
                  "placeholder": "", "aria": "", "aname": "", "type": "button",
                  "cls": "", "svgicon": ""}

        self.assertEqual(engine._rank_candidates(intent, [remove]), [])

    def test_composite_removal_outcome_becomes_two_hidden_assertions(self):
        intent = _intent("assertion", 5, "removed backpack and cart badge")
        intent.update({"check": "not_visible",
                       "expected": "Sauce Labs Backpack disappears and the cart badge is no longer displayed."})

        assertions = engine._split_composite_negative_assertion(intent)

        self.assertEqual([item["target"] for item in assertions],
                         ["Sauce Labs Backpack", "shopping cart badge"])
        self.assertTrue(all(item["assert_before_action"] for item in assertions))

    def test_playwright_emits_exact_hidden_assertion_without_healing(self):
        intent = _intent("assertion", 5, "Sauce Labs Backpack")
        intent.update({"check": "not_visible", "kind": "text",
                       "assert_before_action": True,
                       "inspection_source": "live",
                       "live_locator": {"by": "css", "value": "#item_4_title_link"}})
        lines, seeds = [], {}
        automation_targets._emit_pw_intent(lines, "1.0.0", intent,
                                           engine._seed_locator_for_intent, seeds)

        self.assertIn("heal.assertHidden", lines[0])
        self.assertNotIn("assertVisible", lines[0])
        self.assertEqual(seeds["1.0.0"]["value"], "#item_4_title_link")

    def test_detects_cart_click_that_requires_navigation_verification(self):
        intent = _intent("action", 4, "shopping cart", "click", kind="link")
        intent["keywords"] = ["Shopping Cart", "cart"]
        self.assertTrue(engine._is_shopping_cart_navigation(intent))

    def test_explicit_login_flow_starts_logged_out(self):
        intents = [
            _intent("action", 1, "username field", "type", kind="input"),
            _intent("action", 2, "password field", "type", kind="input"),
            _intent("action", 3, "Login button", "click"),
        ]
        self.assertTrue(engine._case_has_explicit_login_flow(intents))

    def test_combined_login_and_app_case_uses_login_transition_then_app_actions(self):
        login_actions = [
            _intent("action", 1, "username field", "type", kind="input"),
            _intent("action", 2, "password field", "type", kind="input"),
            _intent("action", 3, "Login button", "click"),
        ]
        app_action = _intent("action", 4, "Add Sauce Labs Backpack to cart", "click")
        case = {
            "title": "Add one item to the cart", "ctype": "interaction",
            "page_context": "app", "bucket": 3, "explicit_login_flow": True,
            "intents": login_actions + [app_action], "needs_review": False,
        }
        java, _ = engine.generate_selfhealing_test_class({"id": "file"}, [case],
                                                          "com.qastudio")
        self.assertIn("openLoginPage();", java)
        self.assertIn("performLogin();", java)
        self.assertIn("Add Sauce Labs Backpack to cart", java)
        self.assertNotIn("username field", java)
        # The login fields are not emitted individually, so they cannot inflate
        # the runtime-TODO count for this post-login cart scenario.
        self.assertEqual(engine._count_null_seeds(3, case["intents"], True), 0)

    def test_multi_action_row_keeps_each_inspected_locator(self):
        tc = {"steps": [{"locator_bindings": [
            {"role": "action", "verb": "type", "target": "username field",
             "keywords": ["Username"], "kind": "input",
             "locator": {"by": "css", "value": "#user-name"}, "source": "live"},
            {"role": "action", "verb": "type", "target": "password field",
             "keywords": ["Password"], "kind": "input",
             "locator": {"by": "css", "value": "#password"}, "source": "live"},
        ]}]}
        compiled = self._compile(tc, [
            _intent("action", 1, "username field", "type", kind="input"),
            _intent("action", 1, "password field", "type", kind="input"),
        ])
        self.assertEqual(compiled[0]["live_locator"]["value"], "#user-name")
        self.assertEqual(compiled[1]["live_locator"]["value"], "#password")

    def test_playwright_explicit_login_flow_runs_only_post_login_actions(self):
        intents = [
            _intent("action", 1, "username field", "type", kind="input"),
            _intent("action", 2, "password field", "type", kind="input"),
            _intent("action", 3, "Login button", "click"),
            _intent("action", 4, "Add Sauce Labs Backpack to cart", "click"),
        ]
        spec, _ = automation_targets._pw_spec({"id": "file", "title": "Import"}, [{
            "title": "Add one item to the cart", "bucket": 3,
            "explicit_login_flow": True, "intents": intents,
        }], engine._seed_locator_for_intent, {})
        self.assertIn("await performLogin(page);", spec)
        self.assertIn("Add Sauce Labs Backpack to cart", spec)
        self.assertNotIn('"username field"', spec)

    def test_playwright_unresolved_assertion_stays_a_todo(self):
        intent = _intent("assertion", 1, "Products heading", kind="text")
        intent["inspection_unresolved"] = "guess"
        lines = []
        automation_targets._emit_pw_intent(lines, "1.0.0", intent,
                                           engine._seed_locator_for_intent, {})
        emitted = "\n".join(lines)
        self.assertIn(", null,", emitted)
        self.assertIn("TODO verify locator", emitted)

    def test_unresolved_assertion_is_emitted_as_a_runtime_todo(self):
        tc = {
            "steps": [{"Action": "Verify success", "assert_locator": None,
                       "assert_locator_src": "guess"}],
        }
        compiled = self._compile(tc, [_intent("assertion", 1, "Success message",
                                             kind="message")])
        self.assertEqual(compiled[0]["inspection_unresolved"], "guess")
        self.assertEqual(engine._seed_locator_for_intent(compiled[0]),
                         ("cssSelector", "TODO_RESOLVE_AT_RUNTIME", False))
        self.assertEqual(engine._count_null_seeds(3, compiled), 1)
        lines = []
        engine._emit_intent(lines, "1.0.0", compiled[0])
        emitted = "\n".join(lines)
        self.assertIn(", null,", emitted)
        self.assertIn("TODO verify locator", emitted)

    def test_js_targets_follow_the_same_inspection_upgrade_rule(self):
        with tempfile.TemporaryDirectory() as out_dir:
            automation_targets._merge_seed_locators(out_dir, {
                "1.0.0": {"by": "cssSelector", "value": "#old", "source": "seed"},
            }, lambda *args: None)
            automation_targets._merge_seed_locators(out_dir, {
                "1.0.0": {"by": "cssSelector", "value": "#verified",
                          "source": "inspection"},
            }, lambda *args: None)
            with open(out_dir + "/locators.json", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["1.0.0"]["source"], "inspection")

    def test_collapses_only_duplicate_model_output_for_the_same_source_step(self):
        tc = _case("Open the account menu")
        intents = [
            _intent("action", 1, "Account menu", "click"),
            _intent("action", 1, "Account menu", "click"),
        ]

        compiled = self._compile(tc, intents)

        self.assertEqual(len(compiled), 1)
        self.assertEqual(compiled[0]["from_steps"], [1])

    def test_navigation_preserves_the_authored_http_url(self):
        url = "https://the-internet.herokuapp.com/checkboxes"
        tc = _case("Navigate to checkboxes page: " + url)
        compiled = self._compile(tc, [
            _intent("action", 1, "checkboxes page", "navigate", kind="link"),
        ])

        self.assertEqual(compiled[0]["value"], url)

    def test_navigation_rejects_url_credentials_and_tokens(self):
        tc = _case("Open https://demo:password@example.test/path")
        compiled = self._compile(tc, [
            _intent("action", 1, "target page", "navigate", kind="link"),
        ])

        self.assertEqual(compiled[0]["value"], "")

    def test_composite_visible_outcome_becomes_atomic_assertions(self):
        intent = _intent("assertion", 4, "secure area success message", kind="text")
        intent.update({"keywords": ["Secure Area", "You logged"],
                       "expected": "The Secure Area heading and success message are visible."})

        assertions = engine._split_composite_positive_assertion(intent)

        self.assertEqual([item["target"] for item in assertions],
                         ["Secure Area", "You logged"])
        self.assertTrue(all(item["kind"] == "text" for item in assertions))

    def test_field_assertion_matches_an_input_tag_without_exposing_values(self):
        intent = _intent("assertion", 3, "input field visible", kind="text")
        intent["keywords"] = ["input"]
        field = {"idx": 0, "tag": "input", "visible": True, "id": "input-example",
                 "text": "", "name": "", "role": "", "testid": "", "placeholder": "",
                 "aria": "", "aname": "", "type": "text", "cls": "", "svgicon": ""}

        self.assertEqual(engine._rank_candidates(intent, [field])[0][1]["id"], "input-example")

    def test_generated_targets_emit_direct_navigation_for_a_verified_url(self):
        intent = _intent("action", 1, "checkboxes page", "navigate", kind="link")
        intent["value"] = "https://the-internet.herokuapp.com/checkboxes"
        pw, cy, java = [], [], []
        automation_targets._emit_pw_intent(pw, "1.0.0", intent,
                                           engine._seed_locator_for_intent, {})
        automation_targets._emit_cy_intent(cy, "1.0.0", intent,
                                           engine._seed_locator_for_intent, {})
        engine._emit_intent(java, "1.0.0", intent)

        self.assertIn("page.goto", pw[0])
        self.assertIn("cy.visit", cy[0])
        self.assertIn("driver.get", java[0])

    def test_verified_plan_accepts_direct_url_navigation_without_a_locator(self):
        intent = _intent("action", 1, "checkboxes page", "navigate", kind="link")
        intent.update({"value": "https://the-internet.herokuapp.com/checkboxes",
                       "inspection_source": "start_state"})
        payload = [{"story": {"id": "file"}, "test_cases": [{
            "title": "Select a checkbox",
            "inspection_plan": {"case_type": "interaction", "page_context": "app",
                                "intents": [intent]},
        }]}]

        result = engine.sequence_verified_inspection_plans(payload)

        self.assertIsNotNone(result)
        self.assertEqual(result[0]["cases"][0]["intents"][0]["value"], intent["value"])

    def test_verified_plan_accepts_browser_context_transition_without_locator(self):
        intent = _intent("action", 2, "Accept alert", "dialog_accept", kind="context")
        intent["inspection_source"] = "context"
        payload = [{"story": {"id": "file"}, "test_cases": [{
            "title": "Alert", "inspection_plan": {"case_type": "interaction",
                "page_context": "app", "intents": [intent]},
        }]}]

        self.assertIsNotNone(engine.sequence_verified_inspection_plans(payload))

    def test_native_select_never_binds_page_text_instead_of_select_control(self):
        intent = _intent("action", 2, "dropdown option", "select", "Option 2", "select")
        intent["keywords"] = ["Option 2"]
        body = {"idx": 0, "tag": "body", "visible": True, "id": "", "text": "Dropdown List Option 2",
                "name": "", "role": "", "testid": "", "placeholder": "", "aria": "", "aname": "",
                "type": "", "cls": "", "svgicon": ""}
        dropdown = {**body, "idx": 1, "tag": "select", "id": "dropdown", "text": "",
                    "aname": "", "options": ["Please select an option", "Option 1", "Option 2"]}

        ranked = engine._rank_candidates(intent, [body, dropdown])

        self.assertEqual([element["tag"] for _score, element in ranked], ["select"])

    def test_explicit_button_cannot_bind_a_same_word_heading(self):
        intent = _intent("action", 1, "Add button", "click", kind="button")
        intent["keywords"] = ["Add"]
        heading = {"idx": 0, "tag": "h4", "visible": True, "id": "", "text": "Remove/add",
                   "name": "", "role": "", "testid": "", "placeholder": "", "aria": "", "aname": "",
                   "type": "", "cls": "", "svgicon": ""}

        self.assertEqual(engine._rank_candidates(intent, [heading]), [])

    def test_derived_accessible_name_never_becomes_a_nonexistent_aria_locator(self):
        element = {"idx": 0, "tag": "input", "visible": True, "id": "", "name": "",
                   "text": "", "aname": "checkbox 1", "aria": "", "testid": "", "placeholder": "",
                   "type": "checkbox", "cls": "", "svgicon": "",
                   "css": "#checkboxes > input:nth-of-type(1)", "xpath": "/html/body/input[1]"}

        self.assertEqual(engine._to_locator(element),
                         {"by": "css", "value": "#checkboxes > input:nth-of-type(1)"})

    def test_checked_assertion_binds_the_checked_checkbox_as_a_live_locator(self):
        intent = _intent("assertion", 2, "Checkbox 1 checked", kind="input")
        intent.update({"keywords": ["Checkbox 1"], "check": "checked"})
        first = {"idx": 0, "tag": "input", "visible": True, "id": "", "name": "",
                 "text": "", "aname": "checkbox 1", "aria": "", "testid": "", "placeholder": "",
                 "type": "checkbox", "checked": True, "disabled": False, "cls": "", "svgicon": "",
                 "css": "#checkboxes > input:nth-of-type(1)", "xpath": "/html/body/input[1]"}
        second = {**first, "idx": 1, "aname": "checkbox 2", "checked": False,
                  "css": "#checkboxes > input:nth-of-type(2)"}

        ranked = engine._rank_candidates(intent, [first, second])

        self.assertEqual([element["css"] for _score, element in ranked], [first["css"]])

    def test_generated_targets_assert_real_control_states(self):
        intent = _intent("assertion", 2, "Checkbox 1 checked", kind="input")
        intent.update({"keywords": ["Checkbox 1"], "check": "checked",
                       "live_locator": {"by": "css", "value": "#checkboxes > input:nth-of-type(1)"}})
        pw, cy, java = [], [], []
        automation_targets._emit_pw_intent(pw, "1.0.0", intent,
                                           engine._seed_locator_for_intent, {})
        automation_targets._emit_cy_intent(cy, "1.0.0", intent,
                                           engine._seed_locator_for_intent, {})
        engine._emit_intent(java, "1.0.0", intent)

        self.assertIn("assertChecked", pw[0])
        self.assertIn("healAssertChecked", cy[0])
        self.assertIn("assertChecked", "\n".join(java))

    def test_generated_targets_select_native_options_by_visible_label(self):
        intent = _intent("action", 2, "dropdown", "select", "Option 2", "select")
        intent["live_locator"] = {"by": "id", "value": "dropdown"}
        pw, cy, java = [], [], []
        automation_targets._emit_pw_intent(pw, "1.0.0", intent,
                                           engine._seed_locator_for_intent, {})
        automation_targets._emit_cy_intent(cy, "1.0.0", intent,
                                           engine._seed_locator_for_intent, {})
        engine._emit_intent(java, "1.0.0", intent)

        self.assertIn("heal.act", pw[0])
        self.assertIn("cy.healAct", cy[0])
        self.assertIn('heal.act', java[0])
        self.assertIn("selectOption", automation_targets._PW_HEALER_JS)
        self.assertIn(".select(String(value))", automation_targets._CY_HEALER_JS)
        self.assertIn("selectByVisibleText", engine._sh_healer("com.qastudio"))

    def test_reviewed_file_metadata_makes_frame_and_window_first_class_operations(self):
        tc = {"title": "Frame and popup", "steps": [
            {"action": "Switch to editor frame", "expected": "",
             "element_type": "Iframe", "preferred_locator": "iframe#editor"},
            {"action": "Switch to new browser window", "expected": "",
             "element_type": "Window context"},
        ]}
        model = [_intent("action", 1, "editor frame", "click"),
                 _intent("action", 2, "new browser window", "select")]

        compiled = self._compile(tc, model)

        self.assertEqual([item["verb"] for item in compiled], ["frame", "window_switch"])
        self.assertEqual(compiled[0]["kind"], "frame")
        self.assertEqual(compiled[0]["preferred_locator"], "iframe#editor")

    def test_reviewed_preferred_locator_is_preserved_for_an_assertion(self):
        tc = {"title": "Nested frame", "steps": [{
            "action": "Verify left frame text", "expected": "LEFT",
            "element_type": "Frame body", "preferred_locator": "body",
        }]}
        compiled = self._compile(tc, [_intent("assertion", 1, "LEFT", kind="text")])

        self.assertEqual(compiled[0]["preferred_locator"], "body")

    def test_raw_fallback_keeps_reviewed_upload_as_an_upload_operation(self):
        tc = {"title": "Upload", "steps": [{
            "action": "Choose a test file", "expected": "",
            "element_type": "File input", "preferred_locator": "input[type=file]",
        }]}
        intents = engine._intents_from_raw_steps(tc)

        self.assertEqual(intents[0]["verb"], "upload")
        self.assertEqual(intents[0]["value"], "fixtures/qastudio-upload.txt")

    def test_upload_wording_keeps_reviewed_link_or_submit_control_contracts(self):
        link = engine._source_operation_contract({
            "action": "Click the File Upload link", "element_type": "Anchor"})
        submit = engine._source_operation_contract({
            "action": "Click the Upload button", "element_type": "Submit button"})

        self.assertEqual((link["verb"], link["kind"]), ("click", "link"))
        self.assertEqual((submit["verb"], submit["kind"]), ("click", "button"))

    def test_reviewed_anchor_and_native_select_cannot_be_rewritten_as_navigation_or_body_click(self):
        link = engine._source_operation_contract({
            "action": "Open Dropdown page", "element_type": "Anchor"})
        select = engine._source_operation_contract({
            "action": "Select Option 2 from the dropdown", "element_type": "Native select"})

        self.assertEqual((link["verb"], link["kind"]), ("click", "link"))
        self.assertEqual((select["verb"], select["kind"], select["value"]),
                         ("select", "select", "Option 2"))

    def test_dialog_context_runs_before_its_opener_assertion_and_removes_dialog_dom_check(self):
        tc = {"title": "Confirm", "steps": [
            {"action": "Click confirm", "expected": "A confirmation dialog opens."},
            {"action": "Dismiss the confirmation dialog", "expected": "Cancelled result is shown.",
             "element_type": "Browser dialog"},
        ]}
        model = [
            _intent("action", 1, "Confirm", "click"),
            _intent("assertion", 1, "confirmation dialog", kind="text"),
            _intent("action", 2, "dismiss", "click"),
            _intent("assertion", 2, "result", kind="text"),
        ]
        compiled = self._compile(tc, model)

        self.assertEqual([item["role"] + ":" + item.get("verb", "") for item in compiled],
                         ["action:click", "action:dialog_dismiss", "assertion:"])
        self.assertTrue(compiled[-1]["context_result"])


    def test_playwright_emits_context_operations_without_fake_locators(self):
        intents = [
            _intent("action", 1, "Open popup", "click"),
            _intent("action", 2, "Switch popup", "window_switch", kind="context"),
            _intent("action", 3, "Upload file", "upload", "fixtures/qastudio-upload.txt", "input"),
        ]
        spec, _ = automation_targets._pw_spec({"id": "file", "title": "Import"}, [{
            "title": "Context", "bucket": 3, "explicit_login_flow": False, "intents": intents,
        }], engine._seed_locator_for_intent, {})

        self.assertIn("heal.prepareWindow();", spec)
        self.assertIn("await heal.switchWindow();", spec)
        self.assertIn('"upload"', spec)
        self.assertIn("setInputFiles", automation_targets._PW_HEALER_JS)
        self.assertIn("location.href !== 'about:blank'", automation_targets._PW_HEALER_JS)


if __name__ == "__main__":
    unittest.main()
