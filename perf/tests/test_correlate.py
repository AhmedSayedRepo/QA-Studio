"""Tests for service.parameterize and service.correlate."""
import unittest

from perf import service
from perf.models import PerfRequest, PerfScenario


def _sc(*reqs):
    return [PerfScenario(id="s", title="s", requests=list(reqs))]


class TestParameterize(unittest.TestCase):
    def test_replaces_in_url_body_headers(self):
        r = PerfRequest(method="POST", url="https://x/order?p=SKU123",
                        body='{"sku":"SKU123"}', headers={"X-Sku": "SKU123"})
        out = service.parameterize(_sc(r), [("SKU123", "{{product}}")])
        rq = out[0].requests[0]
        self.assertIn("{{product}}", rq.url)
        self.assertIn("{{product}}", rq.body)
        self.assertEqual(rq.headers["X-Sku"], "{{product}}")

    def test_empty_rules_noop(self):
        orig = _sc(PerfRequest(url="https://x/a"))
        self.assertIs(service.parameterize(orig, []), orig)


class TestCorrelate(unittest.TestCase):
    def test_json_extraction_on_matching_request(self):
        reqs = _sc(PerfRequest(url="https://x/cart", method="POST"),
                   PerfRequest(url="https://x/order", method="POST"))
        out = service.correlate(reqs, [{"var": "cartId", "json_path": "$.id",
                                        "match": "/cart"}])
        exs = out[0].requests[0].extractions
        self.assertEqual(len(exs), 1)
        self.assertEqual(exs[0].var, "cartId")
        self.assertEqual(exs[0].json_path, "$.id")
        self.assertEqual(out[0].requests[1].extractions, [])   # not the order request

    def test_regex_and_default_first_request(self):
        out = service.correlate(_sc(PerfRequest(url="https://x/a")),
                                [{"var": "csrf", "regex": "tok=([a-z]+)"}])
        self.assertEqual(out[0].requests[0].extractions[0].regex, "tok=([a-z]+)")

    def test_invalid_rule_skipped(self):
        orig = _sc(PerfRequest(url="https://x/a"))
        # missing var / no json_path or regex -> no-op
        self.assertIs(service.correlate(orig, [{"json_path": "$.x"}]), orig)


class TestWithLogin(unittest.TestCase):
    def test_prepends_login_and_sets_auth(self):
        from perf.models import PerfRequest
        login = PerfRequest(method="POST", url="https://x/login", body='{"u":"a"}')
        scs = service.with_login(_sc(PerfRequest(url="https://x/order")), login,
                                 token_var="token", token_json_path="$.access_token")
        reqs = scs[0].requests
        self.assertEqual(len(reqs), 2)                       # login prepended
        self.assertEqual(reqs[0].url, "https://x/login")
        self.assertEqual(reqs[0].extractions[0].json_path, "$.access_token")
        self.assertEqual(reqs[1].headers["Authorization"], "Bearer {{token}}")

    def test_none_login_is_noop(self):
        orig = _sc(PerfRequest(url="https://x/a"))
        self.assertIs(service.with_login(orig, None), orig)


if __name__ == "__main__":
    unittest.main()
