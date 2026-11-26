"""Jira + Zephyr: a test case whose story link fails says so in the run log
(with Zephyr's reason) instead of failing silently; and the run-report email
links items through the connected backend - never a dev.azure.com fallback
off Azure."""
import os
import types
import unittest
from unittest import mock

import backend_setup as B
import engine as E
from tracker.errors import TrackerError
from tracker.jira_zephyr import JiraZephyrBackend
from tracker.models import Project, Ref, Story, Suite

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Zephyr:
    def __init__(self, link_fails=True):
        self.link_fails, self.posts = link_fails, []

    def post(self, path, json=None, **kw):
        self.posts.append(path)
        if path == "testcases":
            return {"id": 123, "key": "SS-T7"}
        if self.link_fails:
            raise TrackerError("Request failed (404). Issue not found", backend="zephyr", status=404)
        return {}


def make_backend(link_fails=True):
    return JiraZephyrBackend(site="https://acme.atlassian.net", jira=object(), zephyr=Zephyr(link_fails))


STORY = Story(ref=Ref(id="10045", key="SS-45"), title="Login")
PROJ = Project(ref=Ref(id="1", key="SS"), name="SS")
SUITE = Suite(ref=Ref(id="77"), name="SS-45 Login")


class LinkFailure(unittest.TestCase):
    def test_failed_link_is_remembered_not_raised(self):
        be = make_backend()
        case = be.create_test_case(PROJ, None, SUITE, "Valid login", STORY)
        self.assertEqual(case.ref.key, "SS-T7")
        self.assertIn("testcases/SS-T7/links/issues", be._zephyr.posts)
        why = be.pop_link_error("SS-T7")
        self.assertIn("SS-45", why)
        self.assertIn("Issue not found", why)
        self.assertEqual(be.pop_link_error("SS-T7"), "")       # popped once

    def test_successful_link_has_no_error(self):
        be = make_backend(link_fails=False)
        be.create_test_case(PROJ, None, SUITE, "Valid login", STORY)
        self.assertEqual(be.pop_link_error("SS-T7"), "")

    def _ops(self, be, found):
        be.fetch_stories = lambda refs: found
        app = types.SimpleNamespace(creds={"backend": "jira_zephyr"}, project="SS")
        with mock.patch.object(B, "is_azure", return_value=False), \
                mock.patch.object(B, "get_backend", return_value=be), \
                mock.patch.object(B, "_as_project", side_effect=lambda b, p: PROJ), \
                mock.patch.object(E, "case_labels", return_value=()):
            ops = B.generation_ops(app)
            cid = ops.create_case("SS", "F1", "77", "Valid login", "SS-45")
        return ops, cid

    def test_run_log_gets_the_warning(self):
        ops, cid = self._ops(make_backend(), [STORY])
        self.assertEqual(cid, "SS-T7")
        logs = []
        ops.log_warnings(lambda ev, d: logs.append(d))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["tone"], "warn")
        self.assertIn("Created SS-T7 but couldn't link it to story SS-45", logs[0]["msg"])
        ops.log_warnings(lambda ev, d: logs.append(d))
        self.assertEqual(len(logs), 1)                       # warnings are popped

    def test_story_not_readable_is_reported(self):
        ops, _ = self._ops(make_backend(link_fails=False), [])
        logs = []
        ops.log_warnings(lambda ev, d: logs.append(d))
        self.assertIn("couldn't be read from Jira", logs[0]["msg"])

    def test_azure_ops_have_no_warnings(self):
        ops = E._default_ops()
        self.assertIsNone(ops.pop_warnings)
        ops.log_warnings(lambda *a: self.fail("no warnings expected"))


class EmailLinks(unittest.TestCase):
    def build(self, url_map, org):
        return E.build_report_email(
            "Test cases", "done", {"Created": 1}, [], [], per_story=[],
            log_lines=[{"msg": "Valid login", "id": "SS-T7", "tone": "ok"},
                       {"msg": "Other", "id": "SS-T8", "tone": "ok"}],
            org=org, project="SS", url_map=url_map)

    def test_backend_links_and_no_azure_fallback(self):
        z = "https://acme.atlassian.net/projects/SS?selectedItem=x#!/testCase/SS-T7"
        html = self.build({"SS-T7": z}, None)
        self.assertIn(z.replace("&", "&amp;"), html)
        self.assertNotIn("dev.azure.com", html)              # SS-T8 unmapped: no link at all

    def test_main_maps_log_ids_and_drops_org_off_azure(self):
        src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
        self.assertIn('+ [l for l in email_log if l.get("id")]', src)
        self.assertIn('org=(E.AZURE_ORG if backend_setup.is_azure(self.creds) else None)', src)


if __name__ == "__main__":
    unittest.main()
