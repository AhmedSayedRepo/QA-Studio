"""Batch 10: visual checks (a screenshot against a saved baseline), CI pipeline
files (GitHub Actions + Azure Pipelines, Newman for the API collection) and
cloud grids (GRID=browserstack / sauce / lambdatest / a grid URL) - parsed
from the authored wording and emitted for Selenium, Playwright and Cypress."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402
import automation_targets as A  # noqa: E402
import release_notes  # noqa: E402
import strings  # noqa: E402

NODE = shutil.which("node")
SEED = lambda *a, **k: ("css", "#x", True)  # noqa: E731
LANGS = ("en", "ar", "fr", "tr", "es", "de", "nl")


def _node(js, env=None):
    r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=60,
                       env=dict(os.environ, **(env or {})))
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


class VisualParsing(unittest.TestCase):
    def test_phrasings(self):
        cases = {
            'The page looks the same as the visual baseline "home"': "home",
            'The page looks the same as the baseline, ignoring "#clock" and ".ads", within 2%.': "|#clock, .ads|2",
            'The dashboard page matches the visual baseline "dash" ignoring ".avatar" with 5% tolerance':
                "dash|.avatar|5",
            "No visual changes": "",
            'It visually matches "cart" within 0.5%.': "cart||0.5",
            "The screen looks exactly the same as before": "",
        }
        for text, value in cases.items():
            self.assertEqual(E._page_checks_in(text)[0][:2], ("visual", value), text)

    def test_not_visual(self):
        for text in ("The page looks the same as the old design", 'The page shows "Visual baseline"'):
            self.assertFalse([c for c in E._page_checks_in(text) if c[0] == "visual"], text)

    def test_parts_and_label(self):
        self.assertEqual(E._visual_parts("home|#clock, .ads|2"), ("home", "#clock, .ads", 2.0))
        self.assertEqual(E._visual_parts(""), ("", "", E._VISUAL_MAX_PCT))
        self.assertEqual(E._page_check_label("visual", "home|#clock|2"),
                         'page looks the same as the visual baseline "home" (ignoring #clock) within 2%')

    def test_quoted_name_and_selectors_are_not_page_text(self):
        steps = [{"action": "Open https://example.com",
                  "expected": 'The page looks the same as the visual baseline "home", ignoring "#clock"'}]
        out = E._apply_assertion_pack([], steps)
        self.assertEqual([(i["check"], i["value"]) for i in out], [("visual", "home|#clock")])

    def test_compiler_copy_of_the_check_is_replaced(self):
        # The compiler restated the visual check itself (no name); the authored
        # wording wins, and the page text stays.
        steps = [{"action": "Open https://the-internet.herokuapp.com/login",
                  "expected": 'The page shows "Login Page". The page looks the same as the visual baseline "login"'}]
        model = [{"role": "action", "verb": "navigate", "target": "login page", "from_steps": [1],
                  "value": "https://the-internet.herokuapp.com/login"},
                 {"role": "assertion", "target": "Login Page", "check": "", "from_steps": [1],
                  "expected": steps[0]["expected"]},
                 {"role": "assertion", "target": "page looks the same as the visual baseline", "check": "visual",
                  "value": "", "from_steps": [1], "expected": steps[0]["expected"]}]
        out = E._apply_assertion_pack(model, steps)
        self.assertEqual([(i.get("check"), i.get("value")) for i in out if i["role"] == "assertion"],
                         [("visual", "login"), ("", None)])

    def test_login_navigate_runs_when_not_on_that_page(self):
        class D:
            current_url = "https://the-internet.herokuapp.com/"
        self.assertFalse(E._already_on_page(D(), "https://the-internet.herokuapp.com/login"))
        D.current_url = "https://the-internet.herokuapp.com/login/"
        self.assertTrue(E._already_on_page(D(), "https://the-internet.herokuapp.com/login"))
        self.assertTrue(E._already_on_page(D(), ""))     # no URL: the login start page

    def test_inspection_passes_with_a_note(self):
        self.assertTrue(E._page_check_ok(object(), "visual", "home"))
        self.assertIn("visual", E._PAGE_CHECKS)

    def test_prompt_shows_the_phrasing(self):
        self.assertIn("The page looks the same as the visual baseline", E.EXTERNAL_CASE_PROMPT)


class VisualEmitted(unittest.TestCase):
    def test_every_framework(self):
        it = {"role": "assertion", "check": "visual", "value": "home|#clock|2", "target": "x"}
        j, p, c = [], [], []
        E._emit_intent(j, "k1", it)
        A._emit_pw_intent(p, "k1", it, SEED, {})
        A._emit_cy_intent(c, "k1", it, SEED, {})
        self.assertIn('heal.assertPage("visual", "home|#clock|2")', "\n".join(j))
        self.assertIn('heal.assertPage("visual"', "\n".join(p))
        self.assertIn('cy.healAssertPage("visual", "home|#clock|2")', "\n".join(c))

    def test_runtimes_implement_it(self):
        healer = E._sh_healer("com.qastudio")
        for s in ('"visual".equals(check)', "private String visualDiff(String spec)", "UPDATE_BASELINES",
                  '"visual-baselines"', '"visual-diffs"', "executeAsyncScript(VISUAL_CMP_JS"):
            self.assertIn(s, healer)
        self.assertNotRegex(healer, r"__VISUAL_[A-Z]+__")
        pw = A._with_batch2_js(A._PW_HEALER_JS)
        cy = A._with_batch2_js(A._CY_HEALER_JS)
        tasks = A._with_batch2_js(A._CY_TASKS_JS)
        for s in ("check === 'visual'", "async visualDiff(value)", "'visual-baselines'", "'visual-diffs'"):
            self.assertIn(s, pw)
        for s in ("check === 'visual'", "function _qaVisual(value)", "cy.task('qaVisual'"):
            self.assertIn(s, cy)
        self.assertIn("qaVisual({ op, key, shot, diff })", tasks)
        self.assertIn("qaVisual: tasks.qaVisual", A._CY_CONFIG)
        for text in (pw, cy, tasks):
            self.assertNotRegex(text, r"__VISUAL_[A-Z]+__")

    @unittest.skipUnless(NODE, "node not installed")
    def test_generated_js_parses(self):
        d = tempfile.mkdtemp()
        for name, text in (("healer.js", A._with_batch2_js(A._PW_HEALER_JS)),
                           ("cy-healer.js", A._with_batch2_js(A._CY_HEALER_JS).replace("__UI_LOCALE__", "en")),
                           ("cy-tasks.js", A._with_batch2_js(A._CY_TASKS_JS)),
                           ("browsers.js", A._PW_BROWSERS_JS),
                           ("run-browsers.js", A._CY_RUN_BROWSERS_JS)):
            path = os.path.join(d, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            r = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, name + ": " + r.stderr)


class Grids(unittest.TestCase):
    def test_selenium_driver_factory(self):
        df = E._driver_factory("com.qastudio")
        for s in ("static String gridName()", "private static WebDriver remote(String b",
                  '"bstack:options"', '"sauce:options"', '"LT:Options"', "hub.lambdatest.com",
                  "hub-cloud.browserstack.com", "saucelabs.com/wd/hub", "RemoteWebDriver(new java.net.URL(hub)",
                  'need("BROWSERSTACK_USERNAME")', 'need("SAUCE_ACCESS_KEY")', 'need("LT_ACCESS_KEY")'):
            self.assertIn(s, df)

    @unittest.skipUnless(NODE, "node not installed")
    def test_playwright_endpoints(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "browsers.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(A._PW_BROWSERS_JS.replace("require('dotenv').config();", ""))
        js = "const b = require(%s); const out = []; try { out.push(b.gridEndpoint('chrome')); } " \
             "catch (e) { out.push('ERR ' + e.message); } " \
             "try { out.push(JSON.stringify(b.projects()[0].use)); } catch (e) { out.push(''); } " \
             "console.log(JSON.stringify(out));" % json.dumps(path)
        run = lambda env: json.loads(_node(js, env).splitlines()[-1])  # noqa: E731
        self.assertEqual(run({"GRID": "local"})[0], None)
        err = run({"GRID": "browserstack", "BROWSERSTACK_USERNAME": ""})[0]
        self.assertIn("BROWSERSTACK_USERNAME", err)
        ws, use = run({"GRID": "browserstack", "BROWSERSTACK_USERNAME": "u", "BROWSERSTACK_ACCESS_KEY": "k"})
        self.assertTrue(ws.startswith("wss://cdp.browserstack.com/playwright?caps="))
        self.assertIn('"connectOptions"', use)
        ws = run({"GRID": "lambdatest", "LT_USERNAME": "u", "LT_ACCESS_KEY": "k"})[0]
        self.assertTrue(ws.startswith("wss://cdp.lambdatest.com/playwright?capabilities="))
        self.assertEqual(run({"GRID": "ws://grid:3000/"})[0], "ws://grid:3000/")
        self.assertIsNone(run({"GRID": "sauce"})[0])

    def test_playwright_global_login_skips_fast_without_a_form(self):
        gs = A._PW_GLOBAL_SETUP
        self.assertIn("no APP_USER in .env", gs)
        self.assertIn("no login form at APP_LOGIN_URL", gs)
        self.assertLess(gs.index("waitFor({ state: 'visible', timeout: 8000 })"), gs.index("await performLogin(page)"))

    def test_cypress_runs_on_vendor_clis(self):
        rb = A._CY_RUN_BROWSERS_JS
        for s in ("function runOnGrid(grid, list)", "browserstack-cypress-cli", "lambdatest-cypress-cli",
                  "browserstack.json", "lambdatest-config.json"):
            self.assertIn(s, rb)


class CiPipelines(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _read(self, rel):
        with open(os.path.join(self.d, *rel.split("/")), encoding="utf-8") as f:
            return f.read()

    def test_each_framework(self):
        try:
            import yaml
        except ImportError:
            yaml = None
        for target, run in (("selenium", "mvn -B test"), ("playwright", "npm test"),
                            ("cypress", "npm run test:headless")):
            d = tempfile.mkdtemp()
            self.assertEqual(E.write_ci_pipelines(d, target, "chrome,firefox"), [E._CI_GITHUB, E._CI_AZURE])
            for rel in (E._CI_GITHUB, E._CI_AZURE):
                with open(os.path.join(d, *rel.split("/")), encoding="utf-8") as f:
                    text = f.read()
                self.assertIn(E._CI_MARKER, text)
                self.assertIn(run, text)
                self.assertIn("chrome,firefox", text)
                self.assertNotIn("newman", text)        # no API collection
                if yaml:
                    yaml.safe_load(text)
            gh = open(os.path.join(d, ".github", "workflows", "qa-tests.yml"), encoding="utf-8").read()
            self.assertIn("APP_PASS: ${{ secrets.APP_PASS }}", gh)
            self.assertIn("APP_BASE_URL: ${{ vars.APP_BASE_URL }}", gh)
            self.assertIn('HEADLESS: "true"', gh)
            self.assertNotIn("@v4", gh)                 # Node 20 actions are deprecated

    def test_newman_job_with_the_api_collection(self):
        os.makedirs(os.path.join(self.d, E._POSTMAN_DIR))
        with open(os.path.join(self.d, E._POSTMAN_DIR, E._POSTMAN_COLLECTION), "w") as f:
            f.write("{}")
        E.write_ci_pipelines(self.d, "playwright", "chrome")
        for rel in (E._CI_GITHUB, E._CI_AZURE):
            text = self._read(rel)
            self.assertIn("npx -y newman run", text)
            self.assertIn(E._POSTMAN_COLLECTION, text)

    def test_user_owned_file_is_kept(self):
        E.write_ci_pipelines(self.d, "cypress", "chrome")
        path = os.path.join(self.d, E._CI_AZURE)
        with open(path, "w", encoding="utf-8") as f:
            f.write("# my own pipeline\n")
        self.assertEqual(E.write_ci_pipelines(self.d, "cypress", "edge"), [E._CI_GITHUB])
        self.assertEqual(self._read(E._CI_AZURE), "# my own pipeline\n")
        self.assertEqual(E.write_ci_pipelines(self.d, "cypress", "edge"), [])     # unchanged

    def test_apply_run_browsers_writes_ci_and_env(self):
        with open(os.path.join(self.d, "package.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(self.d, ".env"), "w") as f:
            f.write("BROWSER=\n")
        E.apply_run_browsers(self.d, "playwright", ["edge", "firefox"])
        self.assertIn('BROWSER: "edge,firefox"', self._read(E._CI_GITHUB))
        env = self._read(".env")
        self.assertIn("GRID=local", env)
        self.assertIn("UPDATE_BASELINES=false", env)
        self.assertEqual(E.ensure_env_batch10(self.d), [])      # idempotent


class Texts(unittest.TestCase):
    def test_release_notes_and_help(self):
        batch10 = ("upd_note_batch10_visual", "upd_note_batch10_ci_grid", "upd_note_batch10_run_push")
        self.assertEqual(release_notes.exact_keys_for("4.0.3"), batch10)       # released in 4.0.3
        keys = batch10 + ("help_automation_d15", "help_automation_p15",
                                                          "auto_run_push")
        for lang in LANGS:
            for k in keys:
                self.assertTrue(strings._STRINGS[lang].get(k), lang + " " + k)

    def test_sample_cases_parse(self):
        import csv
        path = os.path.join(ROOT, "samples", "batch10-visual-test-cases.csv")
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        visual = [E._page_checks_in(r["Expected Result"]) for r in rows]
        self.assertEqual(sum(1 for v in visual for c in v if c[0] == "visual"), 5)


class _Resp:
    def __init__(self, code, payload=None):
        self.status_code = code
        self._p = payload or {}

    def json(self):
        return self._p


class FakeGitHub:
    """Just enough of the Actions variables / secrets API."""
    def __init__(self, variables=(), secrets=(), deny=False):
        self.variables = dict.fromkeys(variables, "old")
        self.secrets = dict.fromkeys(secrets, "old")
        self.deny = deny

    def _name(self, url):
        return url.rstrip("/").rsplit("/", 1)[-1]

    def get(self, url, headers=None, timeout=None):
        if self.deny:
            return _Resp(403)
        if url.endswith("/secrets/public-key"):
            return _Resp(200, {"key": "PUBKEY", "key_id": "kid"})
        pool = self.variables if "/variables/" in url else self.secrets
        return _Resp(200 if self._name(url) in pool else 404)

    def post(self, url, headers=None, json=None, timeout=None):
        self.variables[json["name"]] = json["value"]
        return _Resp(201)

    def put(self, url, headers=None, json=None, timeout=None):
        self.secrets[self._name(url)] = json
        return _Resp(201)


class GitHubSettings(unittest.TestCase):
    URL = "https://github.com/me/shop-tests.git"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        E.write_ci_pipelines(self.d, "playwright", "chrome")
        with open(os.path.join(self.d, ".env"), "w", encoding="utf-8") as f:
            f.write("APP_BASE_URL=https://shop.test\nAPP_LOGIN_URL=https://your-login.example.com/x\n"
                    "APP_USER=qa@shop.test\nAPP_PASS=s3cret\nOTP_SECRET=\n")
        self.log = []

    def _run(self, gh, nacl=True):
        fake_nacl = types.ModuleType("nacl")
        mods = {"nacl": fake_nacl} if nacl else {"nacl": None}
        with mock.patch.object(E, "requests", gh), mock.patch.dict(sys.modules, mods), \
                mock.patch.object(E, "_seal_secret", lambda key, v: "sealed:" + key + ":" + v):
            return E.github_ci_settings(self.d, self.URL, "tok", lambda m, *a: self.log.append(m))

    def test_adds_only_missing_names(self):
        gh = FakeGitHub(variables=("APP_LOGIN_URL",), secrets=("APP_PASS",))
        done = self._run(gh)
        self.assertEqual(done, {"variables": ["APP_BASE_URL"], "secrets": ["APP_USER"]})
        self.assertEqual(gh.variables["APP_LOGIN_URL"], "old")              # never replaced
        self.assertEqual(gh.secrets["APP_PASS"], "old")
        self.assertEqual(gh.secrets["APP_USER"], {"encrypted_value": "sealed:PUBKEY:qa@shop.test", "key_id": "kid"})
        self.assertNotIn("OTP_SECRET", gh.secrets)                           # empty in .env

    def test_placeholder_urls_are_skipped(self):
        gh = FakeGitHub()
        self.assertEqual(self._run(gh)["variables"], ["APP_BASE_URL"])     # login URL is the example one

    def test_without_pynacl_secrets_are_left_to_the_user(self):
        gh = FakeGitHub()
        done = self._run(gh, nacl=False)
        self.assertEqual(done["secrets"], [])
        self.assertTrue(any("pynacl" in m for m in self.log))

    def test_token_without_permission(self):
        done = self._run(FakeGitHub(deny=True))
        self.assertEqual(done, {"variables": [], "secrets": []})
        self.assertTrue(any("can't change this repository's Actions settings" in m for m in self.log))

    def test_not_github_or_no_workflow(self):
        self.assertEqual(E.github_ci_settings(self.d, "https://dev.azure.com/o/p/_git/r", "tok"),
                         {"variables": [], "secrets": []})
        os.remove(os.path.join(self.d, ".github", "workflows", "qa-tests.yml"))
        self.assertEqual(self._run(FakeGitHub()), {"variables": [], "secrets": []})

    def test_selenium_urls_from_config_properties(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "config.properties"), "w", encoding="utf-8") as f:
            f.write("app.base.url=https://shop.test\napp.login.url=https://shop.test/login\n")
        self.assertEqual(E._ci_project_settings(d)[0],
                         {"APP_BASE_URL": "https://shop.test", "APP_LOGIN_URL": "https://shop.test/login"})

    def test_push_calls_it(self):
        import inspect
        self.assertIn("_github_ci_settings_safe(repo_dir, remote_url, token, cb)", inspect.getsource(E.push_to_git))

    def test_ci_keeps_the_visual_baselines(self):
        for rel in (E._CI_GITHUB, E._CI_AZURE):
            with open(os.path.join(self.d, *rel.split("/")), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("qa-visual-baselines", text)
            self.assertIn("visual-baselines", text)


class EnvCredentials(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        self.p = mock.patch.object(E, "_env_cred_store", lambda: os.path.join(self.home, "store.json"))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def _env(self, text=None):
        path = os.path.join(self.d, ".env")
        if text is not None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_cleared_when_opt_in_is_off(self):
        self._env("APP_BASE_URL=https://x.test\nAPP_USER=qa@x.test\nAPP_PASS=pw\n")
        E.sync_env_credentials(self.d, {"APP_USER": "qa@x.test", "APP_PASS": "pw"})     # opted in
        self.assertIn("APP_PASS=pw", self._env())
        log = []
        cleared, kept = E.sync_env_credentials(self.d, {}, lambda m, *a: log.append(m))  # box unticked
        self.assertEqual((cleared, kept), (["APP_USER", "APP_PASS"], []))
        self.assertEqual(self._env(), "APP_BASE_URL=https://x.test\nAPP_USER=\nAPP_PASS=\n")

    def test_hand_typed_value_is_kept(self):
        self._env("APP_USER=mine@x.test\nAPP_PASS=\n")
        log = []
        cleared, kept = E.sync_env_credentials(self.d, {}, lambda m, *a: log.append(m))
        self.assertEqual((cleared, kept), ([], ["APP_USER"]))
        self.assertIn("APP_USER=mine@x.test", self._env())
        self.assertTrue(log)

    def test_changed_by_hand_after_qa_studio_wrote_it(self):
        self._env("APP_USER=qa@x.test\n")
        E.sync_env_credentials(self.d, {"APP_USER": "qa@x.test"})
        self._env("APP_USER=other@x.test\n")
        self.assertEqual(E.sync_env_credentials(self.d, {}), ([], ["APP_USER"]))

    def test_store_holds_no_plain_values(self):
        self._env("APP_PASS=s3cret-value\n")
        E.sync_env_credentials(self.d, {"APP_PASS": "s3cret-value"})
        with open(os.path.join(self.home, "store.json"), encoding="utf-8") as f:
            self.assertNotIn("s3cret-value", f.read())

    def test_generate_calls_it(self):
        import inspect
        self.assertEqual(inspect.getsource(E).count("_sync_env_credentials_safe(out_dir, login, cb)"), 2)


class RunAndPush(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        E.write_ci_pipelines(self.d, "playwright", "chrome")
        self.log = []
        self.cb = lambda m, *a: self.log.append(m)

    def test_push_after_a_failing_run_then_fetch(self):
        calls = []
        with mock.patch.object(E, "run_project_tests", lambda *a, **k: calls.append("run") or {"ran": True, "code": 1}), \
                mock.patch.object(E, "push_to_git", lambda *a, **k: calls.append("push") or (True, "ok")), \
                mock.patch.object(E, "github_fetch_ci_baselines", lambda *a, **k: calls.append("fetch") or []):
            E.run_and_push(self.d, "playwright", {"url": "https://github.com/me/r.git", "token": "t"}, self.cb)
        self.assertEqual(calls, ["run", "push", "fetch"])

    def test_no_git_settings_no_push(self):
        with mock.patch.object(E, "run_project_tests", lambda *a, **k: {"ran": True, "code": 0}), \
                mock.patch.object(E, "push_to_git", lambda *a, **k: self.fail("pushed")):
            E.run_and_push(self.d, "playwright", {"url": "", "token": ""}, self.cb)
        self.assertTrue(any("Not pushed" in m for m in self.log))

    def test_azure_remote_is_not_followed(self):
        with mock.patch.object(E, "run_project_tests", lambda *a, **k: {"ran": True, "code": 0}), \
                mock.patch.object(E, "push_to_git", lambda *a, **k: (True, "ok")), \
                mock.patch.object(E, "github_fetch_ci_baselines", lambda *a, **k: self.fail("followed")):
            E.run_and_push(self.d, "playwright", {"url": "https://dev.azure.com/o/p/_git/r", "token": "t"}, self.cb)

    def test_fetch_adds_only_new_baselines_and_pushes(self):
        import io
        import zipfile
        os.makedirs(os.path.join(self.d, "visual-baselines"))
        with open(os.path.join(self.d, "visual-baselines", "old-linux.png"), "wb") as f:
            f.write(b"old")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("old-linux.png", b"new?")
            z.writestr("home-chrome-linux.png", b"png")

        class R:
            def __init__(self, code, js=None, content=b""):
                self.status_code, self._js, self.content = code, js, content

            def json(self):
                return self._js

        def get(url, headers=None, params=None, timeout=None):
            if url.endswith("/workflows/qa-tests.yml/runs"):
                return R(200, {"workflow_runs": [{"id": 7, "status": "completed", "conclusion": "failure",
                                                  "html_url": "https://github.com/me/r/actions/runs/7"}]})
            if url.endswith("/runs/7/artifacts"):
                return R(200, {"artifacts": [{"name": "qa-visual-baselines", "archive_download_url": "https://z"}]})
            return R(200, content=buf.getvalue())
        pushed = []
        with mock.patch.object(E, "requests", types.SimpleNamespace(get=get)), \
                mock.patch.object(E, "_git_head", lambda d: "abc"), \
                mock.patch.object(E, "push_to_git", lambda *a, **k: pushed.append(k.get("message")) or (True, "")):
            added = E.github_fetch_ci_baselines(self.d, "https://github.com/me/r.git", "t", cb=self.cb, poll=0)
        self.assertEqual(added, ["home-chrome-linux.png"])
        with open(os.path.join(self.d, "visual-baselines", "old-linux.png"), "rb") as f:
            self.assertEqual(f.read(), b"old")                                   # never replaced
        self.assertEqual(pushed, ["Add the CI visual baselines"])

    def test_run_project_tests_streams_and_summarises(self):
        os.makedirs(os.path.join(self.d, "node_modules"))
        script = "import os; print('  ok 1 [Chrome] login'); print('[visual] saved the baseline x'); " \
                 "print('noise'); print('  1 failed'); print('  4 passed (12s)'); " \
                 "print('HEADLESS=' + os.environ.get('HEADLESS', '')); raise SystemExit(1)"
        with mock.patch.object(E, "_test_command", lambda d, t: [sys.executable, "-c", script]):
            r = E.run_project_tests(self.d, "playwright", self.cb)
        self.assertEqual((r["ran"], r["code"]), (True, 1))
        self.assertEqual(r["summary"], "1 failed · 4 passed (12s)")
        self.assertTrue(any("[visual] saved" in m for m in self.log))
        self.assertNotIn("   noise", self.log)

    def test_new_repo_url_is_created_even_after_an_earlier_push(self):
        import inspect
        src = inspect.getsource(E.push_to_git)
        self.assertIn('(_m.get("git_pushed") or {}).get("remote") != remote_url', src)

    def test_failed_run_reason_is_shown(self):
        class R:
            def __init__(self, code, js):
                self.status_code, self._js = code, js

            def json(self):
                return self._js

        msg = ("The job was not started because recent account payments have failed or your spending "
               "limit needs to be increased.")

        def get(url, headers=None, timeout=None, params=None):
            if url.endswith("/runs/7/jobs"):
                return R(200, {"jobs": [{"name": "ui-tests", "conclusion": "failure", "steps": [],
                                         "check_run_url": "https://api/check-runs/9"}]})
            return R(200, [{"annotation_level": "failure", "message": msg},
                           {"annotation_level": "notice", "message": "ubuntu-latest moves"}])
        with mock.patch.object(E, "requests", types.SimpleNamespace(get=get)):
            out = E._github_run_failure_reasons("https://api/repos/me/r/actions", 7, {})
        self.assertEqual(out[0], "ui-tests: " + msg)
        self.assertIn("make the repo public", out[1])
        self.assertNotIn("ubuntu-latest", " ".join(out))

    def test_public_option_reaches_repo_creation(self):
        import inspect
        self.assertIn('"private": not public', inspect.getsource(E._github_ensure_repo))
        self.assertIn("public=public", inspect.getsource(E.push_to_git))
        self.assertIn('public=bool((git or {}).get("public"))', inspect.getsource(E.run_and_push))
        with open(os.path.join(ROOT, "automation.py"), encoding="utf-8") as f:
            self.assertIn('strings.t("auto_git_public")', f.read())
        for lang in LANGS:
            self.assertTrue(strings._STRINGS[lang].get("auto_git_public"), lang)

    def test_checkbox_and_flow_are_wired(self):
        with open(os.path.join(ROOT, "automation.py"), encoding="utf-8") as f:
            ui = f.read()
        with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
            app = f.read()
        self.assertIn('strings.t("auto_run_push")', ui)
        self.assertIn('app.creds["auto_run_push"]', ui)
        self.assertIn("E.run_and_push(project_dir, _tgt,", app)

    def test_baselines_are_per_os(self):
        self.assertIn("process.platform", A._with_batch2_js(A._PW_HEALER_JS).split("async visualDiff")[1][:1500])
        self.assertIn("key = key + '-' + process.platform", A._with_batch2_js(A._CY_TASKS_JS))
        self.assertIn('"win32"', E._sh_healer("com.q").split("private String visualDiff")[1][:2500])


if __name__ == "__main__":
    unittest.main()
