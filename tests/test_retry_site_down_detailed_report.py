"""One retry per test, a "Site unavailable" label on host-error pages, and the
Selenium detailed report."""
import engine as E
import automation_targets as AT


def test_selenium_retry_and_site_label():
    j = E._sh_report_mailer("com.qastudio")
    assert "IAnnotationTransformer" in j and "a.setRetryAnalyzer(Retry.class);" in j
    assert 'cfg("RETRIES", "1")' in j
    assert "if (r.wasRetried()) continue;" in j
    assert "application error" in j and 'r.setAttribute("qa.site", why);' in j


def test_selenium_detailed_report_written_and_attached():
    j = E._sh_report_mailer("com.qastudio")
    assert 'new File("target/qa-studio-detailed.html")' in j
    assert "File detail = writeDetailed();" in j
    assert '"qa-studio-detailed.html"' in j
    assert "__DETAIL_CSS__" not in j and "__DETAIL_JS__" not in j
    assert "Passed on retry" in j and "Failed here" in j


def test_story_title_on_generated_class():
    src, _ = E.generate_selfhealing_test_class({"id": "42", "title": "Checkout"}, [], "com.x")
    assert 'public static final String QA_STORY = "Story 42 — Checkout";' in src
    assert 'getField("QA_STORY")' in E._sh_report_mailer("com.x")


def test_playwright_and_cypress_retry_and_site_label():
    assert "retries: Number(process.env.RETRIES ?? 1)" in AT._PW_CONFIG
    assert "retries: { runMode: Number(process.env.RETRIES ?? 1), openMode: 0 }" in AT._CY_CONFIG
    assert "function qaSiteDown" in AT._PW_FIXTURES and "'Site unavailable'" in AT._PW_FIXTURES
    assert "function qaSiteDown" in AT._CY_SUPPORT_E2E and "Cypress.on('fail'" in AT._CY_SUPPORT_E2E
    assert "Site unavailable" in AT._REPORT_MAILER_JS


def test_playwright_and_cypress_write_the_detailed_report():
    js = AT._report_mailer_js()
    assert "function writeDetailed(run, dir, frameworkReport)" in js
    assert "module.exports = { writeDetailed, openFile," in js
    assert "__DETAIL_CSS__" not in js and "__DETAIL_JS__" not in js
    assert "writeDetailed(run, dir, 'index.html')" in AT._PW_AFTER_RUN_JS
    assert "writeDetailed(run, dir, 'index.html')" in AT._CY_CONFIG
    assert "qaSteps(" in AT._CY_CONFIG and "cy.task('qaSteps'" in AT._CY_SUPPORT_E2E
    assert "'passed -- '" in AT._CY_SUPPORT_E2E


def test_data_file_name_is_reported():
    import os
    import automation_file_source as F
    here = os.path.join(os.path.dirname(__file__), "..", "samples", "data-driven-test-cases.csv")
    assert F.load_stories_payload(here)[0]["data_source"] == "data-driven-test-cases-data.csv"
