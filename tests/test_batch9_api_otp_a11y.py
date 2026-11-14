"""Batch 9: API steps (send a request, check status / body, keep a field for
later steps), two-factor login with {{OTP}} (RFC 6238 from OTP_SECRET) and
accessibility checks (axe-core) - parsed from the authored wording, run in
inspection / replay and emitted for Selenium, Playwright and Cypress."""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402
import automation_targets as A  # noqa: E402

NODE = shutil.which("node")
SEED = lambda *a, **k: ("css", "#x", True)  # noqa: E731
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"   # ASCII "12345678901234567890"


def _node(js):
    r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


class NodeDriver:
    """Just enough of a Selenium driver: scripts run in Node."""
    def __init__(self, url="about:blank", cookies=None):
        self.current_url = url
        self.cookies = cookies or []

    def get_cookies(self):
        return self.cookies

    def execute_script(self, script, *args):
        if script.strip() == "return navigator.userAgent":
            return "QA-Test-Agent"
        out = _node("const r = (function(){ %s }).apply(null, %s); console.log(JSON.stringify(r === undefined ? null : r));"
                    % (script, json.dumps(list(args))))
        return json.loads(out)


def _pack(*actions, expected=""):
    steps = [{"action": a} for a in actions]
    if expected:
        steps.append({"action": "Check it", "expected": expected})
    return E._apply_assertion_pack([], steps)


class Parsing(unittest.TestCase):
    def test_api_requests(self):
        self.assertEqual(E._page_action_in('Send a GET request to "/notes/api/health-check"'),
                         ("api_call", "GET|/notes/api/health-check||"))
        verb, value = E._page_action_in(
            'Send a POST request to "https://x.io/api/notes" with body {"title": "a|b", "tags": [1, 2]} '
            'and header "Authorization: Bearer {{token}}"')
        self.assertEqual(value, 'POST|https://x.io/api/notes|Authorization: Bearer {{token}}|'
                                '{"title": "a|b", "tags": [1, 2]}')
        self.assertEqual(E._page_action_in('Call the API DELETE "/api/notes/{{noteId}}"')[1],
                         "DELETE|/api/notes/{{noteId}}||")
        self.assertEqual(E._page_action_in('PUT "/api/users/1" with body "name=x"')[1], "PUT|/api/users/1||name=x")

    def test_plain_steps_are_not_api_calls(self):
        for a in ('Make sure you get "Welcome"', 'Click the "GET" button', 'Post a comment "Hello"'):
            self.assertIsNone(E._page_action_in(a), a)

    def test_api_and_a11y_checks(self):
        checks = E._page_checks_in('The response status is 201, the response field "data.id" exists, '
                                   'response field "message" is "Created" and the response contains "success"')
        self.assertEqual([(c, v) for c, v, _ in checks], [
            ("api_status", "201"), ("api_body", "field|data.id|exists|"),
            ("api_body", "field|message|is|Created"), ("api_body", "contains|success")])
        self.assertEqual(E._page_checks_in("Status code is 2xx")[0][:2], ("api_status", "2xx"))
        self.assertEqual([v for _, v, _ in E._page_checks_in(
            'The response field "data" is absent, response field "x" is missing and response field "y" is present')],
            ["field|data|absent|", "field|x|absent|", "field|y|exists|"])
        self.assertEqual(E._page_checks_in("The page has no accessibility violations")[0][:2], ("a11y", "serious"))
        self.assertEqual(E._page_checks_in("No critical accessibility issues")[0][:2], ("a11y", "critical"))
        self.assertEqual(E._page_checks_in("The order status is shipped"), [])

    def test_remember_a_response_field_and_otp(self):
        steps = [{"action": 'Save the response field "data.id" as {{noteId}}'},
                 {"action": 'Enter the authenticator code in the "Code" field'}]
        out = E._apply_assertion_pack(
            [{"role": "action", "verb": "type", "value": "123456", "target": "Code", "from_steps": [2]}], steps)
        self.assertEqual((out[0]["verb"], out[0]["value"], out[0]["remember_as"]), ("remember", "api:data.id", "noteId"))
        self.assertEqual(out[1]["value"], "{{OTP}}")
        self.assertFalse(E._needs_data_file([{"steps": steps + [{"action": 'Open "/notes/{{noteId}}"'},
                                                                {"action": "Type {{OTP}}"}]}]))

    def test_labels_never_need_a_locator(self):
        self.assertIn("api_call", E._PAGE_ACTIONS)
        for c in ("api_status", "api_body", "a11y"):
            self.assertIn(c, E._PAGE_CHECKS)
        self.assertEqual(E._page_action_label("api_call", "POST|/api/x||{}"), 'Send POST request to "/api/x" with a body')
        self.assertEqual(E._page_check_label("api_body", "field|data.id|is|5"), 'API response field "data.id" is "5"')
        self.assertEqual(E._page_check_label("a11y", "critical"), "no critical accessibility violations")


class NotALoginForm(unittest.TestCase):
    """API steps whose body mentions email / password are not the login form,
    and "Open https://other-site/2fa" is a real navigation, not the configured
    login page (both made the inspection skip steps and the replay fail)."""
    def tearDown(self):
        E._set_login_url("")

    def test_api_steps_are_not_login_actions(self):
        api = {"role": "action", "verb": "api_call", "target": 'Send POST request to "/notes/api/users/login"',
               "keywords": ["email", "{{email}}", "password"], "value": "POST|/x||{}"}
        self.assertFalse(E._is_login_flow_action(api))
        self.assertFalse(A._is_login_transition_action(api))
        self.assertFalse(E._case_has_explicit_login_flow([api, dict(api, verb="remember")]))

    def test_navigation_uses_the_configured_login_url(self):
        E._set_login_url("https://practice.expandtesting.com/login")
        nav = lambda url, t: {"role": "action", "verb": "navigate", "value": url, "target": t}  # noqa: E731
        other = nav("https://authenticationtest.com/totpChallenge/", "two-factor login page")
        self.assertFalse(E._is_login_flow_action(other))
        self.assertFalse(A._is_login_transition_action(other))
        self.assertTrue(E._is_login_flow_action(nav("https://practice.expandtesting.com/login/", "login page")))
        self.assertFalse(E._is_login_flow_action(nav("https://practice.expandtesting.com/notes/app", "Notes")))
        E._set_login_url("")
        self.assertTrue(E._is_login_flow_action(nav("https://x.io/a", "the login page")))   # wording, as before

    def test_replay_forgets_the_last_api_response(self):
        drv = mock.MagicMock()
        E._API_LAST[id(drv)] = {"status": 200, "body": "{}"}
        E._replay_reset_context(drv, {})
        self.assertNotIn(id(drv), E._API_LAST)


class FragmentedTextChecks(unittest.TestCase):
    def test_a_fragment_must_still_show_the_whole_sentence(self):
        steps = [{"expected": 'The URL contains "/loginSuccess" and "Success! You are now logged in!" is shown'}]
        it = {"role": "assertion", "check": "text_visible", "target": "Success!", "keywords": [], "from_steps": [1]}
        self.assertFalse(E._holds_authored_text({"text": "Sorry -- You have not successfully logged in."}, it, steps))
        self.assertTrue(E._holds_authored_text({"text": "Success! You are now logged in!"},
                                               dict(it, target="logged in"), steps))


class OneTimeCodes(unittest.TestCase):
    def test_rfc6238_vector(self):
        self.assertEqual(E._totp(RFC_SECRET, at=59), "287082")
        self.assertEqual(E._totp(RFC_SECRET, at=1111111109), "081804")
        self.assertEqual(E._totp(""), "")

    def test_otp_token_is_filled_from_the_environment(self):
        with mock.patch.dict(os.environ, {"OTP_SECRET": RFC_SECRET}):
            code = E._data_fill("{{OTP}}", {}, {})
            self.assertRegex(code, r"^\d{6}$")
            self.assertIn(code, (E._totp(RFC_SECRET), E._totp(RFC_SECRET, at=time.time() - 30)))
            self.assertRegex(E._fill_tokens("code {{otp}}"), r"^code \d{6}$")
        with mock.patch.dict(os.environ, {"OTP_SECRET": ""}):
            self.assertEqual(E._data_fill("{{OTP}}", {}, {}, login={"otp_secret": RFC_SECRET}),
                             E._totp(RFC_SECRET))

    def test_screen_secret_reaches_inspection_fills(self):
        # Inspection / replay fill {{OTP}} without the login dict: the secret
        # from the Automation screen must still be used (it typed "{{OTP}}").
        with mock.patch.dict(os.environ, {"OTP_SECRET": ""}):
            msgs = []
            E._set_run_otp({}, lambda m, k: msgs.append(k), [{"steps": [{"action": 'Type "{{OTP}}" in "Code"'}]}])
            self.assertEqual(msgs, ["warn"])
            E._set_run_otp({"otp_secret": RFC_SECRET})
            try:
                self.assertIn(E._data_fill("{{OTP}}", {}, {}),
                              (E._totp(RFC_SECRET), E._totp(RFC_SECRET, at=time.time() - 30)))
            finally:
                E._set_run_otp({})

    def test_read_env_value(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
            f.write('# x\n#OTP_SECRET=old\nOTP_SECRET="JBSWY3DPEHPK3PXP"\n')
        self.assertEqual(E.read_env_value(d, "OTP_SECRET"), "JBSWY3DPEHPK3PXP")
        self.assertEqual(E.read_env_value(d, "NOPE"), "")

    def test_opted_in_secret_is_written_to_env(self):
        d = tempfile.mkdtemp()
        E._write_dotenv(d, {"APP_USER": "", "OTP_SECRET": "JBSWY3DPEHPK3PXP"})
        self.assertEqual(E.read_env_value(d, "OTP_SECRET"), "JBSWY3DPEHPK3PXP")
        self.assertEqual(E.read_env_value(d, "APP_USER"), "")
        js = tempfile.mkdtemp()
        A._prepare_env(js, {"otp_secret": "JBSWY3DPEHPK3PXP"}, [])
        self.assertEqual(E.read_env_value(js, "OTP_SECRET"), "JBSWY3DPEHPK3PXP")
        js2 = tempfile.mkdtemp()
        A._prepare_env(js2, {}, [])                 # not opted in: left empty
        self.assertEqual(E.read_env_value(js2, "OTP_SECRET"), "")

    def test_app_wires_the_field(self):
        import ast
        main = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
        ast.parse(main)
        self.assertIn('self.auto_otp_secret = self.creds.get("auto_otp_secret", "")', main)
        self.assertIn('login["env_otp"] = self.auto_otp_secret.strip()', main)
        self.assertEqual(main.count('"otp_secret": (getattr(self, "auto_otp_secret", "") or "").strip()'), 2)
        ui = open(os.path.join(ROOT, "automation.py"), encoding="utf-8").read()
        self.assertIn('strings.t("auto_lbl_otp_secret"), "auto_otp_secret"', ui)
        self.assertIn('strings.t("auto_otp_note")', ui)
        import strings as S
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            self.assertTrue(S._STRINGS[lang].get("auto_lbl_otp_secret") and S._STRINGS[lang].get("auto_otp_note"))

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_code_matches_python(self):
        times = [59, 1111111109, 1234567890, 2000000000, int(time.time())]
        for secret in (RFC_SECRET, "JBSWY3DPEHPK3PXP", "jbsw y3dp ehpk 3pxp"):
            got = json.loads(_node("const f = %s; console.log(JSON.stringify(%s.map((t) => f(%s, t * 1000))));"
                                   % (E._TOTP_FN, json.dumps(times), json.dumps(secret))))
            self.assertEqual(got, [E._totp(secret, at=t) for t in times], secret)

    @unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "JDK not installed")
    def test_java_code_matches_python(self):
        healer = E._sh_healer("com.qastudio")
        start = healer.index("    static String totp(String secret)")
        body = healer[start:healer.index("\n    }\n", start) + 7]
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "T.java"), "w", encoding="utf-8") as f:
            f.write("public class T {\n%s\n public static void main(String[] a) { System.out.println(totp(a[0])); }\n}\n"
                    % body)
        subprocess.run(["javac", "-d", d, os.path.join(d, "T.java")], check=True, capture_output=True)
        for _ in range(2):        # a 30 s window can roll over between the two calls
            before = E._totp(RFC_SECRET)
            got = subprocess.run(["java", "-cp", d, "T", RFC_SECRET], capture_output=True, text=True).stdout.strip()
            if got == before or got == E._totp(RFC_SECRET):
                break
        self.assertIn(got, (before, E._totp(RFC_SECRET)))


class _Api(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _reply(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        _Api.seen.append(("GET", self.path, dict(self.headers), ""))
        if self.path == "/api/missing":
            return self._reply(404, {"success": False, "message": "Not found"})
        self._reply(200, {"success": True, "data": {"id": 42, "items": [{"name": "first"}]}})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        _Api.seen.append(("POST", self.path, dict(self.headers), body))
        self._reply(201, {"success": True, "data": {"id": "n-7", "title": json.loads(body).get("title")}})


@unittest.skipUnless(NODE, "node not installed")
class ApiInInspection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Api)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_request_checks_and_remember(self):
        drv = NodeDriver(self.base + "/app/page", cookies=[{"name": "sid", "value": "abc", "domain": "127.0.0.1"}])
        msg = E._run_page_action(drv, "api_call", 'POST|/api/notes|X-Trace: t1|{"title": "Buy milk"}')
        self.assertEqual(msg, "POST /api/notes -> 201")
        method, path, headers, body = _Api.seen[-1]
        self.assertEqual((method, path, json.loads(body)), ("POST", "/api/notes", {"title": "Buy milk"}))
        self.assertEqual((headers.get("Cookie"), headers.get("X-Trace"), headers.get("Content-Type")),
                         ("sid=abc", "t1", "application/json"))
        self.assertTrue(E._page_check_ok(drv, "api_status", "201"))
        self.assertTrue(E._page_check_ok(drv, "api_status", "2xx"))
        self.assertFalse(E._page_check_ok(drv, "api_status", "200"))
        self.assertTrue(E._page_check_ok(drv, "api_body", "field|data.title|is|Buy milk"))
        self.assertTrue(E._page_check_ok(drv, "api_body", "contains|buy MILK"))
        self.assertTrue(E._page_check_ok(drv, "api_body", "field|data.nope|absent|"))
        self.assertEqual(E._remember_read(drv, "api:data.id"), "n-7")

    def test_error_statuses_are_kept_not_raised(self):
        drv = NodeDriver(self.base + "/")
        E._run_page_action(drv, "api_call", "GET|%s/api/missing||" % self.base)
        self.assertTrue(E._page_check_ok(drv, "api_status", "404"))
        self.assertTrue(E._page_check_ok(drv, "api_body", "field|message|contains|not"))

    def test_json_paths(self):
        drv = NodeDriver(self.base + "/")
        E._run_page_action(drv, "api_call", "GET|/api/x||")
        self.assertEqual(E._api_field(drv, "data.items[0].name"), "first")
        self.assertEqual(E._api_field(drv, "$.data.items.0.name"), "first")
        self.assertEqual(json.loads(E._api_field(drv, "data.items")), [{"name": "first"}])
        self.assertEqual(E._api_field(drv, "data.nope"), "")

    def test_relative_url_needs_an_open_page(self):
        with self.assertRaises(ValueError):
            E._api_request(NodeDriver("about:blank"), "GET|/api/x||")


@unittest.skipUnless(NODE, "node not installed")
class Accessibility(unittest.TestCase):
    VIOLATIONS = json.dumps([{"id": "image-alt", "impact": "critical", "nodes": 2, "help": "x"},
                             {"id": "color-contrast", "impact": "serious", "nodes": 1, "help": "y"},
                             {"id": "region", "impact": "moderate", "nodes": 5, "help": "z"}])

    def _fails(self, raw, level):
        return _node("console.log((%s)(%s, %s))" % (E._A11Y_FAILS_FN, json.dumps(raw), json.dumps(level)))

    def test_levels(self):
        self.assertEqual(self._fails(self.VIOLATIONS, "critical"), "image-alt (critical, 2 elements)")
        self.assertEqual(self._fails(self.VIOLATIONS, "serious"),
                         "image-alt (critical, 2 elements), color-contrast (serious, 1 element)")
        self.assertIn("region (moderate, 5 elements)", self._fails(self.VIOLATIONS, "moderate"))
        self.assertEqual(self._fails("[]", "minor"), "")
        self.assertIn("scan failed", self._fails("ERR:axe-core is not loaded", "serious"))

    def test_run_function_reports_violations(self):
        out = _node("""
          global.document = {}; global.window = { axe: { run: () => Promise.resolve({ violations: [
            { id: 'label', impact: 'critical', nodes: [1], help: 'h' }, { id: 'list', impact: null, nodes: [], help: '' }] }) } };
          (%s)((r) => console.log(r));""" % E._A11Y_RUN_FN)
        self.assertEqual(json.loads(out)[0], {"id": "label", "impact": "critical", "nodes": 1, "help": "h"})
        self.assertEqual(json.loads(out)[1]["impact"], "minor")

    def test_axe_source_override(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "axe.js")
        with open(p, "w", encoding="utf-8") as f:
            f.write("window.axe = 1;")
        with mock.patch.dict(os.environ, {"AXE_SOURCE": p}):
            self.assertEqual(E._axe_source(), "window.axe = 1;")


class EmittedForEveryFramework(unittest.TestCase):
    def _all(self, intent):
        j, p, c = [], [], []
        E._emit_intent(j, "k1", intent)
        A._emit_pw_intent(p, "k1", intent, SEED, {})
        A._emit_cy_intent(c, "k1", intent, SEED, {})
        return "\n".join(j), "\n".join(p), "\n".join(c)

    def test_steps(self):
        j, p, c = self._all({"role": "action", "verb": "api_call", "target": "x", "value": "GET|/api/x||"})
        self.assertIn('heal.pageAction("api_call", "GET|/api/x||")', j)
        self.assertIn('heal.pageAction("api_call"', p)
        self.assertIn('cy.healPageAction("api_call"', c)
        j, p, c = self._all({"role": "assertion", "check": "a11y", "value": "serious", "target": "x"})
        self.assertIn('heal.assertPage("a11y", "serious")', j)
        self.assertIn('cy.healAssertPage("a11y", "serious")', c)
        j, p, c = self._all({"role": "action", "verb": "remember", "value": "api:data.id", "remember_as": "id",
                             "target": "x"})
        self.assertIn('heal.remember("api:data.id", "id")', j)

    def test_runtimes_implement_them(self):
        healer = E._sh_healer("com.qastudio")
        for s in ('case "api_call": apiCall(', '"a11y".equals(check)', 'pattern.startsWith("api:")',
                  '"otp".equals(key)', "static String totp(String secret)", "HmacSHA1"):
            self.assertIn(s, healer)
        self.assertNotIn("__API_JS__", healer)
        pw = A._with_batch2_js(A._PW_HEALER_JS)
        cy = A._with_batch2_js(A._CY_HEALER_JS)
        tasks = A._with_batch2_js(A._CY_TASKS_JS)
        for s in ("verb === 'api_call'", "check === 'a11y'", "async accessibilityViolations(level)",
                  "String(pattern).indexOf('api:') === 0", "key === 'otp'"):
            self.assertIn(s, pw)
        for s in ("verb === 'api_call'", "cy.task('qaAxeSource'", "String(pattern).indexOf('api:') === 0",
                  "key === 'otp'", "Cypress.env('OTP_SECRET')"):
            self.assertIn(s, cy)
        self.assertIn("qaAxeSource() { return qaAxeSource(); }", tasks)
        self.assertIn("qaAxeSource: tasks.qaAxeSource", A._CY_CONFIG)
        self.assertIn("OTP_SECRET: process.env.OTP_SECRET", A._CY_CONFIG)
        for text in (pw, cy, tasks):
            self.assertNotRegex(text, r"__[A-Z0-9_]+_(FN|CDN|VER)__")
        self.assertIn('"axe-core"', A._PKG_JSON)
        self.assertIn('"axe-core"', A._CY_PKG_JSON)
        self.assertIn("OTP_SECRET=", A._PW_ENV_EXAMPLE)

    @unittest.skipUnless(NODE, "node not installed")
    def test_generated_js_parses(self):
        d = tempfile.mkdtemp()
        for name, text in (("healer.js", A._with_batch2_js(A._PW_HEALER_JS)),
                           ("cy-tasks.js", A._with_batch2_js(A._CY_TASKS_JS))):
            path = os.path.join(d, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            r = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, name + ": " + r.stderr)

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_otp_fill_and_api_fn(self):
        pw = A._with_batch2_js(A._PW_HEALER_JS)
        consts = "\n".join(ln for ln in pw.splitlines()
                           if ln.startswith(("const API_FN", "const TOTP_FN", "const A11Y_", "const qaApiFn",
                                             "const qaTotp", "const qaA11yFails")))
        start = pw.index("function qaDataKey")
        fill = pw[start:pw.index("\n}\n", pw.index("function qaDataFill")) + 3]
        out = _node(consts + "\n" + fill + """
          process.env.OTP_SECRET = '%s';
          console.log(JSON.stringify([qaDataFill('{{OTP}}', {}, {}),
            qaApiFn(200, '{"a":{"b":[5]}}', 'field', 'a.b[0]'), qaApiFn(204, '', 'api_status', '2xx')]));""" % RFC_SECRET)
        code, field, ok = json.loads(out)
        self.assertIn(code, (E._totp(RFC_SECRET), E._totp(RFC_SECRET, at=time.time() - 30)))
        self.assertEqual((field, ok), ("5", True))


if __name__ == "__main__":
    unittest.main()
