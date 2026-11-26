"""Jira + Zephyr Scale is listed in the backend picker again (only standalone
TestRail stays out), and the Zephyr adapter no longer claims a delete its API
doesn't have, and links test cases to the Zephyr app page."""
import unittest

import backend_setup as B
from tracker.base import Capability
from tracker.errors import BackendUnsupported, NotFound, TrackerError
from tracker.jira_zephyr import JiraZephyrBackend
from tracker.models import Project, Ref
from tracker.xray import XrayBackend

PAGE = ("https://acme.atlassian.net/projects/QA?selectedItem=com.atlassian.plugins."
        "atlassian-connect-plugin:com.kanoah.test-manager__main-project-page")


class ZephyrPickerVisibilityTests(unittest.TestCase):
    def test_listed_whatever_is_selected(self):
        for current in (B.AZURE, B.JIRA_ZEPHYR, B.XRAY, ""):
            self.assertNotIn(B.JIRA_ZEPHYR, B._picker_hidden(current))

    def test_standalone_testrail_stays_hidden(self):
        self.assertIn(B.TESTRAIL, B._picker_hidden(B.AZURE))


class ZephyrAdapterTests(unittest.TestCase):
    def setUp(self):
        self.be = JiraZephyrBackend(site="https://acme.atlassian.net", jira=object(), zephyr=object())

    def test_no_delete_capability(self):
        self.assertNotIn(Capability.DELETE_TEST_CASE, self.be.capabilities)
        with self.assertRaises(BackendUnsupported):
            self.be.delete_test_case(None, None, None, Ref(id="1", key="QA-T1"))

    def test_case_links_open_the_zephyr_page(self):
        self.assertEqual(self.be._case_url("123", "QA-T7"), PAGE + "#!/v2/testCase/123")
        self.assertEqual(self.be._case_url("", "QA-T7"), PAGE + "#!/testCase/QA-T7")
        self.assertEqual(self.be.case_url(Ref(id="QA-T7", key="QA-T7")), PAGE + "#!/testCase/QA-T7")
        proj = Project(ref=Ref(id="1", key="QA"), name="QA")
        self.assertEqual(self.be.case_url(Ref(id="555", key="555"), proj), PAGE + "#!/v2/testCase/555")
        self.assertEqual(self.be.item_url(Ref(id="QA-T7", key="QA-T7")), PAGE + "#!/testCase/QA-T7")
        # Jira issues (stories) still open at /browse/.
        self.assertEqual(self.be.item_url(Ref(id="10001", key="QA-12")), "https://acme.atlassian.net/browse/QA-12")

    def test_project_not_enabled_in_zephyr_reads_plainly(self):
        class Z:
            def paginate(self, *a, **k):
                raise TrackerError('Request failed (400). {"errorCode":400,"message":"Project \'DA\' Not Found"}',
                                   backend="zephyr", status=400)
                yield
            def post(self, *a, **k):
                return next(self.paginate())
        be = JiraZephyrBackend(site="https://acme.atlassian.net", jira=object(), zephyr=Z())
        for call in (lambda: be.fetch_test_plans("DA"), lambda: be.create_test_plan("DA", "Sprint 1")):
            with self.assertRaises(NotFound) as cm:
                call()
            msg = str(cm.exception)
            self.assertIn("Zephyr isn't enabled for the Jira project DA", msg)
            self.assertIn("Project settings", msg)
            self.assertNotIn("errorCode", msg)

    def test_other_zephyr_errors_pass_through(self):
        class Z:
            def paginate(self, *a, **k):
                raise TrackerError("Request failed (500). boom", backend="zephyr", status=500)
                yield
        be = JiraZephyrBackend(site="https://acme.atlassian.net", jira=object(), zephyr=Z())
        with self.assertRaises(TrackerError) as cm:
            be.fetch_test_plans("DA")
        self.assertNotIsInstance(cm.exception, NotFound)
        self.assertIn("boom", str(cm.exception))

    def test_xray_cases_are_jira_issues(self):
        x = XrayBackend.__new__(XrayBackend)
        x.site = "https://acme.atlassian.net"
        self.assertEqual(x.case_url(Ref(id="QA-45", key="QA-45")), "https://acme.atlassian.net/browse/QA-45")
        self.assertIn(Capability.DELETE_TEST_CASE, XrayBackend.capabilities)


if __name__ == "__main__":
    unittest.main()
