"""Test videos in every framework's detailed report + cross-browser runs."""
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402
import automation_targets as AT  # noqa: E402

PKG = "com.qastudio"
CFG = {"base_url": "https://example.com", "login_url": "https://example.com/login"}


class SeleniumVideoAndBrowsers(unittest.TestCase):
    def test_driver_factory_supports_three_browsers_and_records(self):
        src = engine._driver_factory(PKG)
        for cls in ("ChromeDriver", "EdgeDriver", "FirefoxDriver"):
            self.assertIn("new %s(options)" % cls, src)
        for wdm in ("chromedriver()", "edgedriver()", "firefoxdriver()"):
            self.assertIn("WebDriverManager.%s" % wdm, src)
        self.assertIn('Config.setting("BROWSER", "chrome")', src)
        self.assertIn("WebDriver wrapped = VideoRecorder.wrap(driver);", src)
        self.assertIn('raw.split("[,;\\\\s]+")', src)

    def test_video_recorder_writes_gif_with_jdk_only(self):
        src = engine._video_recorder(PKG)
        self.assertIn('ImageIO.getImageWritersByFormatName("gif")', src)
        self.assertIn("EventFiringDecorator<WebDriver>(rec).decorate(driver)", src)
        # A screenshot with a JS dialog open would dismiss it: frames skip it.
        self.assertIn("real.switchTo().alert()", src)
        self.assertNotIn("jcodec", src.lower())

    def test_report_mailer_runs_each_browser_and_shows_recording(self):
        src = engine._sh_report_mailer(PKG)
        self.assertIn("IAlterSuiteListener", src)
        self.assertIn('params.put("qa.browser", b);', src)
        self.assertIn("VideoRecorder.begin();", src)
        self.assertIn("VideoRecorder.awaitAll();", src)
        self.assertIn("<div class='lbl'>Recording</div>", src)
        self.assertNotIn('{"Browser", "Chrome"}', src)

    def test_base_test_reads_browser_of_the_xml_test(self):
        src = engine._sh_base_test(PKG)
        self.assertIn('getParameter("qa.browser")', src)
        self.assertIn("public void setUp(ITestContext ctx)", src)

    def test_run_configs_per_browser_and_props(self):
        xml = engine._idea_maven_config("Run tests (Edge)", ["test"], "-Dqa.browser=edge")
        self.assertIn('<option name="cmdOptions" value="-Dqa.browser=edge" />', xml)
        self.assertIn('<option name="cmdOptions" />', engine._idea_maven_config("Run tests", ["test"]))
        props = engine._sh_config_properties("a", "b", "c", "d", "e")
        self.assertIn("browser=chrome", props)
        self.assertIn("video=retain-on-failure", props)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.properties")
            open(p, "w").write("app.base.url=x\n")
            engine._append_run_props(p)
            engine._append_run_props(p)
            self.assertEqual(open(p).read().count("browser=chrome"), 1)


    def test_project_keeps_video_recorder_and_writes_run_configs_once(self):
        with tempfile.TemporaryDirectory() as d:
            engine.build_selfhealing_project(d, [], "https://example.com",
                                             login={"url": "https://example.com/login"},
                                             cb=lambda *a, **k: None)
            core = os.path.join(d, "src", "main", "java", "com", "qastudio", "core")
            self.assertTrue(os.path.isfile(os.path.join(core, "VideoRecorder.java")))
            rc = os.path.join(d, ".run", "Run tests.run.xml")
            with open(rc, "a", encoding="utf-8") as f:
                f.write("<!-- user env -->")
            engine.build_selfhealing_project(d, [], "https://example.com",
                                             login={"url": "https://example.com/login"},
                                             cb=lambda *a, **k: None)
            self.assertIn("user env", open(rc, encoding="utf-8").read())
            self.assertTrue(os.path.isfile(os.path.join(d, ".run", "Run tests (all browsers).run.xml")))


class BrowserPickerFromQaStudio(unittest.TestCase):
    def test_selenium_config_gets_the_picked_browsers(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.properties")
            open(p, "w").write("app.base.url=x\nbrowser=chrome\nvideo=on\n")
            self.assertEqual(engine.apply_run_browsers(d, "selenium", ["firefox", "chrome"]), "chrome,firefox")
            text = open(p).read()
            self.assertIn("browser=chrome,firefox\n", text)
            self.assertEqual(text.count("browser="), 1)

    def test_js_env_gets_browser_and_none_picked_means_chrome(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            open(p, "w").write("APP_USER=a\n")
            engine.apply_run_browsers(d, "cypress", ["edge"])
            self.assertIn("BROWSER=edge", open(p).read())
            engine.apply_run_browsers(d, "playwright", None)
            self.assertIn("BROWSER=chrome", open(p).read())
            self.assertEqual(engine.apply_run_browsers(os.path.join(d, "missing"), "selenium", ["edge"]), "")

    def test_ui_and_worker_are_wired(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ui = open(os.path.join(root, "automation.py"), encoding="utf-8").read()
        self.assertIn('_browser_box("firefox", "Firefox")', ui)
        self.assertIn('app.creds["auto_browsers"]', ui)
        main = open(os.path.join(root, "main.py"), encoding="utf-8").read()
        self.assertIn("E.apply_run_browsers(project_dir", main)


class JsVideoAndBrowsers(unittest.TestCase):
    def test_detailed_report_plays_videos(self):
        js = AT._report_mailer_js()
        self.assertIn("<video class='vid' controls", js)
        self.assertIn("detailedHtml(run, frameworkReport, dir)", js)

    def test_playwright_projects_from_browser(self):
        self.assertIn("projects: browsers.projects(),", AT._PW_CONFIG)
        self.assertIn("channel: 'msedge'", AT._PW_BROWSERS_JS)
        self.assertIn("a.name === 'video'", AT._PW_AFTER_RUN_JS)
        pkg = json.loads(AT._PKG_JSON)
        self.assertEqual(pkg["scripts"]["pretest"], "node support/browsers.js --install")
        self.assertEqual(pkg["scripts"]["test:firefox"], "cross-env BROWSER=firefox npm test")

    def test_cypress_runs_browsers_and_links_spec_video(self):
        pkg = json.loads(AT._CY_PKG_JSON)
        self.assertEqual(pkg["scripts"]["test:all"], "node cypress/run-browsers.js --browser all")
        self.assertIn("process.env.QA_BATCH_DIR", AT._CY_CONFIG)
        self.assertIn("video: r.video || ''", AT._CY_CONFIG)
        self.assertIn("on('after:spec'", AT._CY_CONFIG)

    def test_generated_projects_have_browser_run_configs_and_env_keys(self):
        with tempfile.TemporaryDirectory() as out:
            AT.build_cypress_project(out, [], CFG, lambda *a, **k: None, "return [];")
            self.assertTrue(os.path.isfile(os.path.join(out, ".run", "Run tests (Firefox).run.xml")))
            self.assertTrue(os.path.isfile(os.path.join(out, "cypress", "run-browsers.js")))
            env = open(os.path.join(out, ".env")).read()
            self.assertTrue(re.search(r"(?m)^BROWSER=", env))
            self.assertTrue(re.search(r"(?m)^VIDEO=", env))


if __name__ == "__main__":
    unittest.main()
