"""QA Studio's current URLs, report recipients and the opted-in test account
reach the generated project on every generate, not only the first one."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402
import automation_targets as AT  # noqa: E402


class SettingsSync(unittest.TestCase):
    def test_selenium_config_and_dotenv_follow_qa_studio(self):
        with tempfile.TemporaryDirectory() as d:
            login = {"url": "practice.expandtesting.com/login", "report_to": "a@b.com",
                     "env_user": "practice", "env_pass": "pw1"}
            E.build_selfhealing_project(d, [], "practice.expandtesting.com", login=login,
                                        cb=lambda *a, **k: None)
            login.update(url="https://new.example.com/login", env_pass="pw2", report_to="c@d.com")
            E.build_selfhealing_project(d, [], "https://new.example.com", login=login,
                                        cb=lambda *a, **k: None)
            props = open(os.path.join(d, "config.properties"), encoding="utf-8").read()
            self.assertIn("app.base.url=https://new.example.com\n", props)
            self.assertIn("app.login.url=https://new.example.com/login\n", props)
            self.assertIn("report.email.to=c@d.com\n", props)
            env = open(os.path.join(d, ".env"), encoding="utf-8").read()
            self.assertIn("APP_PASS=pw2\n", env)
            self.assertNotIn("pw1", env)
            self.assertIn(".env", open(os.path.join(d, ".gitignore"), encoding="utf-8").read())

    def test_js_env_values_from_qa_studio_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            open(p, "w").write("APP_BASE_URL=old.example.com\nBROWSER=edge\n")
            AT._env_fill(p, overwrite=True, values={"APP_BASE_URL": "https://new.example.com", "APP_PASS": ""})
            text = open(p).read()
            self.assertIn("APP_BASE_URL=https://new.example.com", text)
            self.assertIn("BROWSER=edge", text)

    def test_driver_factory_handles_edge_cdn_and_missing_browsers(self):
        src = E._driver_factory("com.qastudio")
        self.assertIn("https://msedgedriver.microsoft.com/", src)
        self.assertIn("isn't installed on this PC", src)
        self.assertIn("public static String getenv(String k)", E._sh_config("com.qastudio", "a", "b"))


if __name__ == "__main__":
    unittest.main()
