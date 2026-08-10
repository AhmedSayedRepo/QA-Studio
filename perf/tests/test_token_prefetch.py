"""Tests for perf/token_prefetch.py (concurrency/aggregation via injected login_fn)."""
import json
import os
import tempfile
import unittest

from perf.token_prefetch import (LoginConfig, make_http_login, prefetch_tokens, _dig,
                                 detect_login_config_from_har)


def _write_login_har(req_body, req_mime, resp_body, url="https://app/api/login"):
    har = {"log": {"entries": [{
        "request": {"method": "POST", "url": url,
                    "postData": {"mimeType": req_mime, "text": req_body}},
        "response": {"status": 200, "content": {"mimeType": "application/json",
                                                "text": resp_body}},
    }]}}
    fd, p = tempfile.mkstemp(suffix=".har")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(har, f)
    return p


class TestPrefetch(unittest.TestCase):
    def test_happy_path_adds_token_column(self):
        rows = [{"email": "a@x.com", "password": "1"},
                {"email": "b@x.com", "password": "2"}]
        login = lambda u, p: (f"tok-{u}", "")
        out, ok, fails = prefetch_tokens(rows, login, "email", "password")
        self.assertEqual(ok, 2)
        self.assertEqual(fails, [])
        self.assertEqual(out[0]["token"], "tok-a@x.com")
        self.assertIn("email", out[0])            # original columns preserved

    def test_failures_reported_and_token_blank(self):
        rows = [{"email": "good@x.com", "password": "1"},
                {"email": "bad@x.com", "password": "2"}]
        login = lambda u, p: (("tok", "") if u.startswith("good") else ("", "HTTP 401"))
        out, ok, fails = prefetch_tokens(rows, login, "email", "password", retries=0)
        self.assertEqual(ok, 1)
        self.assertEqual(out[1]["token"], "")
        self.assertEqual(fails, [("bad@x.com", "HTTP 401")])

    def test_retry_then_succeed(self):
        state = {"n": 0}

        def flaky(u, p):
            state["n"] += 1
            return ("tok", "") if state["n"] >= 2 else ("", "temporary")
        out, ok, _ = prefetch_tokens([{"e": "x", "p": "y"}], flaky, "e", "p", retries=3)
        self.assertEqual(ok, 1)

    def test_custom_token_column(self):
        out, _, _ = prefetch_tokens([{"e": "x", "p": "y"}], lambda u, p: ("t", ""),
                                    "e", "p", token_col="bearer")
        self.assertEqual(out[0]["bearer"], "t")

    def test_dig_dotted_path(self):
        self.assertEqual(_dig({"data": {"access_token": "abc"}}, "data.access_token"), "abc")
        self.assertEqual(_dig({"x": 1}, "y.z"), "")

    def test_make_http_login_returns_callable(self):
        self.assertTrue(callable(make_http_login(LoginConfig(url="https://x/login"))))


class TestDetect(unittest.TestCase):
    def test_detect_json_login_and_nested_token(self):
        p = _write_login_har(
            '{"username":"a@x.com","password":"secret"}', "application/json",
            '{"data":{"accessToken":"eyJabcdefghijklmnop"}}')
        try:
            cfg = detect_login_config_from_har(p)
        finally:
            os.remove(p)
        self.assertTrue(cfg["ok"])
        self.assertEqual(cfg["url"], "https://app/api/login")
        self.assertEqual(cfg["body_format"], "json")
        self.assertEqual(cfg["user_field"], "username")
        self.assertEqual(cfg["pass_field"], "password")
        self.assertEqual(cfg["token_json_path"], "data.accessToken")

    def test_detect_form_login(self):
        p = _write_login_har("email=a%40x.com&pwd=s", "application/x-www-form-urlencoded",
                             '{"token":"eyJ0123456789abcdef"}')
        try:
            cfg = detect_login_config_from_har(p)
        finally:
            os.remove(p)
        self.assertEqual(cfg["body_format"], "form")
        self.assertEqual(cfg["user_field"], "email")
        self.assertEqual(cfg["pass_field"], "pwd")
        self.assertEqual(cfg["token_json_path"], "token")

    def test_detect_no_login_entry(self):
        import json as _j
        fd, p = tempfile.mkstemp(suffix=".har")
        with os.fdopen(fd, "w") as f:
            _j.dump({"log": {"entries": [{"request": {"method": "GET",
                     "url": "https://app/x"}, "response": {}}]}}, f)
        try:
            self.assertFalse(detect_login_config_from_har(p)["ok"])
        finally:
            os.remove(p)


if __name__ == "__main__":
    unittest.main()
