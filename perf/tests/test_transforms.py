"""Tests for service.rebase / service.with_auth scenario transforms."""
import unittest

from perf import service
from perf.models import PerfRequest, PerfScenario


def _sc(*urls, headers=None):
    return [PerfScenario(id="s", title="s", requests=[
        PerfRequest(method="GET", url=u, headers=dict(headers or {})) for u in urls])]


class TestRebase(unittest.TestCase):
    def test_relative_and_host_placeholder_prefixed(self):
        scs = service.rebase(_sc("/api/login", "https://{{host}}/search", "products"),
                             "https://app.example.com/")
        urls = [r.url for r in scs[0].requests]
        self.assertEqual(urls[0], "https://app.example.com/api/login")
        self.assertEqual(urls[1], "https://app.example.com/search")
        self.assertEqual(urls[2], "https://app.example.com/products")

    def test_absolute_urls_untouched(self):
        scs = service.rebase(_sc("https://real.com/x"), "https://app.example.com")
        self.assertEqual(scs[0].requests[0].url, "https://real.com/x")

    def test_empty_base_is_noop(self):
        orig = _sc("/a")
        self.assertIs(service.rebase(orig, ""), orig)


class TestWithAuth(unittest.TestCase):
    def test_injects_header_on_every_request(self):
        scs = service.with_auth(_sc("/a", "/b"), "Bearer tok")
        for r in scs[0].requests:
            self.assertEqual(r.headers["Authorization"], "Bearer tok")

    def test_overwrites_existing(self):
        scs = service.with_auth(_sc("/a", headers={"Authorization": "old"}), "Bearer new")
        self.assertEqual(scs[0].requests[0].headers["Authorization"], "Bearer new")

    def test_empty_is_noop(self):
        orig = _sc("/a")
        self.assertIs(service.with_auth(orig, ""), orig)


if __name__ == "__main__":
    unittest.main()
