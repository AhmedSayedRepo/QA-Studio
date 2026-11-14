"""PARALLEL switch (one .env line) for Selenium, Playwright and Cypress, and the
progress window stopping its clock at 100% and closing without holding the run."""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402
import automation_targets as A  # noqa: E402

NODE = shutil.which("node")


def _js_files(build):
    d = tempfile.mkdtemp()
    cfg = {"base_url": "https://x", "login_url": "https://x/login", "env_user": "u", "env_pass": "p"}
    try:
        build(d, [], cfg, lambda *a, **k: None, "return [];")
        files = {os.path.relpath(f, d).replace(os.sep, "/"): open(f, encoding="utf-8").read()
                 for f in glob.glob(os.path.join(d, "**", "*"), recursive=True)
                 if os.path.isfile(f) and not f.endswith((".png", ".jpg"))}
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return files


class EnvDefaults(unittest.TestCase):
    def test_lines_added_once_and_user_values_kept(self):
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(E.ensure_env_defaults(d), ["PARALLEL", "PROGRESS"])   # file created
            text = open(os.path.join(d, ".env"), encoding="utf-8").read()
            self.assertIn("PARALLEL=true\n", text)
            self.assertIn("PROGRESS=on\n", text)
            self.assertEqual(E.ensure_env_defaults(d), [])                         # idempotent
            with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
                f.write("APP_USER=a\nPARALLEL=false\n")
            self.assertEqual(E.ensure_env_defaults(d), ["PROGRESS"])
            text = open(os.path.join(d, ".env"), encoding="utf-8").read()
            self.assertIn("PARALLEL=false", text)
            self.assertNotIn("PARALLEL=true", text)
            self.assertTrue(text.startswith("APP_USER=a\n"))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_apply_run_browsers_adds_them_for_selenium(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "config.properties"), "w", encoding="utf-8") as f:
                f.write("browser=chrome\n")
            E.apply_run_browsers(d, "selenium", ["chrome", "firefox"])
            self.assertIn("PARALLEL=true", open(os.path.join(d, ".env"), encoding="utf-8").read())
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_new_playwright_and_cypress_env_examples_have_the_switch(self):
        self.assertIn("\nPARALLEL=true\n", A._PW_ENV_EXAMPLE)
        self.assertIn("\nPROGRESS=on\n", A._PW_ENV_EXAMPLE)


class SeleniumParallel(unittest.TestCase):
    def test_suite_runs_browser_tests_in_parallel(self):
        rm = E._sh_report_mailer("com.qa")
        self.assertIn("suite.setParallel(XmlSuite.ParallelMode.TESTS);", rm)
        self.assertIn("suite.setThreadCount(Math.max(1, suite.getTests().size()));", rm)
        self.assertIn('Config.setting("PARALLEL", "true")', rm)
        # the clock stops when the last test ends, before the reports are written
        on_finish = rm[rm.index("public void onFinish(ISuite suite)"):]
        self.assertTrue(on_finish.split("\n")[1].strip().startswith("ProgressWindow.wrapUp();"))

    def test_shared_state_is_safe_for_parallel_browsers(self):
        df = E._driver_factory("com.qa")
        self.assertIn("File downloads = new File(DOWNLOADS, b);", df)          # own folder per browser
        self.assertNotIn("DOWNLOADS.listFiles()", df)                          # never clears the others
        self.assertIn("synchronized (DriverFactory.class) { WebDriverManager.chromedriver().setup(); }", df)
        self.assertIn("DriverFactory.downloadsOf(driver).listFiles()", E._sh_healer("com.qa"))
        store = E._sh_locator_store("com.qa")
        self.assertIn("synchronized (LocatorStore.class)", store)
        self.assertIn("disk.put(key, e);", store)
        self.assertIn("return r != null || manyThreads ? r : last;", E._video_recorder("com.qa"))

    def test_progress_window_finish_does_not_block(self):
        pw = E._progress_window("com.qa")
        self.assertNotIn("Thread.sleep(3000)", pw)
        self.assertIn("public static void wrapUp()", pw)
        self.assertIn("close.setRepeats(false);", pw)

    @unittest.skipUnless(shutil.which("javac") and shutil.which("Xvfb"), "javac/Xvfb missing")
    def test_swing_window_freezes_clock_and_closes_by_itself(self):
        d = tempfile.mkdtemp()
        xvfb = None
        try:
            src = os.path.join(d, "src", "p", "core")
            os.makedirs(src)
            with open(os.path.join(src, "ProgressWindow.java"), "w", encoding="utf-8") as f:
                f.write(E._progress_window("p"))
            with open(os.path.join(src, "Config.java"), "w", encoding="utf-8") as f:
                f.write("package p.core;\npublic final class Config { public static String setting(String k, String d) { return d; } }\n")
            with open(os.path.join(src, "Main.java"), "w", encoding="utf-8") as f:
                f.write("""package p.core;
import javax.swing.*;
public class Main {
  static Object get(String n) throws Exception { java.lang.reflect.Field f = ProgressWindow.class.getDeclaredField(n); f.setAccessible(true); return f.get(null); }
  static String el() throws Exception { final String[] o = new String[1];
    SwingUtilities.invokeAndWait(() -> { try { o[0] = ((JLabel) get("elapsed")).getText(); } catch (Exception e) { o[0] = "ERR"; } }); return o[0]; }
  public static void main(String[] a) throws Exception {
    ProgressWindow.start(2); Thread.sleep(600);
    ProgressWindow.testStarted("one", "Chrome"); ProgressWindow.testDone("passed"); Thread.sleep(1100);
    ProgressWindow.testStarted("two", "Firefox"); ProgressWindow.testDone("passed"); Thread.sleep(400);
    String e1 = el(); Thread.sleep(2100); String e2 = el();
    ProgressWindow.wrapUp(); Thread.sleep(200);
    long t0 = System.currentTimeMillis(); ProgressWindow.finish("All 2 tests passed");
    long ms = System.currentTimeMillis() - t0; Thread.sleep(2200);
    System.out.println("RESULT frozen=" + e1.equals(e2) + " finishMs=" + ms + " closed=" + (get("frame") == null));
  }
}
""")
            out = os.path.join(d, "out")
            c = subprocess.run(["javac", "-d", out] + glob.glob(os.path.join(src, "*.java")),
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            self.assertEqual(c.returncode, 0, c.stderr)
            xvfb = subprocess.Popen(["Xvfb", ":96", "-screen", "0", "1280x800x24"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1)
            env = dict(os.environ, DISPLAY=":96")
            env.pop("CI", None)
            t0 = time.time()
            r = subprocess.run(["java", "-cp", out, "p.core.Main"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=60, env=env)
            total = time.time() - t0
            line = [x for x in r.stdout.splitlines() if x.startswith("RESULT")][-1]
        finally:
            if xvfb:
                xvfb.kill()
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(line, line.replace("frozen=false", ""))
        self.assertIn("frozen=true", line)
        self.assertIn("closed=true", line)
        self.assertLess(int(line.split("finishMs=")[1].split()[0]), 500)   # the run is not held
        self.assertLess(total, 20)                                            # and the JVM exits


class PlaywrightParallel(unittest.TestCase):
    def test_workers_follow_the_switch(self):
        cfg = _js_files(A.build_playwright_project)["playwright.config.js"]
        self.assertIn("workers: parallel ? workerCount : 1,", cfg)
        self.assertIn("const workerCount = Math.max(2, browsers.projects().length);", cfg)
        self.assertIn("timeout: 60000,", cfg)       # room to close the browser and save its video
        self.assertIn("String(process.env.PARALLEL || 'true')", cfg)
        rep = _js_files(A.build_playwright_project)["support/progress-reporter.js"]
        self.assertIn("progress.wrapUp();", rep)

    @unittest.skipUnless(NODE, "node not installed")
    def test_parallel_locator_saves_keep_every_key(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "locators.js"), "w", encoding="utf-8") as f:
                f.write(A._PW_LOCATORS_JS)
            with open(os.path.join(d, "w.js"), "w", encoding="utf-8") as f:
                f.write("const { load, save } = require('./locators');\n"
                        "const me = process.argv[2]; const store = load();\n"
                        "for (let i = 0; i < 40; i++) { store[me + i] = { by: 'css', value: '#' + i }; save(store, me + i); }\n")
            with open(os.path.join(d, "locators.json"), "w", encoding="utf-8") as f:
                f.write('{"seed": {"by": "id", "value": "x"}}')
            procs = [subprocess.Popen([NODE, "w.js", k], cwd=d) for k in ("a", "b", "c")]
            for p in procs:
                p.wait(60)
            data = json.load(open(os.path.join(d, "locators.json"), encoding="utf-8"))
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertIn("seed", data)
        # Three processes saving at the same time: every key of every one survives.
        for k in ("a", "b", "c"):
            self.assertEqual(sum(1 for x in data if x.startswith(k)), 40, k)


@unittest.skipUnless(NODE, "node not installed")
class CypressParallel(unittest.TestCase):
    def test_parallel_browser_processes_keep_every_healed_locator(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "cy-tasks.js"), "w", encoding="utf-8") as f:
                f.write(A._CY_TASKS_JS)
            os.makedirs(os.path.join(d, "node_modules", "dotenv"))
            with open(os.path.join(d, "node_modules", "dotenv", "index.js"), "w", encoding="utf-8") as f:
                f.write("module.exports={config(){}};\n")
            with open(os.path.join(d, "w.js"), "w", encoding="utf-8") as f:
                f.write("const t = require('./cy-tasks'); const me = process.argv[2];\n"
                        "for (let i = 0; i < 40; i++) t.saveLocator({ key: me + i, spec: { by: 'css', value: '#' + i } });\n")
            procs = [subprocess.Popen([NODE, "w.js", k], cwd=d) for k in ("a", "b", "c")]
            for p in procs:
                p.wait(60)
            data = json.load(open(os.path.join(d, "locators.json"), encoding="utf-8"))
            leftovers = [x for x in os.listdir(d) if x.endswith((".tmp", ".lock"))]
        finally:
            shutil.rmtree(d, ignore_errors=True)
        for k in ("a", "b", "c"):
            self.assertEqual(sum(1 for x in data if x.startswith(k)), 40, k)
        self.assertEqual(leftovers, [])

    def _project(self):
        f = _js_files(A.build_cypress_project)
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "cypress"))
        with open(os.path.join(d, "cypress", "run-browsers.js"), "w", encoding="utf-8") as fh:
            fh.write(f["cypress/run-browsers.js"])
        with open(os.path.join(d, "cypress", "progress.js"), "w", encoding="utf-8") as fh:
            fh.write(f["cypress/progress.js"])
        with open(os.path.join(d, "cypress", "report-mailer.js"), "w", encoding="utf-8") as fh:
            fh.write("const fs=require('fs'),path=require('path');\n"
                     "module.exports={openFile(){},async mailReport(){},writeDetailed(){return '';},\n"
                     " writeReport(run,dir){fs.mkdirSync(dir,{recursive:true});const p=path.join(dir,'merged.json');\n"
                     "  fs.writeFileSync(p,JSON.stringify(run));return p;}};\n")
        nm = os.path.join(d, "node_modules")
        os.makedirs(os.path.join(nm, "dotenv"))
        with open(os.path.join(nm, "dotenv", "index.js"), "w", encoding="utf-8") as fh:
            fh.write("module.exports={config(){}};\n")
        os.makedirs(os.path.join(nm, "cypress", "bin"))
        with open(os.path.join(nm, "cypress", "package.json"), "w", encoding="utf-8") as fh:
            # Like the real package: "exports" hide bin/cypress from require.resolve.
            fh.write('{"name":"cypress","main":"index.js","exports":{".":"./index.js","./package.json":"./package.json"}}')
        # Fake `cypress run`: takes ~1.5 s and saves its run like the real config does.
        fake = ("const fs=require('fs'),path=require('path');\n"
                "const b=process.env.QA_BATCH_BROWSER; const t0=Date.now();\n"
                "console.log('running in '+b);\n"
                "setTimeout(()=>{const dest=path.join(process.env.QA_BATCH_DIR,b);fs.mkdirSync(dest,{recursive:true});\n"
                " fs.writeFileSync(path.join(dest,'run.json'),JSON.stringify({startedAt:t0,duration:Date.now()-t0,\n"
                "  stories:[{name:'S',tests:[{title:'t',status:'passed'}]}],env:{browserVersion:'1'}}));\n"
                " fs.appendFileSync(path.join(process.env.QA_BATCH_DIR,'..','times.txt'),b+' '+t0+' '+Date.now()+'\\n');\n"
                " process.exit(0);},1500);\n")
        with open(os.path.join(nm, "cypress", "bin", "cypress"), "w", encoding="utf-8") as fh:
            fh.write(fake)
        with open(os.path.join(nm, "cypress", "index.js"), "w", encoding="utf-8") as fh:
            fh.write("const {execFileSync}=require('child_process');\n"
                     "module.exports={async run(o){execFileSync(process.execPath,[require.resolve('./bin/cypress')],"
                     "{stdio:'inherit',env:process.env});return {totalFailed:0};}};\n")
        return d

    def _run(self, parallel):
        d = self._project()
        try:
            env = dict(os.environ, BROWSER="chrome,firefox", PARALLEL=parallel, PROGRESS="off", HEADLESS="true")
            t0 = time.time()
            r = subprocess.run([NODE, os.path.join("cypress", "run-browsers.js")], cwd=d,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)
            took = time.time() - t0
            times = open(os.path.join(d, "cypress", "times.txt"), encoding="utf-8").read().split("\n")
            merged = json.load(open(os.path.join(d, "cypress", "reports", "merged.json"), encoding="utf-8"))
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = {x.split()[0]: (int(x.split()[1]), int(x.split()[2])) for x in times if x.strip()}
        self.assertEqual(sorted(rows), ["chrome", "firefox"])
        self.assertEqual(sorted(s["name"] for s in merged["stories"]), ["S · Chrome", "S · Firefox"])
        return rows, r.stdout, took

    def test_browsers_run_at_the_same_time(self):
        rows, out, _ = self._run("true")
        (cs, ce), (fs, fe) = rows["chrome"], rows["firefox"]
        self.assertLess(max(cs, fs), min(ce, fe))            # the two runs overlap
        self.assertIn("[Chrome] running in chrome", out)
        self.assertIn("[Firefox] running in firefox", out)
        self.assertIn("at the same time (PARALLEL=true)", out)

    def test_parallel_false_runs_one_after_another(self):
        rows, out, _ = self._run("false")
        (cs, ce), (fs, fe) = rows["chrome"], rows["firefox"]
        self.assertTrue(ce <= fs or fe <= cs)                # no overlap
        self.assertIn("(1/2)", out)

    def test_config_gives_each_browser_its_own_folders(self):
        cfg = _js_files(A.build_cypress_project)["cypress.config.js"]
        self.assertIn("screenshotsFolder: path.join(batchDir, 'screenshots')", cfg)
        self.assertIn("downloadsFolder: path.join(batchDir, 'downloads')", cfg)
        self.assertIn("reportDir: batchDir ? path.join(batchDir, 'mochawesome') : 'cypress/reports'", cfg)


@unittest.skipUnless(NODE, "node not installed")
class NodeProgressClock(unittest.TestCase):
    def test_clock_stops_at_100_percent_and_finish_is_short(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "progress.js"), "w", encoding="utf-8") as fh:
                fh.write(A._PROGRESS_JS)
            script = r"""
const http = require('http');
require('child_process').spawn = () => ({ on() {}, unref() {}, pid: 0 });
const p = require('./progress');
const get = (port) => new Promise((res) => http.get({ host: '127.0.0.1', port, path: '/state' }, (r) => {
  let b = ''; r.on('data', (c) => b += c); r.on('end', () => res(JSON.parse(b))); }));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const keep = setInterval(() => {}, 1000);
(async () => {
  const port = await p.start(2);
  p.testStarted('A', 'Chrome'); p.testDone('passed'); await wait(300);
  p.testStarted('B', 'Chrome'); p.testDone('passed');
  const a = await get(port); await wait(1200); const b = await get(port);
  p.wrapUp(); const w = await get(port);
  const t0 = Date.now(); await p.finish(); const ms = Date.now() - t0;
  console.log(JSON.stringify({ frozen: a.elapsed === b.elapsed, ended: !!a.ended, wrapping: w.wrapping,
    finishMs: ms, page: p._page.indexOf('ALL TESTS RAN') > 0 }));
  clearInterval(keep);
})();
"""
            with open(os.path.join(d, "t.js"), "w", encoding="utf-8") as fh:
                fh.write(script)
            env = dict(os.environ, PROGRESS="on")
            env.pop("CI", None)
            r = subprocess.run([NODE, "t.js"], cwd=d, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertTrue(out["frozen"])
        self.assertTrue(out["ended"])
        self.assertTrue(out["wrapping"])
        self.assertTrue(out["page"])
        self.assertLess(out["finishMs"], 2500)


if __name__ == "__main__":
    unittest.main()


class CopyAiPromptPhrasings(unittest.TestCase):
    """Every phrasing the Copy AI prompt teaches is one QA Studio understands."""

    def test_page_actions_and_checks_in_the_prompt_parse(self):
        t = E.EXTERNAL_CASE_PROMPT
        for a in ["Go to the next page", "Go to page 3", 'Click Next page until "Sophia" is shown',
                  'Set cookie "theme" to "dark"', 'Delete cookie "theme"', "Delete all cookies",
                  'Set local storage "token" to "abc"', 'Set session storage "cart" to "2"',
                  "Clear local storage", "Clear local and session storage",
                  'Open "/admin" with basic auth "{{APP_USER}}" / "{{APP_PASS}}"',
                  "Refresh the page", "Go back", "Go forward"]:
            self.assertIn(a.split('"')[0].strip(), t)
            self.assertIsNotNone(E._page_action_in(a), a)
        for c, kind in [("Page 3 is the current page", "page"), ('Cookie "theme" is "dark"', "cookie"),
                        ('Cookie "theme" exists', "cookie"), ('Cookie "theme" is deleted', "cookie"),
                        ('Local storage "token" contains "ab"', "storage"),
                        ('Local storage "token" is removed', "storage")]:
            self.assertEqual([x[0] for x in E._page_checks_in(c)], [kind], c)
        self.assertIn('The "Home" link has "href" containing "/home"', t)

    def test_attribute_check_is_not_also_a_url_check(self):
        self.assertEqual([x[0] for x in E._page_checks_in('The "Home" link has "href" containing "/home"')],
                         ["attr"])
        self.assertEqual(E._page_checks_in('The URL contains "/secure"')[0][:2], ("url", "contains|/secure"))


class FramelessJavaWindow(unittest.TestCase):
    """Playwright / Cypress use the Selenium projects' frameless Swing window
    (no title bar) when Java 11+ is there, else the Edge / Chrome window."""

    def test_projects_get_the_java_window_and_it_compiles(self):
        pw = _js_files(A.build_playwright_project)
        cy = _js_files(A.build_cypress_project)
        self.assertIn("support/QaProgressWindow.java", pw)
        self.assertIn("cypress/QaProgressWindow.java", cy)
        src = pw["support/QaProgressWindow.java"]
        self.assertTrue(src.index("public class QaProgressWindow") < src.index("final class ProgressWindow"))
        self.assertIn("frame.setUndecorated(true)", src)
        js = pw["support/progress.js"]
        self.assertIn("path.join(__dirname, 'QaProgressWindow.java')", js)
        self.assertIn("openBrowserWindow(url)", js)
        if not shutil.which("javac"):
            self.skipTest("javac missing")
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "QaProgressWindow.java"), "w", encoding="utf-8") as f:
                f.write(src)
            r = subprocess.run(["javac", "-d", os.path.join(d, "out"), os.path.join(d, "QaProgressWindow.java")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(r.returncode, 0, r.stderr)


@unittest.skipUnless(NODE and os.name != "nt", "needs node and a POSIX shell for the fake java")
class OldJavaFallsBack(unittest.TestCase):
    """An old Java on PATH (Java 8) is only asked its version: the Edge/Chrome
    window opens instead of javaw's "A JNI error has occurred" dialog."""

    def test_java_8_is_never_started_with_the_window(self):
        js = A._PROGRESS_JS
        self.assertNotIn("'javaw", js)                       # only mentioned in a comment
        self.assertIn("javaVersion(c) >= 11 && hasCompiler(c)", js)    # a JRE goes straight to the browser
        d = tempfile.mkdtemp()
        try:
            for name, text in (("progress.js", js), ("QaProgressWindow.java", "// stub")):
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write(text)
            bindir = os.path.join(d, "bin")
            os.makedirs(bindir)
            log = os.path.join(d, "ran.log")
            with open(os.path.join(bindir, "java"), "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\necho 'java version \"1.8.0_381\"' >&2\necho \"$*\" >> %s\nexit 1\n" % log)
            os.chmod(os.path.join(bindir, "java"), 0o755)
            with open(os.path.join(d, "t.js"), "w", encoding="utf-8") as f:
                f.write("require('child_process').spawn = (exe, args) => { console.log('SPAWN ' + exe); "
                        "return { on() {}, unref() {}, pid: 0 }; };\n"
                        "const p = require('./progress');\n"
                        "p.start(1).then(async () => { await p.finish(); process.exit(0); });\n")
            env = dict(os.environ, PROGRESS="on", PATH=bindir + os.pathsep + "/usr/bin:/bin")
            env.pop("JAVA_HOME", None)
            env.pop("CI", None)
            r = subprocess.run([NODE, "t.js"], cwd=d, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60, env=env)
            ran = open(log, encoding="utf-8").read().split()
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(ran, ["-version"])                    # only asked its version
        self.assertNotIn("SPAWN java", r.stdout)               # never started with the window
        self.assertIn("SPAWN", r.stdout)                       # the browser window instead


class BrowserRows(unittest.TestCase):
    """The progress window shows one row per browser (name, own bar, done/total)."""

    def test_every_framework_reports_the_browser(self):
        pw = E._progress_window("p")
        self.assertIn("public static void expect(String browserName, int count)", pw)
        self.assertIn("public static void setLane(", pw)
        self.assertIn('label("BROWSERS"', pw)
        rm = E._sh_report_mailer("p")
        self.assertIn("perBrowser.forEach(ProgressWindow::expect);", rm)
        for st in ("failed", "passed", "skipped"):
            self.assertIn('ProgressWindow.testDone("%s", browserOf(r))' % st, rm)
        rep = _js_files(A.build_playwright_project)["support/progress-reporter.js"]
        self.assertIn("progress.expect(name, per[name])", rep)
        self.assertIn("'skipped', browser);", rep)
        cy = _js_files(A.build_cypress_project)
        self.assertIn("op: 'done', status, browser: Cypress.browser.displayName", cy["cypress/support/e2e.js"])
        self.assertIn("progress.expect(label(b), perBrowser)", cy["cypress/run-browsers.js"])
        self.assertIn('str(js, "lanes")', A.progress_window_java())

    @unittest.skipUnless(NODE, "node not installed")
    def test_node_state_counts_each_browser(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "progress.js"), "w", encoding="utf-8") as f:
                f.write(A._PROGRESS_JS)
            with open(os.path.join(d, "t.js"), "w", encoding="utf-8") as f:
                f.write("require('child_process').spawn = () => ({ on() {}, unref() {}, pid: 0 });\n"
                        "const p = require('./progress'); const http = require('http');\n"
                        "p.start(6).then((port) => {\n"
                        "  p.expect('Chrome', 3); p.expect('Firefox', 3);\n"
                        "  p.testDone('passed', 'Chrome'); p.testDone('failed', 'Firefox'); p.testDone('skipped', 'Chrome');\n"
                        "  http.get({ host: '127.0.0.1', port, path: '/state' }, (r) => { let b = '';\n"
                        "    r.on('data', (c) => b += c); r.on('end', async () => { console.log(JSON.parse(b).lanes);\n"
                        "      await p.finish(); process.exit(0); }); }); });\n")
            env = dict(os.environ, PROGRESS="on")
            env.pop("CI", None)
            r = subprocess.run([NODE, "t.js"], cwd=d, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60, env=env)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "Chrome|3|1|0|1;Firefox|3|0|1|0")


class HttpAuthPages(unittest.TestCase):
    """Basic / digest pages: Cypress never puts the password in the URL (it hung
    on the browser's sign-in prompt) and answers Digest itself; Playwright sends
    Basic on that page only."""

    def test_generated_code_asks_the_scheme_first(self):
        cy = _js_files(A.build_cypress_project)
        heal = [v for k, v in cy.items() if k.endswith("healer.js")][0]
        self.assertIn("cy.task('qaDigestHeader'", heal)
        self.assertNotIn("u.username = p[1]", heal)
        self.assertIn("qaDigestHeader: tasks.qaDigestHeader", cy["cypress.config.js"])
        pw = [v for k, v in _js_files(A.build_playwright_project).items() if k.endswith("healer.js")][0]
        self.assertIn("ctx.request.get(u.toString(), { failOnStatusCode: false, maxRedirects: 0 })", pw)
        self.assertIn("authorization: header", pw)

    @unittest.skipUnless(NODE, "node not installed")
    def test_digest_answer_matches_rfc(self):
        import hashlib
        import re as _re
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "cy-tasks.js"), "w", encoding="utf-8") as f:
                f.write(A._CY_TASKS_JS)
            os.makedirs(os.path.join(d, "node_modules", "dotenv"))
            with open(os.path.join(d, "node_modules", "dotenv", "index.js"), "w", encoding="utf-8") as f:
                f.write("module.exports={config(){}};\n")
            challenge = 'Digest realm="Restricted Area", qop="auth,auth-int", nonce="abc123", opaque="op9"'
            js = ("console.log(require('./cy-tasks').qaDigestHeader({ url: 'https://h.test/digest-auth?x=1', "
                  "method: 'GET', user: 'admin', pass: 'admin', challenge: %s }))" % json.dumps(challenge))
            r = subprocess.run([NODE, "-e", js], cwd=d, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        h = r.stdout.strip()
        f = {k: a or b for k, a, b in _re.findall(r'(\w+)=(?:"([^"]*)"|([^,\s]*))', h[len("Digest "):])}
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()
        want = md5("%s:abc123:%s:%s:auth:%s" % (md5("admin:Restricted Area:admin"), f["nc"], f["cnonce"],
                                                md5("GET:/digest-auth?x=1")))
        self.assertEqual(f["response"], want)
        self.assertEqual((f["uri"], f["opaque"], f["qop"]), ("/digest-auth?x=1", "op9", "auth"))
