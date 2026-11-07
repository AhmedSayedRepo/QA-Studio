"""A native <select> value check compares the selected option TEXT, and the
log rail's coalescing flag can't get stuck after a failed render."""
import inspect
import engine
import automation_targets as at
import pathlib
_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_replay_value_check_reads_selected_option_text():
    src = inspect.getsource(engine)
    assert "n.selectedOptions" in src
    assert 'el.get_attribute("value") or el.text' not in src


def test_java_healer_assert_value_handles_select():
    java = engine._sh_healer("com.x")
    i = java.index("public boolean assertValue")
    assert "selectedOptions" in java[i:i + 900]


def test_playwright_and_cypress_value_checks_handle_select():
    pw, cy = at._PW_HEALER_JS, at._CY_HEALER_JS
    i = pw.index("async assertValue(")
    assert "selectedOptions" in pw[i:i + 900]
    j = cy.index("Cypress.Commands.add('healAssertValue'")
    assert "option:selected" in cy[j:j + 700]


def test_log_update_flag_cleared_in_finally():
    src = (_ROOT / "main.py").read_text(encoding="utf-8")
    i = src.index("def upd():", src.index("_auto_log_upd_pending = True"))
    body = src[i:src.index("self.ui_safe(upd)", i)]
    fin = body.rindex("finally:")
    assert "self._auto_log_upd_pending = False" in body[fin:]


def test_fresh_log_column_resets_flag():
    assert "app._auto_log_upd_pending = False" in (_ROOT / "automation.py").read_text(encoding="utf-8")
