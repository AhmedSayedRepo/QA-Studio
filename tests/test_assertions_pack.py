"""Assertions pack: URL / title / count / sort / pattern checks and
remember-and-reuse, from the authored wording to every framework."""
import os
import engine as E
import automation_targets as AT
import automation_file_source as F

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "samples", "assertions-test-cases.csv")


def test_page_checks_parsed_from_expected_text():
    assert E._page_checks_in('The page title is "The Internet" and the URL contains "/login"') == [
        ("title", "is|The Internet", "The Internet"), ("url", "contains|/login", "/login")]
    assert E._page_checks_in("The table has at least 2 rows") == [("count", "rows|2|gte", "")]
    assert E._page_checks_in('"edit" appears 8 times') == [("count", "text|edit|8|eq", "edit")]
    assert E._page_checks_in('The "Due" column is sorted high to low') == [("sorted", "Due|desc", "Due")]
    assert E._page_checks_in('The page shows text matching "ORD-\\d+"')[0][:2] == ("pattern", "ORD-\\d+")


def test_pack_replaces_restated_assertions_and_adds_remember():
    raw = [{"action": "Open https://x.test/t", "expected": 'The page title is "Shop" and "Welcome" is shown'},
           {"action": 'Remember the text matching "ORD-(\\d+)" as {{order}}', "expected": ""}]
    clean = [{"role": "action", "verb": "navigate", "value": "https://x.test/t", "from_steps": [1]},
             {"role": "assertion", "expected": "Shop", "keywords": ["Shop"], "from_steps": [1]},
             {"role": "assertion", "expected": "Welcome", "keywords": ["Welcome"], "from_steps": [1]},
             {"role": "action", "verb": "click", "target": "order", "from_steps": [2]}]
    out = E._apply_assertion_pack(clean, raw)
    checks = [(i.get("check"), i.get("expected")) for i in out if i["role"] == "assertion"]
    assert ("title", 'page title is "Shop"') in checks and (None, "Welcome") in checks
    assert not any(c == (None, "Shop") for c in checks)
    rem = [i for i in out if i.get("verb") == "remember"]
    assert len(rem) == 1 and rem[0]["value"] == "ORD-(\\d+)" and rem[0]["remember_as"] == "order"
    assert not any(i.get("verb") == "click" for i in out)


def test_remembered_values_need_no_data_file():
    cases = F.load_stories_payload(SAMPLE)[0]["test_cases"]
    assert not E._needs_data_file(cases)
    assert E._needs_data_file([{"steps": [{"action": 'Type "{{username}}"', "expected": ""}]}])


def test_refill_keeps_original_placeholders():
    intents = [{"role": "action", "verb": "type", "value": "{{due}}", "_data_orig": {}}]
    E._intents_refill(intents, {"due": "$50.00"}, {})
    assert intents[0]["value"] == "$50.00" and intents[0]["_data_orig"]["value"] == "{{due}}"


def _case():
    return {"title": "Pack", "bucket": 3, "ctype": "interaction", "page_context": "app", "intents": [
        {"role": "action", "verb": "navigate", "value": "https://x.test/t", "target": "t", "from_steps": [1]},
        {"role": "assertion", "check": "count", "value": "rows|4|eq", "kind": "text", "from_steps": [1]},
        {"role": "action", "verb": "remember", "value": "ORD-(\\d+)", "remember_as": "order", "kind": "text",
         "target": "t", "from_steps": [2]},
        {"role": "assertion", "check": "url", "value": "contains|/secure", "kind": "text", "from_steps": [3]}]}


def test_all_frameworks_emit_page_checks_and_remember():
    java, _ = E.generate_selfhealing_test_class({"id": "1", "title": "t"}, [_case()], "com.x")
    assert 'heal.assertPage("count", "rows|4|eq")' in java and 'heal.remember("ORD-(\\\\d+)", "order");' in java
    healer = E._sh_healer("com.x")
    assert "public boolean assertPage(String check, String value)" in healer and "__PAGE_CHECK_JS__" not in healer
    pw = AT._pw_spec({"id": "1", "title": "t"}, [_case()], E._seed_locator_for_intent, {})
    pw = pw[0] if isinstance(pw, tuple) else pw
    assert 'heal.assertPage("url", "contains|/secure")' in pw and 'await heal.remember(' in pw
    cy = AT._cy_spec({"id": "1", "title": "t"}, [_case()], E._seed_locator_for_intent, {})
    cy = cy[0] if isinstance(cy, tuple) else cy
    assert 'cy.healRemember(' in cy and cy.count("cy.then(() => {") == 1
    assert cy.count("    });") >= 1
    assert "async assertPage(check, value)" in AT._PW_HEALER_JS
    assert "Cypress.Commands.add('healAssertPage'" in AT._CY_HEALER_JS
    assert "__PAGE_CHECK_FN__" not in AT._with_batch2_js(AT._PW_HEALER_JS)


def test_pending_note_in_all_languages():
    import release_notes as R
    import strings as S
    assert "upd_note_assertions_pack" in R.PENDING_RELEASE_NOTE_KEYS
    for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
        assert S._STRINGS[lang].get("upd_note_assertions_pack")


def test_restated_value_check_on_page_title_is_replaced():
    raw = [{"action": "Open https://x.test/login", "expected": 'The page title is "The Internet"'}]
    clean = [{"role": "action", "verb": "navigate", "value": "https://x.test/login", "from_steps": [1]},
             {"role": "assertion", "check": "value", "kind": "any", "target": "page title",
              "expected": "The Internet", "from_steps": [1]}]
    out = E._apply_assertion_pack(clean, raw)
    assert [i.get("check") for i in out if i["role"] == "assertion"] == ["title"]
