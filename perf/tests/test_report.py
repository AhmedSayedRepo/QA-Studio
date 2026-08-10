"""Tests for perf/report.py HTML rendering."""
import unittest

from perf import report
from perf.models import LoadProfile, PerfResult, RequestStat


def _res(**kw):
    base = dict(scenario_id="s", target="jmeter", samples=474, errors=0,
                p50_ms=71, p90_ms=177, p95_ms=246, p99_ms=391,
                throughput_rps=7.9, threshold_pass=True,
                per_request=[RequestStat(label="GET /api", samples=474, errors=0,
                                         avg_ms=90, p95_ms=246, min_ms=59, max_ms=784)])
    base.update(kw)
    return PerfResult(**base)


class TestRenderHtml(unittest.TestCase):
    def test_is_complete_html_with_key_numbers(self):
        h = report.render_html(_res(), LoadProfile(users=20, ramp_up_s=15, duration_s=60))
        self.assertTrue(h.strip().startswith("<!doctype html>"))
        self.assertIn("246", h)             # p95
        self.assertIn("PASS", h)
        self.assertIn("GET /api", h)        # per-request row
        self.assertIn("20 virtual users", h)

    def test_logo_html_injected_when_provided(self):
        h = report.render_html(_res(), None, {"logo_html": "<img id='logo'>"})
        self.assertIn("<img id='logo'>", h)

    def test_fail_badge(self):
        h = report.render_html(_res(threshold_pass=False))
        self.assertIn("FAIL", h)

    def test_escapes_request_labels(self):
        h = report.render_html(_res(per_request=[
            RequestStat(label="GET /a?x=<b>&y", samples=1)]))
        self.assertIn("&lt;b&gt;", h)
        self.assertNotIn("<b>&y", h)

    def test_no_per_request_is_graceful(self):
        h = report.render_html(_res(per_request=[]))
        self.assertIn("No per-request breakdown", h)

    def test_failures_section_rendered(self):
        from perf.models import FailureGroup
        h = report.render_html(_res(errors=5, threshold_pass=False, failures=[
            FailureGroup(label="POST /login", code="403", message="Forbidden", count=5)]))
        self.assertIn("Why requests failed", h)
        self.assertIn("403", h)
        self.assertIn("Forbidden", h)

    def test_no_failures_section_when_clean(self):
        h = report.render_html(_res(failures=[]))
        self.assertNotIn("Why requests failed", h)

    def test_url_query_secrets_are_redacted(self):
        from perf.models import RequestStat
        h = report.render_html(_res(per_request=[
            RequestStat(label="GET /api/x?access_token=SECRET123&id=5", samples=1)]))
        self.assertNotIn("SECRET123", h)
        self.assertIn("[REDACTED]", h)
        self.assertIn("id=5", h)          # non-secret params kept


if __name__ == "__main__":
    unittest.main()
