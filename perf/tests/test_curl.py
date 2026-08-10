"""Tests for perf/curl.py cURL parsing."""
import unittest

from perf.curl import scenarios_from_curl
from perf.models import AssertionKind  # noqa: F401  (import kept for parity)


class TestCurl(unittest.TestCase):
    def test_simple_get(self):
        scs = scenarios_from_curl("curl 'https://api.example.com/search?q=x'")
        r = scs[0].requests[0]
        self.assertEqual(r.method, "GET")
        self.assertEqual(r.url, "https://api.example.com/search?q=x")

    def test_headers_and_post_body(self):
        cmd = ("curl 'https://api.example.com/login' "
               "-H 'Authorization: Bearer tok' "
               "-H 'Content-Type: application/json' "
               "--data-raw '{\"u\":\"a\"}'")
        r = scenarios_from_curl(cmd)[0].requests[0]
        self.assertEqual(r.method, "POST")            # inferred from body
        self.assertEqual(r.headers["Authorization"], "Bearer tok")
        self.assertEqual(r.headers["Content-Type"], "application/json")
        self.assertEqual(r.body, '{"u":"a"}')

    def test_explicit_method_and_volatile_headers_dropped(self):
        cmd = ("curl -X PUT 'https://api.example.com/x' "
               "-H 'Host: api.example.com' -H 'Content-Length: 3' "
               "-H 'X-Keep: yes'")
        r = scenarios_from_curl(cmd)[0].requests[0]
        self.assertEqual(r.method, "PUT")
        self.assertNotIn("Host", r.headers)
        self.assertNotIn("Content-Length", r.headers)
        self.assertIn("X-Keep", r.headers)

    def test_basic_auth_becomes_authorization(self):
        r = scenarios_from_curl("curl -u user:pass 'https://api.example.com/x'")[0].requests[0]
        self.assertTrue(r.headers["Authorization"].startswith("Basic "))

    def test_multiple_commands_become_multiple_requests(self):
        blob = ("curl 'https://api.example.com/a'\n"
                "curl 'https://api.example.com/b' -X POST --data 'y'")
        reqs = scenarios_from_curl(blob)[0].requests
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[1].method, "POST")

    def test_line_continuations_joined(self):
        blob = "curl 'https://api.example.com/x' \\\n  -H 'Accept: */*'"
        r = scenarios_from_curl(blob)[0].requests[0]
        self.assertEqual(r.headers["Accept"], "*/*")

    def test_skip_value_flag_not_taken_as_url(self):
        r = scenarios_from_curl(
            "curl --max-time 10 'https://api.example.com/x'")[0].requests[0]
        self.assertEqual(r.url, "https://api.example.com/x")

    def test_empty_returns_no_scenarios(self):
        self.assertEqual(scenarios_from_curl("not a command"), [])


if __name__ == "__main__":
    unittest.main()
