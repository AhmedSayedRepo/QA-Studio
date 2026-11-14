"""Wait-and-scroll before actions + batch 6-8 (pagination, cookies / storage,
basic & digest auth).

Guards the behaviour in every place it lives - QA Studio's inspection and
replay, and the generated Selenium, Playwright and Cypress code - so a later
change can't quietly drop it from one framework (as happened before with the
in-place framework switch)."""
import glob
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402
import automation_targets as A  # noqa: E402

NEW_ACTIONS = ("next_page", "go_to_page", "set_cookie", "delete_cookies",
               "set_storage", "clear_storage", "auth_open")
NEW_CHECKS = ("page", "storage", "cookie")


def _healer_java():
    return E._sh_healer("com.qastudio")


def _pw():
    return A._with_batch2_js(A._PW_HEALER_JS)


def _cy():
    return A._with_batch2_js(A._CY_HEALER_JS)


class Parsing(unittest.TestCase):
    ACTIONS = {
        'Open "https://practice.expandtesting.com/basic-auth" with basic auth "admin" / "admin"':
            ("auth_open", "https://practice.expandtesting.com/basic-auth|admin|admin"),
        'Open "/digest-auth" with digest authentication as {{APP_USER}} / {{APP_PASS}}':
            ("auth_open", "/digest-auth|{{APP_USER}}|{{APP_PASS}}"),
        "Go to page 3": ("go_to_page", "3"),
        'Click page "2" in the pagination': ("go_to_page", "2"),
        "Go to the next page": ("next_page", ""),
        'Click Next page until "Jason" is shown': ("next_page", "Jason"),
        'Set cookie "theme" to "dark"': ("set_cookie", "theme=dark"),
        'Add a cookie "lang=en"': ("set_cookie", "lang=en"),
        "Delete all cookies": ("delete_cookies", ""),
        'Delete cookie "theme"': ("delete_cookies", "theme"),
        'Set local storage "token" to "abc"': ("set_storage", "local|token|abc"),
        'Set session storage "cart" to "2"': ("set_storage", "session|cart|2"),
        "Clear local storage": ("clear_storage", "local"),
        "Clear local and session storage": ("clear_storage", "all"),
    }

    def test_actions(self):
        for text, want in self.ACTIONS.items():
            self.assertEqual(E._page_action_in(text), want, text)

    def test_wizard_next_is_not_pagination(self):
        # "Click Next" in a multi-step form is an ordinary click.
        self.assertIsNone(E._page_action_in("Click Next"))
        self.assertIsNone(E._page_action_in('Click "Next" button'))

    def test_checks(self):
        cases = {
            "Page 3 is the current page": [("page", "3")],
            "The current page is 2": [("page", "2")],
            'Cookie "theme" is "dark"': [("cookie", "theme|is|dark")],
            'Cookie "theme" exists': [("cookie", "theme|exists|")],
            'Cookie "theme" is deleted': [("cookie", "theme|absent|")],
            'Local storage "token" contains "ab"': [("storage", "local|token|contains|ab")],
            'Session storage "cart" is removed': [("storage", "session|cart|absent|")],
        }
        for text, want in cases.items():
            self.assertEqual([(c, v) for c, v, _ in E._page_checks_in(text)], want, text)

    def test_value_phrase_does_not_leave_a_text_assertion(self):
        # 'Cookie "theme" is "dark"' must not also expect "dark" on the page.
        raw = [{"action": 'Set cookie "theme" to "dark"', "expected": 'Cookie "theme" is "dark"'}]
        ai = [{"role": "action", "verb": "click", "target": "theme", "from_steps": [1]},
              {"role": "assertion", "check": "", "target": "dark", "keywords": ["dark"],
               "expected": 'Text "dark" is shown', "from_steps": [1]}]
        out = E._apply_assertion_pack(ai, raw)
        self.assertEqual([(i["role"], i.get("verb") or i.get("check")) for i in out],
                         [("action", "set_cookie"), ("assertion", "cookie")])

    def test_labels_never_show_the_password(self):
        label = E._page_action_label("auth_open", "https://x/basic-auth|admin|S3cret!")
        self.assertNotIn("S3cret", label)
        self.assertIn("admin", label)
        for v in NEW_ACTIONS:
            self.assertTrue(E._page_action_label(v, "a|b|c"))
        for c in NEW_CHECKS:
            self.assertNotEqual(E._page_check_label(c, "a|is|b"), "%s %s" % (c, "a|is|b"))


class PythonRuntime(unittest.TestCase):
    def test_auth_url(self):
        self.assertEqual(E._auth_url("/basic-auth", "ad min", "p@ss", "https://h.com/x?y=1"),
                         "https://ad%20min:p%40ss@h.com/basic-auth")
        self.assertEqual(E._auth_url("https://old:o@h.com/d", "a", "b"), "https://a:b@h.com/d")

    def test_fill_tokens(self):
        self.assertEqual(E._fill_tokens("/a|{{app_user}}|{{ APP_PASS }}", {"user": "u", "password": "p"}),
                         "/a|u|p")
        self.assertEqual(E._fill_tokens("/a|{{APP_USER}}|x", {}), "/a|{{APP_USER}}|x")

    def test_cookie_ok(self):
        self.assertTrue(E._cookie_ok("Dark", "theme|is|dark"))
        self.assertTrue(E._cookie_ok("darkest", "theme|contains|dark"))
        self.assertTrue(E._cookie_ok("", "theme|exists|"))
        self.assertFalse(E._cookie_ok(None, "theme|exists|"))
        self.assertTrue(E._cookie_ok(None, "theme|absent|"))
        self.assertFalse(E._cookie_ok("x", "theme|absent|"))

    def test_ready_modes(self):
        self.assertEqual(E._ready_mode("click"), "click")
        self.assertEqual(E._ready_mode("type"), "input")
        self.assertEqual(E._ready_mode("set_value"), "view")
        self.assertIsNone(E._ready_mode("upload"))

    def test_wait_ready_waits_for_cover_and_movement_to_stop(self):
        seq = iter(["covered|1", "ok|1,1,5,5", "ok|1,1,9,9", "ok|1,1,9,9", "ok|never"])

        class D:
            calls = 0

            def execute_script(self, js, *a):
                D.calls += 1
                return next(seq)
        self.assertEqual(E._wait_ready(D(), object(), "click"), "ok")
        self.assertEqual(D.calls, 4)

    def test_wait_ready_gives_up_and_lets_the_action_run(self):
        class D:
            def execute_script(self, js, *a):
                return "covered|1"
        old = E._READY_COVERED_S
        E._READY_COVERED_S = 0.3
        try:
            self.assertEqual(E._wait_ready(D(), object(), "click"), "covered")
        finally:
            E._READY_COVERED_S = old
        self.assertEqual(E._wait_ready(D(), object(), "upload"), "ok")   # no wait for uploads

    def test_scroll_search_finds_lazy_content_and_resets_when_missing(self):
        class D:
            def __init__(self):
                self.log = []

            def execute_script(self, js, *a):
                self.log.append(a[0] if a else js)
                return "moved"
        d, n = D(), {"i": 0}

        def probe():
            n["i"] += 1
            return "EL" if n["i"] == 3 else None
        old = E.time.sleep
        E.time.sleep = lambda s: None
        try:
            self.assertEqual(E._scroll_search(d, probe), "EL")
            d2 = D()
            self.assertIsNone(E._scroll_search(d2, lambda: None, steps=4))
        finally:
            E.time.sleep = old
        self.assertEqual(d2.log[-1], True)          # back to the top

    def test_inspection_and_replay_wait_and_scroll(self):
        src = inspect.getsource(E.explore_and_map)
        act = src[src.index("def _act("):src.index("def _act(") + 2500]
        self.assertIn("_scroll_search(", act)
        self.assertIn("_wait_ready(driver, live, verb", act)
        rep = inspect.getsource(E.replay_sequenced)
        self.assertIn("_wait_ready(driver, el, verb)", rep)
        self.assertIn("_scroll_search(driver, any_hit)", rep)
        for fn in (E.explore_and_map, E.replay_sequenced):
            self.assertIn("_page_check_ok(driver", inspect.getsource(fn))
            self.assertIn("login=login", inspect.getsource(fn))


class GeneratedCode(unittest.TestCase):
    def test_selenium_waits_and_scrolls_before_every_action(self):
        j = _healer_java()
        act = j[j.index("public void act("):]
        self.assertLess(act.index("WebElement el = find("), act.index("ready(el, verb);"))
        self.assertLess(act.index("ready(el, verb);"), act.index("switch (verb == null"))
        find = j[j.index("public WebElement find("):j.index("public void act(")]
        self.assertLess(find.index("scrollSearch(cached, seed)"), find.index("if (Config.HEAL)"))
        self.assertNotIn("__READY", j)
        self.assertNotIn("__SCROLL", j)
        self.assertNotIn("__PAGINATE", j)

    def test_playwright_waits_and_scrolls_before_every_action(self):
        h = _pw()
        act = h[h.index("  async act(key, verb"):]
        self.assertLess(act.index("this.resolve("), act.index("await this._ready(el, verb);"))
        self.assertLess(act.index("await this._ready(el, verb);"), act.index("switch (verb)"))
        res = h[h.index("  async resolve("):h.index("  async _scrollStep(")]
        self.assertLess(res.index("this._scrollSearch("), res.index("if (HEAL)"))
        self.assertNotIn("__READY", h)

    def test_cypress_waits_and_scrolls_before_every_action(self):
        c = _cy()
        self.assertIn("cy.healSelector(key, seed, intent).then((sel) => _ready(sel, verb)).then((sel) => {", c)
        sel = c[c.index("Cypress.Commands.add('healSelector'"):c.index("function _scrollStep(")]
        self.assertLess(sel.index("_scrollSearch("), sel.index("_healWithAI(key, intent)"))
        self.assertNotIn("__READY", c)

    def test_every_page_action_and_check_runs_in_every_framework(self):
        j, h, c = _healer_java(), _pw(), _cy()
        pa_j = j[j.index("public void pageAction("):]
        pa_h = h[h.index("  async pageAction("):]
        pa_c = c[c.index("Cypress.Commands.add('healPageAction'"):]
        for verb in E._PAGE_ACTIONS:
            if verb == "remember":
                continue
            self.assertIn('"%s"' % verb, pa_j, "Selenium " + verb)
            if verb == "dismiss_if_present":
                continue            # the JS frameworks' default branch
            self.assertIn("'%s'" % verb, pa_h, "Playwright " + verb)
            self.assertIn("'%s'" % verb, pa_c, "Cypress " + verb)
        # Cookies are read natively (HttpOnly too) in every framework.
        self.assertIn('if ("cookie".equals(check)) return assertCookie(value);', j)
        self.assertIn("this.page.context().cookies()", h)
        self.assertIn("cy.getCookie(name", c)
        self.assertIn("network.http.phishy-userpass-length", E._driver_factory("com.qastudio"))

    def test_emitters_route_new_steps_without_locators(self):
        for verb in NEW_ACTIONS:
            it = {"role": "action", "verb": verb, "value": "x", "target": "t"}
            pw, cy = [], []
            A._emit_pw_intent(pw, "k", dict(it), lambda *a, **k: ("css", "#x", True), {})
            A._emit_cy_intent(cy, "k", dict(it), lambda *a, **k: ("css", "#x", True), {})
            self.assertIn("heal.pageAction(%s" % json.dumps(verb), "\n".join(pw))
            self.assertIn("cy.healPageAction(%s" % json.dumps(verb), "\n".join(cy))


NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node not installed")
class SharedScriptsBehave(unittest.TestCase):
    """Run the shared browser scripts in node against tiny fake DOMs."""

    def _run(self, body):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.js")
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            r = subprocess.run([NODE, p], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_ready_states(self):
        out = self._run("""
const READY = %s;
function mk(o) {
  const el = Object.assign({ isConnected: true, disabled: false, labels: [], scrolled: false,
    getAttribute: () => null, contains: (t) => t === el,
    scrollIntoView() { el.scrolled = true; } }, o.el || {});
  const rect = Object.assign({ left: 10, top: 10, width: 50, height: 20, right: 60, bottom: 30 }, o.rect || {});
  el.getBoundingClientRect = () => rect;
  const top = o.top === 'self' || !o.top ? el : o.top;
  el.ownerDocument = { documentElement: {}, elementFromPoint: () => top,
    defaultView: { innerWidth: 800, innerHeight: 600,
      getComputedStyle: () => Object.assign({ visibility: 'visible', display: 'block' }, o.style || {}) } };
  return el;
}
const cover = { shadowRoot: null, contains: () => false };
const label = { shadowRoot: null, contains: (t) => t === label };
const off = mk({ rect: { top: 900, bottom: 920 } });
const res = {
  ok: READY(mk({}), 'click'),
  hidden: READY(mk({ style: { display: 'none' } }), 'click'),
  disabled: READY(mk({ el: { disabled: true } }), 'input'),
  viewIgnoresDisabled: READY(mk({ el: { disabled: true } }), 'view'),
  offscreen: READY(off, 'click'), offScrolled: off.scrolled,
  covered: READY(mk({ top: cover }), 'click'),
  typingIgnoresCover: READY(mk({ top: cover }), 'input'),
  labelOk: READY(mk({ top: label, el: { labels: [label] } }), 'click'),
  gone: READY(mk({ el: { isConnected: false } }), 'click'),
};
console.log(JSON.stringify(Object.fromEntries(Object.entries(res).map(([k, v]) => [k, typeof v === 'string' ? v.split('|')[0] : v]))));
""" % E._READY_FN)
        self.assertEqual(out, {"ok": "ok", "hidden": "hidden", "disabled": "disabled",
                               "viewIgnoresDisabled": "ok", "offscreen": "offscreen", "offScrolled": True,
                               "covered": "covered", "typingIgnoresCover": "ok", "labelOk": "ok",
                               "gone": "gone"})

    def test_pagination_next_number_and_last_page(self):
        out = self._run("""
const PAG = %s;
function link(text, o) {
  o = o || {};
  const el = { innerText: text, className: '', disabled: false, clicked: 0,
    attrs: o.attrs || {}, getAttribute(n) { return this.attrs[n] == null ? null : this.attrs[n]; },
    getBoundingClientRect: () => ({ width: 20, height: 20 }), scrollIntoView() {},
    click() { this.clicked++; },
    closest(sel) { return sel === 'li' ? (o.li || null) : (o.inBox ? {} : null); } };
  return el;
}
function doc(els) {
  return { querySelectorAll: () => els,
    defaultView: { getComputedStyle: () => ({ visibility: 'visible', display: 'block' }) } };
}
const wizardNext = link('Next');
const pagerNext = link('Next', { inBox: true });
const two = link('2', { inBox: true });
const d1 = doc([wizardNext, two, pagerNext]);
const r1 = PAG('next', d1), r2 = PAG('2', d1);
const lastNext = link('\\u00bb', { inBox: true, li: { className: 'page-item disabled' } });
const r3 = PAG('next', doc([lastNext]));
const r4 = PAG('next', doc([link('Home')]));
const r5 = PAG('next', doc([link('', { inBox: true, attrs: { 'aria-label': 'Next page' } })]));
console.log(JSON.stringify({ r1, pagerClicked: pagerNext.clicked, wizardClicked: wizardNext.clicked,
  r2, twoClicked: two.clicked, r3, lastClicked: lastNext.clicked, r4, r5 }));
""" % E._PAGINATE_FN)
        self.assertEqual(out["pagerClicked"], 1)      # the pager's Next, not the wizard's
        self.assertEqual(out["wizardClicked"], 0)
        self.assertEqual((out["r2"], out["twoClicked"]), ("2", 1))
        self.assertEqual((out["r3"], out["lastClicked"]), ("end", 0))
        self.assertEqual(out["r4"], "")
        self.assertTrue(out["r5"])

    def test_storage_and_cookie_checks(self):
        out = self._run("""
const CHECK = %s, STORE = %s;
const mem = (init) => { const m = Object.assign({}, init); return {
  getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, clear: () => { for (const k in m) delete m[k]; } }; };
const win = { localStorage: mem({ token: 'abc123' }), sessionStorage: mem({}) };
const doc = { defaultView: win, cookie: 'theme=dark; lang=en%%20US' };
const loc = { href: 'https://x/list?page=3' };
const r = {};
r.is = CHECK('storage', 'local|token|is|abc123', doc, loc);
r.contains = CHECK('storage', 'local|token|contains|c12', doc, loc);
r.missing = CHECK('storage', 'session|cart|exists', doc, loc);
r.absent = CHECK('storage', 'session|cart|absent', doc, loc);
STORE('set', 'session', 'cart', '2', win);
r.afterSet = CHECK('storage', 'session|cart|is|2', doc, loc);
STORE('clear', 'all', '', '', win);
r.afterClear = CHECK('storage', 'local|token|absent', doc, loc);
r.cookie = CHECK('cookie', 'theme|is|dark', doc, loc);
r.cookieDecoded = CHECK('cookie', 'lang|is|en us', doc, loc);
r.cookieAbsent = CHECK('cookie', 'nope|absent|', doc, loc);
doc.querySelectorAll = () => [];
r.pageFromUrl = CHECK('page', '3', doc, loc);
r.pageWrong = CHECK('page', '4', doc, loc);
console.log(JSON.stringify(r));
""" % (E._PAGE_CHECK_FN, E._STORAGE_FN))
        self.assertEqual(out, {"is": True, "contains": True, "missing": False, "absent": True,
                               "afterSet": True, "afterClear": True, "cookie": True,
                               "cookieDecoded": True, "cookieAbsent": True,
                               "pageFromUrl": True, "pageWrong": False})


if __name__ == "__main__":
    unittest.main()


class ReportFixes(unittest.TestCase):
    """Fixes from the Chrome IntelliJ run and the more-components inspection."""

    def test_generated_chrome_matches_inspection_chrome(self):
        df = E._driver_factory("com.qastudio")
        chromium = df[df.index("private static void chromium("):]
        for needle in ('"profile.password_manager_leak_detection", false',
                       '"credentials_enable_service", false',
                       "--disable-features=PasswordLeakDetection",
                       "UnexpectedAlertBehaviour.IGNORE"):
            self.assertIn(needle, chromium)
        self.assertIn("import org.openqa.selenium.UnexpectedAlertBehaviour;", df)

    def test_upload_timestamp_links_use_the_stable_name(self):
        el = {"tag": "a", "text": "1794528885455_qastudio-upload.txt",
              "testid": "1794528885455_qastudio-upload.txt",
              "css": "#core > div.row > div.d-flex.mb-2:nth-of-type(92) > a:nth-of-type(1)"}
        cands = [c["value"] for c in E._locator_candidates(el)]
        self.assertTrue(cands)
        self.assertTrue(all("qastudio-upload.txt" in c and "1794528885455" not in c for c in cands), cands)
        self.assertFalse(any("nth-of-type" in c for c in cands))
        self.assertNotIn("1794528885455", E._to_locator(el)["value"])
        # Ordinary text keeps its exact locator.
        self.assertIsNone(E._stable_text("Login"))

    def test_case_opening_the_login_page_runs_logged_out(self):
        self.assertEqual(E._infer_page_context(
            {"title": "Check element attributes",
             "steps": [{"action": "Open https://practice.expandtesting.com/login"}]}), "login")
        self.assertEqual(E._infer_page_context(
            {"title": "x", "steps": [{"action": "Open https://practice.expandtesting.com/inputs"}]}), "app")

    def test_attribute_check_covers_all_its_phrases(self):
        (c, v, p), = E._page_checks_in('The "Login" button has "type" equal to "submit"')
        self.assertEqual((c, v), ("attr", "Login|type|is|submit"))
        self.assertEqual(set(p), {"Login", "type", "submit"})

    def test_disappearance_checks_wait_in_every_runner(self):
        j = E._sh_healer("com.qastudio")
        hid = j[j.index("public boolean assertHidden("):][:900]
        self.assertIn("System.currentTimeMillis() + 10000", hid)
        h = _pw()
        self.assertIn("evaluateAll(", h[h.index("  async assertHidden("):][:900])
        c = _cy()
        cy_hid = c[c.index("Cypress.Commands.add('healAssertHidden'"):][:900]
        self.assertIn("poll(40)", cy_hid)
        self.assertNotIn("_has(seed)", cy_hid)          # gone already = pass
        rep = inspect.getsource(E.replay_sequenced)
        self.assertIn('return ("" if gone else "still-visible"), False', rep)

    def test_progress_window_is_generated_kept_and_hooked(self):
        src = inspect.getsource(E)
        self.assertIn('"ProgressWindow"}', src.replace("\n", "").replace(" ", "").replace('"VideoRecorder","ProgressWindow"}', '"ProgressWindow"}'))
        self.assertIn('_w(os.path.join(src_main, "ProgressWindow.java"), _progress_window(pkg))', src)
        pw = E._progress_window("com.qastudio")
        self.assertIn("setFocusableWindowState(false)", pw)     # never steals typing
        self.assertIn("GraphicsEnvironment.isHeadless()", pw)
        self.assertIn("frame.dispose()", pw)                     # lets the JVM exit
        rm = E._sh_report_mailer("com.qastudio") if hasattr(E, "_sh_report_mailer") else src
        for hook in ("ProgressWindow.start(", "ProgressWindow.testStarted(", 'ProgressWindow.testDone("failed", browserOf(r))',
                     'ProgressWindow.testDone("passed", browserOf(r))', 'ProgressWindow.testDone("skipped", browserOf(r))',
                     "ProgressWindow.finish("):
            self.assertIn(hook, rm)

    def test_activity_label_never_wraps(self):
        with open(os.path.join(ROOT, "automation.py"), encoding="utf-8") as f:
            ui = f.read()
        self.assertIn('ft.Text(strings.t("auto_activity"), size=11, weight=ft.FontWeight.BOLD,\n'
                      '                           color=T.INK_3, expand=True, no_wrap=True, max_lines=1)', ui)


class EdgeDriverHost(unittest.TestCase):
    def test_edge_uses_the_new_microsoft_host(self):
        df = E._driver_factory("com.qastudio")
        edge = df[df.index('} else if ("edge".equals(b)) {'):]
        self.assertIn('System.setProperty("SE_DRIVER_MIRROR_URL", "https://msedgedriver.microsoft.com");', edge)
        self.assertIn("wdm.edgeDownloadUrlPattern", edge)
        self.assertIn('Config.setting("EDGE_DRIVER", "")', edge)
        self.assertNotIn("https://msedgedriver.azureedge.net", df)
        self.assertIn("edge.driver=C:/path/to/msedgedriver.exe", df)


class EnabledStateWaits(unittest.TestCase):
    def test_enabled_checks_wait_everywhere(self):
        j = E._sh_healer("com.qastudio")
        self.assertIn("Duration.ofSeconds(10)).until(d -> el.isEnabled() == enabled)", j)
        h = _pw()
        self.assertIn("if ((await el.isEnabled()) === enabled) return true;", h)
        self.assertIn("if not poll(_state_ok, min(wait, 10)):", inspect.getsource(E.replay_sequenced))

    def test_merged_assertion_takes_its_own_rows_locator(self):
        src = inspect.getsource(E)
        self.assertIn("if is_check and step_no in action_rows:", src)
        self.assertIn("steps_for.reverse()", src)


class ProgressWindowUi(unittest.TestCase):
    def test_minimize_compact_and_hide(self):
        pw = E._progress_window("com.qastudio")
        self.assertIn("frame.setExtendedState(Frame.ICONIFIED)", pw)          # minimize to the taskbar
        # a non-focusable window has no taskbar button: rebuilt as focusable before minimizing
        self.assertIn("frame.dispose();\n            frame.setExtendedState(Frame.ICONIFIED);", pw)
        self.assertIn("frame.setFocusableWindowState(true);", pw)     # so it can be restored there
        self.assertIn("toggleCompact()", pw)                          # one-line view
        self.assertIn("frame.setAutoRequestFocus(false)", pw)         # never steals typing on show
        self.assertIn("setUndecorated(true)", pw)
        self.assertNotIn("__PKG__", pw)


class VolatileCacheReinspect(unittest.TestCase):
    PLAN = {"steps": [{"locator": {"by": "css", "value": '[data-testid="1794528885455_qastudio-upload.txt"]',
                                   "fallbacks": [{"by": "xpath",
                                                  "value": '//a[normalize-space()="1794528885455_qastudio-upload.txt"]'}]}}]}

    def test_saved_upload_timestamp_locator_is_reinspected(self):
        self.assertTrue(E._plan_has_volatile_locator(self.PLAN))
        stable = {"steps": [{"locator": {"by": "xpath",
                                         "value": '(//a[contains(normalize-space(), "qastudio-upload.txt")])[last()]'}}]}
        self.assertFalse(E._plan_has_volatile_locator(stable))
        # Ordinary values keep the case reused: prices, short ids, headings.
        self.assertFalse(E._plan_has_volatile_locator(
            {"locator": {"by": "xpath", "value": '//td[normalize-space()="$100.00"]'}}))

    def test_split_cached_cases_skips_the_volatile_case(self):
        tmp = tempfile.mkdtemp()
        tc_vol, tc_ok = {"title": "upload", "steps": []}, {"title": "ok", "steps": []}
        fp = lambda tc: E._case_fingerprint(tc, "https://x", "https://x/login")
        os.makedirs(os.path.join(tmp, ".qastudio"))
        ok_plan = {"steps": [{"locator": {"by": "id", "value": "submit"}}]}
        with open(E._verified_cache_path(tmp), "w", encoding="utf-8") as f:
            json.dump({"cases": {fp(tc_vol): {"plan": self.PLAN}, fp(tc_ok): {"plan": ok_plan}}}, f)
        old = E._plan_fully_verified
        E._plan_fully_verified = lambda plan: True
        try:
            walk, reused = E.split_cached_cases([{"test_cases": [tc_vol, tc_ok]}], tmp,
                                                "https://x", "https://x/login")
        finally:
            E._plan_fully_verified = old
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(reused, 1)
        self.assertEqual([t["title"] for p in walk for t in p["test_cases"]], ["upload"])

    def test_proof_accepts_the_stable_last_link_among_several_uploads(self):
        el = {"tag": "a", "text": "1794618849769_qastudio-upload.txt", "xpath": "/html/body/a[3]"}

        class Hit:
            def __init__(self, t): self.text = t
            def get_attribute(self, n): return self.text
            def is_displayed(self): return True
        links = [Hit("1794618524857_qastudio-upload.txt"), Hit("1794618849769_qastudio-upload.txt"),
                 Hit("1794619192554_qastudio-upload.txt")]

        class D:
            def find_elements(self, by, value):
                if value == el["xpath"]:
                    return [links[1]]                     # bound: a middle upload
                if value.endswith("[last()]"):
                    return [links[-1]]
                if value.endswith("[1]"):
                    return [links[0]]
                return links

        class By:
            XPATH, CSS_SELECTOR, ID, NAME = "xpath", "css", "id", "name"
        got = E._prove_locator(D(), el, By)
        self.assertTrue(got["verified"])
        self.assertTrue(got["value"].endswith("[last()]"))
        self.assertIn("qastudio-upload.txt", got["value"])


class ChecksDontInheritActionLocators(unittest.TestCase):
    """EB07: 'Click the Remove button' (row locator #checkbox-example button)
    with expected 'the checkbox disappears' bound the disappearance check to
    the button, which stays on screen ('still-visible')."""

    def _compile(self, rows, ai_intents):
        tc = {"title": "t", "steps": rows}
        old = E.ai_complete
        E.ai_complete = lambda *a, **k: json.dumps({"intents": ai_intents})
        try:
            return E.compile_test_case(tc, log=lambda *a, **k: None)
        finally:
            E.ai_complete = old

    def test_eb07_rows(self):
        rows = [
            {"action": "Open https://practice.expandtesting.com/dynamic-controls", "expected": ""},
            {"action": 'Click the "Remove" button', "expected": 'The "A checkbox" checkbox disappears',
             "preferred_locator": "#checkbox-example button"},
            {"action": "Check the message", "expected": '"It\'s gone!" is shown'},
            {"action": 'Click the "Enable" button', "expected": '"It\'s enabled!" is shown',
             "preferred_locator": "#input-example button"},
            {"action": "Check the text field", "expected": "The text field is enabled",
             "preferred_locator": "#input-example input"},
        ]
        intents = [
            {"role": "action", "verb": "navigate", "target": "page", "from_steps": [1]},
            {"role": "action", "verb": "click", "target": "Remove button", "keywords": ["Remove"], "from_steps": [2]},
            {"role": "assertion", "target": "checkbox", "keywords": ["A checkbox"], "from_steps": [2, 3]},
            {"role": "action", "verb": "click", "target": "Enable button", "keywords": ["Enable"], "from_steps": [4]},
            {"role": "assertion", "check": "enabled", "target": "text field", "keywords": ["text field"],
             "from_steps": [4, 5]},
        ]
        out = self._compile(rows, intents)
        by_target = {i.get("target"): i for i in out}
        self.assertEqual(by_target["Remove button"].get("preferred_locator"), "#checkbox-example button")
        self.assertFalse(by_target["checkbox"].get("preferred_locator"))
        self.assertEqual(by_target["Enable button"].get("preferred_locator"), "#input-example button")
        self.assertEqual(by_target["text field"].get("preferred_locator"), "#input-example input")


class ProofSummaryAndPauseTone(unittest.TestCase):
    def test_disappears_checks_are_not_warnings(self):
        payload = [{"story": {"id": "s"}, "test_cases": [{"title": "EB07", "steps": [
            {"locator": {"by": "css", "value": "#checkbox-example button", "verified": True}},
            {"assert_locator": {"by": "css", "value": "#checkbox > input", "verified": False,
                                "reason": "disappears"}},
            {"assert_locator": {"by": "css", "value": "#x", "verified": False, "reason": "not-unique"}},
        ]}]}]
        p = E._proof_summary(payload)
        self.assertEqual(p["verified"], 1)
        self.assertEqual([u["reason"] for u in p["unverified"]], ["not-unique"])
        self.assertEqual([r["case"] for r in p["replay_checked"]], ["EB07"])
        src = inspect.getsource(E.explore_and_map)
        self.assertIn('captured["reason"] = "disappears"', src)
        self.assertIn('are confirmed by the replay instead', src)

    def test_pause_and_resume_have_their_own_colour(self):
        with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
            m = f.read()
        self.assertIn('"then Resume — or Stop to abort.", "pause")', m)
        self.assertIn('self._auto_logmsg(strings.t("main_resuming"), "pause")', m)
        self.assertIn('"pause": _PAUSE_COLOR}', m)
        import log_view
        self.assertNotIn("pause", log_view.ISSUE_TONES)       # not counted as an issue


class RecentReportEmails(unittest.TestCase):
    def _app(self):
        class App:
            creds = {}
        return App()

    @staticmethod
    def _helpers():
        try:
            import regression as R             # the real module (needs Flet)
            return R
        except ImportError:                    # no Flet here: just the helpers
            with open(os.path.join(ROOT, "regression.py"), encoding="utf-8") as f:
                src = f.read()
            block = src[src.index("_RECENT_EMAILS_KEY = "):src.index("def email_recipient_picker(")]
            ns = {}
            exec(block, ns)
            return types.SimpleNamespace(**{k: v for k, v in ns.items() if not k.startswith("__")})

    def test_typed_emails_are_remembered_newest_first_and_capped(self):
        R = self._helpers()
        saved = []
        fake = types.ModuleType("store")
        fake.save = lambda d: saved.append(list(d.get("email_recent") or []))
        real = sys.modules.get("store")
        sys.modules["store"] = fake           # never touch the real settings file
        try:
            app = self._app()
            R._remember_email(app, "a@x.com")
            R._remember_email(app, "b@x.com")
            R._remember_email(app, "A@X.com")              # same address, other case
            self.assertEqual(R._recent_emails(app), ["A@X.com", "b@x.com"])
            for i in range(30):
                R._remember_email(app, "u%d@x.com" % i)
            self.assertEqual(len(R._recent_emails(app)), R._RECENT_EMAILS_MAX)
            self.assertEqual(R._recent_emails(app)[0], "u29@x.com")
            n = len(saved)
            R._remember_email(app, "u29@x.com")             # already newest: no save
            self.assertEqual(len(saved), n)
        finally:
            if real is not None:
                sys.modules["store"] = real
            else:
                sys.modules.pop("store", None)

    def test_picker_offers_recent_emails_and_remembers_typed_ones(self):
        with open(os.path.join(ROOT, "regression.py"), encoding="utf-8") as f:
            src = f.read()
        picker = src[src.index("def email_recipient_picker("):src.index("def resource_name_picker(")]
        self.assertIn('strings.t("reg_email_recent")', picker)
        self.assertIn("_remember_email(app, raw)", picker)
        import strings as S
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            self.assertIn("reg_email_recent", S._STRINGS[lang])


class Batch68FirstRunFixes(unittest.TestCase):
    """From the first real run of expandtesting-batch6-8-test-cases.csv."""

    def test_storage_set_runs_before_its_check(self):
        rows = [{"action": "Open https://practice.expandtesting.com", "expected": ""},
                {"action": 'Set session storage "qa_cart" to "2"', "expected": 'Session storage "qa_cart" is "2"'}]
        ai = [{"role": "action", "verb": "navigate", "target": "practice page", "from_steps": [1]},
              {"role": "precondition", "target": "set session storage qa_cart", "from_steps": [2]},
              {"role": "assertion", "target": "qa_cart", "keywords": ["qa_cart", "2"], "from_steps": [2]}]
        old = E.ai_complete
        E.ai_complete = lambda *a, **k: json.dumps({"intents": ai})
        try:
            out = E.compile_test_case({"title": "t", "steps": rows}, log=lambda *a, **k: None)
        finally:
            E.ai_complete = old
        step2 = [(i["role"], i.get("verb") or i.get("check")) for i in out if 2 in (i.get("from_steps") or [])]
        self.assertEqual(step2, [("action", "set_storage"), ("assertion", "storage")])

    def test_url_phrase_is_not_split_into_page_text(self):
        steps = [{"expected": ""}, {"expected": 'The URL contains "/digest-auth". "Congratulations" is shown'}]
        it = {"role": "assertion", "target": "digest auth success", "keywords": [],
              "expected": 'The URL contains "/digest-auth". "Congratulations" is shown', "from_steps": [2]}
        parts = E._split_by_quoted_expected(it, steps)
        self.assertEqual([p["target"] for p in parts], ["Congratulations"])

    def test_table_cell_text_can_be_checked_as_page_text(self):
        steps = [{"expected": '"Alice Johnson" is shown'},
                 {"expected": 'Page 3 is the current page. "John Doe" is shown'}]
        self.assertEqual(E._page_text_phrase(
            {"role": "assertion", "target": "Alice Johnson", "from_steps": [1]}, steps), "Alice Johnson")
        self.assertEqual(E._page_text_phrase(
            {"role": "assertion", "target": "student", "keywords": ["John Doe"], "from_steps": [2]}, steps),
            "John Doe")
        # Not for field values / states, and not for text the author didn't quote.
        self.assertIsNone(E._page_text_phrase(
            {"role": "assertion", "check": "value", "target": "Alice Johnson", "from_steps": [1]}, steps))
        self.assertIsNone(E._page_text_phrase(
            {"role": "assertion", "target": "Bob", "from_steps": [1]}, steps))
        src = inspect.getsource(E.explore_and_map)
        self.assertIn('it.update({"check": "pattern", "value": _re_literal(_pt),', src)
        self.assertEqual(E._re_literal("Alice Johnson"), "Alice Johnson")
        self.assertEqual(E._re_literal("$100.00 (due)"), "\\$100\\.00 \\(due\\)")


class ProgressWindowAllFrameworks(unittest.TestCase):
    """The IDE progress window exists for Selenium (Swing), Playwright and Cypress."""

    def _build(self, build):
        d = tempfile.mkdtemp()
        cfg = {"base_url": "https://x", "login_url": "https://x/login", "env_user": "u", "env_pass": "p"}
        try:
            build(d, [], cfg, lambda *a, **k: None, "return [];")
            files = {os.path.relpath(f, d).replace(os.sep, "/"): open(f, encoding="utf-8").read()
                     for f in glob.glob(os.path.join(d, "**", "*.js"), recursive=True)}
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return files

    def test_playwright_reporter_is_registered(self):
        f = self._build(A.build_playwright_project)
        self.assertIn("support/progress.js", f)
        self.assertIn("support/progress-reporter.js", f)
        self.assertIn("['./support/progress-reporter.js']", f["playwright.config.js"])
        rep = f["support/progress-reporter.js"]
        self.assertIn("result.retry < test.retries", rep)          # a retried try isn't counted twice

    def test_cypress_hooks_and_shared_window(self):
        f = self._build(A.build_cypress_project)
        self.assertIn("cypress/progress.js", f)
        cfg = f["cypress.config.js"]
        self.assertIn("qaProgress(msg) { progress.event(msg); return null; }", cfg)
        self.assertIn("await progress.start(", cfg)
        self.assertIn("await finishing;", cfg)
        sup = f["cypress/support/e2e.js"]
        self.assertIn("cy.task('qaProgress', { op: 'test'", sup)
        self.assertIn("cy.task('qaProgress', { op: 'done', status, browser: Cypress.browser.displayName }", sup)
        runner = f["cypress/run-browsers.js"]
        self.assertIn("process.env.QA_PROGRESS_PORT = String(progressPort)", runner)
        self.assertIn("const finishing = progress.finish();", runner)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_progress_module_counts_and_child_runs_report_to_the_window(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "progress.js"), "w", encoding="utf-8") as fh:
                fh.write(A._PROGRESS_JS)
            script = r"""
const http = require('http');
const { execFile } = require('child_process');
require('child_process').spawn = () => ({ on() {}, unref() {}, pid: 0 });   // no real window in tests
const p = require('./progress');
const get = (port, u) => new Promise((res) => http.get({ host: '127.0.0.1', port, path: u }, (r) => {
  let b = ''; r.on('data', (c) => b += c); r.on('end', () => res(b)); }));
const keep = setInterval(() => {}, 1000);
(async () => {
  const port = await p.start(4);
  p.testStarted('A', 'Chrome'); p.testDone('passed');
  p.testStarted('B', 'Firefox'); p.testDone('failed');
  await new Promise((res) => execFile(process.execPath, ['-e',
    "const p=require('./progress');p.testStarted('C','Edge');p.testDone('passed');"],
    { cwd: __dirname, env: Object.assign({}, process.env, { QA_PROGRESS_PORT: String(port) }), timeout: 10000 }, res));
  await new Promise((r) => setTimeout(r, 300));
  const s = JSON.parse(await get(port, '/state'));
  const page = await get(port, '/');
  await p.finish();
  console.log(JSON.stringify({ total: s.total, done: s.done, passed: s.passed, failed: s.failed,
    current: s.current, browser: s.browser, page: page.indexOf('setInterval(tick,500)') > 0,
    verdict: p._state.verdict }));
  clearInterval(keep);
})();
"""
            with open(os.path.join(d, "t.js"), "w", encoding="utf-8") as fh:
                fh.write(script)
            env = dict(os.environ, PROGRESS="on")
            env.pop("CI", None)
            r = subprocess.run(["node", "t.js"], cwd=d, capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(out, {"total": 4, "done": 3, "passed": 2, "failed": 1, "current": "C",
                               "browser": "Edge", "page": True, "verdict": "1 of 3 tests failed"})


class TableCellTextChecks(unittest.TestCase):
    """'"Alice Johnson" is shown' on a scripted DataTables page: no harvested
    element holds a <td>, so the check is page text - never the search box or
    the pager's "3" link (the second run's wrong bindings)."""
    STEPS = [{"expected": '"Alice Johnson" is shown'},
             {"expected": 'Page 3 is the current page. "John Doe" is shown'}]

    def test_text_visible_check_is_page_text(self):
        it = {"role": "assertion", "target": "Alice Johnson", "keywords": ["Alice Johnson"],
              "check": "text_visible", "from_steps": [1]}
        self.assertEqual(E._page_text_phrase(it, self.STEPS), "Alice Johnson")
        for chk in ("", "text", "visible", "text_contains"):
            self.assertEqual(E._page_text_phrase(dict(it, check=chk), self.STEPS), "Alice Johnson", chk)
        for chk in ("value", "page", "table", "attr", "enabled"):
            self.assertIsNone(E._page_text_phrase(dict(it, check=chk), self.STEPS), chk)

    def test_element_without_the_text_is_rejected(self):
        it = {"role": "assertion", "target": "page 3 results", "keywords": ["3", "John Doe"],
              "check": "text_visible", "from_steps": [2]}
        self.assertFalse(E._holds_authored_text({"tag": "a", "text": "3"}, it, self.STEPS))
        self.assertFalse(E._holds_authored_text({"tag": "input", "placeholder": "Search"}, it, self.STEPS))
        self.assertTrue(E._holds_authored_text({"tag": "td", "text": "John Doe"}, it, self.STEPS))
        self.assertTrue(E._holds_authored_text({"tag": "div", "text": ""}, it, self.STEPS))     # unknown text
        plain = dict(it, keywords=["3"], target="page 3")                                    # no quoted phrase
        self.assertTrue(E._holds_authored_text({"tag": "a", "text": "3"}, plain, self.STEPS))
