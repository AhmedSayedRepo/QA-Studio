"""Target site URL typed without https:// (or left empty) is fixed before a run,
and a URL error stops the run instead of generating unverified scripts."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine as E  # noqa: E402

PAYLOAD = [{"test_cases": [{"steps": [{"action": "Open https://practice.expandtesting.com/login"}]}]}]


class ResolveSiteUrl(unittest.TestCase):
    def test_adds_scheme(self):
        self.assertEqual(E.resolve_site_url("practice.expandtesting.com"), "https://practice.expandtesting.com")
        self.assertEqual(E.resolve_site_url("localhost:3000/app"), "http://localhost:3000/app")

    def test_keeps_valid_and_invalid_values(self):
        self.assertEqual(E.resolve_site_url("https://example.com"), "https://example.com")
        self.assertEqual(E.resolve_site_url("http://u:p@x.com"), "http://u:p@x.com")
        self.assertEqual(E.resolve_site_url("not a url"), "not a url")

    def test_empty_uses_first_open_step(self):
        self.assertEqual(E.resolve_site_url("", PAYLOAD), "https://practice.expandtesting.com")

    def test_config_error_stops_the_run(self):
        try:
            import selenium  # noqa: F401
        except ImportError:
            self.skipTest("selenium not installed")
        out = E.explore_and_map(PAYLOAD, {"url": "ftp://x"}, "ftp://x")
        self.assertTrue(out.get("config_error"))
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        main = open(os.path.join(root, "main.py"), encoding="utf-8").read()
        self.assertIn('if inspection.get("config_error"):', main)
        self.assertIn("E.resolve_site_url(self.auto_site_url", main)


if __name__ == "__main__":
    unittest.main()
