"""Report email: an English title that quotes an Arabic name stays LTR (it used
to flip to RTL and scramble its word order), and on Jira + Zephyr the button
opens Zephyr's project page with a Zephyr label - not "Azure DevOps"."""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import backend_setup as B  # noqa: E402
import engine as E  # noqa: E402

MIXED = 'CX Channel Optimization Project (G2C) [Cert #14]: "Debt Certificate | شهادة مديونية". Adding Total'
ARABIC = "التحقق من إصدار شهادة المديونية"


def _app():
    return types.SimpleNamespace(project="SS", creds={
        "backend": "jira_zephyr", "jira_site": "https://acme.atlassian.net"})


class EmailRtlAndCta(unittest.TestCase):
    def test_starts_rtl(self):
        self.assertFalse(E._starts_rtl(MIXED))
        self.assertFalse(E._starts_rtl("Story SS-1 · " + MIXED))
        self.assertTrue(E._starts_rtl(ARABIC))

    def test_zephyr_link_and_label(self):
        app = _app()
        url = B.plan_url(app, "63525850")
        self.assertTrue(url.startswith("https://acme.atlassian.net/projects/SS?selectedItem="), url)
        self.assertIn("test-manager__main-project-page", url)
        self.assertEqual(B.plan_link_label(app), "Open in Zephyr")
        self.assertEqual(B.plan_link_label(types.SimpleNamespace(creds={"backend": "azure"})),
                         "Open plan in Azure DevOps")

    def test_email_html(self):
        app = _app()
        html = E.build_report_email(
            "Test Case Titles", "done", {"Created": 1},
            per_story=[{"id": "SS-30552", "title": MIXED, "total": 1, "ok": 1},
                       {"id": "SS-2", "title": ARABIC, "total": 1, "ok": 1}],
            plan_url=B.plan_url(app, "1"), plan_label=B.plan_link_label(app),
            log_lines=[{"tone": "story", "msg": "Story SS-30552 · " + MIXED}],
            org=None, project="SS", url_map={"SS-30552": "https://acme.atlassian.net/browse/SS-30552"})
        self.assertIn("Open in Zephyr &rarr;", html)
        self.assertNotIn("Azure DevOps", html)
        i = html.index("Debt Certificate")
        row = html[html.rindex("<div", 0, i):i]
        self.assertIn("dir='ltr'", row)
        self.assertNotIn("direction:rtl", row)
        j = html.index(ARABIC)
        self.assertIn("dir='rtl'", html[html.rindex("<div", 0, j):j])
        # the email's log line for the story is LTR too
        k = html.index("Story SS-30552")
        self.assertIn("dir='ltr'", html[html.rindex("<td", 0, k):k])
        self.assertEqual(html.count("def plan_link_label"), 0)

    def test_single_plan_link_label(self):
        src = open(os.path.join(ROOT, "backend_setup.py"), encoding="utf-8").read()
        self.assertEqual(src.count("def plan_link_label("), 1)


if __name__ == "__main__":
    unittest.main()
