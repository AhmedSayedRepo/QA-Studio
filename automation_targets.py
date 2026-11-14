"""automation_targets.py — multi-framework emitters for QA Studio automation.

The AI-compiled *intent model* produced by engine.validate_and_sequence_suite()
is framework-agnostic. This module turns that IR into runnable projects for
targets beyond Selenium/Java. Today: Playwright (JavaScript, self-healing).

Design: ONE shared IR → per-target emitter. The cross-cutting concerns that make
QA Studio's Selenium output nice (a manifest for resume, seeded+committed
locators.json, orphan pruning, write-if-changed, and a hash-guard that never
clobbers hand-edited files) are implemented once here, framework-agnostic, so
every JS target inherits the same behaviour as engine.build_selfhealing_project.

The emitter is deliberately decoupled from engine.py: it receives `seed_fn`
(engine._seed_locator_for_intent) and `harvest_js` (engine._HARVEST_JS) as
arguments, so it can be unit-tested without importing the (large) engine module.
"""
import os, json, re, time, hashlib

TARGETS = ("selenium", "playwright", "cypress")


# ────────────────────────── shared, framework-agnostic ──────────────────────
def _sha1(s):
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()


def _manifest_path(out_dir):
    return os.path.join(out_dir, ".qastudio", "manifest.json")


def _load_manifest(out_dir):
    try:
        with open(_manifest_path(out_dir), "r", encoding="utf-8") as f:
            m = json.load(f)
            return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def _save_manifest(out_dir, m):
    try:
        os.makedirs(os.path.dirname(_manifest_path(out_dir)), exist_ok=True)
        with open(_manifest_path(out_dir), "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _with_batch2_js(text):
    """Fill the shared Batch 2 scripts (option picker, scroll limit) from engine,
    so inspection, replay and every generated framework use the same rule."""
    import engine as _E
    return (text.replace("__PICK_OPTION_FN__", json.dumps(_E._PICK_OPTION_FN))
                .replace("__SCROLL_MAX__", str(_E._SCROLL_MAX))
                .replace("__DROP_TARGET_FN__", json.dumps(_E._DROP_TARGET_FN))
                .replace("__TABLE_CELL_FN__", json.dumps(_E._TABLE_CELL_FN))
                .replace("__PAGE_CHECK_FN__", json.dumps(_E._PAGE_CHECK_FN))
                .replace("__REMEMBER_FN__", json.dumps(_E._REMEMBER_FN))
                .replace("__DISMISS_FN__", json.dumps(_E._DISMISS_FN))
                .replace("__READY_FN__", json.dumps(_E._READY_FN))
                .replace("__SCROLL_PAGE_FN__", json.dumps(_E._SCROLL_PAGE_FN))
                .replace("__PAGINATE_FN__", json.dumps(_E._PAGINATE_FN))
                .replace("__STORAGE_FN__", json.dumps(_E._STORAGE_FN))
                .replace("__SCROLL_SEARCH_STEPS__", str(_E._SCROLL_SEARCH_STEPS))
                .replace("__PAGINATE_MAX__", str(_E._PAGINATE_MAX))
                .replace("__READY_LIMITS__", json.dumps({"wait": _E._READY_WAIT_S * 1000,
                                                        "disabled": _E._READY_DISABLED_S * 1000,
                                                        "covered": _E._READY_COVERED_S * 1000})))


def _wif(path, content, written, out_dir):
    """write-if-changed: skip identical files so re-runs don't churn git/mtimes."""
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                if f.read() == content:
                    return
    except Exception:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    written.append(os.path.relpath(path, out_dir))


def _guarded_write(path, content, prev_hash, cb):
    """Write a generated spec UNLESS the on-disk file was hand-edited since we last
    generated it. Keeps the user's file, drops a `<name>.new` sibling with the fresh
    generation, and warns. Returns (wrote_bool, hash_to_record)."""
    new_hash = _sha1(content)
    if prev_hash and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cur = f.read()
        except Exception:
            cur = None
        cur_hash = _sha1(cur) if cur is not None else None
        if cur_hash and cur_hash != prev_hash and cur_hash != new_hash:
            try:
                with open(path + ".new", "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass
            cb("Kept your manual edits to %s — fresh generation saved as %s.new "
               "(diff & merge by hand)." % (os.path.basename(path),
                                            os.path.basename(path)), "ok")
            return False, prev_hash
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True, new_hash


def _merge_seed_locators(out_dir, seeds, cb):
    """Merge generation-time seed locators into the COMMITTED locators.json without
    clobbering any entry already healed at runtime or hand-edited."""
    path = os.path.join(out_dir, "locators.json")
    data = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
    except Exception:
        data = {}
    added = 0
    for key, seed in (seeds or {}).items():
        source = seed.get("source", "seed")
        existing = data.get(key)
        # A fresh direct inspection supersedes an older generated seed *and*
        # a previous inspection of the same key. Runtime-healed/manual entries
        # remain authoritative and are never overwritten.
        can_upgrade = (source == "inspection" and isinstance(existing, dict)
                       and existing.get("source") in ("seed", "inspection"))
        if not existing or can_upgrade:
            data[key] = {"by": seed["by"], "value": seed["value"], "source": source}
            added += 1
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        return 0
    if added:
        cb("Seeded %d new locator(s) into locators.json (%d total)." % (added, len(data)), "dim")
    return added


def _prune_specs(tests_dir, owned, cb):
    """Delete generated spec files we no longer own (dropped story). Scoped to the
    tests dir; only touches *.spec.js / *.cy.js, never a user's `.new` sibling."""
    removed = []
    if not os.path.isdir(tests_dir):
        return removed
    for fn in sorted(os.listdir(tests_dir)):
        if (fn.endswith(".spec.js") or fn.endswith(".cy.js")) and fn not in owned:
            try:
                os.remove(os.path.join(tests_dir, fn))
                removed.append(fn)
            except Exception:
                pass
    if removed:
        cb("Pruned %d stale spec(s): %s" % (len(removed), ", ".join(removed)), "dim")
    return removed


# ────────────────────────── Playwright (JavaScript) ─────────────────────────
_PKG_JSON = """{
  "name": "qastudio-playwright-tests",
  "version": "1.0.0",
  "private": true,
  "description": "Self-healing Playwright tests generated by QA Studio.",
  "scripts": {
    "postinstall": "playwright install chromium",
    "pretest": "node support/browsers.js --install",
    "test": "playwright test",
    "test:chrome": "cross-env BROWSER=chrome npm test",
    "test:edge": "cross-env BROWSER=edge npm test",
    "test:firefox": "cross-env BROWSER=firefox npm test",
    "test:all": "cross-env BROWSER=all npm test",
    "test:headed": "playwright test --headed",
    "test:headless": "cross-env HEADLESS=true playwright test",
    "report": "node support/report-mailer.js --open playwright-report/qa-studio-report.html",
    "report:detail": "playwright show-report"
  },
  "engines": { "node": ">=18" },
  "devDependencies": { "@playwright/test": "^1.48.0", "cross-env": "^7.0.3" },
  "dependencies": { "dotenv": "^16.4.5", "nodemailer": "^6.9.14" }
}
"""

_PROGRESS_LAUNCHER_JAVA = r"""
/** Runs the Selenium projects' progress window for a Playwright / Cypress run:
 *  `java QaProgressWindow.java http://127.0.0.1:<port>/` (Java 11+). It reads
 *  the run's state from support/progress.js's tiny server twice a second and
 *  closes by itself when the run ends (or the server is gone). */
public class QaProgressWindow {
    public static void main(String[] args) throws Exception {
        String url = args.length > 0 ? args[0] : System.getenv("QA_PROGRESS_URL");
        if (url == null || url.isEmpty()) System.exit(2);
        if (!url.endsWith("/")) url += "/";
        int seenPass = 0, seenFail = 0, seenSkip = 0, misses = 0;
        String lastCurrent = null;
        boolean started = false, wrapped = false;
        while (true) {
            String js = fetch(url + "state");
            if (js == null) {
                if (++misses >= 6) System.exit(0);           // the run is gone
                Thread.sleep(500);
                continue;
            }
            misses = 0;
            if (!started) { ProgressWindow.start(Math.max(1, num(js, "total"))); started = true; }
            String cur = str(js, "current");
            if (!cur.isEmpty() && !cur.equals(lastCurrent)) {
                ProgressWindow.testStarted(cur, str(js, "browser"));
                lastCurrent = cur;
            }
            for (int p = num(js, "passed"); seenPass < p; seenPass++) ProgressWindow.testDone("passed");
            for (int f = num(js, "failed"); seenFail < f; seenFail++) ProgressWindow.testDone("failed");
            for (int k = num(js, "skipped"); seenSkip < k; seenSkip++) ProgressWindow.testDone("skipped");
            // One row per browser: "Chrome|9|3|1|0;Firefox|9|2|0|0" (name|total|passed|failed|skipped).
            for (String lane : str(js, "lanes").split(";")) {
                String[] f = lane.split("\\|");
                if (f.length == 5) {
                    try {
                        ProgressWindow.setLane(f[0], Integer.parseInt(f[1]), Integer.parseInt(f[2]),
                                               Integer.parseInt(f[3]), Integer.parseInt(f[4]));
                    } catch (NumberFormatException ignored) { }
                }
            }
            if (!wrapped && bool(js, "wrapping")) { ProgressWindow.wrapUp(); wrapped = true; }
            if (bool(js, "finished")) {
                String v = str(js, "verdict");
                ProgressWindow.finish(v.isEmpty() ? "Finished" : v);
                Thread.sleep(2000);
                System.exit(0);
            }
            Thread.sleep(400);
        }
    }

    static String fetch(String u) {
        try {
            java.net.HttpURLConnection c = (java.net.HttpURLConnection) new java.net.URL(u).openConnection();
            c.setConnectTimeout(800);
            c.setReadTimeout(800);
            try (java.io.InputStream in = c.getInputStream()) {
                return new String(in.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
            }
        } catch (Exception e) {
            return null;
        }
    }

    static int num(String js, String key) {
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("\"" + key + "\":(-?\\d+)").matcher(js);
        return m.find() ? Integer.parseInt(m.group(1)) : 0;
    }

    static boolean bool(String js, String key) {
        return js.contains("\"" + key + "\":true");
    }

    static String str(String js, String key) {
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("\"" + key + "\":\"((?:[^\"\\\\]|\\\\.)*)\"").matcher(js);
        if (!m.find()) return "";
        String raw = m.group(1);
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < raw.length(); i++) {
            char ch = raw.charAt(i);
            if (ch != '\\' || i + 1 >= raw.length()) { out.append(ch); continue; }
            char n = raw.charAt(++i);
            switch (n) {
                case 'n': out.append(' '); break;
                case 't': out.append(' '); break;
                case 'r': break;
                case 'u':
                    if (i + 4 < raw.length()) {
                        out.append((char) Integer.parseInt(raw.substring(i + 1, i + 5), 16));
                        i += 4;
                    }
                    break;
                default: out.append(n);
            }
        }
        return out.toString();
    }
}

/** PROGRESS was already checked by progress.js; this only satisfies the window. */
final class Config {
    static String setting(String key, String def) {
        String v = System.getenv(key);
        return v == null || v.isEmpty() ? def : v;
    }
}
"""


def progress_window_java():
    """support/QaProgressWindow.java (Playwright) / cypress/QaProgressWindow.java:
    the Selenium projects' frameless Swing progress window, run as one Java
    source file by progress.js (falls back to an Edge/Chrome window when Java
    11+ isn't installed)."""
    import engine as _E
    src = _E._progress_window("qa")
    src = re.sub(r"(?m)^package [^;]+;\s*\n", "", src, count=1)
    src = src.replace("public final class ProgressWindow", "final class ProgressWindow", 1)
    first_decl = re.search(r"(?m)^(/\*\*|final class ProgressWindow)", src)
    imports, rest = src[:first_decl.start()], src[first_decl.start():]
    return ("// Generated by QA Studio - the IDE progress window (frameless, always on top).\n"
            + imports + _PROGRESS_LAUNCHER_JAVA.lstrip("\n") + "\n" + rest)


_PROGRESS_JS = r"""// Live progress window while tests run from the IDE (or npm test on a desktop):
// current test and browser, a progress bar split into passed / failed /
// skipped, counts and time left. It is the same frameless window as the
// Selenium projects (QaProgressWindow.java, run with Java 11+), or a small
// Edge / Chrome app window without Java, fed by a tiny server inside this test
// run; the run never depends on it. PROGRESS=off (.env or environment)
// turns it off; CI never shows it. With several browsers in one `npm test`
// (Cypress), the parent run owns the window and each browser run reports to it.
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, spawnSync, execFile } = require('child_process');

const state = { total: 0, done: 0, passed: 0, failed: 0, skipped: 0, current: '', browser: '',
  started: Date.now(), ended: 0, wrapping: false, finished: false, verdict: '', browsers: {} };

// One progress row per browser (shown with two or more).
function laneOf(name) {
  if (!state.browsers[name]) state.browsers[name] = { total: 0, done: 0, passed: 0, failed: 0, skipped: 0 };
  return state.browsers[name];
}
function lanesText() {
  return Object.keys(state.browsers).map((n) => {
    const l = state.browsers[n];
    return [String(n).replace(/[|;]/g, ' '), l.total, l.passed, l.failed, l.skipped].join('|');
  }).join(';');
}
let server = null;
let child = null;
let enabled = null;
const parentPort = Number(process.env.QA_PROGRESS_PORT || 0);

function isOn() {
  if (enabled === null) {
    const m = String(process.env.PROGRESS || 'on').trim().toLowerCase();
    enabled = !['off', 'false', '0', 'no'].includes(m) && !process.env.CI;
  }
  return enabled;
}

function forward(msg) {
  try {
    const body = JSON.stringify(msg);
    const req = http.request({ host: '127.0.0.1', port: parentPort, path: '/event', method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } });
    req.on('error', () => {});
    req.end(body);
  } catch (e) { /* the window is optional */ }
}

function apply(msg) {
  if (!msg) return;
  if (msg.op === 'test') {
    state.current = String(msg.title || '');
    state.browser = String(msg.browser || '');
    state.ended = 0;                                  // more tests than counted: the clock runs on
  } else if (msg.op === 'done') {
    state.done++;
    if (msg.status === 'passed') state.passed++;
    else if (msg.status === 'failed') state.failed++;
    else state.skipped++;
    if (state.done > state.total) state.total = state.done;
    if (state.done >= state.total && !state.ended) state.ended = Date.now();   // 100%: the clock stops
    if (msg.browser) {
      const l = laneOf(String(msg.browser));
      l.done++;
      l[msg.status === 'passed' ? 'passed' : msg.status === 'failed' ? 'failed' : 'skipped']++;
      if (l.done > l.total) l.total = l.done;
    }
  } else if (msg.op === 'expect' && msg.browser) {
    laneOf(String(msg.browser)).total = Math.max(Number(msg.count) || 0, 0);
  }
}

function event(msg) {
  if (!isOn()) return;
  if (parentPort) forward(msg); else apply(msg);
}
function testStarted(title, browser) { event({ op: 'test', title, browser }); }
function testDone(status, browser) { event({ op: 'done', status, browser: browser || '' }); }
/** How many tests a browser will run (one progress row per browser). */
function expect(browser, count) { if (browser) event({ op: 'expect', browser, count }); }

function verdict() {
  const t = state.done;
  if (!t) return 'No tests ran';
  return state.failed ? state.failed + ' of ' + t + ' tests failed' : 'All ' + t + ' tests passed';
}

function browserExe() {
  const e = process.env;
  const win = [e['ProgramFiles(x86)'], e.ProgramFiles, e.LOCALAPPDATA].filter(Boolean);
  const cands = process.platform === 'win32'
    ? win.map((b) => path.join(b, 'Microsoft', 'Edge', 'Application', 'msedge.exe'))
        .concat(win.map((b) => path.join(b, 'Google', 'Chrome', 'Application', 'chrome.exe')))
    : process.platform === 'darwin'
      ? ['/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
         '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
      : ['/usr/bin/microsoft-edge', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
  return cands.find((p) => { try { return fs.existsSync(p); } catch (err) { return false; } });
}

// Java 11+ runs the same frameless window as the Selenium projects
// (QaProgressWindow.java next to this file). No Java, an older one, or a JRE
// without the compiler: fall back to an Edge / Chrome app window.
// `java` (not javaw) with a hidden console: an old javaw showed a "JNI error"
// dialog box instead of just failing.
let fellBack = false;
function javaVersion(exe) {
  try {
    const r = spawnSync(exe, ['-version'], { encoding: 'utf8', windowsHide: true, timeout: 8000 });
    const m = String((r.stderr || '') + (r.stdout || '')).match(/version "(\d+)(?:\.(\d+))?/);
    if (!m) return 0;
    const major = Number(m[1]);
    return major === 1 ? Number(m[2] || 0) : major;     // "1.8.0_381" is Java 8
  } catch (e) { return 0; }
}
function javaExe() {
  const name = process.platform === 'win32' ? 'java.exe' : 'java';
  const cands = [];
  if (process.env.JAVA_HOME) cands.push(path.join(process.env.JAVA_HOME, 'bin', name));
  cands.push('java');
  for (const c of cands) {
    if (c !== 'java' && !fs.existsSync(c)) continue;
    if (javaVersion(c) >= 11 && hasCompiler(c)) return c;
  }
  return null;
}
// A JRE (runtime only) can't run a .java source file: it needs a JDK.
function hasCompiler(exe) {
  try {
    const r = spawnSync(exe, ['--list-modules'], { encoding: 'utf8', windowsHide: true, timeout: 8000 });
    return /\bjdk\.compiler\b/.test(String(r.stdout || '') + String(r.stderr || ''));
  } catch (e) { return false; }
}

function openWindow(url) {
  const src = path.join(__dirname, 'QaProgressWindow.java');
  const java = fs.existsSync(src) ? javaExe() : null;
  if (!java) { openBrowserWindow(url); return; }
  const fallBack = () => {
    if (fellBack || state.finished) return;
    fellBack = true;
    child = null;
    openBrowserWindow(url);
  };
  try {
    child = spawn(java, [src, url], { stdio: 'ignore', windowsHide: true,
      detached: process.platform !== 'win32' });
    child.on('error', fallBack);
    child.on('exit', (code) => { if (code) fallBack(); });
    child.unref();
  } catch (e) { fallBack(); }
}

function openBrowserWindow(url) {
  const exe = browserExe();
  if (!exe) { console.log('[progress] no Edge or Chrome found - progress window skipped'); return; }
  const profile = path.join(os.tmpdir(), 'qa-studio-progress-profile');
  try {
    child = spawn(exe, ['--app=' + url, '--window-size=460,270', '--user-data-dir=' + profile,
      '--no-first-run', '--no-default-browser-check', '--disable-extensions'],
      { stdio: 'ignore', detached: process.platform !== 'win32' });
    child.on('error', () => { child = null; });
    child.unref();
  } catch (e) { child = null; }
}

function closeWindow() {
  if (!child || !child.pid) return;
  try {
    if (process.platform === 'win32') execFile('taskkill', ['/pid', String(child.pid), '/T', '/F'], () => {});
    else process.kill(-child.pid);
  } catch (e) { try { child.kill(); } catch (err) { /* already closed */ } }
  child = null;
}

/** Opens the window; resolves with its server's port (0 when off). */
function start(total) {
  if (!isOn() || parentPort || server) return Promise.resolve(port());
  state.total = Math.max(Number(total) || 0, 1);
  state.started = Date.now();
  server = http.createServer((req, res) => {
    if (req.method === 'POST' && req.url === '/event') {
      let body = '';
      req.on('data', (c) => { body += c; });
      req.on('end', () => { try { apply(JSON.parse(body)); } catch (e) { /* ignore */ } res.end('ok'); });
      return;
    }
    if (req.url && req.url.indexOf('/state') === 0) {
      res.setHeader('Content-Type', 'application/json');
      res.setHeader('Cache-Control', 'no-store');
      res.end(JSON.stringify(Object.assign({}, state, { elapsed: (state.ended || Date.now()) - state.started,
        lanes: lanesText() })));
      return;
    }
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.end(PAGE);
  });
  return new Promise((resolve) => {
    server.on('error', () => { server = null; resolve(0); });
    server.listen(0, '127.0.0.1', () => {
      const p = server.address().port;
      openWindow('http://127.0.0.1:' + p + '/');
      resolve(p);
    });
    server.unref();
  });
}

/** The port the window's server listens on (for child runs), or 0. */
function port() { return server && server.address() ? server.address().port : 0; }

/** Every test ran: the clock stops and the window says the report is being written. */
function wrapUp() {
  if (!isOn() || parentPort || !server) return;
  if (!state.ended) state.ended = Date.now();
  state.wrapping = true;
}

/** Shows the verdict briefly, then closes the window. */
async function finish(text) {
  if (!isOn() || parentPort || !server) return;
  if (!state.ended) state.ended = Date.now();
  state.finished = true;
  state.verdict = text || verdict();
  await new Promise((r) => setTimeout(r, 1500));
  closeWindow();
  try { server.close(); } catch (e) { /* closed */ }
  server = null;
}

/** Number of tests in the given spec files (it( calls), for the bar's total. */
function countTests(files) {
  let n = 0;
  for (const f of files || []) {
    try { n += (fs.readFileSync(f, 'utf8').match(/\bit\s*\(/g) || []).length; } catch (e) { /* unreadable */ }
  }
  return n;
}

const PAGE = [
  '<!doctype html><html><head><meta charset="utf-8"><title>QA Studio - running tests</title>',
  '<style>',
  ':root{--bg:#111727;--card:#1c243a;--line:#303a56;--ink:#e8ecf4;--ink2:#a4adc0;--ink3:#707a91;',
  '--teal:#22b8c7;--green:#3fb950;--red:#f85149;--grey:#76809a}',
  'html,body{margin:0;background:var(--bg);color:var(--ink);font:13px "Segoe UI",system-ui,sans-serif;overflow:hidden}',
  '.wrap{padding:12px 18px 14px}',
  '.hd{display:flex;align-items:center;gap:8px}',
  '.dot{width:8px;height:8px;border-radius:50%;background:var(--teal);box-shadow:0 0 0 0 rgba(34,184,199,.6);animation:p 1.5s infinite}',
  '.dot.ok{background:var(--green);animation:none}.dot.bad{background:var(--red);animation:none}',
  '@keyframes p{0%{box-shadow:0 0 0 0 rgba(34,184,199,.55)}100%{box-shadow:0 0 0 9px rgba(34,184,199,0)}}',
  '.name{font-weight:700}.st{color:var(--ink2);flex:1}',
  '.btn{background:none;border:0;color:var(--ink2);cursor:pointer;width:28px;height:24px;border-radius:7px;font-size:14px}',
  '.btn:hover{background:#28324c}',
  '.lbl{margin-top:10px;font-size:10px;font-weight:700;color:var(--ink3);letter-spacing:.4px}',
  '.cur{margin-top:4px;font-size:14px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
  '.meta{display:flex;justify-content:space-between;align-items:center;margin-top:10px}',
  '.pill{font-size:11px;font-weight:700;padding:3px 10px;border-radius:11px;color:var(--teal);background:rgba(34,184,199,.18)}',
  '.pill.firefox{color:#ff8c32;background:rgba(255,140,50,.18)}.pill.edge,.pill.msedge{color:#4296ff;background:rgba(66,150,255,.18)}',
  '.pill:empty{display:none}',
  '.ratio{font-weight:700;color:var(--ink2);font-size:12px}',
  '.bar{display:flex;height:10px;border-radius:5px;background:var(--card);overflow:hidden;margin-top:8px}',
  '.bar i{display:block;height:100%;transition:width .3s}.bar .p{background:var(--green)}.bar .f{background:var(--red)}.bar .s{background:var(--grey)}',
  '.chips{display:flex;gap:8px;margin-top:12px}',
  '.chip{background:var(--card);border-radius:13px;padding:4px 11px 4px 9px;font-weight:700;font-size:12px}',
  '.chip b{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px}',
  '.el{margin-top:10px;font-size:11px;color:var(--ink3)}',
  '.lanes{margin-top:12px}.lanes:empty{display:none}.ln{display:flex;align-items:center;gap:8px;margin-top:6px}',
  '.ln .pill{width:58px;text-align:center}.ln .bar{flex:1;height:6px;margin-top:0}.ln .c{width:48px;text-align:right;font-weight:700;color:var(--ink2);font-size:11px}',
  'body.compact .full{display:none}body.compact .bar{height:4px;margin-top:9px}',
  '</style></head><body><div class="wrap">',
  '<div class="hd"><span class="dot" id="dot"></span><span class="name">QA Studio</span>',
  '<span class="st" id="st">Running tests</span>',
  '<button class="btn" id="cmp" title="Compact view">&#8964;</button></div>',
  '<div class="full"><div class="lbl" id="lbl">NOW RUNNING</div><div class="cur" id="cur">Starting the browser...</div>',
  '<div class="meta"><span class="pill" id="pill"></span><span class="ratio" id="ratio"></span></div></div>',
  '<div class="bar"><i class="p" id="bp"></i><i class="f" id="bf"></i><i class="s" id="bs"></i></div>',
  '<div class="full"><div class="chips"><span class="chip"><b style="background:var(--green)"></b><span id="np">0</span> passed</span>',
  '<span class="chip"><b style="background:var(--red)"></b><span id="nf">0</span> failed</span>',
  '<span class="chip"><b style="background:var(--grey)"></b><span id="ns">0</span> skipped</span></div>',
  '<div class="lanes" id="lanes"></div>',
  '<div class="el" id="el"></div></div></div>',
  '<script>',
  'var $=function(i){return document.getElementById(i)},compact=false,full=[460,270],small=[460,110];',
  'function clock(ms){var s=Math.floor(ms/1000),h=Math.floor(s/3600),m=Math.floor(s/60)%60,x=s%60;',
  ' return (h?h+":"+(m<10?"0":""):"")+m+":"+(x<10?"0":"")+x}',
  '$("cmp").onclick=function(){compact=!compact;document.body.className=compact?"compact":"";',
  ' $("cmp").innerHTML=compact?"&#8963;":"&#8964;";$("cmp").title=compact?"Full view":"Compact view";',
  ' try{var z=compact?small:full;window.resizeTo(z[0],z[1])}catch(e){}};',
  'document.querySelector(".hd").ondblclick=function(){$("cmp").onclick()};',
  'function draw(s){var t=Math.max(s.total,1),pct=Math.round(100*s.done/t);',
  ' $("bp").style.width=(100*s.passed/t)+"%";$("bf").style.width=(100*s.failed/t)+"%";$("bs").style.width=(100*s.skipped/t)+"%";',
  ' $("np").textContent=s.passed;$("nf").textContent=s.failed;$("ns").textContent=s.skipped;',
  ' $("ratio").textContent=s.done+" / "+t+"   "+pct+"%";',
  ' var p=$("pill");p.textContent=s.browser||"";p.className="pill "+String(s.browser||"").toLowerCase().split(" ")[0];',
  ' var left=(!s.finished&&!s.ended&&s.done>0&&s.done<t)?"   ·   about "+clock(s.elapsed/s.done*(t-s.done))+" left":"";',
  ' $("el").textContent="Elapsed "+clock(s.elapsed)+left;',
  ' if(s.finished){$("st").textContent="Finished";$("lbl").textContent="RESULT";$("cur").textContent=s.verdict;',
  '  $("dot").className="dot "+(s.failed?"bad":"ok");}',
  ' else if(s.wrapping){$("st").textContent=compact?"Writing the report...":"All tests ran";',
  '  $("lbl").textContent="ALL TESTS RAN";$("cur").textContent="Writing the report...";}',
  ' else{$("cur").textContent=s.current||"Starting the browser...";$("cur").title=s.current||"";',
  '  $("st").textContent=s.ended?(compact?s.done+" / "+t+"  ·  wrapping up":"Wrapping up")',
  '   :compact?(s.done+" / "+t+(s.failed?"  ·  "+s.failed+" failed":"  ·  running")):"Running tests";}',
  ' var names=Object.keys(s.browsers||{}),h="";',
  ' if(names.length>1)names.forEach(function(n){var l=s.browsers[n],lt=Math.max(l.total,1);',
  '  h+="<div class=\\"ln\\"><span class=\\"pill "+n.toLowerCase()+"\\">"+n+"</span><div class=\\"bar\\">"',
  '   +"<i class=\\"p\\" style=\\"width:"+(100*l.passed/lt)+"%\\"></i><i class=\\"f\\" style=\\"width:"+(100*l.failed/lt)+"%\\"></i>"',
  '   +"<i class=\\"s\\" style=\\"width:"+(100*l.skipped/lt)+"%\\"></i></div><span class=\\"c\\">"+l.done+" / "+l.total+"</span></div>";});',
  ' $("lanes").innerHTML=h;',
  ' if(names.length>1&&!window.qaSized&&!compact){window.qaSized=1;full=[460,280+28*names.length];try{window.resizeTo(full[0],full[1])}catch(e){}}',
  ' document.title=(s.finished?"Finished":s.done+"/"+t)+" - QA Studio";}',
  'function tick(){fetch("/state",{cache:"no-store"}).then(function(r){return r.json()}).then(draw).catch(function(){',
  ' $("st").textContent="Finished";});}',
  'tick();setInterval(tick,500);',
  '</script></body></html>'].join('\n');

module.exports = { start, testStarted, testDone, expect, event, wrapUp, finish, countTests, port, verdict, _state: state, _page: PAGE };
"""

_PW_PROGRESS_REPORTER_JS = r"""// Playwright reporter: feeds the progress window (support/progress.js) while the
// tests run - which test is running in which browser, and the counts.
const progress = require('./progress');

class ProgressReporter {
  onBegin(config, suite) {
    const tests = suite.allTests();
    progress.start(tests.length);
    // One progress row per browser (project): how many tests each runs.
    const per = {};
    for (const t of tests) {
      let name = '';
      try { name = t.parent.project().name; } catch (e) { /* older Playwright */ }
      if (name) per[name] = (per[name] || 0) + 1;
    }
    for (const name of Object.keys(per)) progress.expect(name, per[name]);
  }

  onTestBegin(test) {
    let browser = '';
    try { browser = test.parent.project().name; } catch (e) { /* older Playwright */ }
    progress.testStarted(test.title, browser);
  }

  onTestEnd(test, result) {
    const bad = ['failed', 'timedOut', 'interrupted'].includes(result.status);
    if (bad && result.retry < test.retries) return;   // it runs again; count the last try
    let browser = '';
    try { browser = test.parent.project().name; } catch (e) { /* older Playwright */ }
    progress.testDone(result.status === 'passed' ? 'passed' : bad ? 'failed' : 'skipped', browser);
  }

  async onEnd() {
    progress.wrapUp();
    await progress.finish();
  }

  printsToStdio() { return false; }
}

module.exports = ProgressReporter;
"""


_PW_CONFIG = """// Playwright config. baseURL and everything environment-specific come from the
// environment (.env, git-ignored) or real env vars — nothing is baked in, so the
// same project runs against any environment with no regeneration.
require('dotenv').config();
const { defineConfig } = require('@playwright/test');
const browsers = require('./support/browsers');
// PARALLEL=false in .env runs everything one test at a time (one worker);
// true (default) lets Playwright run browsers and spec files side by side.
const parallel = !['false', 'off', '0', 'no'].includes(String(process.env.PARALLEL || 'true').trim().toLowerCase());
// One worker per browser (at least 2). Playwright's default (half the CPU
// cores) opened up to 8 visible browsers all recording video: closing one
// (saving its video) then ran past the test timeout ("Tearing down context").
const workerCount = Math.max(2, browsers.projects().length);

module.exports = defineConfig({
  testDir: './tests',
  // Includes closing the browser and saving its video after the last step.
  timeout: 60000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  workers: parallel ? workerCount : 1,
  retries: 0,
  // Logs in ONCE before the whole run and saves the session to .auth/state.json
  // (see global-setup.js). Playwright gives every test a fresh isolated browser
  // context — nothing survives from one test to the next — so a login performed
  // inside one test can NEVER carry over; this global setup + storageState is
  // the supported way for app-page tests to start already authenticated.
  globalSetup: require.resolve('./global-setup'),
  // A rich HTML report is written to ./playwright-report on EVERY run (pass or
  // fail), listing every test with status, steps, screenshots and traces. Open it
  // with `npx playwright show-report` (or `npm run report`).
  // After the html report is written, support/after-run.js opens it and emails it.
  // support/progress-reporter.js shows a live progress window while tests run
  // (PROGRESS=off in .env turns it off; never on CI).
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }],
             ['./support/progress-reporter.js'], ['./support/after-run.js']],
  use: {
    baseURL: process.env.APP_BASE_URL || '',
    storageState: '.auth/state.json',   // every test starts logged in (see global-setup.js)
    // The browser is visible while tests run; HEADLESS=true (or CI) hides it.
    headless: !!process.env.CI || String(process.env.HEADLESS).toLowerCase() === 'true',
    screenshot: 'on',              // capture a screenshot for EVERY test (not only failures)
    // A video of EVERY test in the report; VIDEO=retain-on-failure keeps only
    // failed ones (smaller report), VIDEO=off records none.
    video: process.env.VIDEO || 'on',
    trace: 'retain-on-failure',
    actionTimeout: 15000,
  },
  // A failed test is run once more (a site blip shouldn't fail the run); the
  // report marks a test that passed on the retry as "flaky". RETRIES=0 turns it off.
  retries: Number(process.env.RETRIES ?? 1),
  // Browsers (one project each): BROWSER = chromium (default), chrome, edge,
  // firefox, a list (chrome,firefox) or all. See support/browsers.js.
  projects: browsers.projects(),
});
"""

_PW_BROWSERS_JS = """// Which browsers a run uses (one Playwright project each). BROWSER (env or
// .env) = chromium (default: Playwright's own), chrome (installed Google Chrome),
// edge (installed Microsoft Edge), firefox, a list (chrome,firefox) or all
// (chrome, edge and firefox). `node support/browsers.js --install` (npm's pretest)
// downloads the Playwright browsers the choice needs.
require('dotenv').config();
const BROWSERS = {
  chromium: { label: 'Chromium', use: { browserName: 'chromium' }, install: 'chromium' },
  chrome: { label: 'Chrome', use: { browserName: 'chromium', channel: 'chrome' }, install: '' },
  edge: { label: 'Edge', use: { browserName: 'chromium', channel: 'msedge' }, install: '' },
  firefox: { label: 'Firefox', use: { browserName: 'firefox' }, install: 'firefox' },
};
const ALIASES = { msedge: 'edge', 'microsoft-edge': 'edge', ff: 'firefox', 'google-chrome': 'chrome', googlechrome: 'chrome' };

function selected() {
  const raw = String(process.env.BROWSER || '').trim().toLowerCase();
  if (!raw) return ['chromium'];
  if (raw === 'all') return ['chrome', 'edge', 'firefox'];
  const out = [];
  for (const part of raw.split(/[\\s,;]+/).filter(Boolean)) {
    const b = ALIASES[part] || part;
    if (!BROWSERS[b]) { console.log('[browser] unknown browser "' + part + '" - use chrome, edge, firefox or chromium'); continue; }
    if (!out.includes(b)) out.push(b);
  }
  return out.length ? out : ['chromium'];
}

function projects() {
  return selected().map((b) => ({ name: BROWSERS[b].label, use: BROWSERS[b].use }));
}

module.exports = { selected, projects, BROWSERS };

if (require.main === module && process.argv.includes('--install')) {
  const need = new Set(['chromium']);   // global-setup.js logs in with Chromium
  for (const b of selected()) if (BROWSERS[b].install) need.add(BROWSERS[b].install);
  require('child_process').execSync('npx playwright install ' + Array.from(need).join(' '), { stdio: 'inherit' });
}
"""

_PW_GLOBAL_SETUP = """// Runs ONCE before the whole suite: performs a real login and saves the browser
// storage state (cookies + localStorage) to .auth/state.json. playwright.config.js
// points use.storageState at that file, so every app-page test starts ALREADY
// AUTHENTICATED. Login-page specs opt back out per-group with
// test.use({ storageState: { cookies: [], origins: [] } }).
require('dotenv').config();
const fs = require('fs');
const path = require('path');
const { chromium } = require('@playwright/test');
const { performLogin } = require('./support/auth');

module.exports = async () => {
  const dir = path.join(__dirname, '.auth');
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, 'state.json');
  const browser = await chromium.launch();
  const page = await browser.newPage();
  try {
    await performLogin(page);
    await page.context().storageState({ path: file });
    console.log('[auth] logged in once for the run — session saved to .auth/state.json');
  } catch (e) {
    // Fail soft with a LOUD message: tests still run (logged out), and every
    // app-page failure that follows traces back to this one line instead of
    // looking like dozens of unrelated locator failures.
    fs.writeFileSync(file, JSON.stringify({ cookies: [], origins: [] }));
    console.log('[auth] GLOBAL LOGIN FAILED (' + e.message + ') — app-page tests will run logged out. Check APP_USER/APP_PASS/APP_LOGIN_URL in .env');
  } finally {
    await browser.close();
  }
};
"""

_PW_GITIGNORE = """node_modules/
test-results/
playwright-report/
# per-run login session state (written by global-setup.js, never committed):
.auth/
# environment-specific config (URLs + secrets stay out of git):
.env
# local DOM snapshots from an opt-in live inspection:
inspection-screens.json
# QA Studio's local record of verified cases (reused on regenerate):
.qastudio/verified-cases.json
# locators.json is COMMITTED on purpose — the UI is identical across environments,
# so healed locators are shared. Only URLs/creds differ, and those live in .env.
# a locators.json save in progress:
locators.json.*.tmp
locators.json.lock
"""

_PW_ENV_EXAMPLE = """# Copy to .env (git-ignored) and fill in for your environment.
# Real environment variables of the same name override these.
APP_BASE_URL=https://your-app.example.com
APP_LOGIN_URL=https://your-login.example.com/realms/.../protocol/openid-connect/auth
APP_USER=
APP_PASS=
# Login-page language (ui_locales) — must match the generated tests' expected-
# message language (e.g. ar for Arabic test cases). Baked at generation time.
APP_UI_LOCALE=ar
# AI self-healing (optional but recommended — auto-repairs a locator when it breaks).
# Without a key, tests still run using the seeded/semantic locators; they just
# won't self-heal. Provider vars also work as fallbacks (ANTHROPIC_API_KEY, ...).
QA_AI_API_KEY=
QA_AI_PROVIDER=anthropic
QA_AI_BASE_URL=https://api.anthropic.com
QA_AI_MODEL=claude-sonnet-4-6
# Run settings: the browser is visible unless HEADLESS=true; the HTML report
# opens after every run unless OPEN_REPORT=false (both are off when CI is set).
HEADLESS=false
OPEN_REPORT=true
# Report email after every run (Gmail: SMTP_PASS = an App Password).
REPORT_EMAIL_TO=
SMTP_USER=
SMTP_PASS=
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
# Videos in the detailed report: on (every test), retain-on-failure or off.
VIDEO=on
# Browser(s): chrome, edge, firefox, a list (chrome,firefox) or all. Empty = the
# framework's default (Playwright: its own Chromium, Cypress: Electron).
BROWSER=
# Browsers run at the same time (true) or one after another (false).
PARALLEL=true
# Live progress window while tests run in the IDE (on/off; never on CI).
PROGRESS=on
# Shown in the report as "Run from" (detected automatically: IntelliJ IDEA,
# VS Code, CI, Terminal…). Set it only to override the detected name.
# QA_IDE=IntelliJ IDEA
"""

_PW_FIXTURES = """// Extends Playwright's test with a `heal` fixture — a self-healing locator
// resolver available to every spec as ({ page, heal }).
const base = require('@playwright/test');
const { Healer } = require('./support/healer');

// A failed test whose page is a host/server error page (Heroku "Application
// error", 502/503/504, the browser's "can't be reached") is labelled "Site
// unavailable": the site was down, not the test. Only short pages are checked,
// so a real page that merely mentions an error isn't mislabelled.
function qaSiteDown(title, text) {
  const body = String(text || '');
  if (body.length > 2500) return '';
  const s = (String(title || '') + ' ' + body).toLowerCase();
  const rules = [
    ['application error', 'the page showed "Application error" (the Heroku app is down)'],
    ['502 bad gateway', 'the server answered 502 Bad Gateway'],
    ['503 service', 'the server answered 503 Service Unavailable'],
    ['service unavailable', 'the server answered Service Unavailable'],
    ['504 gateway', 'the server answered 504 Gateway Timeout'],
    ['gateway time-out', 'the server answered 504 Gateway Timeout'],
    ['internal server error', 'the server answered 500 Internal Server Error'],
    ["this site can't be reached", 'the browser could not reach the site'],
    ['this site can\u2019t be reached', 'the browser could not reach the site'],
    ['this page isn\u2019t working', 'the site returned a server error page'],
    ["this page isn't working", 'the site returned a server error page'],
    ['err_name_not_resolved', 'the site address could not be resolved (DNS)'],
    ['err_connection_refused', 'the site refused the connection'],
    ['err_connection_timed_out', 'the connection to the site timed out'],
  ];
  for (const [needle, why] of rules) if (s.includes(needle)) return why;
  return '';
}

async function pageText(page) {
  const title = await page.title().catch(() => '');
  const text = await page.evaluate(() => {
    const out = [document.body ? document.body.innerText : ''];
    document.querySelectorAll('iframe').forEach((f) => {
      try { out.push(f.contentDocument.body.innerText); } catch (e) { /* cross-origin */ }
    });
    return out.join(' ');
  }).catch(() => '');
  return { title, text };
}

const test = base.test.extend({
  heal: async ({ page }, use, testInfo) => {
    await use(new Healer(page));
    if (testInfo.status !== testInfo.expectedStatus) {
      const { title, text } = await pageText(page);
      const why = qaSiteDown(title, text);
      if (why) testInfo.annotations.push({ type: 'Site unavailable',
        description: why + ' — the site was down or erroring, not a test problem.' });
    }
  },
});

module.exports = { test, expect: base.expect, qaSiteDown };
"""

_PW_LOCATORS_JS = """// Load/save the committed locators.json and translate a stored {by,value} into a
// Playwright selector string.
const fs = require('fs');
const path = require('path');
const FILE = path.join(process.cwd(), 'locators.json');

function load() {
  try { return JSON.parse(fs.readFileSync(FILE, 'utf8')); } catch (e) { return {}; }
}
// One writer at a time across workers / browser processes: a lock file next to
// locators.json (a lock older than 5 s is a crashed run's and is taken over).
function withLock(fn) {
  const lock = FILE + '.lock';
  const until = Date.now() + 3000;
  let fd = null;
  while (fd === null) {
    try { fd = fs.openSync(lock, 'wx'); }
    catch (e) {
      try { if (Date.now() - fs.statSync(lock).mtimeMs > 5000) fs.unlinkSync(lock); } catch (x) { /* gone */ }
      if (Date.now() > until) break;                       // write anyway rather than lose the heal
      const t = Date.now() + 15; while (Date.now() < t) { /* brief spin */ }
    }
  }
  try { return fn(); }
  finally { if (fd !== null) { try { fs.closeSync(fd); fs.unlinkSync(lock); } catch (e) { /* gone */ } } }
}
// Parallel workers and browsers share the file: save merges `key` into what is
// on disk now and swaps the file in whole (a reader never sees half a file).
function save(store, key) {
  try {
    withLock(() => {
      const out = key ? Object.assign(load(), { [key]: store[key] }) : store;
      const tmp = FILE + '.' + process.pid + '.tmp';
      fs.writeFileSync(tmp, JSON.stringify(out, null, 2));
      try { fs.renameSync(tmp, FILE); }
      catch (e) { fs.writeFileSync(FILE, JSON.stringify(out, null, 2)); try { fs.unlinkSync(tmp); } catch (x) { /* gone */ } }
    });
  } catch (e) {}
}
function toSelector(spec) {
  const by = (spec && spec.by) || 'css';
  const v = (spec && spec.value) || '';
  switch (by) {
    case 'id':          return '[id="' + v + '"]';   // valid even when the id starts with a digit
    case 'name':        return '[name=\"' + v + '\"]';
    case 'className':   return '.' + v;
    case 'tagName':     return v;
    case 'linkText':
    case 'text':        return 'text=' + v;
    case 'xpath':       return 'xpath=' + v;
    // Shadow DOM "host >>> inner": each part is a CSS query inside the previous
    // element; Playwright's CSS engine pierces open shadow roots.
    case 'shadow':      return v.split(' >>> ').map((p) => 'css=' + p).join(' >> ');
    case 'css':
    case 'cssSelector':
    default:            return v;
  }
}
module.exports = { load, save, toSelector };
"""

_PW_AICLIENT_JS = """// Minimal AI client for runtime locator healing. Reads config from the
// environment (baked defaults come from the QA Studio connection you generated
// with). Supports Anthropic's messages API and any OpenAI-compatible /chat/
// completions endpoint (OpenAI, NVIDIA, Groq, DeepSeek, Qwen, Azure, Ollama, ...).
require('dotenv').config();

const PROVIDER = process.env.QA_AI_PROVIDER || '__PROVIDER__';
const BASE     = process.env.QA_AI_BASE_URL || '__AI_BASE__';
const MODEL    = process.env.QA_AI_MODEL    || '__MODEL__';
const KEY = process.env.QA_AI_API_KEY || process.env.ANTHROPIC_API_KEY ||
           process.env.OPENAI_API_KEY || process.env.NVIDIA_API_KEY ||
           process.env.GROQ_API_KEY || process.env.GEMINI_API_KEY || '';
const HEAL = !!KEY;

const SYS = 'You resolve web locators. Given a step intent and a JSON array of ' +
  'candidate DOM elements, reply with ONLY a JSON object {\"by\":\"...\",\"value\":\"...\"} ' +
  'identifying the single best matching element. \"by\" must be one of: css, id, ' +
  'name, xpath, text. No prose, no code fences.';

function _extractJson(text) {
  if (!text) return null;
  const m = text.match(/\\{[\\s\\S]*\\}/);
  try { return JSON.parse(m ? m[0] : text); } catch (e) { return null; }
}

async function pickLocator(intent, dom) {
  if (!HEAL) return null;
  const user = 'Intent: ' + JSON.stringify(intent) + '\\n\\nCandidates:\\n' + dom;
  // Up to 3 attempts with backoff. Node's fetch has NO retry of its own, and a
  // run with several parallel workers healing at once trips transient
  // "fetch failed" network errors and provider 429/529s — confirmed live: the
  // key was set and healing worked for other steps in the SAME run, while
  // these one-shot failures took their tests down with a misleading
  // "Set QA_AI_API_KEY" message (see healer.js).
  let lastErr = '';
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      let resp, text;
      if (PROVIDER === 'anthropic') {
        resp = await fetch(BASE.replace(/\\/$/, '') + '/v1/messages', {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'x-api-key': KEY,
                     'anthropic-version': '2023-06-01' },
          body: JSON.stringify({ model: MODEL, max_tokens: 300, system: SYS,
                                 messages: [{ role: 'user', content: user }] }),
        });
      } else {
        resp = await fetch(BASE.replace(/\\/$/, '') + '/chat/completions', {
          method: 'POST',
          headers: { 'content-type': 'application/json',
                     'authorization': 'Bearer ' + KEY },
          body: JSON.stringify({ model: MODEL, temperature: 0,
            messages: [{ role: 'system', content: SYS },
                       { role: 'user', content: user }] }),
        });
      }
      if (resp.status === 429 || resp.status >= 500) {
        throw new Error('provider HTTP ' + resp.status);
      }
      const j = await resp.json();
      if (PROVIDER === 'anthropic') {
        text = j && j.content && j.content[0] && j.content[0].text;
      } else {
        text = j && j.choices && j.choices[0] && j.choices[0].message &&
               j.choices[0].message.content;
      }
      return _extractJson(text);
    } catch (e) {
      lastErr = e.message;
      console.log('[heal] AI call failed (attempt ' + attempt + '/3): ' + e.message);
      if (attempt < 3) await new Promise(r => setTimeout(r, 1500 * attempt));
    }
  }
  console.log('[heal] giving up after 3 attempts: ' + lastErr);
  return null;
}
module.exports = { pickLocator, HEAL, PROVIDER, MODEL };
"""

# harvest.js — wraps QA Studio's DOM harvester (browser JS) as an evaluatable expr.
_PW_HARVEST_HEAD = """// DOM harvester — returns a compact JSON list of interactable elements for the
// AI healer to choose from. Body is QA Studio's shared harvest script.
function __qaHarvest() {
"""
_PW_HARVEST_TAIL = """
}
module.exports = { HARVEST_EXPR: '(' + __qaHarvest.toString() + ')()' };
"""

_PW_HEALER_JS = """// Self-healing locator resolver. Order per step key:
//   1) locator saved in locators.json   2) the generated seed locator
//   3) ask the configured AI provider to pick one from the live DOM, then save it
//      back into locators.json so every later run reuses it.
const { load, save, toSelector } = require('./locators');
const { pickLocator, HEAL, PROVIDER } = require('./aiClient');
const { HARVEST_EXPR } = require('./harvest');

// Arabic-aware text normalization for assertText: the app's on-screen copy and
// the AI-authored expected keywords routinely differ ONLY in orthography —
// tashkeel/diacritics (عذرًا vs عذراً), alef variants (أ/إ/آ vs ا), teh marbuta
// vs heh (ة/ه), yaa variants (ى/ي) and tatweel. Exact substring matching failed
// live on 8 assertions in one run whose ACTIONS had all succeeded. Both sides
// are normalized before comparing.
function normText(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/[ً-ْٰـ]/g, '')
    .replace(/[أإآ]/g, 'ا')
    .replace(/ة/g, 'ه')
    .replace(/ى/g, 'ي');
}

// Visible option/suggestion by text (custom dropdowns, autocomplete lists):
// the same script QA Studio's live inspection used.
const PICK_FN = __PICK_OPTION_FN__;
const SCROLL_MAX = __SCROLL_MAX__;
// Drag & drop target by text, and the table-cell check (shared with QA Studio).
const DROP_FN = __DROP_TARGET_FN__;
const TABLE_FN = __TABLE_CELL_FN__;
// Page-level checks (URL, title, counts, sort, pattern) and remember-and-reuse.
const PAGE_FN = __PAGE_CHECK_FN__;
const REMEMBER_FN = __REMEMBER_FN__;
// "Close X if it is shown" (pop-ups, ads, cookie banners).
const DISMISS_FN = __DISMISS_FN__;
// Wait-and-scroll before every action, pagination and storage steps (the same
// scripts QA Studio's inspection uses).
const READY_FN = __READY_FN__;
const SCROLL_PAGE_FN = __SCROLL_PAGE_FN__;
const PAGINATE_FN = __PAGINATE_FN__;
const STORAGE_FN = __STORAGE_FN__;
const SCROLL_SEARCH_STEPS = __SCROLL_SEARCH_STEPS__;
const PAGINATE_MAX = __PAGINATE_MAX__;
const READY = __READY_LIMITS__;
// click: enabled + not covered; input: enabled; view: shown; null: no wait.
function readyMode(verb) {
  const v = String(verb || 'click');
  if (['upload', 'frame', 'navigate', 'wait', 'scroll_to'].includes(v)) return null;
  if (['type', 'clear', 'key_press'].includes(v)) return 'input';
  return v === 'set_value' ? 'view' : 'click';
}
function _n(s) { return String(s == null ? '' : s).trim().replace(/ +/g, ' ').toLowerCase(); }
function cookieOk(value, spec) {
  const p = String(spec || '').split('|');
  const op = p[1] || 'exists', want = _n(p.slice(2).join('|'));
  if (op === 'absent') return value == null;
  if (value == null) return false;
  let v = String(value);
  try { v = decodeURIComponent(v); } catch (e) {}
  v = _n(v);
  return op === 'exists' ? true : op === 'is' ? v === want : v.indexOf(want) >= 0;
}

// Real functions (sent to the browser by Playwright's evaluate).
const READY_F = new Function('return ' + READY_FN)();
const SCROLL_F = new Function('return function (html, reset) { return (' + SCROLL_PAGE_FN + ')(reset, html.ownerDocument); }')();

// QA Studio key names (ENTER, ARROWUP…) → Playwright key names (Enter, ArrowUp…).
const _PW_KEYS = { ENTER: 'Enter', ESCAPE: 'Escape', ESC: 'Escape', TAB: 'Tab', SPACE: 'Space',
  BACKSPACE: 'Backspace', DELETE: 'Delete', ARROWUP: 'ArrowUp', ARROWDOWN: 'ArrowDown',
  ARROWLEFT: 'ArrowLeft', ARROWRIGHT: 'ArrowRight', HOME: 'Home', END: 'End',
  PAGEUP: 'PageUp', PAGEDOWN: 'PageDown' };
function pwKey(value) {
  // 'CONTROL+A' -> 'Control+a', 'SHIFT+TAB' -> 'Shift+Tab', 'ENTER' -> 'Enter'.
  const mods = { CONTROL: 'Control', CTRL: 'Control', SHIFT: 'Shift', ALT: 'Alt', META: 'Meta', CMD: 'Meta' };
  return String(value || 'Enter').split('+').map((p) => {
    const k = p.trim().toUpperCase().replace(/[_\\s]/g, '');
    if (mods[k]) return mods[k];
    if (_PW_KEYS[k]) return _PW_KEYS[k];
    return k.length === 1 ? k.toLowerCase() : p.trim();
  }).join('+');
}

// Data-driven runs: {{name}} placeholders take the current data row's value;
// {{random.email}}, {{random.number}}, {{random.text}}, {{today}}, {{today+3d}}
// and {{now}} are generated once per test. Unknown placeholders stay as written.
function qaDataKey(n) { return String(n || '').toLowerCase().replace(/[^0-9a-z]+/g, '_').replace(/^_+|_+$/g, ''); }
function qaDataFill(text, values, cache) {
  if (text == null || String(text).indexOf('{{') === -1) return text;
  const vals = {};
  Object.keys(values || {}).forEach((k) => { vals[qaDataKey(k)] = values[k]; });
  return String(text).replace(/\\{\\{\\s*([^{}]+?)\\s*\\}\\}/g, (all, raw) => {
    const key = qaDataKey(raw);
    if (key === 'app_user') return process.env.APP_USER || all;
    if (key === 'app_pass') return process.env.APP_PASS || all;
    if (Object.prototype.hasOwnProperty.call(vals, key)) return String(vals[key]);
    const g = String(raw).toLowerCase().replace(/\\s+/g, '');
    if (cache[g] != null) return cache[g];
    let v = null;
    if (g === 'random.email') v = 'qa.' + Date.now() + Math.floor(Math.random() * 1e4) + '@example.com';
    else if (g === 'random.number') v = String(100000 + Math.floor(Math.random() * 900000));
    else if (g === 'random.text') v = 'QA ' + Math.random().toString(36).slice(2, 8);
    else if (g === 'now') v = String(Date.now());
    else {
      const m = g.match(/^today(?:([+-])(\\d+)d)?$/);
      if (m) { const d = new Date(); d.setDate(d.getDate() + (m[1] === '-' ? -1 : 1) * Number(m[2] || 0)); v = d.toISOString().slice(0, 10); }
    }
    if (v == null) return all;
    cache[g] = v;
    return v;
  });
}

class Healer {
  constructor(page) { this.page = page; this.scope = page; this.store = load(); this.pendingPopup = null; this.data = {}; this.dataCache = {}; }

  // Data-driven test: the current row's values (see qaDataFill).
  useData(values) { this.data = values || {}; this.dataCache = {}; }
  fill(text) { return qaDataFill(text, this.data, this.dataCache); }

  async pickOption(text) {
    const end = Date.now() + 8000;
    do {
      const h = await this.page.evaluateHandle('(' + PICK_FN + ')(' + JSON.stringify(String(text)) + ')');
      const opt = h.asElement();
      if (opt) { await opt.click(); return; }
      await this.page.waitForTimeout(250);
    } while (Date.now() < end);
    throw new Error('Option "' + text + '" did not appear after opening the list');
  }

  // Infinite scroll / lazy load: scroll down until the step's element exists.
  async scrollTo(key, seed, intent) {
    const specs = [this.store[key], seed].concat((seed && seed.fallbacks) || []).filter(Boolean);
    for (let i = 0; i < SCROLL_MAX; i++) {
      for (const spec of specs) {
        if (await this._ok(spec, 500)) {
          try { await this._loc(spec).scrollIntoViewIfNeeded(); } catch (e) {}
          return;
        }
      }
      await this.page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      await this.page.waitForTimeout(1200);
    }
    await this.resolve(key, seed, intent);   // last chance: AI healing, or a clear failure
  }

  _loc(spec) { return this.scope.locator(toSelector(spec)).first(); }

  // Waits up to `ms` for the locator to be attached (count() alone never
  // waits, so a not-yet-rendered element went straight to AI healing).
  async _ok(spec, ms) {
    try {
      await this.scope.locator(toSelector(spec)).first().waitFor({ state: 'attached', timeout: ms || 0 });
      return true;
    } catch (e) { return false; }
  }

  // Order (same as the Selenium Healer): page load once -> saved locator
  // (short probe when a seed can back it up) -> seed (full wait) -> the
  // explorer's PROVEN fallbacks -> AI only as the last resort.
  async resolve(key, seed, intent) {
    try { await this.page.waitForLoadState('load', { timeout: 12000 }); } catch (e) {}
    const cached = this.store[key];
    if (cached && await this._ok(cached, seed ? 2000 : 12000)) return this._loc(cached);
    if (seed && await this._ok(seed, 12000)) return this._loc(seed);
    for (const fb of (seed && seed.fallbacks) || []) {
      if (await this._ok(fb, 2000)) return this._loc(fb);
    }
    // Lazy-loaded further down the page: scroll until it shows.
    const scrolled = await this._scrollSearch([cached, seed].concat((seed && seed.fallbacks) || []).filter(Boolean));
    if (scrolled) return scrolled;
    if (HEAL) {
      console.log('[heal] resolving ' + key + ' via AI (' + PROVIDER + ')');
      let dom = '[]';
      // ``scope`` is a Page at top level and a FrameLocator after enterFrame.
      // Both expose locator(), whereas FrameLocator has no page-level evaluate.
      try { dom = JSON.stringify(await this.scope.locator('html').evaluate(HARVEST_EXPR)); } catch (e) {}
      const picked = await pickLocator(intent, dom);
      if (picked && await this._ok(picked, 2000)) {
        this.store[key] = { by: picked.by, value: picked.value, source: 'healed',
                            resolvedAt: new Date().toISOString(), provider: PROVIDER };
        save(this.store, key);
        console.log('[heal] ' + key + ' -> ' + picked.by + '=' + picked.value);
        return this._loc(picked);
      }
    }
    // Honest failure message: "Set QA_AI_API_KEY" is only the cure when
    // healing was actually DISABLED — with a key set, this line used to blame
    // the key for what was really a transient AI/network failure or an
    // element that genuinely isn't on this page (confirmed live).
    throw new Error('Could not resolve step ' + key + (HEAL
      ? ' — cached/seed locators matched nothing and the AI heal attempt '
        + 'failed or found no match (see [heal] lines above; the element may '
        + 'not exist on this page at all).'
      : '. Set QA_AI_API_KEY to enable AI healing.'));
  }

  async _scrollStep(reset) {
    return this.scope.locator('html').evaluate(SCROLL_F, reset);
  }

  // Scroll down step by step until one of the locators matches; back to the top if not.
  async _scrollSearch(specs) {
    if (!specs.length) return null;
    for (let i = 0; i < SCROLL_SEARCH_STEPS; i++) {
      let moved;
      try { moved = await this._scrollStep(false); } catch (e) { return null; }
      await this.page.waitForTimeout(moved === 'end' ? 1100 : 350);
      for (const spec of specs) {
        if (await this._ok(spec, 200)) {
          console.log('[wait] found ' + (spec.value || '') + ' after scrolling the page');
          return this._loc(spec);
        }
      }
      if (moved === 'end') {
        try { if ((await this._scrollStep(false)) === 'end') break; } catch (e) { break; }
      }
    }
    try { await this._scrollStep(true); } catch (e) {}
    return null;
  }

  // Before an action: visible, centred, enabled, not moving and (for clicks)
  // not covered. Never throws - after the limit the action runs anyway.
  async _ready(el, verb) {
    const mode = readyMode(verb);
    try { await el.waitFor({ state: mode ? 'visible' : 'attached', timeout: READY.wait }); } catch (e) {}
    try { await el.scrollIntoViewIfNeeded({ timeout: 2000 }); } catch (e) {}
    if (!mode) return;
    const start = Date.now();
    let last = null;
    for (;;) {
      let raw = 'ok|';
      try { raw = String(await el.evaluate(READY_F, mode)); } catch (e) { return; }
      const state = raw.split('|')[0], box = raw.slice(state.length + 1);
      if (state === 'gone' || (state === 'ok' && box === last)) return;
      last = state === 'ok' ? box : null;
      const limit = state === 'disabled' ? READY.disabled : state === 'covered' ? READY.covered : READY.wait;
      if (Date.now() - start > limit) {
        console.log('[wait] element still ' + state + ' after ' + Math.round(limit / 1000) + ' s - acting anyway');
        return;
      }
      await this.page.waitForTimeout(150);
    }
  }

  async act(key, verb, seed, intent, value) {
    if (verb === 'navigate' || verb === 'wait') return;
    if (verb === 'scroll_to') return this.scrollTo(key, seed, intent);
    const el = await this.resolve(key, seed, intent);
    await this._ready(el, verb);
    switch (verb) {
      case 'type':   await el.fill(value == null ? '' : String(value)); break;
      case 'select': {
        const opts = String(value == null ? '' : value).split(';').map((o) => o.trim()).filter(Boolean);
        const tag = await el.evaluate((n) => n.tagName.toLowerCase());
        if (tag === 'select') {
          await el.selectOption(opts.length > 1 ? opts.map((label) => ({ label })) : { label: String(value) });
        } else {
          // Custom dropdown: open the trigger, then pick the option by its text.
          await el.click();
          await this.pickOption(String(value));
        }
        break;
      }
      case 'drag': {
        // Drop target = the element showing `value`; dragTo covers both mouse-
        // driven (jQuery UI, sortable lists) and HTML5 draggable items.
        let target = null;
        const end = Date.now() + 8000;
        while (!target && Date.now() < end) {
          target = (await this.page.evaluateHandle('(' + DROP_FN + ')(' + JSON.stringify(String(value)) + ')')).asElement();
          if (!target) await this.page.waitForTimeout(250);
        }
        if (!target) throw new Error('Drop target "' + value + '" not found');
        // Real mouse down / move / up: works for mouse-driven widgets and, in
        // Chromium, for HTML5 draggable items too.
        await el.hover();
        await this.page.mouse.down();
        const box = await target.boundingBox();
        await this.page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 12 });
        await this.page.mouse.up();
        await this.page.waitForTimeout(300);
        break;
      }
      case 'autocomplete': {
        const raw = String(value == null ? '' : value);
        const cut = raw.indexOf('=>');
        const typed = (cut >= 0 ? raw.slice(0, cut) : raw).trim();
        const pick = (cut >= 0 ? raw.slice(cut + 2) : raw).trim() || typed;
        await el.click();
        await el.fill('');
        await el.pressSequentially(typed || pick, { delay: 50 });   // real keystrokes fire the suggestions
        await this.pickOption(pick);
        break;
      }
      case 'hover':  await el.hover(); break;
      case 'double_click': await el.dblclick(); break;
      case 'right_click':
        // Fire the page's right-click handlers without the browser's native menu
        // (it cannot be closed from a test and stays open over the page in a
        // headed run). setTimeout lets evaluate return before an alert() opens.
        await el.hover();
        await el.evaluate((n) => {
          const r = n.getBoundingClientRect();
          const o = { bubbles: true, cancelable: true, composed: true, view: window, button: 2,
            buttons: 2, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
          setTimeout(() => { n.dispatchEvent(new MouseEvent('mousedown', o));
            n.dispatchEvent(new MouseEvent('mouseup', o));
            n.dispatchEvent(new MouseEvent('contextmenu', o)); }, 0);
        });
        await this.page.waitForTimeout(200);
        break;
      case 'set_value':
        // date/time/range/color: set the value and fire input + change
        await el.evaluate((node, v) => {
          const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value');
          if (d && d.set) d.set.call(node, v); else node.value = v;
          node.dispatchEvent(new Event('input', { bubbles: true }));
          node.dispatchEvent(new Event('change', { bubbles: true }));
        }, String(value == null ? '' : value));
        break;
      case 'upload': await el.setInputFiles(String(value)); break;
      case 'key_press': await el.press(pwKey(value)); break;
      case 'clear': await el.fill(''); break;
      case 'click':
      default:
        try { await el.click({ timeout: 10000 }); }
        catch (e) {
          // Still blocked (an overlay that never goes away): JavaScript click,
          // like the Selenium tests - only for an element that is really shown.
          if (!(await el.isVisible())) throw e;
          console.log('[wait] click blocked - using a JavaScript click');
          await el.evaluate((n) => n.click());
        }
        break;
    }
  }

  async enterFrame(key, seed, intent) {
    const frame = await (await this.resolve(key, seed, intent)).contentFrame();
    if (!frame) throw new Error('Could not enter frame for ' + key);
    this.scope = frame;
  }

  prepareDialog(action, text) {
    this.page.once('dialog', async dialog => {
      if (action === 'dismiss') await dialog.dismiss();
      else await dialog.accept(text == null ? undefined : String(text));   // prompt answer
    });
  }

  exitFrame() { this.scope = this.page; }

  prepareWindow() {
    this.opener = this.page;
    this.pendingPopup = this.page.context().waitForEvent('page');
  }

  async backToOriginalWindow(closeCurrent) {
    const opener = this.opener || this.page.context().pages()[0];
    if (closeCurrent && this.page !== opener) await this.page.close();
    this.page = opener;
    this.scope = opener;
    await opener.bringToFront();
  }

  // In a table row containing `row`, the column headed `column` shows `expected`.
  /** URL / title / row or text count / column sort / text pattern, retried for 10 s. */
  async assertPage(check, value) {
    const end = Date.now() + 10000;
    if (check === 'cookie') {
      // Read through the browser context, so HttpOnly cookies count too.
      const name = String(value).split('|')[0].trim();
      do {
        const c = (await this.page.context().cookies()).find((k) => k.name === name);
        if (cookieOk(c ? c.value : null, value)) return true;
        await this.page.waitForTimeout(300);
      } while (Date.now() < end);
      return false;
    }
    const args = [check, value].map((v) => JSON.stringify(String(v))).join(',');
    do {
      try {
        if (await this.page.evaluate('(' + PAGE_FN + ')(' + args + ', document, location)')) return true;
      } catch (e) {}
      await this.page.waitForTimeout(300);
    } while (Date.now() < end);
    return false;
  }

  /** Reads the first page text matching `pattern`; later steps use it as {{name}}. */
  // Back / forward / refresh, and "close X if it is shown" (not a failure when
  // nothing is shown).
  async _paginate(what) {
    const end = Date.now() + 4000;
    let got = '';
    do {
      try { got = await this.page.evaluate('(' + PAGINATE_FN + ')(' + JSON.stringify(String(what)) + ', document)'); }
      catch (e) { got = ''; }
      if (got) return got;
      await this.page.waitForTimeout(300);
    } while (Date.now() < end);
    return got;
  }

  async _afterPageChange() {
    await this.page.waitForTimeout(800);
    try { await this.page.waitForLoadState('load', { timeout: 10000 }); } catch (e) {}
  }

  // Next page (optionally until `target` is shown) or page number `target`.
  async _pages(verb, target) {
    if (verb === 'go_to_page' || !target) {
      const what = verb === 'go_to_page' ? target : 'next';
      const got = await this._paginate(what);
      if (!got) throw new Error('No "' + what + '" control in the pagination');
      if (got === 'end') throw new Error('Already on the last page');
      await this._afterPageChange();
      return;
    }
    for (let n = 0; n <= PAGINATE_MAX; n++) {
      const txt = await this.page.evaluate(() => (document.body ? document.body.innerText : ''));
      if (normText(txt).indexOf(normText(target)) >= 0) { console.log('[heal] found "' + target + '" on page ' + (n + 1)); return; }
      const got = await this._paginate('next');
      if (!got || got === 'end') throw new Error('"' + target + '" was not found on any page (' + (n + 1) + ' checked)');
      await this._afterPageChange();
    }
    throw new Error('"' + target + '" was not found within ' + PAGINATE_MAX + ' pages');
  }

  async pageAction(verb, value) {
    if (verb === 'next_page' || verb === 'go_to_page') return this._pages(verb, String(this.fill(value) || '').trim());
    if (verb === 'set_cookie') {
      const v = String(this.fill(value) || ''), i = v.indexOf('=');
      const name = (i < 0 ? v : v.slice(0, i)).trim();
      await this.page.context().addCookies([{ name, value: i < 0 ? '' : v.slice(i + 1).trim(), url: this.page.url() }]);
      return;
    }
    if (verb === 'delete_cookies') {
      const ctx = this.page.context(), name = String(value || '').trim();
      if (!name) { await ctx.clearCookies(); return; }
      const keep = (await ctx.cookies()).filter((c) => c.name !== name);
      await ctx.clearCookies();
      if (keep.length) await ctx.addCookies(keep);
      return;
    }
    if (verb === 'set_storage' || verb === 'clear_storage') {
      const p = String(this.fill(value) || '').split('|'), set = verb === 'set_storage';
      const args = [set ? 'set' : 'clear', p[0] || (set ? 'local' : 'all'), p[1] || '', p.slice(2).join('|')];
      await this.page.evaluate('(' + STORAGE_FN + ')(' + args.map((x) => JSON.stringify(x)).join(',') + ', window)');
      return;
    }
    if (verb === 'auth_open') {
      // Basic / digest auth: the browser answers the challenge (password never logged).
      const p = String(this.fill(value) || '').split('|');
      const cur = this.page.url();
      const u = new URL(p[0].trim(), /^https?:/i.test(cur) ? cur : (process.env.APP_BASE_URL || undefined));
      const ctx = this.page.context();
      const user = p[1] || '', pass = p[2] || '';
      // Ask the page which scheme it wants. Basic: send the header on that page
      // only (setHTTPCredentials mid-test is deprecated and was flaky: the
      // browser may reuse an earlier answer). Digest: the browser answers it.
      let www = '';
      try {
        const res = await ctx.request.get(u.toString(), { failOnStatusCode: false, maxRedirects: 0 });
        www = String(res.headers()['www-authenticate'] || '');
      } catch (e) { /* unreachable: try Basic */ }
      if (/^\\s*digest/i.test(www) && typeof ctx.setHTTPCredentials === 'function') {
        await ctx.setHTTPCredentials({ username: user, password: pass });
        await this.page.goto(u.toString());
      } else {
        const target = u.origin + u.pathname;
        const header = 'Basic ' + Buffer.from(user + ':' + pass).toString('base64');
        const handler = (route) => {
          const r = route.request();
          if ((r.url().split('?')[0]) !== target) return route.continue();
          return route.continue({ headers: Object.assign({}, r.headers(), { authorization: header }) });
        };
        await this.page.route('**/*', handler);
        try { await this.page.goto(u.toString()); }
        finally { await this.page.unroute('**/*', handler); }
      }
      console.log('[heal] opened ' + u.origin + u.pathname + ' with HTTP authentication');
      return;
    }
    if (verb === 'go_back') { await this.page.goBack(); return; }
    if (verb === 'go_forward') { await this.page.goForward(); return; }
    if (verb === 'refresh') { await this.page.reload(); return; }
    const end = Date.now() + 4000;
    let got = '';
    do {
      try { got = await this.page.evaluate('(' + DISMISS_FN + ')(' + JSON.stringify(String(value || '')) + ', document)'); }
      catch (e) { got = ''; }
      if (got) break;
      await this.page.waitForTimeout(300);
    } while (Date.now() < end);
    console.log(got ? '[heal] closed "' + got + '"' : '[heal] nothing to close (not shown)');
  }

  async remember(pattern, name) {
    const end = Date.now() + 10000;
    let v = '';
    do {
      try { v = await this.page.evaluate('(' + REMEMBER_FN + ')(' + JSON.stringify(String(pattern)) + ', document)'); } catch (e) { v = ''; }
      if (v) break;
      await this.page.waitForTimeout(300);
    } while (Date.now() < end);
    if (!v) throw new Error('Nothing on the page matches ' + pattern + ' to remember as ' + name);
    this.data = Object.assign({}, this.data || {}, { [name]: v });
    console.log('[heal] remembered ' + name + ' = ' + v);
  }

  async assertTableCell(row, column, expected) {
    const end = Date.now() + 10000;
    do {
      try {
        const args = [row, column, expected].map((v) => JSON.stringify(String(v))).join(',');
        if (await this.page.evaluate('(' + TABLE_FN + ')(' + args + ')')) return true;
      } catch (e) {}
      await this.page.waitForTimeout(300);
    } while (Date.now() < end);
    return false;
  }

  // Downloads: listen BEFORE the click that starts it, then check the file name.
  prepareDownload() {
    this.pendingDownload = this.page.waitForEvent('download', { timeout: 20000 });
  }

  async assertDownloaded(name) {
    try {
      const d = await this.pendingDownload;
      this.pendingDownload = null;
      const file = d.suggestedFilename();
      await d.saveAs(require('path').join('downloads', file));
      return file.toLowerCase().includes(String(name).toLowerCase());
    } catch (e) { console.log('[heal] assertDownloaded failed: ' + e.message); return false; }
  }

  async assertValue(key, seed, intent, expected) {
    try {
      const el = await this.resolve(key, seed, intent);
      const want = normText(expected);
      const end = Date.now() + 5000;
      do {
        // An input's value, or the text a custom dropdown shows.
        let shown = '';
        // A native <select> shows its selected option's text, not its value.
        try {
          shown = await el.evaluate((n) => n.tagName === 'SELECT'
            ? Array.from(n.selectedOptions).map((o) => o.text + ' ' + o.value).join(' ')
            : (('value' in n && n.value) || n.innerText || ''));
        } catch (e) { shown = await el.innerText(); }
        if (normText(shown).includes(want)) return true;
        await this.page.waitForTimeout(250);
      } while (Date.now() < end);
      return false;
    } catch (e) { console.log('[heal] assertValue ' + key + ' failed: ' + e.message); return false; }
  }

  async assertInvalid(key, seed, intent) {
    try {
      return await (await this.resolve(key, seed, intent))
        .evaluate((node) => (node.checkValidity ? !node.checkValidity() : false));
    } catch (e) { console.log('[heal] assertInvalid ' + key + ' failed: ' + e.message); return false; }
  }

  async switchWindow() {
    if (!this.pendingPopup) throw new Error('No popup was prepared before window switch');
    this.page = await this.pendingPopup;
    await this.page.waitForLoadState('domcontentloaded');
    await this.page.waitForFunction(() =>
      location.href !== 'about:blank' && !!(document.body && document.body.innerText.trim()));
    this.scope = this.page;
    this.pendingPopup = null;
  }

  // Late content ('Hello World!' after a loading bar): wait up to 10 s for
  // the element to become visible instead of reading its state once.
  async assertVisible(key, seed, intent) {
    try {
      const el = await this.resolve(key, seed, intent);
      await el.waitFor({ state: 'visible', timeout: 10000 });
      return true;
    } catch (e) { console.log('[heal] assertVisible ' + key + ' failed: ' + e.message); return false; }
  }

  async assertVisibleNow(key, seed, intent) {
    // The failure reason is LOGGED, not swallowed: without this, a step that
    // couldn't resolve at all surfaced in the report as a bare
    // "expect(received).toBeTruthy() — received false" with zero hint that
    // the element was never found (vs. found but hidden).
    try { return await (await this.resolve(key, seed, intent)).isVisible(); }
    catch (e) { console.log('[heal] assertVisible ' + key + ' failed: ' + e.message); return false; }
  }

  async assertChecked(key, seed, intent) {
    try { return await (await this.resolve(key, seed, intent)).isChecked(); }
    catch (e) { console.log('[heal] assertChecked ' + key + ' failed: ' + e.message); return false; }
  }

  async assertEnabled(key, seed, intent, enabled) {
    try {
      // Often enabled only after a loading bar: wait up to 10 s.
      const el = await this.resolve(key, seed, intent);
      const end = Date.now() + 10000;
      do {
        if ((await el.isEnabled()) === enabled) return true;
        await this.page.waitForTimeout(250);
      } while (Date.now() < end);
      return false;
    }
    catch (e) { console.log('[heal] assertEnabled ' + key + ' failed: ' + e.message); return false; }
  }

  async assertSelected(key, seed, intent, expected) {
    try {
      // A multi-select can have several checked options; read them all.
      const texts = await (await this.resolve(key, seed, intent)).locator('option:checked').allTextContents();
      return texts.some((t) => normText(t).includes(normText(expected)));
    } catch (e) { console.log('[heal] assertSelected ' + key + ' failed: ' + e.message); return false; }
  }

  async assertHidden(key, seed) {
    // Absence is not a healing problem: retrying an AI locator search after a
    // successful removal could bind a different, still-visible product. Test
    // only the exact locator captured immediately before the removing action.
    if (!seed || !seed.by || !seed.value) return false;
    // Removal is often async (a loading bar first): wait up to 10 s. Any
    // still-visible match counts (isVisible() alone throws on several matches).
    const end = Date.now() + 10000;
    do {
      let shown = false;
      try {
        shown = await this._loc(seed).evaluateAll((ns) =>
          ns.some((n) => !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length)));
      } catch (e) { shown = false; }
      if (!shown) return true;
      await this.page.waitForTimeout(250);
    } while (Date.now() < end);
    return false;
  }

  // Verify by PAGE TEXT (no locator, no AI): true if the page contains ANY
  // keyword — Arabic-normalized on both sides (see normText above).
  async assertText(keywords) {
    const end = Date.now() + 8000;
    do {
      let t = '';
      try { t = normText(await this.scope.locator('body').innerText()); }
      catch (e) {}
      for (const k of keywords) {
        if (k && t.includes(normText(k))) return true;
      }
      await this.page.waitForTimeout(300);
    } while (Date.now() < end);
    return false;
  }
}
module.exports = { Healer };
"""

_PW_AUTH_JS = """// Login helpers. Credentials come from the environment, never hard-coded.
require('dotenv').config();

// The language the LOGIN PAGE should render in — must match the language of
// the generated test cases' expected messages. The frozen APP_LOGIN_URL can
// carry a stale ui_locales=… (confirmed live: ui_locales=en forced the page
// into English while every assertion expected the Arabic version of the SAME
// message — behavior passed, language failed). Baked from QA Studio's output
// language at generation time; override per-environment via APP_UI_LOCALE.
const UI_LOCALE = process.env.APP_UI_LOCALE || '__UI_LOCALE__';

function _withLocale(url) {
  if (!UI_LOCALE || !/^https?:/.test(url)) return url;
  if (/[?&]ui_locales=/.test(url)) {
    return url.replace(/([?&])ui_locales=[^&]*/, '$1ui_locales=' + UI_LOCALE);
  }
  return url + (url.includes('?') ? '&' : '?') + 'ui_locales=' + UI_LOCALE;
}

async function openLoginPage(page) {
  await page.goto(_withLocale(process.env.APP_LOGIN_URL || process.env.APP_BASE_URL || '/'));
}

async function performLogin(page) {
  await openLoginPage(page);
  // Same breadth as QA Studio's explorer login: also '#user-name'-style fields.
  const user = page.locator('#username, input[type=email], input[name=email], input[name=username], '
                            + 'input[name*=user i], input[id*=user i]').first();
  const pass = page.locator('#password, input[type=password]').first();
  try { await user.fill(process.env.APP_USER || ''); } catch (e) {}
  try { await pass.fill(process.env.APP_PASS || ''); } catch (e) {}
  await page.locator('#kc-login, button[type=submit], input[type=submit]').first().click();
  try { await page.waitForLoadState('networkidle', { timeout: 12000 }); } catch (e) {}
}
// App cases start here: the app's base URL, logging in first when that URL
// shows the login form (some sites' root IS the login page even with a
// session, e.g. SauceDemo) — the login then lands on the app's home.
async function openAppHome(page) {
  await page.goto(process.env.APP_BASE_URL || '/');
  let loginForm = false;
  try {
    await page.locator('input[type=password]').first().waitFor({ state: 'visible', timeout: 2000 });
    // A real login form also has a username/email field; a page with only a
    // password input (a form under test) is not a login.
    loginForm = (await page.locator('#username, #user-name, input[type=email], input[name=email], '
      + 'input[name=username], input[name*=user i], input[id*=user i]').count()) > 0;
  } catch (e) {}
  if (loginForm) await performLogin(page);
}
module.exports = { openLoginPage, performLogin, openAppHome };
"""

_PW_README = """# QA Studio — self-healing Playwright tests

Generated by QA Studio. Locators live in `locators.json` (committed): it is seeded
at generation time, and when a step's locator fails at RUNTIME the framework asks
your AI provider to pick the right element from the live DOM, then writes the
verified locator back into `locators.json` — so the AI is asked at most once per
step and every later run reuses it.

## Any environment, no regeneration
Everything environment-specific — the app URLs and credentials — comes from `.env`
(git-ignored) or real environment variables, never from the generated code. Copy
`.env.example` to `.env`, fill it in, and the same project runs against dev / test /
staging / prod. Because the UI is identical across environments, `locators.json` is
shared (committed).

## Run
Needs Node.js 18+ (https://nodejs.org). QA Studio already ran `npm install` here
when Node was available (that also downloads the Chromium browser).

- **VS Code:** Terminal → Run Task → **Run tests** (installs anything missing first).
- **IntelliJ / WebStorm:** pick the **Run tests** configuration (top-right) and Run.
- **Terminal:** `npm install` once, then `npm test`.

`.env` already holds the app URLs (and the test account, if you chose to save it
in QA Studio). Otherwise set `APP_USER` / `APP_PASS` there or as environment
variables. `QA_AI_API_KEY` is only needed for runtime self-healing.

The browser is visible while tests run (`HEADLESS=true` or `npm run test:headless`
hides it). After every run the HTML report opens (`OPEN_REPORT=false` turns that
off) and is emailed when `REPORT_EMAIL_TO`, `SMTP_USER` and `SMTP_PASS` (a Gmail
App Password) are set in `.env`.

Browsers: `BROWSER=` in `.env` — `chrome`, `edge`, `firefox`, a list (`chrome,firefox`)
or `all` (empty = Playwright's own Chromium). They run side by side; `PARALLEL=false` in
`.env` runs one test at a time. `PROGRESS=off` hides the progress window. Or `npm run test:chrome` / `test:edge` /
`test:firefox` / `test:all` (IntelliJ: **Run tests (Edge)** etc.). Each browser is a
Playwright project; the reports group the results per browser. Every test's video
(`VIDEO=on`, `retain-on-failure` or `off`) plays in `qa-studio-detailed.html`.

## Self-healing
Watch stdout for `[heal] resolving ...` and `[heal] <step> -> by=value`. Each
resolved locator is written back into `locators.json` and should be committed so
your team and every environment reuse it.
"""


def _pw_env_real(cfg):
    return (
        "# QA Studio — environment-specific config (GIT-IGNORED). Env vars override.\n"
        "APP_BASE_URL=%s\n"
        "APP_LOGIN_URL=%s\n"
        "APP_USER=\n"
        "APP_PASS=\n"
        "# Login-page language (ui_locales) — must match the tests' expected-message language.\n"
        "APP_UI_LOCALE=%s\n"
        "# AI self-healing — paste a key to enable auto-repair of locators.\n"
        "QA_AI_API_KEY=\n"
        "QA_AI_PROVIDER=%s\n"
        "QA_AI_BASE_URL=%s\n"
        "QA_AI_MODEL=%s\n"
        % (cfg.get("base_url", ""), cfg.get("login_url", ""),
           cfg.get("ui_locale", "ar"),
           cfg.get("ai_provider", "anthropic"),
           cfg.get("ai_base_url", "https://api.anthropic.com"),
           cfg.get("ai_model", "claude-sonnet-4-6")))


def _spec_name(sid):
    safe = "".join(ch if (ch.isalnum()) else "" for ch in str(sid)) or "0"
    return "story-%s.spec.js" % safe


def _login_steps_as_authored(case):
    """Same rule as engine._login_steps_as_authored: a login case whose
    username/password/submit steps were all verified live is emitted as written
    instead of through the shared login helper (which opens the base URL)."""
    if not case.get("explicit_login_flow"):
        return False
    acts = [i for i in (case.get("intents") or [])
            if _is_login_transition_action(i) and i.get("verb") != "navigate"]
    return bool(acts) and all((i.get("live_locator") or {}).get("verified") for i in acts)


def _starts_with_absolute_navigate(case):
    first = next((i for i in (case.get("intents") or []) if i.get("role") == "action"), {})
    return (first.get("verb") == "navigate" and
            str(first.get("value") or "").lower().startswith(("http://", "https://")))


def _is_login_transition_action(intent):
    """Whether an authored action is fulfilled by the shared login helper."""
    if intent.get("role") != "action":
        return False
    text = " ".join([
        str(intent.get("target") or "").lower(),
        " ".join(str(k).lower() for k in (intent.get("keywords") or [])),
    ])
    verb = str(intent.get("verb") or "")
    is_user = any(k in text for k in ("username", "user name", "email", "e-mail", "اسم المستخدم", "البريد"))
    is_password = any(k in text for k in ("password", "passcode", "كلمة المرور", "كلمه المرور"))
    is_submit = verb == "click" and any(k in text for k in ("login", "log in", "sign in", "signin", "تسجيل الدخول"))
    is_login_page = verb == "navigate" and any(k in text for k in ("login", "log in", "sign in", "signin", "تسجيل الدخول"))
    return is_user or is_password or is_submit or is_login_page


# Runtime credential placeholders written into saved plans in place of the
# real test credentials — must equal engine.APP_USER_TOKEN / APP_PASS_TOKEN
# (not imported: this module stays importable without engine).
_APP_USER_TOKEN = "{{APP_USER}}"
_APP_PASS_TOKEN = "{{APP_PASS}}"


def _js_value(value, env_read):
    """JS expression for a typed value: placeholders read the credential from
    the environment at run time; anything else is an authored literal."""
    if value == _APP_USER_TOKEN:
        return env_read("APP_USER")
    if value == _APP_PASS_TOKEN:
        return env_read("APP_PASS")
    return json.dumps(value, ensure_ascii=False)


_CRED_MISSING = ("%s is not set: add it to .env (copy .env.example) or to the run "
                 "configuration's environment variables, then run again.")


def _pw_env(name):
    # A missing credential fails right here with a clear message, instead of
    # typing "" and failing later on a misleading check (e.g. the URL).
    return "(process.env.%s || (() => { throw new Error(%s); })())" % (name, json.dumps(_CRED_MISSING % name))


def _cy_env(name):
    return "(Cypress.env('%s') || (() => { throw new Error(%s); })())" % (name, json.dumps(_CRED_MISSING % name))


def _with_fallbacks(seed, intent, xpath_ok=True):
    """Attach the explorer's PROVEN fallback locators (engine._prove_locator)
    to a step's seed, so the runtime healer tries them before any AI call.
    Cypress can't evaluate XPath without a plugin, so it gets none."""
    if seed is None:
        return None
    fbs = []
    for fb in ((intent.get("live_locator") or {}).get("fallbacks") or []):
        by = fb.get("by")
        if not (by and fb.get("value")) or (by == "xpath" and not xpath_ok):
            continue
        fbs.append({"by": "css" if by == "cssSelector" else by, "value": fb["value"]})
    if fbs:
        seed = dict(seed, fallbacks=fbs)
    return seed


def _emit_pw_intent(lines, key, intent, seed_fn, seeds, app_page=False):
    """Append the Playwright JS for one intent. Mirrors engine._emit_intent: keyed
    steps record a known seed into `seeds` for locators.json pre-seeding.
    `app_page` — see engine._seed_locator_for_intent: disables the login-form
    seed rules for bucket-3 (post-login app page) cases."""
    role = intent.get("role")
    target = intent.get("target", "")
    ij = {"target": target, "keywords": intent.get("keywords", []),
          "kind": intent.get("kind", "any"), "verb": intent.get("verb", "")}
    by, val, _known = seed_fn(intent, app_page=app_page)
    seed = None if val == "TODO_RESOLVE_AT_RUNTIME" else {"by": by, "value": val}
    seed = _with_fallbacks(seed, intent)

    def _record():
        if seed is not None:
            seeds[key] = {"by": by, "value": val,
                          "source": "inspection" if intent.get("live_locator") else "seed"}

    if role == "precondition":
        lines.append("    // precondition (no UI action): %s" %
                     (str(target)[:70].replace("\n", " ")))
        return
    if role == "assertion" and (intent.get("check") or "") == "table":
        row, col, want = (str(intent.get("value") or "").split("|") + ["", "", ""])[:3]
        lines.append("    expect(await heal.assertTableCell(%s, %s, %s)).toBeTruthy();"
                     % tuple(json.dumps(x.strip(), ensure_ascii=False) for x in (row, col, want)))
        return
    if role == "assertion" and (intent.get("check") or "") == "download":
        lines.append("    expect(await heal.assertDownloaded(%s)).toBeTruthy();"
                     % json.dumps(str(intent.get("value") or ""), ensure_ascii=False))
        return
    if role == "assertion" and (intent.get("check") or "") in _E_PAGE_CHECKS:
        lines.append("    expect(await heal.assertPage(%s, %s), %s).toBeTruthy();"
                     % (json.dumps(intent["check"]), json.dumps(str(intent.get("value") or ""), ensure_ascii=False),
                        json.dumps(_page_label(intent), ensure_ascii=False)))
        return
    if role == "action" and (intent.get("verb") or "") == "remember":
        lines.append("    await heal.remember(%s, %s);"
                     % (json.dumps(str(intent.get("value") or ""), ensure_ascii=False),
                        json.dumps(str(intent.get("remember_as") or "value"), ensure_ascii=False)))
        return
    if role == "action" and (intent.get("verb") or "") in _E_PAGE_ACTIONS:
        lines.append("    await heal.pageAction(%s, %s);"
                     % (json.dumps(intent["verb"]), json.dumps(str(intent.get("value") or ""), ensure_ascii=False)))
        return
    if role == "assertion":
        kind = (intent.get("kind") or "").lower()
        kws = [k for k in (intent.get("keywords") or []) if k] or ([target] if target else [])
        if intent.get("assert_before_action"):
            lines.append("    expect(await heal.assertHidden(%s, %s)).toBeTruthy();"
                         % (json.dumps(key), json.dumps(seed, ensure_ascii=False)))
            _record()
            return
        check = (intent.get("check") or "").lower()
        if check == "checked":
            lines.append("    expect(await heal.assertChecked(%s, %s, %s)).toBeTruthy();"
                         % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                            json.dumps(ij, ensure_ascii=False)))
            _record()
            return
        if check in ("enabled", "disabled"):
            lines.append("    expect(await heal.assertEnabled(%s, %s, %s, %s)).toBeTruthy();"
                         % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                            json.dumps(ij, ensure_ascii=False),
                            "true" if check == "enabled" else "false"))
            _record()
            return
        if check == "invalid":
            lines.append("    expect(await heal.assertInvalid(%s, %s, %s)).toBeTruthy();"
                         % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                            json.dumps(ij, ensure_ascii=False)))
            _record()
            return
        if check == "value":
            want = str(intent.get("value") or intent.get("expected") or "")
            lines.append("    expect(await heal.assertValue(%s, %s, %s, %s)).toBeTruthy();"
                         % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                            json.dumps(ij, ensure_ascii=False), json.dumps(want, ensure_ascii=False)))
            _record()
            return
        if check in ("selected", "selected_value", "value_selected"):
            expected = (intent.get("value") or (kws[0] if kws else "") or
                        intent.get("expected") or target)
            lines.append("    expect(await heal.assertSelected(%s, %s, %s, %s)).toBeTruthy();"
                         % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                            json.dumps(ij, ensure_ascii=False), json.dumps(expected, ensure_ascii=False)))
            _record()
            return
        if (seed is None and not intent.get("inspection_unresolved") and
                (kind in ("text", "message", "menu", "validation", "error")
                 or not kind)):
            lines.append("    expect(await heal.assertText(%s)).toBeTruthy();"
                         % json.dumps(kws, ensure_ascii=False))
            return
        _todo = "  // TODO verify locator (resolved at runtime)" if seed is None else ""
        lines.append("    expect(await heal.assertVisible(%s, %s, %s)).toBeTruthy();%s"
                     % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                        json.dumps(ij, ensure_ascii=False), _todo))
        _record()
        return
    verb = intent.get("verb") or "click"
    value = intent.get("value", "")
    if verb == "dialog_accept" or verb == "dialog_dismiss":
        # The listener is installed immediately before the authored click that
        # opens the dialog; this intent documents the completed context change.
        lines.append("    // browser dialog %s was handled by the preceding action" %
                     ("dismiss" if verb == "dialog_dismiss" else "accept"))
        return
    if verb == "window_switch":
        lines.append("    await heal.switchWindow();")
        return
    if verb == "frame_exit":
        lines.append("    heal.exitFrame();")
        return
    if verb in ("window_back", "window_close"):
        lines.append("    await heal.backToOriginalWindow(%s);"
                     % ("true" if verb == "window_close" else "false"))
        return
    if verb == "navigate" and str(value).lower().startswith(("http://", "https://")):
        # Navigation is a state transition captured from the source URL, not a
        # locator for a link on whichever page happened to precede it.
        lines.append("    await page.goto(%s);" % json.dumps(value, ensure_ascii=False))
        return
    if verb == "frame":
        _todo = "  // TODO verify locator (resolved at runtime)" if seed is None else ""
        lines.append("    await heal.enterFrame(%s, %s, %s);%s"
                     % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                        json.dumps(ij, ensure_ascii=False), _todo))
        _record()
        return
    _todo = "  // TODO verify locator (resolved at runtime)" if seed is None else ""
    lines.append("    await heal.act(%s, %s, %s, %s, %s);%s"
                 % (json.dumps(key), json.dumps(verb),
                    json.dumps(seed, ensure_ascii=False),
                    json.dumps(ij, ensure_ascii=False), _js_value(value, _pw_env),
                    _todo))
    _record()


_JS_STR_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def _page_label(intent):
    import engine as _E
    return "expected " + _E._page_check_label(intent.get("check"), intent.get("value"))


class _PageChecks(tuple):
    """engine._PAGE_CHECKS, read lazily (engine imports this module's callers)."""
    def __contains__(self, item):
        import engine as _E
        return item in _E._PAGE_CHECKS


_E_PAGE_CHECKS = _PageChecks()


class _PageActions(tuple):
    """engine._PAGE_ACTIONS (no-locator steps), read lazily like _PageChecks."""
    def __contains__(self, item):
        import engine as _E
        return item in _E._PAGE_ACTIONS


_E_PAGE_ACTIONS = _PageActions()


def _wrap_data_literals(lines, fn):
    """Wrap every JS string literal that holds a {{data}} placeholder in fn(...)
    so the value is filled from the current data row when the test runs."""
    import engine as _E

    def sub(m):
        lit = m.group(0)
        try:
            text = json.loads(lit)
        except Exception:
            return lit
        return "%s(%s)" % (fn, lit) if _E._has_data_tokens(text) else lit
    return [_JS_STR_RE.sub(sub, ln) if "{{" in ln else ln for ln in lines]


def _case_data_rows(c):
    rows = c.get("data") or (c.get("tc") or {}).get("data") or []
    return [{"name": str(r.get("name") or "Row %d" % (i + 1)), "values": dict(r.get("values") or {})}
            for i, r in enumerate(rows)]


def _pw_spec(story, cases, seed_fn, seeds):
    """Emit a Playwright spec file for one story from compiled intents.

    Session model (mirrors the Selenium output's one-browser-per-class flow,
    translated to Playwright's PER-TEST ISOLATION — the naive port ran every
    app-page test logged OUT, confirmed live: profile tests redirected to the
    login page, where their elements genuinely don't exist, so seeds failed AND
    AI healing failed — it can only pick from the page that's actually there):
      - bucket <= 2 (login-page cases + the successful-login case) go in a
        nested describe that OVERRIDES the shared storageState with a clean,
        logged-out session — they test the login page itself.
      - bucket 3 (app cases) inherit use.storageState from playwright.config.js:
        global-setup.js logged in ONCE and saved the session, so goto(BASE_URL)
        lands authenticated, on the real app page."""
    sid = str(story.get("id", "0"))

    def _one_case(L, ci, c, indent=""):
        bucket = c.get("bucket", 3)
        explicit_login_flow = bool(c.get("explicit_login_flow"))
        L.append("")
        rows = _case_data_rows(c)
        title = c.get("title", "case %d" % ci)
        if rows:
            # Data-driven: one test per data row, named after the row.
            L.append(indent + "  for (const row of %s) {" % json.dumps(rows, ensure_ascii=False))
            L.append(indent + "  test(%s + row.name, async ({ page, heal }) => {"
                     % json.dumps(title + " · ", ensure_ascii=False))
        else:
            L.append(indent + "  test(%s, async ({ page, heal }) => {" % json.dumps(title))
        body = ["    heal.useData(row.values);"] if rows else []
        as_authored = _login_steps_as_authored(c)
        if as_authored:
            # Verified login steps run as written (each test starts logged out).
            if not _starts_with_absolute_navigate(c):
                body.append("    await openLoginPage(page);")
            explicit_login_flow = False
        elif explicit_login_flow:
            body.append("    await performLogin(page);")
        elif bucket < 2:
            body.append("    await openLoginPage(page);")
        elif bucket == 2:
            body.append("    await performLogin(page);")
        else:
            body.append("    await openAppHome(page);")
        intents = c.get("intents", [])
        for ii, intent in enumerate(intents):
            if (bucket == 2 and not as_authored and intent.get("role") == "action") or (
                    explicit_login_flow and _is_login_transition_action(intent)):
                continue
            # Browser dialogs and popups are delivered by Playwright as events.
            # Register the listener before the preceding click, never after the
            # event has already fired.  Context actions themselves are emitted
            # as explicit no-locator transitions below.
            next_intent = intents[ii + 1] if ii + 1 < len(intents) else {}
            step_start = len(body)
            if intent.get("role") == "action" and next_intent.get("role") == "action":
                next_verb = next_intent.get("verb")
                if next_verb == "dialog_accept" and next_intent.get("value"):
                    body.append("    heal.prepareDialog('accept', %s);"
                                % json.dumps(str(next_intent.get("value")), ensure_ascii=False))
                elif next_verb == "dialog_accept":
                    body.append("    heal.prepareDialog('accept');")
                elif next_verb == "dialog_dismiss":
                    body.append("    heal.prepareDialog('dismiss');")
                elif next_verb == "window_switch":
                    body.append("    heal.prepareWindow();")
            if (intent.get("role") == "action" and next_intent.get("role") == "assertion"
                    and (next_intent.get("check") or "") == "download"):
                body.append("    heal.prepareDownload();")
            _emit_pw_intent(body, "%s.%d.%d" % (sid, ci, ii), intent, seed_fn,
                            seeds, app_page=(bucket == 3 or explicit_login_flow))
            # A named step in the HTML report ("Step 2 · Select "Fast" in …")
            # with its own time, instead of the raw heal.act(…) call.
            import engine as _E
            label = _E._intent_step_label(intent)
            if label and len(body) > step_start:
                chunk = body[step_start:]
                body[step_start:] = (["    await test.step(%s, async () => {"
                                      % json.dumps(label, ensure_ascii=False)]
                                     + ["  " + ln for ln in chunk] + ["    });"])
        L.extend(indent + ln for ln in _wrap_data_literals(body, "heal.fill"))
        L.append(indent + "  });")
        if rows:
            L.append(indent + "  }")

    L = []
    L.append("const { test, expect } = require('../fixtures');")
    L.append("const { openLoginPage, performLogin, openAppHome } = require('../support/auth');")
    L.append("")
    L.append("test.describe(%s, () => {"
             % json.dumps("Story %s — %s" % (sid, story.get("title", ""))))
    login_cases = [(ci, c) for ci, c in enumerate(cases)
                   if c.get("bucket", 3) <= 2 or c.get("explicit_login_flow")]
    app_cases = [(ci, c) for ci, c in enumerate(cases)
                 if c.get("bucket", 3) == 3 and not c.get("explicit_login_flow")]
    if login_cases:
        L.append("")
        L.append("  // These cases test the LOGIN PAGE itself — they opt out of the")
        L.append("  // shared logged-in session (global-setup.js) with a clean state.")
        L.append("  test.describe('logged out (login page)', () => {")
        L.append("    test.use({ storageState: { cookies: [], origins: [] } });")
        for ci, c in login_cases:
            _one_case(L, ci, c, indent="  ")
        L.append("  });")
    for ci, c in app_cases:
        _one_case(L, ci, c)
    L.append("});")
    return "\n".join(L) + "\n", _spec_name(sid)


# ───────────── ready-to-run: IDE configs, .env values, npm install ─────────────
_VSCODE_TASKS = """{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Install dependencies",
      "type": "shell",
      "command": "npm install",
      "problemMatcher": []
    },
    {
      "label": "Run tests",
      "type": "shell",
      "command": "npm test",
      "dependsOn": "Install dependencies",
      "group": { "kind": "test", "isDefault": true },
      "problemMatcher": []
    },
    {
      "label": "Open report",
      "type": "shell",
      "command": "npm run report",
      "problemMatcher": []
    }
  ]
}
"""

_VSCODE_EXT = {
    "playwright": '{ "recommendations": ["ms-playwright.playwright"] }\n',
    "cypress": '{ "recommendations": [] }\n',
}


_REPORT_MAILER_JS = """// QA Studio — after every run (pass or fail): writes the branded summary report
// (qa-studio-report.html: results per story, failures with screenshots, run
// environment), opens it, and emails it with the detailed framework report.
// Settings come from .env:
//   OPEN_REPORT=false            don't open the report (always off when CI is set)
//   REPORT_EMAIL_TO=a@x.com,b@y.com
//   SMTP_USER=sender@gmail.com   SMTP_PASS=<Gmail App Password>
//   SMTP_HOST=smtp.gmail.com     SMTP_PORT=465
require('dotenv').config();
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const QA_VERSION = '__QA_VERSION__';
const LOGO = path.join(__dirname, 'qa-logo.png');
const C = { paper: '#EEF0F4', card: '#FFFFFF', tint: '#F7F8FA', ink: '#1B1A22', ink2: '#5F5D6B',
  ink3: '#9C9AA6', line: '#E6E7EC', brand: '#0E9CC0', brandInk: '#0B6E86', brandSoft: '#D6F4FB',
  green: '#1F8A52', greenSoft: '#E7F4ED', red: '#D6414A', redSoft: '#FBEAEC',
  amber: '#AB780C', amberSoft: '#F7EFD8', grey: '#6B6975', greySoft: '#EFEFF3' };
const FONT = "'Segoe UI',Roboto,Helvetica,Arial,sans-serif";
const MONO = 'Consolas,Menlo,monospace';
const MAX_SHOTS = 6;

function openFile(file) {
  if (process.env.CI || String(process.env.OPEN_REPORT).toLowerCase() === 'false') return;
  if (!file || !fs.existsSync(file)) return;
  try {
    if (process.platform === 'win32') {
      spawn('cmd', ['/c', 'start', '""', '"' + file + '"'],
        { detached: true, stdio: 'ignore', windowsVerbatimArguments: true }).unref();
    } else {
      spawn(process.platform === 'darwin' ? 'open' : 'xdg-open', [file],
        { detached: true, stdio: 'ignore' }).unref();
    }
  } catch (e) { console.log('[report] could not open the report: ' + e.message); }
}

function esc(v) {
  return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function stripAnsi(s) { return String(s || '').replace(/\\u001b\\[[0-9;]*m/g, ''); }
function secs(ms) {
  const s = Math.round((ms || 0) / 1000);
  return s >= 60 ? Math.floor(s / 60) + 'm ' + (s % 60) + 's' : s + 's';
}
function exists(p) { try { return !!p && fs.statSync(p).isFile(); } catch (e) { return false; } }
function dataUri(p) {
  try { return 'data:image/png;base64,' + fs.readFileSync(p).toString('base64'); } catch (e) { return ''; }
}

// The first absolute URL a spec opens = the site that story tests.
// Every site a spec opens (page.goto / cy.visit with a full URL), in order;
// falls back to APP_BASE_URL when the spec only uses relative paths.
function sitesOf(specFile) {
  const out = [];
  try {
    const re = /(?:goto|visit)\\(\\s*["'`](https?:\\/\\/[^"'`/]+)/g;
    const text = fs.readFileSync(specFile, 'utf8');
    let m;
    while ((m = re.exec(text))) if (out.indexOf(m[1]) === -1) out.push(m[1]);
  } catch (e) { /* fall back */ }
  if (!out.length && process.env.APP_BASE_URL) out.push(process.env.APP_BASE_URL);
  return out;
}
function siteOf(specFile) { return sitesOf(specFile).join(', '); }

// Headline shared by the summary and detailed reports.
function verdict(total, failed, skipped) {
  if (failed) return failed + ' of ' + total + ' tests failed';
  if (total && skipped === total) return 'None of the ' + total + ' tests ran (all skipped)';
  if (skipped) return (total - skipped) + ' of ' + total + ' tests passed, ' + skipped + ' skipped';
  return 'All ' + total + ' tests passed';
}

function totals(run) {
  const t = { passed: 0, failed: 0, skipped: 0, flaky: 0, total: 0 };
  for (const s of run.stories) {
    s.counts = { passed: 0, failed: 0, skipped: 0, flaky: 0, duration: 0 };
    for (const x of s.tests) {
      const k = x.status === 'flaky' ? 'flaky' : x.status;
      s.counts[k] = (s.counts[k] || 0) + 1;
      if (x.status === 'flaky') s.counts.passed++;
      s.counts.duration += x.duration || 0;
    }
    t.passed += s.counts.passed; t.failed += s.counts.failed;
    t.skipped += s.counts.skipped; t.flaky += s.counts.flaky;
    t.total += s.tests.length;
  }
  return t;
}

// ---- HTML (table + inline styles: renders the same in Gmail/Outlook/browser) ----
function pill(status) {
  const m = { passed: [C.green, C.greenSoft, 'Passed'], failed: [C.red, C.redSoft, 'Failed'],
    skipped: [C.grey, C.greySoft, 'Skipped'], flaky: [C.amber, C.amberSoft, 'Flaky'] }[status]
    || [C.grey, C.greySoft, status];
  return '<span style="display:inline-block;padding:2px 9px;border-radius:10px;font-size:11px;'
    + 'font-weight:700;color:' + m[0] + ';background:' + m[1] + '">' + m[2] + '</span>';
}
function kpi(label, value, color) {
  return '<td width="20%" style="padding:12px 6px;text-align:center;border-right:1px solid ' + C.line + '">'
    + '<div style="font-size:22px;font-weight:800;color:' + (color || C.ink) + '">' + esc(value) + '</div>'
    + '<div style="font-size:11px;color:' + C.ink3 + ';text-transform:uppercase;letter-spacing:.4px">'
    + label + '</div></td>';
}
function section(title, inner) {
  return '<tr><td style="padding:22px 28px 0">'
    + '<div style="font-size:13px;font-weight:800;color:' + C.brandInk + ';text-transform:uppercase;'
    + 'letter-spacing:.6px;margin-bottom:10px">' + title + '</div>' + inner + '</td></tr>';
}
const TH = 'style="padding:8px 10px;text-align:left;font-size:11px;color:' + C.ink3
  + ';text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid ' + C.line + '"';
const TD = 'style="padding:9px 10px;font-size:13px;color:' + C.ink + ';border-bottom:1px solid ' + C.line + ';vertical-align:top"';

// mode 'email': images by cid:; mode 'file': images inlined (self-contained page).
function buildHtml(run, mode, detailName) {
  const t = totals(run);
  // A run where every test was skipped did NOT pass.
  const ok = !t.failed && !(t.total && t.skipped === t.total);
  const rate = t.total ? Math.round(((t.passed) / t.total) * 100) : 0;
  const logo = mode === 'email' ? (exists(LOGO) ? 'cid:qa-logo' : '') : (exists(LOGO) ? dataUri(LOGO) : '');
  const when = new Date(run.startedAt || Date.now());
  const stamp = when.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
  const head = '<tr><td style="padding:22px 28px;border-bottom:1px solid ' + C.line + '">'
    + '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
    + (logo ? '<td width="46" valign="middle"><img src="' + logo + '" width="38" height="38" alt="QA Studio" style="display:block;border:0"></td>' : '')
    + '<td valign="middle"><div style="font-size:17px;font-weight:800;color:' + C.ink + '">QA Studio</div>'
    + '<div style="font-size:12px;color:' + C.ink2 + '">' + esc(run.framework) + ' automation run'
    + ((run.env || {}).ide ? ' · run from <b>' + esc(run.env.ide) + '</b>' : '') + '</div></td>'
    + '<td valign="middle" align="right" style="font-size:12px;color:' + C.ink2 + '">' + esc(stamp) + '</td>'
    + '</tr></table></td></tr>';
  const banner = '<tr><td style="padding:22px 28px 6px">'
    + '<div style="font-size:24px;font-weight:800;color:' + (ok ? C.green : C.red) + '">'
    + verdict(t.total, t.failed, t.skipped) + '</div>'
    + '<div style="font-size:13px;color:' + C.ink2 + ';margin-top:4px">Pass rate ' + rate + '% · '
    + run.stories.length + ' stor' + (run.stories.length === 1 ? 'y' : 'ies') + ' · ' + secs(run.duration) + '</div>'
    + '<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;border-radius:5px;overflow:hidden"><tr>'
    + (t.passed ? '<td height="8" width="' + Math.max(1, Math.round(t.passed / t.total * 100)) + '%" style="background:' + C.green + ';font-size:0;line-height:0">&nbsp;</td>' : '')
    + (t.failed ? '<td height="8" width="' + Math.max(1, Math.round(t.failed / t.total * 100)) + '%" style="background:' + C.red + ';font-size:0;line-height:0">&nbsp;</td>' : '')
    + (t.skipped ? '<td height="8" width="' + Math.max(1, Math.round(t.skipped / t.total * 100)) + '%" style="background:' + C.ink3 + ';font-size:0;line-height:0">&nbsp;</td>' : '')
    + '</tr></table></td></tr>';
  const kpis = '<tr><td style="padding:14px 28px 0"><table width="100%" cellpadding="0" cellspacing="0" '
    + 'style="border:1px solid ' + C.line + ';border-radius:8px;background:' + C.tint + '"><tr>'
    + kpi('Passed', t.passed, C.green) + kpi('Failed', t.failed, t.failed ? C.red : C.ink)
    + kpi('Skipped', t.skipped) + kpi('Flaky', t.flaky, t.flaky ? C.amber : C.ink)
    + kpi('Duration', secs(run.duration)) + '</tr></table></td></tr>';

  const storyRows = run.stories.map((s) => '<tr><td ' + TD + '><b>' + esc(s.name) + '</b>'
    + '<div style="font-size:11px;color:' + C.ink3 + '">' + esc(s.file) + (s.site ? ' · ' + esc(s.site) : '') + '</div></td>'
    + '<td ' + TD + ' align="center" style="color:' + C.green + '">' + s.counts.passed + '</td>'
    + '<td ' + TD + ' align="center">' + (s.counts.failed ? '<b style="color:' + C.red + '">' + s.counts.failed + '</b>' : '0') + '</td>'
    + '<td ' + TD + ' align="center">' + s.counts.skipped + '</td>'
    + '<td ' + TD + ' align="right" nowrap>' + secs(s.counts.duration) + '</td></tr>').join('');
  const stories = section('Results by story', '<table width="100%" cellpadding="0" cellspacing="0">'
    + '<tr><th ' + TH + '>Story</th><th ' + TH + '>Passed</th><th ' + TH + '>Failed</th><th ' + TH + '>Skipped</th>'
    + '<th ' + TH + ' style="text-align:right">Time</th></tr>' + storyRows + '</table>');

  let shots = 0;
  const failedTests = [];
  run.stories.forEach((s) => s.tests.forEach((x) => { if (x.status === 'failed') failedTests.push([s, x]); }));
  const failures = failedTests.length ? section('Failures', failedTests.map(([s, x], i) => {
    let img = '';
    if (x.screenshot && exists(x.screenshot) && shots < MAX_SHOTS) {
      const src = mode === 'email' ? 'cid:shot' + i : dataUri(x.screenshot);
      if (src) { img = '<img src="' + src + '" alt="Screenshot" style="display:block;max-width:100%;width:560px;margin-top:10px;border:1px solid ' + C.line + ';border-radius:6px">'; shots++; x._cid = 'shot' + i; }
    }
    return '<div style="border:1px solid ' + C.line + ';border-left:4px solid ' + C.red + ';border-radius:6px;padding:12px 14px;margin-bottom:12px;background:' + C.card + '">'
      + '<div style="font-size:14px;font-weight:700;color:' + C.ink + '">' + esc(x.title) + '</div>'
      + '<div style="font-size:11px;color:' + C.ink3 + ';margin-top:2px">' + esc(s.name) + '</div>'
      + (x.site ? '<div style="font-size:12px;font-weight:700;color:#8A5A00;background:#FFF4DB;border:1px solid #F1D9A6;border-radius:5px;padding:6px 9px;margin-top:8px">Site unavailable \u2014 ' + esc(x.site) + '</div>' : '')
      + (x.step ? '<div style="font-size:12px;color:' + C.ink2 + ';margin-top:8px"><b>Failing step:</b> ' + esc(x.step) + '</div>' : '')
      + (x.error ? '<pre style="white-space:pre-wrap;word-break:break-word;font-family:' + MONO + ';font-size:12px;color:' + C.red + ';background:' + C.redSoft + ';padding:8px 10px;border-radius:5px;margin:8px 0 0">' + esc(x.error) + '</pre>' : '')
      + img + '</div>';
  }).join('')) : '';

  const allTests = mode === 'file' ? section('All tests', run.stories.map((s) =>
    '<div style="font-size:13px;font-weight:700;color:' + C.ink + ';margin:8px 0 4px">' + esc(s.name) + '</div>'
    + '<table width="100%" cellpadding="0" cellspacing="0">' + s.tests.map((x) =>
      '<tr><td ' + TD + '>' + esc(x.title) + '</td><td ' + TD + ' width="80">' + pill(x.status) + '</td>'
      + '<td ' + TD + ' width="60" align="right" nowrap>' + secs(x.duration) + '</td></tr>').join('') + '</table>').join('')) : '';

  const e = run.env || {};
  const sites = Array.from(new Set([].concat(...run.stories.map((s) => String(s.site || '').split(', ')))
    .filter(Boolean)));
  const envRows = [['Run from', e.ide], ['Framework', run.framework + (e.frameworkVersion ? ' ' + e.frameworkVersion : '')],
    ['Browser', (e.browser || '') + (e.browserVersion ? ' ' + e.browserVersion : '')],
    ['Mode', e.headless == null ? '' : (e.headless ? 'Headless' : 'Headed (browser visible)')],
    ['Sites', sites.join(', ')], ['OS', e.os], ['Machine', e.host], ['Runtime', e.runtime]]
    .filter((r) => r[1]).map((r) => '<tr><td ' + TD + ' width="110" style="color:' + C.ink2 + '">' + r[0]
      + '</td><td ' + TD + '>' + esc(r[1]) + '</td></tr>').join('');
  const env = section('Environment', '<table width="100%" cellpadding="0" cellspacing="0">' + envRows + '</table>');

  const detail = mode === 'file' && detailName
    ? '<a href="' + esc(detailName) + '" style="color:' + C.brand + ';font-weight:700">Open the detailed report</a> (every test with its steps and screenshot).'
    : 'Attached: <b>qa-studio-report.html</b> (this summary with screenshots) and <b>qa-studio-detailed.html</b> (every test with its steps and screenshot).';
  const foot = '<tr><td style="padding:22px 28px 24px"><div style="font-size:12px;color:' + C.ink2 + ';margin-bottom:12px">' + detail + '</div>'
    + '<div style="font-size:11.5px;font-weight:600;color:' + C.ink3 + ';border-top:1px solid ' + C.line + ';padding-top:12px">'
    + 'Generated by QA Studio' + (QA_VERSION.indexOf('__') === -1 ? ' v' + QA_VERSION : '') + ' · ' + esc(run.framework) + '</div></td></tr>';

  const body = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:' + C.paper + ';padding:24px 0">'
    + '<tr><td align="center"><table role="presentation" width="680" cellpadding="0" cellspacing="0" '
    + 'style="max-width:680px;width:100%;background:' + C.card + ';border-radius:12px;font-family:' + FONT + ';border:1px solid ' + C.line + '">'
    + head + banner + kpis + failures + stories + allTests + env + foot + '</table></td></tr></table>';
  if (mode !== 'file') return body;
  return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    + '<title>QA Studio — ' + esc(run.framework) + ' report</title></head><body style="margin:0;background:' + C.paper + '">'
    + body + '</body></html>';
}

// Writes the self-contained summary next to the framework report; returns its path.
function writeReport(run, dir, detailName) {
  const file = path.join(dir, 'qa-studio-report.html');
  try {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(file, buildHtml(run, 'file', detailName), 'utf8');
    return file;
  } catch (e) { console.log('[report] could not write the summary: ' + e.message); return ''; }
}

async function mailReport(run, summaryFile, detailFile) {
  const to = (process.env.REPORT_EMAIL_TO || '').split(/[,;\\s]+/).filter(Boolean);
  const user = process.env.SMTP_USER || '';
  const pass = process.env.SMTP_PASS || '';
  if (!to.length || !user || !pass) {
    console.log('[report] email skipped — set REPORT_EMAIL_TO, SMTP_USER and SMTP_PASS in .env');
    return;
  }
  let nodemailer;
  try { nodemailer = require('nodemailer'); }
  catch (e) { console.log('[report] email skipped — run npm install (nodemailer missing)'); return; }
  const t = totals(run);
  const html = buildHtml(run, 'email');
  const ide = (run.env || {}).ide;
  const subject = (t.failed ? '✗ ' : '✓ ') + 'QA Studio — ' + run.framework + ' run'
    + (ide ? ' from ' + ide : '') + ': ' + t.passed + '/' + t.total
    + ' passed' + (t.failed ? ', ' + t.failed + ' failed' : '') + (t.skipped ? ', ' + t.skipped + ' skipped' : '');
  const attachments = [];
  if (exists(LOGO)) attachments.push({ filename: 'qa-logo.png', path: LOGO, cid: 'qa-logo' });
  run.stories.forEach((s) => s.tests.forEach((x) => {
    if (x._cid) attachments.push({ filename: x._cid + '.png', path: x.screenshot, cid: x._cid });
  }));
  let budget = 20 * 1024 * 1024;
  for (const [f, name] of [[summaryFile, 'qa-studio-report.html'],
                           [detailFile, path.basename(detailFile || '').indexOf('qa-studio') === 0 ? 'qa-studio-detailed.html'
                             : run.framework.toLowerCase() + '-report.html']]) {
    try {
      const size = exists(f) ? fs.statSync(f).size : 0;
      if (size && size < budget) { attachments.push({ filename: name, path: f }); budget -= size; }
    } catch (e) { /* attach is best effort */ }
  }
  try {
    const port = Number(process.env.SMTP_PORT || 465);
    const tr = nodemailer.createTransport({ host: process.env.SMTP_HOST || 'smtp.gmail.com', port,
      secure: port === 465, auth: { user, pass } });
    await tr.sendMail({ from: '"QA Studio" <' + user + '>', to: to.join(', '), subject, html, attachments });
    console.log('[report] emailed to ' + to.join(', '));
  } catch (e) { console.log('[report] email failed: ' + e.message); }
}

// Where the run was started — shown in the report and the email subject.
// QA_IDE in .env overrides it; otherwise CI variables, the IDE's terminal
// variables, then the parent processes (an IDE's Run button sets no variable).
const IDE_NAMES = [
  [/^idea(64)?(\\.exe)?$|intellij/i, 'IntelliJ IDEA'], [/^webstorm/i, 'WebStorm'],
  [/^pycharm/i, 'PyCharm'], [/^rider/i, 'Rider'], [/^goland/i, 'GoLand'],
  [/^eclipse/i, 'Eclipse'], [/^cursor/i, 'Cursor'], [/^windsurf/i, 'Windsurf'],
  [/^code( - insiders)?(\\.exe)?$|^code-insiders/i, 'VS Code'], [/^devenv(\\.exe)?$/i, 'Visual Studio'],
];
function ideFromParents() {
  try {
    const { execFileSync } = require('child_process');
    const procs = new Map();
    const out = process.platform === 'win32'
      ? execFileSync('powershell', ['-NoProfile', '-Command',
        "Get-CimInstance Win32_Process | ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId,$_.ParentProcessId,$_.Name }"],
      { encoding: 'utf8', timeout: 15000, windowsHide: true })
      : execFileSync('ps', ['-A', '-o', 'pid=,ppid=,comm='], { encoding: 'utf8', timeout: 5000 });
    out.split(/\\r?\\n/).forEach((line) => {
      const m = process.platform === 'win32' ? line.trim().split('|') : line.trim().match(/^(\\d+)\\s+(\\d+)\\s+(.*)$/);
      if (!m) return;
      const parts = process.platform === 'win32' ? m : m.slice(1);
      if (parts.length >= 3) procs.set(Number(parts[0]), { ppid: Number(parts[1]), name: path.basename(String(parts[2]).trim()) });
    });
    let pid = process.ppid;
    for (let i = 0; i < 12 && procs.has(pid); i++) {
      const p = procs.get(pid);
      const hit = IDE_NAMES.find(([re]) => re.test(p.name));
      if (hit) return hit[1];
      pid = p.ppid;
    }
  } catch (e) { /* unknown is fine */ }
  return '';
}
function detectIde() {
  const e = process.env;
  if (e.QA_IDE) return e.QA_IDE;
  if (e.GITHUB_ACTIONS) return 'GitHub Actions';
  if (e.TF_BUILD) return 'Azure Pipelines';
  if (e.GITLAB_CI) return 'GitLab CI';
  if (e.JENKINS_URL) return 'Jenkins';
  if (e.CI) return 'CI';
  const fromParents = ideFromParents();
  if (fromParents) return fromParents;
  if (e.CURSOR_TRACE_ID) return 'Cursor';
  if (e.TERM_PROGRAM === 'vscode' || e.VSCODE_PID || e.VSCODE_IPC_HOOK_CLI) return 'VS Code';
  if (/jetbrains/i.test(e.TERMINAL_EMULATOR || '')) return 'JetBrains IDE';
  return 'Terminal';
}

module.exports = { openFile, mailReport, writeReport, buildHtml, siteOf, stripAnsi, detectIde };

// `node report-mailer.js --open <file>` opens a report on demand (npm run report).
if (require.main === module) {
  const i = process.argv.indexOf('--open');
  if (i > -1 && process.argv[i + 1]) {
    process.env.OPEN_REPORT = 'true';
    openFile(path.resolve(process.argv[i + 1]));
  }
}
"""

_PW_AFTER_RUN_JS = """// Playwright reporter that runs AFTER the HTML report is written (it is listed
// after 'html' in playwright.config.js): writes QA Studio's summary report
// (playwright-report/qa-studio-report.html), opens it and emails both reports.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { openFile, mailReport, writeReport, writeDetailed, siteOf, stripAnsi, detectIde } = require('./report-mailer');

function storyOf(test) {
  let s = test.parent;
  while (s && s.parent && s.parent.type !== 'file') s = s.parent;
  return s && s.type !== 'file' && s.title ? s.title : path.basename(test.location.file);
}

// The browser (project) a test ran in: "Chrome", "Firefox"...
function projectOf(test) {
  try { const p = test.parent.project(); return (p && p.name) || ''; } catch (e) { return ''; }
}

// "heal.assertSelected(..., {"target": "Ham selection", ...})" -> "assertSelected · Ham selection"
function stepOf(err) {
  const line = (stripAnsi(err && err.snippet).split('\\n').find((l) => /^\\s*>/.test(l)) || '')
    .replace(/^\\s*>\\s*\\d*\\s*\\|?\\s*/, '');
  const m = line.match(/heal\\.(\\w+)\\([\\s\\S]*?"target":\\s*"([^"]+)"/);
  if (m) return m[1] + ' · ' + m[2];
  const loc = err && err.location;
  return loc ? path.basename(loc.file) + ':' + loc.line : '';
}

class AfterRun {
  onBegin(config, suite) {
    this.config = config; this.suite = suite; this.ran = 0;
    this.startedAt = Date.now(); this.last = new Map();
  }

  onTestEnd(test, result) { this.ran++; this.last.set(test, result); }

  async onEnd(result) {
    // `--list` (or a run that executed nothing) has no report to open or mail.
    if (process.argv.includes('--list') || !this.ran) return;
    const stories = new Map();
    const multi = (this.config.projects || []).length > 1;
    for (const t of this.suite.allTests()) {
      const r = this.last.get(t) || {};
      const o = t.outcome();
      const status = o === 'expected' ? 'passed' : o === 'flaky' ? 'flaky'
        : o === 'skipped' ? 'skipped' : 'failed';
      const name = storyOf(t) + (multi && projectOf(t) ? ' · ' + projectOf(t) : '');
      if (!stories.has(name)) {
        stories.set(name, { name, file: path.basename(t.location.file),
          site: siteOf(t.location.file), tests: [] });
      }
      const shot = (r.attachments || []).find((a) => a.name === 'screenshot' && a.path);
      const video = (r.attachments || []).find((a) => a.name === 'video' && a.path);
      const err = r.error || (r.errors || [])[0];
      const down = [...(t.annotations || []), ...(r.annotations || [])]
        .find((a) => a && a.type === 'Site unavailable');
      const tsteps = (r.steps || []).filter((x) => x.category === 'test.step' && /^Step \\d+ /.test(x.title));
      stories.get(name).tests.push({ title: t.title, status, duration: r.duration || 0,
        steps: tsteps.map((x) => x.title),
        failIdx: tsteps.findIndex((x) => x.error),
        fullError: err ? stripAnsi(err.message).slice(0, 4000) : '',
        stack: err && err.stack ? stripAnsi(err.stack).split('\\n').filter((l) => /^\\s*at /.test(l)).slice(0, 14).join('\\n') : '',
        site: status === 'failed' && down ? String(down.description || '') : '',
        error: err ? stripAnsi(err.message).split('\\n').filter(Boolean).slice(0, 6).join('\\n').slice(0, 900) : '',
        step: status === 'failed' ? stepOf(err) : '',
        screenshot: shot ? shot.path : '', video: video ? video.path : '' });
    }
    const use = ((this.config.projects || [])[0] || {}).use || {};
    const browserNames = (this.config.projects || []).map((p) => p.name).filter(Boolean).join(', ');
    let pwVersion = '';
    try { pwVersion = require('@playwright/test/package.json').version; } catch (e) { /* optional */ }
    const run = { framework: 'Playwright', startedAt: this.startedAt, duration: result.duration,
      stories: Array.from(stories.values()),
      env: { frameworkVersion: pwVersion, browser: browserNames || use.channel || use.browserName || 'chromium',
        headless: use.headless, os: os.type() + ' ' + os.release(), host: os.hostname(),
        runtime: 'Node ' + process.version, ide: detectIde() } };
    const dir = path.resolve('playwright-report');
    const detailed = writeDetailed(run, dir, 'index.html');
    const summary = writeReport(run, dir, detailed ? 'qa-studio-detailed.html' : 'index.html');
    openFile(summary || path.join(dir, 'index.html'));
    await mailReport(run, summary, detailed || path.join(dir, 'index.html'));
  }
}
module.exports = AfterRun;
"""


def _qa_version():
    """QA Studio's version (VERSION next to this file), for report footers."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"),
                  encoding="utf-8-sig") as f:
            return f.read().strip()
    except Exception:
        return ""


# Detailed report writer (raw string: no escaping); styled by the same CSS/JS as
# the Selenium detailed report (engine._SH_DETAIL_CSS / _SH_DETAIL_JS).
_DETAIL_WRITER_JS = r"""// ---- Detailed report (qa-studio-detailed.html): every test with its steps,
// error, screenshot and retries — same layout as the Selenium one. ----
const DETAIL_CSS = __DETAIL_CSS__;
const DETAIL_JS = __DETAIL_JS__;
function dur(ms) { return ms < 1000 ? Math.round(ms || 0) + ' ms' : secs(ms); }
function detailedHtml(run, frameworkReport, dir) {
  const t = totals(run);
  const total = t.total, failed = t.failed, skipped = t.skipped, flaky = t.flaky;
  const passed = Math.max(0, total - failed - skipped);
  const rate = total ? Math.round(passed * 100 / total) : 0;
  const seg = (cls, n) => n > 0 ? "<span class='" + cls + "' style='width:" + (n * 100 / total) + "%'></span>" : '';
  const kpiCard = (label, v, cls) => "<div class='kpi " + (cls || '') + "'><div class='kv'>" + esc(v) + "</div><div class='kl'>" + label + '</div></div>';
  const e = run.env || {};
  const started = new Date(run.startedAt || Date.now());
  let budget = 8 * 1024 * 1024;   // screenshots are embedded up to this size, then linked
  const out = [];
  out.push("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
    '<title>QA Studio - ' + esc(run.framework) + ' detailed report</title><style>' + DETAIL_CSS + '</style></head><body>',
    "<header class='top'><div class='brand'>" + (exists(LOGO) ? "<img src='" + dataUri(LOGO) + "' alt=''>" : ''),
    "<div><div class='name'>QA Studio</div><div class='sub'>" + esc(run.framework) + ' detailed report' + (e.ide ? ' &middot; run from <b>' + esc(e.ide) + '</b>' : '') + '</div></div></div>',
    "<div class='when'>" + esc(started.toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })) + '</div></header><main>');
  const allSkipped = total > 0 && skipped === total;
  out.push("<section class='hero " + (failed || allSkipped ? 'bad' : 'ok') + "'><div class='verdict'><h1>"
    + verdict(total, failed, skipped) + "</h1><div class='meta'>"
    + run.stories.length + (run.stories.length === 1 ? ' story' : ' stories') + ' &middot; ' + secs(run.duration || 0)
    + (e.browser ? ' &middot; ' + esc(e.browser) : '') + "</div></div><div class='bar'>"
    + seg('pass', passed) + seg('fail', failed) + seg('skip', skipped) + "</div><div class='kpis'>"
    + kpiCard('Pass rate', rate + '%', failed ? '' : 'pass') + kpiCard('Passed', passed, 'pass')
    + kpiCard('Failed', failed, failed ? 'fail' : '') + kpiCard('Skipped', skipped, '')
    + kpiCard('Flaky', flaky, flaky ? 'flaky' : '') + kpiCard('Duration', secs(run.duration || 0), '') + '</div></section>');
  const st = (x) => x.status === 'flaky' ? 'passed' : x.status;
  out.push("<div class='tools'><div class='chips'>",
    "<button class='chip on' data-f='all'>All <b>" + total + '</b></button>',
    "<button class='chip' data-f='failed'>Failed <b>" + failed + '</b></button>',
    "<button class='chip' data-f='passed'>Passed <b>" + passed + '</b></button>',
    "<button class='chip' data-f='skipped'>Skipped <b>" + skipped + '</b></button>',
    "</div><input id='q' type='search' placeholder='Search tests, steps or errors'>",
    "<button class='ghost' id='xp' type='button'>Expand all</button></div>");
  for (const s of run.stories) {
    const c = s.counts || {};
    out.push("<section class='story'><div class='sh'><h2>" + esc(s.name) + "</h2><div class='sc'>"
      + "<span class='c pass'>" + (c.passed || 0) + ' passed</span>'
      + (c.failed ? "<span class='c fail'>" + c.failed + ' failed</span>' : '')
      + (c.skipped ? "<span class='c skip'>" + c.skipped + ' skipped</span>' : '')
      + "<span class='c'>" + secs(c.duration || 0) + '</span></div></div>');
    for (const x of s.tests) {
      const status = st(x);
      const steps = x.steps || [];
      const search = (x.title + ' ' + steps.join(' ') + ' ' + (x.fullError || x.error || '')).toLowerCase();
      out.push("<details class='t " + status + "' data-s='" + status + "' data-q='" + esc(search) + "'" + (status === 'failed' ? ' open' : '') + '>'
        + "<summary><span class='dot'></span><span class='tt'>" + esc(x.title) + '</span>'
        + (x.status === 'flaky' ? "<span class='badge flaky'>Passed on retry</span>" : '')
        + (x.site ? "<span class='badge site'>Site unavailable</span>" : '')
        + "<span class='st " + status + "'>" + status.charAt(0).toUpperCase() + status.slice(1) + '</span>'
        + "<span class='ms'>" + dur(x.duration) + "</span></summary><div class='body'>");
      if (x.site) out.push("<div class='down'><b>Site unavailable</b> &mdash; " + esc(x.site) + '</div>');
      if (steps.length) {
        const bad = status === 'failed' ? (x.failIdx != null && x.failIdx >= 0 ? x.failIdx : steps.length - 1) : -1;
        out.push("<div class='lbl'>Steps</div><ol class='steps'>");
        steps.forEach((line, i) => {
          const m = /^Step (\d+) · (.*)$/.exec(line);
          out.push('<li' + (i === bad ? " class='bad'" : '') + "><span class='n'>" + esc(m ? m[1] : String(i + 1))
            + "</span><span class='x'>" + esc(m ? m[2] : line) + '</span>'
            + (i === bad ? "<span class='here'>Failed here</span>" : '') + '</li>');
        });
        out.push('</ol>');
      }
      const err = x.fullError || x.error;
      if (err) {
        out.push("<div class='lbl'>Error</div><pre class='err'>" + esc(err) + '</pre>');
        if (x.stack) out.push("<details class='stack'><summary>Stack trace</summary><pre>" + esc(x.stack) + '</pre></details>');
      }
      if (x.screenshot && exists(x.screenshot)) {
        let src = '';
        try {
          const size = fs.statSync(x.screenshot).size;
          if (size <= budget) { src = dataUri(x.screenshot); budget -= size; }
        } catch (e2) { /* link instead */ }
        if (!src) src = 'file:///' + path.resolve(x.screenshot).replace(/\\/g, '/');
        out.push("<div class='lbl'>Screenshot</div><img class='shot' loading='lazy' alt='Screenshot' src='" + src + "'>");
      }
      const hasVideo = !!(x.video && exists(x.video));
      if (hasVideo) {
        // Linked, not embedded (videos are large); the path is relative to the report.
        let rel = path.relative(dir || '.', path.resolve(x.video));
        rel = path.isAbsolute(rel) ? 'file:///' + rel.replace(/\\/g, '/') : encodeURI(rel.split(path.sep).join('/'));
        out.push("<div class='lbl'>Video</div><video class='vid' controls preload='metadata' src='" + esc(rel) + "'></video>"
          + (x.videoNote ? "<div class='vnote'>" + esc(x.videoNote) + '</div>' : ''));
      }
      if (!steps.length && !err && !hasVideo && !(x.screenshot && exists(x.screenshot)))
        out.push("<div class='none'>No steps or screenshot were recorded for this test.</div>");
      out.push('</div></details>');
    }
    out.push('</section>');
  }
  out.push("<div class='empty' id='empty' hidden>No tests match.</div>");
  const sites = [];
  run.stories.forEach((s) => String(s.site || '').split(', ').forEach((u) => { if (u && sites.indexOf(u) === -1) sites.push(u); }));
  const rows = [['Run from', e.ide], ['Framework', run.framework + (e.frameworkVersion ? ' ' + e.frameworkVersion : '')],
    ['Browser', [e.browser, e.browserVersion].filter(Boolean).join(' ')],
    ['Mode', e.headless === undefined ? '' : e.headless ? 'Headless' : 'Headed (browser visible)'],
    ['Retries', process.env.RETRIES || '1'], ['Sites', sites.join(', ')], ['OS', e.os], ['Machine', e.host], ['Runtime', e.runtime]]
    .filter((r) => r[1]).map((r) => '<tr><th>' + esc(r[0]) + '</th><td>' + esc(r[1]) + '</td></tr>').join('');
  out.push("<section class='env'><h2>Environment</h2><table>" + rows + '</table></section>');
  if (frameworkReport) out.push("<p style='text-align:center;margin:16px 0 0'><a href='" + esc(frameworkReport)
    + "' style='color:var(--brand);font-weight:700'>Open the " + esc(run.framework) + " report</a> (traces, videos, every command).</p>");
  out.push('</main><footer>Generated by QA Studio' + (QA_VERSION.indexOf('__') === -1 ? ' v' + QA_VERSION : '')
    + ' &middot; ' + esc(run.framework) + "</footer><div class='zoom' id='zoom' hidden><img alt=''></div><script>" + DETAIL_JS + '</script></body></html>');
  return out.join('');
}
function writeDetailed(run, dir, frameworkReport) {
  const file = path.join(dir, 'qa-studio-detailed.html');
  try {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(file, detailedHtml(run, frameworkReport, dir), 'utf8');
    return file;
  } catch (e) { console.log('[report] could not write the detailed report: ' + e.message); return ''; }
}

"""


def _report_mailer_js():
    import engine as _E
    ver = _qa_version()
    js = _REPORT_MAILER_JS.replace("__QA_VERSION__", ver) if ver else _REPORT_MAILER_JS
    writer = (_DETAIL_WRITER_JS.replace("__DETAIL_CSS__", json.dumps(_E._SH_DETAIL_CSS))
              .replace("__DETAIL_JS__", json.dumps(_E._SH_DETAIL_JS)))
    return js.replace("module.exports = { openFile,", writer + "module.exports = { writeDetailed, openFile,", 1)


def _copy_logo(dest, written, out_dir):
    """Copy QA Studio's email logo next to the report mailer (header of the
    summary report and the emailed run summary). Skipped when unchanged."""
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("qa-logo-email.png", "qa-logo.png", "app.png"):
        src = os.path.join(here, name)
        if not os.path.isfile(src):
            continue
        try:
            with open(src, "rb") as f:
                data = f.read()
            if os.path.isfile(dest):
                with open(dest, "rb") as f:
                    if f.read() == data:
                        return
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            written.append(os.path.relpath(dest, out_dir))
        except Exception:
            pass
        return


def _idea_npm_config(name, command, script=None, install_first=False):
    """IntelliJ / WebStorm npm run configuration (.run/*.run.xml, shared)."""
    scripts = ('    <scripts>\n      <script value="%s" />\n    </scripts>\n' % script
               if script else "")
    before = ('    <method v="2">\n'
              '      <option name="NpmBeforeRunTask" enabled="true">\n'
              '        <package-json value="$PROJECT_DIR$/package.json" />\n'
              '        <command value="install" />\n'
              '        <node-interpreter value="project" />\n'
              '        <envs />\n'
              '      </option>\n'
              '    </method>\n') if install_first else '    <method v="2" />\n'
    return ('<component name="ProjectRunConfigurationManager">\n'
            '  <configuration default="false" name="%s" type="js.build_tools.npm">\n'
            '    <package-json value="$PROJECT_DIR$/package.json" />\n'
            '    <command value="%s" />\n'
            '%s'
            '    <node-interpreter value="project" />\n'
            '    <envs />\n'
            '%s'
            '  </configuration>\n'
            '</component>\n') % (name, command, scripts, before)


def _write_ide_files(out_dir, target, written):
    """One-click run from VS Code and IntelliJ/WebStorm: 'Run tests' installs
    missing dependencies first, then runs npm test."""
    vs = os.path.join(out_dir, ".vscode")
    run = os.path.join(out_dir, ".run")
    os.makedirs(vs, exist_ok=True)
    os.makedirs(run, exist_ok=True)
    _wif(os.path.join(vs, "tasks.json"), _VSCODE_TASKS, written, out_dir)
    _wif(os.path.join(vs, "extensions.json"), _VSCODE_EXT.get(target, _VSCODE_EXT["cypress"]),
         written, out_dir)
    _wif(os.path.join(run, "Run tests.run.xml"),
         _idea_npm_config("Run tests", "run", "test", install_first=True), written, out_dir)
    _wif(os.path.join(run, "Install dependencies.run.xml"),
         _idea_npm_config("Install dependencies", "install"), written, out_dir)
    _wif(os.path.join(run, "Open report.run.xml"),
         _idea_npm_config("Open report", "run", "report"), written, out_dir)
    for _label, _script in (("Chrome", "test:chrome"), ("Edge", "test:edge"),
                            ("Firefox", "test:firefox"), ("all browsers", "test:all")):
        _wif(os.path.join(run, "Run tests (%s).run.xml" % _label),
             _idea_npm_config("Run tests (%s)" % _label, "run", _script, install_first=True),
             written, out_dir)


def _env_fill(path, values, overwrite=False):
    """Fill keys in .env that are missing or EMPTY. With overwrite=True a value
    QA Studio provides replaces the one in the file (QA Studio is the source of
    truth for URLs, the opted-in test account and report settings). Empty
    values never touch the file. Returns the keys written."""
    values = {k: v for k, v in (values or {}).items() if v}
    if not values:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        lines = []
    filled, seen = [], set()
    for i, line in enumerate(lines):
        key, sep, val = line.partition("=")
        key = key.strip()
        if sep and key in values and not key.startswith("#"):
            seen.add(key)
            if not val.strip() or (overwrite and val.strip() != str(values[key])):
                lines[i] = "%s=%s" % (key, values[key])
                filled.append(key)
    for key, val in values.items():
        if key not in seen:
            lines.append("%s=%s" % (key, val))
            filled.append(key)
    if filled:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return filled


def _env_fill_keys(path, keys,
                   header="# Report email after every run (Gmail: SMTP_PASS = an App Password)"):
    """Add `KEY=` lines for keys missing from .env (values untouched)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return
    have = {ln.split("=", 1)[0].strip() for ln in text.splitlines() if "=" in ln}
    missing = [k for k in keys if k not in have]
    if missing:
        with open(path, "a", encoding="utf-8") as f:
            if text and not text.endswith("\n"):
                f.write("\n")
            f.write(header + "\n")
            f.write("".join("%s=\n" % k for k in missing))


def _prepare_env(out_dir, cfg, written):
    """Create .env once, then fill any empty URL/account values from cfg."""
    envp = os.path.join(out_dir, ".env")
    if not os.path.exists(envp):
        with open(envp, "w", encoding="utf-8") as f:
            f.write(_pw_env_real(cfg))
        written.append(".env")
    _env_fill(envp, overwrite=True, values={"APP_BASE_URL": cfg.get("base_url", ""),
                     "APP_LOGIN_URL": cfg.get("login_url", ""),
                     "APP_USER": cfg.get("app_user", ""),
                     "APP_PASS": cfg.get("app_pass", ""),
                     # Report email after every run (recipients = QA Studio's).
                     "REPORT_EMAIL_TO": cfg.get("report_email_to", ""),
                     "SMTP_USER": cfg.get("smtp_user", ""),
                     "SMTP_PASS": cfg.get("smtp_pass", "")})
    # Keys the user may fill later stay visible (empty) in .env.
    _env_fill_keys(envp, ("REPORT_EMAIL_TO", "SMTP_USER", "SMTP_PASS"))
    _env_fill_keys(envp, ("BROWSER", "VIDEO"),
                   "# BROWSER: chrome, edge, firefox, a list (chrome,firefox) or all (empty = default).\n"
                   "# VIDEO: on (default), retain-on-failure or off.")


def _deps_up_to_date(out_dir):
    lock = os.path.join(out_dir, "node_modules", ".package-lock.json")
    pkg = os.path.join(out_dir, "package.json")
    try:
        return os.path.getmtime(lock) >= os.path.getmtime(pkg)
    except OSError:
        return False


def install_dependencies(out_dir, cb=None, should_stop=None, timeout=900):
    """Run `npm install` in the generated project so it opens ready to run.
    Skipped when node_modules is already current. Returns True when the project
    has its dependencies, False otherwise (Node missing / install failed)."""
    import shutil
    import subprocess
    cb = cb or (lambda *a, **k: None)
    should_stop = should_stop or (lambda: False)
    if _deps_up_to_date(out_dir):
        cb("Dependencies already installed (node_modules is current).", "dim")
        return True
    npm = shutil.which("npm")
    if not npm:
        cb("Node.js/npm not found — install Node 18+ from https://nodejs.org; the "
           "IDE's 'Run tests' then installs everything on first run.", "warn")
        return False
    cb("Installing dependencies (npm install) — the first time also downloads the "
       "browser and can take a few minutes…", "info")
    try:
        proc = subprocess.Popen([npm, "install", "--no-audit", "--no-fund"], cwd=out_dir,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as ex:
        cb("Couldn't start npm install: %s" % str(ex)[:160], "warn")
        return False
    start, tail = time.time(), []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            tail = (tail + [line])[-6:]
        if should_stop() or time.time() - start > timeout:
            proc.kill()
            cb("npm install stopped — run 'Install dependencies' from the IDE later.", "warn")
            return False
    proc.wait()
    if proc.returncode != 0:
        cb("npm install failed (exit %s): %s" % (proc.returncode, " | ".join(tail)[-300:]),
           "warn")
        return False
    cb("Dependencies installed — open the folder in your IDE and run 'Run tests'.", "ok")
    return True


def build_playwright_project(out_dir, sequenced, cfg, seed_fn, harvest_js,
                             cb=None, should_stop=None, orig_tcs=None):
    """Write a full self-healing Playwright (JavaScript) project from the IR.
    `cfg` = {base_url, login_url, ai_provider, ai_base_url, ai_model}.
    `seed_fn` = engine._seed_locator_for_intent, `harvest_js` = engine._HARVEST_JS."""
    cb = cb or (lambda *a, **k: None)
    should_stop = should_stop or (lambda: False)
    orig_tcs = orig_tcs or {}
    tests_dir = os.path.join(out_dir, "tests")
    support_dir = os.path.join(out_dir, "support")
    fixtures_dir = os.path.join(out_dir, "fixtures")
    for d in (tests_dir, support_dir, fixtures_dir):
        os.makedirs(d, exist_ok=True)
    written = []
    m = _load_manifest(out_dir)
    m.setdefault("stories", {})
    m["manifest_version"] = 1
    m["target"] = "playwright"

    prov = cfg.get("ai_provider", "anthropic")
    aibase = cfg.get("ai_base_url", "https://api.anthropic.com")
    model = cfg.get("ai_model", "claude-sonnet-4-6")

    cb("Writing self-healing Playwright framework (healer, AI client · %s)…" % prov, "dim")
    _wif(os.path.join(out_dir, "package.json"), _PKG_JSON, written, out_dir)
    _wif(os.path.join(out_dir, "playwright.config.js"), _PW_CONFIG, written, out_dir)
    _wif(os.path.join(out_dir, "global-setup.js"), _PW_GLOBAL_SETUP, written, out_dir)
    _wif(os.path.join(out_dir, ".gitignore"), _PW_GITIGNORE, written, out_dir)
    _wif(os.path.join(out_dir, ".env.example"), _PW_ENV_EXAMPLE, written, out_dir)
    _wif(os.path.join(out_dir, "README.md"), _PW_README, written, out_dir)
    _wif(os.path.join(out_dir, "fixtures.js"), _PW_FIXTURES, written, out_dir)
    _wif(os.path.join(fixtures_dir, "qastudio-upload.txt"),
         "QA Studio generated automation fixture.\n", written, out_dir)
    _wif(os.path.join(support_dir, "locators.js"), _PW_LOCATORS_JS, written, out_dir)
    _wif(os.path.join(support_dir, "aiClient.js"),
         _PW_AICLIENT_JS.replace("__PROVIDER__", prov)
                        .replace("__AI_BASE__", aibase).replace("__MODEL__", model),
         written, out_dir)
    _wif(os.path.join(support_dir, "harvest.js"),
         _PW_HARVEST_HEAD + (harvest_js or "return [];") + _PW_HARVEST_TAIL, written, out_dir)
    _wif(os.path.join(support_dir, "healer.js"), _with_batch2_js(_PW_HEALER_JS), written, out_dir)
    _wif(os.path.join(support_dir, "report-mailer.js"), _report_mailer_js(), written, out_dir)
    _copy_logo(os.path.join(support_dir, "qa-logo.png"), written, out_dir)
    _wif(os.path.join(support_dir, "after-run.js"), _PW_AFTER_RUN_JS, written, out_dir)
    _wif(os.path.join(support_dir, "browsers.js"), _PW_BROWSERS_JS, written, out_dir)
    _wif(os.path.join(support_dir, "progress.js"), _PROGRESS_JS, written, out_dir)
    _wif(os.path.join(support_dir, "QaProgressWindow.java"), progress_window_java(), written, out_dir)
    _wif(os.path.join(support_dir, "progress-reporter.js"), _PW_PROGRESS_REPORTER_JS, written, out_dir)
    _wif(os.path.join(support_dir, "auth.js"),
         _PW_AUTH_JS.replace("__UI_LOCALE__", cfg.get("ui_locale", "ar")),
         written, out_dir)

    # .env holds real environment values — created ONCE; later runs only fill
    # values that are still empty, so a user's edits are never clobbered.
    _prepare_env(out_dir, cfg, written)
    _write_ide_files(out_dir, "playwright", written)

    seeds = {}
    specs_owned = set()
    _todo_total = 0
    for entry in sequenced:
        if should_stop():
            break
        story = entry["story"]
        if not entry.get("cases"):
            continue
        sid = str(story.get("id"))
        cb("  generating spec for story %s (%d case(s))"
           % (story.get("id"), len(entry["cases"])), "dim")
        js, spec = _pw_spec(story, entry["cases"], seed_fn, seeds)
        _todo_total += js.count(", null,")   # null seed = resolved at runtime (TODO)
        specs_owned.add(spec)
        path = os.path.join(tests_dir, spec)
        prior = m["stories"].get(sid) or {}
        wrote, chash = _guarded_write(path, js, prior.get("hash"), cb)
        if wrote:
            written.append(os.path.relpath(path, out_dir))
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        m["stories"][sid] = {
            "spec": spec,
            "hash": chash,
            "provider": prov if wrote else prior.get("provider", prov),
            "model": model if wrote else prior.get("model", model),
            "generatedAt": stamp if wrote else prior.get("generatedAt", stamp),
            "test_cases": {str(tc.get("id")): (tc.get("title", "") or "")
                           for tc in orig_tcs.get(sid, [])},
        }
        _save_manifest(out_dir, m)

    _merge_seed_locators(out_dir, seeds, cb)
    owned = specs_owned | {r.get("spec") for r in m["stories"].values() if r.get("spec")}
    _prune_specs(tests_dir, owned, cb)
    _save_manifest(out_dir, m)
    cb("TODO_LIVE: %d" % _todo_total, "meta")   # reconcile the live counter to the emitted total
    if _todo_total:
        cb("TODO: %d locator(s) to resolve at runtime "
           "(the rest are seeded)." % _todo_total, "warn")
    else:
        cb("Every locator is seeded — none left for runtime self-healing.", "ok")
    cb("Wrote %d files, %d spec(s) this run." % (len(written), len(specs_owned)), "ok")
    return written


# ────────────────────────────── Cypress (JavaScript) ────────────────────────
# Cypress tests run IN THE BROWSER, so they can't touch the filesystem and can't
# freely call external APIs. Both locators.json persistence and the AI call run on
# the NODE side via cy.task() (registered in setupNodeEvents); the healer commands
# run in the browser and talk to Node through those tasks.
_CY_PKG_JSON = """{
  "name": "qastudio-cypress-tests",
  "version": "1.0.0",
  "private": true,
  "description": "Self-healing Cypress tests generated by QA Studio.",
  "scripts": {
    "pretest": "cypress verify",
    "test": "node cypress/run-browsers.js",
    "test:headless": "node cypress/run-browsers.js --headless",
    "test:chrome": "node cypress/run-browsers.js --browser chrome",
    "test:edge": "node cypress/run-browsers.js --browser edge",
    "test:firefox": "node cypress/run-browsers.js --browser firefox",
    "test:all": "node cypress/run-browsers.js --browser all",
    "open": "cypress open",
    "report": "node cypress/report-mailer.js --open cypress/reports/qa-studio-report.html",
    "report:detail": "node cypress/report-mailer.js --open cypress/reports/index.html"
  },
  "engines": { "node": ">=18" },
  "devDependencies": {
    "cypress": "^13.15.0",
    "cypress-mochawesome-reporter": "^3.8.2"
  },
  "dependencies": { "dotenv": "^16.4.5", "nodemailer": "^6.9.14" }
}
"""

_CY_RUN_BROWSERS_JS = r"""// `npm test`: runs the Cypress tests in the browser(s) chosen by BROWSER (env or
// .env): chrome, edge, firefox, electron, a list (chrome,firefox) or all (chrome,
// edge and firefox). Empty = Cypress's default (Electron). `--browser x`
// overrides it and `--headless` hides the browser. With more than one browser it
// runs them at the same time (PARALLEL=true, the default) or one after another
// (PARALLEL=false), then writes ONE QA Studio report for all of them.
require('dotenv').config();
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const cypress = require('cypress');

const LABELS = { chrome: 'Chrome', edge: 'Edge', firefox: 'Firefox', electron: 'Electron', chromium: 'Chromium' };
const ALIASES = { msedge: 'edge', 'microsoft-edge': 'edge', ff: 'firefox', 'google-chrome': 'chrome', googlechrome: 'chrome' };
const label = (b) => LABELS[b] || b;

function pick(raw) {
  const v = String(raw || '').trim().toLowerCase();
  if (!v) return [];
  if (v === 'all') return ['chrome', 'edge', 'firefox'];
  const out = [];
  for (const part of v.split(/[\s,;]+/).filter(Boolean)) {
    const b = ALIASES[part] || part;
    if (!LABELS[b]) { console.log('[browser] unknown browser "' + part + '" - use chrome, edge, firefox or electron'); continue; }
    if (!out.includes(b)) out.push(b);
  }
  return out;
}

const failures = (res) => (!res || res.status === 'failed' ? 1 : (res.totalFailed || 0));
const parallelOn = () => !['false', 'off', '0', 'no'].includes(String(process.env.PARALLEL || 'true').trim().toLowerCase());

// The Cypress command line (node_modules/cypress/bin/cypress). Its package
// "exports" hide that file from require.resolve('cypress/bin/cypress'), so it
// is found from the package's main file (or the project's node_modules).
function cypressBin() {
  const cands = [];
  try { cands.push(path.join(path.dirname(require.resolve('cypress')), 'bin', 'cypress')); } catch (e) { /* below */ }
  cands.push(path.join(__dirname, '..', 'node_modules', 'cypress', 'bin', 'cypress'));
  cands.push(path.join(process.cwd(), 'node_modules', 'cypress', 'bin', 'cypress'));
  return cands.find((p) => { try { return fs.existsSync(p); } catch (e) { return false; } }) || null;
}

// One browser as its own `cypress run` process (its own env), its output lines
// prefixed with the browser name. Resolves to the number of failed tests.
function runAlone(b, headless, env) {
  return new Promise((resolve) => {
    const bin = cypressBin();
    if (!bin) { console.log('[browser] Cypress is not installed (npm install)'); resolve(1); return; }
    const args = [bin, 'run', '--browser', b].concat(headless ? [] : ['--headed']);
    const child = spawn(process.execPath, args, { env: Object.assign({}, process.env, env), stdio: ['ignore', 'pipe', 'pipe'] });
    const tag = '[' + label(b) + '] ';
    const relay = (stream, out) => {
      let rest = '';
      stream.on('data', (d) => {
        const lines = (rest + d.toString()).split(/\r?\n/);
        rest = lines.pop();
        for (const l of lines) out.write(tag + l + '\n');
      });
      stream.on('end', () => { if (rest) out.write(tag + rest + '\n'); });
    };
    relay(child.stdout, process.stdout);
    relay(child.stderr, process.stderr);
    child.on('error', (e) => { console.log(tag + 'could not run: ' + e.message); resolve(1); });
    child.on('close', (code) => resolve(code || 0));
  });
}

(async () => {
  const argv = process.argv.slice(2);
  const at = argv.indexOf('--browser');
  const list = pick(at >= 0 ? argv[at + 1] : process.env.BROWSER);
  const headless = argv.includes('--headless') || !!process.env.CI
    || String(process.env.HEADLESS || '').toLowerCase() === 'true';
  const opts = (b) => Object.assign({ headed: !headless }, b ? { browser: b } : {});

  if (list.length < 2) {
    const res = await cypress.run(opts(list[0]));
    process.exit(failures(res) ? 1 : 0);
  }

  const batch = path.resolve('cypress', 'qa-runs');
  fs.rmSync(batch, { recursive: true, force: true });
  fs.mkdirSync(batch, { recursive: true });
  // One progress window for every browser: each browser run reports to it.
  const progress = require('./progress');
  const specFiles = [];
  const walk = (d) => { for (const f of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, f.name);
    if (f.isDirectory()) walk(p); else if (/[.]cy[.]js$/.test(f.name)) specFiles.push(p);
  } };
  try { walk(path.resolve('cypress', 'e2e')); } catch (e) { /* no specs */ }
  const perBrowser = progress.countTests(specFiles);
  const progressPort = await progress.start(perBrowser * list.length);
  if (list.length > 1) for (const b of list) progress.expect(label(b), perBrowser);   // one row per browser
  if (progressPort) process.env.QA_PROGRESS_PORT = String(progressPort);
  let failed = 0;
  if (parallelOn()) {
    // PARALLEL=true: every browser at the same time, each in its own process.
    console.log('\n[browser] running the tests in ' + list.map(label).join(', ') + ' at the same time (PARALLEL=true)');
    const codes = await Promise.all(list.map((b) => runAlone(b, headless,
      { QA_BATCH_DIR: batch, QA_BATCH_BROWSER: b, QA_PROGRESS_PORT: progressPort ? String(progressPort) : '' })));
    failed = codes.reduce((a, c) => a + c, 0);
  } else {
    for (const b of list) {
      console.log('\n[browser] running the tests in ' + label(b) + ' (' + (list.indexOf(b) + 1) + '/' + list.length + ')');
      process.env.QA_BATCH_DIR = batch;
      process.env.QA_BATCH_BROWSER = b;
      try { failed += failures(await cypress.run(opts(b))); }
      catch (e) { failed++; console.log('[browser] ' + label(b) + ' could not run: ' + e.message); }
    }
  }
  // Every browser finished: the window shows the verdict and closes while the
  // report is written (it never keeps the run waiting).
  const finishing = progress.finish();

  const runs = [];
  for (const b of list) {
    try { runs.push(Object.assign({ b }, JSON.parse(fs.readFileSync(path.join(batch, b, 'run.json'), 'utf8')))); }
    catch (e) { console.log('[browser] no results from ' + label(b) + ' (is it installed?)'); }
  }
  if (runs.length) {
    const { openFile, mailReport, writeReport, writeDetailed } = require('./report-mailer');
    const stories = [];
    for (const r of runs) for (const s of r.stories || []) stories.push(Object.assign({}, s, { name: s.name + ' · ' + label(r.b) }));
    const env = Object.assign({}, runs[0].env || {}, {
      browser: runs.map((r) => [label(r.b), r.env && r.env.browserVersion].filter(Boolean).join(' ')).join(', '),
      browserVersion: '' });
    const run = { framework: 'Cypress', startedAt: Math.min(...runs.map((r) => r.startedAt || Date.now())),
      duration: runs.reduce((a, r) => a + (r.duration || 0), 0), stories, env };
    const dir = path.resolve('cypress', 'reports');
    const detailed = writeDetailed(run, dir, 'index.html');
    const summary = writeReport(run, dir, detailed ? 'qa-studio-detailed.html' : 'index.html');
    openFile(summary || path.join(dir, 'index.html'));
    await mailReport(run, summary, detailed || path.join(dir, 'index.html'));
  }
  await finishing;
  process.exit(failed ? 1 : 0);
})();
"""

_CY_CONFIG = """// Cypress config. baseUrl + credentials come from the environment (.env,
// git-ignored) or real env vars — nothing is baked in, so the same project runs
// against any environment with no regeneration. setupNodeEvents registers the
// Node-side tasks the browser healer uses for locator persistence + AI healing.
require('dotenv').config();
const path = require('path');
const { defineConfig } = require('cypress');
const tasks = require('./cy-tasks');
// One browser of a cross-browser run (cypress/run-browsers.js): screenshots,
// videos, downloads and its report go to its own folder, so browsers running at
// the same time (PARALLEL=true) never clear or overwrite each other's files.
const batchDir = process.env.QA_BATCH_DIR
  ? path.join(process.env.QA_BATCH_DIR, process.env.QA_BATCH_BROWSER || 'browser') : '';

module.exports = defineConfig({
  ...(batchDir ? { screenshotsFolder: path.join(batchDir, 'screenshots'),
                   videosFolder: path.join(batchDir, 'videos'),
                   downloadsFolder: path.join(batchDir, 'downloads') } : {}),
  // A visual HTML report (cypress/reports/index.html) is generated on EVERY run,
  // pass or fail, listing every spec/test with status, steps, screenshots and charts.
  reporter: 'cypress-mochawesome-reporter',
  reporterOptions: {
    reportDir: batchDir ? path.join(batchDir, 'mochawesome') : 'cypress/reports',
    charts: true,
    reportPageTitle: 'QA Studio — Cypress report',
    embeddedScreenshots: true,
    inlineAssets: true,
    overwrite: false,
    saveAllAttempts: true,
  },
  // Test cases may open pages on more than one site (or a login on another
  // domain); in Chrome/Edge/Electron this lets them run without cy.origin().
  chromeWebSecurity: false,
  e2e: {
    baseUrl: process.env.APP_BASE_URL || undefined,
    specPattern: 'cypress/e2e/**/*.cy.js',
    supportFile: 'cypress/support/e2e.js',
    defaultCommandTimeout: 12000,
    screenshotOnRunFailure: true,
    // A video of every spec (shown in the detailed report). VIDEO=retain-on-failure
    // keeps only specs with a failure, VIDEO=off records none.
    video: String(process.env.VIDEO || 'on').toLowerCase() !== 'off',
    // A failed test is run once more in `cypress run` (a site blip shouldn't fail
    // the run). RETRIES=0 turns it off.
    retries: { runMode: Number(process.env.RETRIES ?? 1), openMode: 0 },
    setupNodeEvents(on, config) {
      // Build the mochawesome HTML report, then open it and email it. (Calling the
      // reporter hooks here, instead of its plugin, lets one after:run do both.)
      const { beforeRunHook, afterRunHook } = require('cypress-mochawesome-reporter/lib');
      let browserInfo = {};
      const qaStepStore = new Map();
      // Live progress window (cypress/progress.js). With several browsers,
      // run-browsers.js owns the window and this run only reports to it.
      const progress = require('./cypress/progress');
      on('before:run', async (details) => {
        browserInfo = (details && details.browser) || {};
        await beforeRunHook(details);
        if (config.isTextTerminal) {
          await progress.start(progress.countTests(((details && details.specs) || []).map((x) => x.absolute)));
        }
      });
      on('after:spec', (spec, results) => {
        // VIDEO=retain-on-failure: drop the video of a spec where nothing failed.
        if (!/^retain|^failed/.test(String(process.env.VIDEO || '').toLowerCase())) return;
        const failed = results && (results.tests || []).some((t) => (t.attempts || []).some((a) => a.state === 'failed'));
        if (results && results.video && !failed) { try { require('fs').unlinkSync(results.video); } catch (e) { /* already gone */ } }
      });
      on('after:run', async (results) => {
        await afterRunHook();
        // Shows the verdict while the reports are built; awaited on every way out
        // so the window closes before this run exits.
        const finishing = progress.finish();
        try {
          await qaAfterRun(results);
        } finally {
          await finishing;
        }
      });
      async function qaAfterRun(results) {
        if (!config.isTextTerminal || !results || !results.runs) return;   // only for 'cypress run'
        const path = require('path');
        // The mochawesome report links each video as videos/<spec folder>/<spec>.mp4
        // (e.g. videos/cypress/e2e/x.cy.js.mp4) but Cypress saves it flat
        // (videos/x.cy.js.mp4): copy it where the report looks, or the report
        // shows a 0:00 video that never plays.
        for (const r of results.runs) {
          try {
            if (!r.video || !r.spec || !r.spec.relative || !require('fs').existsSync(r.video)) continue;
            const dest = path.join('cypress', 'reports', 'videos', path.dirname(r.spec.relative), path.basename(r.video));
            require('fs').mkdirSync(path.dirname(dest), { recursive: true });
            require('fs').copyFileSync(r.video, dest);
          } catch (e) { console.log('[report] could not place the video: ' + e.message); }
        }
        const os = require('os');
        const { openFile, mailReport, writeReport, writeDetailed, siteOf, stripAnsi, detectIde } = require('./cypress/report-mailer');
        const stories = new Map();
        for (const r of results.runs) {
          const spec = (r.spec && (r.spec.absolute || r.spec.relative)) || '';
          const shots = (r.screenshots || []).filter((s) => s && s.path);
          for (const t of r.tests || []) {
            const titles = t.title || [];
            const title = titles[titles.length - 1] || '';
            const name = titles.length > 1 ? titles[0] : path.basename(spec);
            if (!stories.has(name)) {
              stories.set(name, { name, file: path.basename(spec), site: siteOf(spec), tests: [] });
            }
            const status = t.state === 'passed' ? 'passed' : t.state === 'failed' ? 'failed' : 'skipped';
            const passShot = status === 'passed'
              ? shots.find((s) => s.path.includes('passed -- ' + title.replace(/[\\/:*?"<>|]/g, ''))) : null;
            const shot = status !== 'failed' ? passShot
              : shots.find((s) => s.path.includes(title) && /failed/i.test(s.path))
                || (shots.filter((s) => /failed/i.test(s.path)).length === 1
                  ? shots.find((s) => /failed/i.test(s.path)) : null);
            const duration = t.duration != null ? t.duration
              : (t.attempts || []).reduce((a, x) => a + (x.duration || 0), 0);
            const downMsg = status === 'failed' ? String(t.displayError || '') : '';
            const downAt = downMsg.indexOf('Site unavailable \u2014 ');
            const derr = stripAnsi(t.displayError || '');
            const dlines = derr.split('\\n');
            const atIdx = dlines.findIndex((l) => /^\\s+at /.test(l));
            const specRel = (r.spec && r.spec.relative) || '';
            const steps = (qaStepStore.get(specRel + '::' + title) || []);
            const flakyRun = status === 'passed' && (t.attempts || []).length > 1;
            stories.get(name).tests.push({ title, status: flakyRun ? 'flaky' : status, duration, step: '',
              steps,
              fullError: status === 'failed' ? (atIdx > 0 ? dlines.slice(0, atIdx) : dlines).join('\\n').trim().slice(0, 4000) : '',
              stack: status === 'failed' && atIdx > 0 ? dlines.slice(atIdx, atIdx + 14).map((l) => l.trim()).join('\\n') : '',
              site: downAt >= 0 ? downMsg.slice(downAt + 19).split('. The site was down')[0] : '',
              error: status === 'failed'
                ? stripAnsi(t.displayError).split('\\n').filter(Boolean).slice(0, 6).join('\\n').slice(0, 900) : '',
              screenshot: shot ? shot.path : '',
              video: r.video || '', videoNote: r.video ? 'Video of the whole spec (' + path.basename(spec) + ')' : '' });
          }
        }
        const run = { framework: 'Cypress', startedAt: Date.parse(results.startedTestsAt) || Date.now(),
          duration: results.totalDuration || 0, stories: Array.from(stories.values()),
          env: { frameworkVersion: results.cypressVersion, browser: results.browserName,
            browserVersion: results.browserVersion, headless: browserInfo.isHeadless,
            os: (results.osName || os.type()) + ' ' + (results.osVersion || os.release()),
            host: os.hostname(), runtime: 'Node ' + process.version, ide: detectIde() } };
        if (process.env.QA_BATCH_DIR) {
          // One browser of a cross-browser run (cypress/run-browsers.js): keep its
          // results, screenshots and videos (Cypress clears them before the next
          // browser runs); one report for every browser is written at the end.
          const fs = require('fs');
          const dest = path.join(process.env.QA_BATCH_DIR, process.env.QA_BATCH_BROWSER || results.browserName || 'browser');
          fs.mkdirSync(path.join(dest, 'files'), { recursive: true });
          const keep = (f) => {
            if (!f || !fs.existsSync(f)) return '';
            const to = path.join(dest, 'files', path.basename(path.dirname(f)) + '-' + path.basename(f));
            try { if (!fs.existsSync(to)) fs.copyFileSync(f, to); return to; } catch (e) { return f; }
          };
          for (const s of run.stories) for (const x of s.tests) { x.screenshot = keep(x.screenshot); x.video = keep(x.video); }
          fs.writeFileSync(path.join(dest, 'run.json'), JSON.stringify(run));
          return;
        }
        const dir = path.resolve('cypress/reports');
        const detailed = writeDetailed(run, dir, 'index.html');
        const summary = writeReport(run, dir, detailed ? 'qa-studio-detailed.html' : 'index.html');
        openFile(summary || path.join(dir, 'index.html'));
        await mailReport(run, summary, detailed || path.join(dir, 'index.html'));
      }
      on('task', {
        // Progress window: which test runs now, and each test's result.
        qaProgress(msg) { progress.event(msg); return null; },
        // Each test's "Step N · …" labels, kept for the detailed report.
        qaSteps({ spec, title, steps }) {
          qaStepStore.set(String(spec || '') + '::' + String(title || ''), steps || []);
          return null;
        },
        loadLocators: tasks.loadLocators,
        saveLocator: tasks.saveLocator,
        pickLocator: tasks.pickLocator,
        qaDigestHeader: tasks.qaDigestHeader,
        // Download steps: is there a finished file whose name contains `name`?
        findDownload(name) {
          const fs = require('fs');
          const dir = config.downloadsFolder || 'cypress/downloads';
          try {
            const want = String(name || '').toLowerCase();
            // A repeat download is saved as "file_1 (1).txt": match name and extension separately.
            const dot = want.lastIndexOf('.');
            const stem = dot > 0 ? want.slice(0, dot) : want, ext = dot > 0 ? want.slice(dot) : '';
            return fs.readdirSync(dir).some((f) => {
              const n = f.toLowerCase();
              return n.includes(stem) && n.endsWith(ext) && !n.endsWith('.crdownload') && !n.endsWith('.tmp');
            });
          } catch (e) { return false; }
        },
      });
      config.env = Object.assign({}, config.env, {
        APP_BASE_URL: process.env.APP_BASE_URL || '',
        APP_LOGIN_URL: process.env.APP_LOGIN_URL || '',
        APP_USER: process.env.APP_USER || '',
        APP_PASS: process.env.APP_PASS || '',
      });
      return config;
    },
  },
});
"""

_CY_TASKS_JS = """// Node-side tasks: locators.json persistence (fs) + AI locator healing.
// The browser healer reaches these via cy.task(). Supports Anthropic's messages
// API and any OpenAI-compatible /chat/completions endpoint.
require('dotenv').config();
const fs = require('fs');
const path = require('path');
const FILE = path.join(process.cwd(), 'locators.json');

const PROVIDER = process.env.QA_AI_PROVIDER || '__PROVIDER__';
const BASE     = process.env.QA_AI_BASE_URL || '__AI_BASE__';
const MODEL    = process.env.QA_AI_MODEL    || '__MODEL__';
const KEY = process.env.QA_AI_API_KEY || process.env.ANTHROPIC_API_KEY ||
           process.env.OPENAI_API_KEY || process.env.NVIDIA_API_KEY ||
           process.env.GROQ_API_KEY || process.env.GEMINI_API_KEY || '';

const SYS = 'You resolve web locators. Given a step intent and a JSON array of ' +
  'candidate DOM elements, reply with ONLY a JSON object {\"by\":\"...\",\"value\":\"...\"} ' +
  'identifying the single best matching element. \"by\" must be one of: css, id, ' +
  'name, xpath, text. No prose, no code fences.';

function _load() { try { return JSON.parse(fs.readFileSync(FILE, 'utf8')); } catch (e) { return {}; } }
// One writer at a time across workers / browser processes: a lock file next to
// locators.json (a lock older than 5 s is a crashed run's and is taken over).
function withLock(fn) {
  const lock = FILE + '.lock';
  const until = Date.now() + 3000;
  let fd = null;
  while (fd === null) {
    try { fd = fs.openSync(lock, 'wx'); }
    catch (e) {
      try { if (Date.now() - fs.statSync(lock).mtimeMs > 5000) fs.unlinkSync(lock); } catch (x) { /* gone */ }
      if (Date.now() > until) break;                       // write anyway rather than lose the heal
      const t = Date.now() + 15; while (Date.now() < t) { /* brief spin */ }
    }
  }
  try { return fn(); }
  finally { if (fd !== null) { try { fs.closeSync(fd); fs.unlinkSync(lock); } catch (e) { /* gone */ } } }
}
// Browsers running in parallel share the file: swap it in whole, so another
// run never reads half a file (and then overwrites everything with one entry).
function _save(s) {
  try {
    const tmp = FILE + '.' + process.pid + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(s, null, 2));
    try { fs.renameSync(tmp, FILE); }
    catch (e) { fs.writeFileSync(FILE, JSON.stringify(s, null, 2)); try { fs.unlinkSync(tmp); } catch (x) { /* gone */ } }
  } catch (e) {}
}
function _extractJson(t) {
  if (!t) return null;
  const m = t.match(/\\{[\\s\\S]*\\}/);
  try { return JSON.parse(m ? m[0] : t); } catch (e) { return null; }
}

async function _pick(intent, dom) {
  if (!KEY) return null;
  const user = 'Intent: ' + JSON.stringify(intent) + '\\n\\nCandidates:\\n' + dom;
  // Same 3-attempt backoff as the Playwright aiClient: Node's fetch has no
  // retry, and parallel healing trips transient "fetch failed" / 429s.
  let lastErr = '';
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      let r, text;
      if (PROVIDER === 'anthropic') {
        r = await fetch(BASE.replace(/\\/$/, '') + '/v1/messages', {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'x-api-key': KEY,
                     'anthropic-version': '2023-06-01' },
          body: JSON.stringify({ model: MODEL, max_tokens: 300, system: SYS,
                                 messages: [{ role: 'user', content: user }] }),
        });
      } else {
        r = await fetch(BASE.replace(/\\/$/, '') + '/chat/completions', {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'authorization': 'Bearer ' + KEY },
          body: JSON.stringify({ model: MODEL, temperature: 0,
            messages: [{ role: 'system', content: SYS }, { role: 'user', content: user }] }),
        });
      }
      if (r.status === 429 || r.status >= 500) throw new Error('provider HTTP ' + r.status);
      const j = await r.json();
      if (PROVIDER === 'anthropic') {
        text = j && j.content && j.content[0] && j.content[0].text;
      } else {
        text = j && j.choices && j.choices[0] && j.choices[0].message &&
               j.choices[0].message.content;
      }
      return _extractJson(text);
    } catch (e) {
      lastErr = e.message;
      console.log('[heal] AI call failed (attempt ' + attempt + '/3): ' + e.message);
      if (attempt < 3) await new Promise(res => setTimeout(res, 1500 * attempt));
    }
  }
  console.log('[heal] giving up after 3 attempts: ' + lastErr);
  return null;
}

module.exports = {
  loadLocators() { return _load(); },
  saveLocator({ key, spec }) {
    withLock(() => {
      const s = _load();
      s[key] = { by: spec.by, value: spec.value, source: 'healed',
                 resolvedAt: new Date().toISOString(), provider: PROVIDER };
      _save(s);
    });
    return null;
  },
  async pickLocator({ intent, dom }) { return (await _pick(intent, dom)) || null; },
  // HTTP Digest answer (RFC 7616, MD5, qop=auth) for one request: Cypress
  // itself only sends Basic credentials. The password never leaves Node.
  qaDigestHeader({ url, method, user, pass, challenge }) {
    const crypto = require('crypto');
    const md5 = (x) => crypto.createHash('md5').update(x).digest('hex');
    const f = {};
    String(challenge || '').replace(/^\\s*Digest\\s+/i, '')
      .replace(/(\\w+)=(?:"([^"]*)"|([^,\\s]*))/g, (m, k, q, v) => { f[k.toLowerCase()] = q !== undefined ? q : v; return m; });
    const u = new URL(url);
    const uri = u.pathname + u.search;
    const ha1 = md5(user + ':' + (f.realm || '') + ':' + pass);
    const ha2 = md5(String(method || 'GET').toUpperCase() + ':' + uri);
    const qop = String(f.qop || '').split(',').map((x) => x.trim()).includes('auth') ? 'auth' : '';
    const nc = '00000001', cnonce = crypto.randomBytes(8).toString('hex');
    const response = qop ? md5([ha1, f.nonce, nc, cnonce, qop, ha2].join(':')) : md5([ha1, f.nonce, ha2].join(':'));
    let h = 'Digest username="' + user + '", realm="' + (f.realm || '') + '", nonce="' + (f.nonce || '') +
            '", uri="' + uri + '", response="' + response + '"';
    if (f.opaque) h += ', opaque="' + f.opaque + '"';
    if (f.algorithm) h += ', algorithm=' + f.algorithm;
    if (qop) h += ', qop=' + qop + ', nc=' + nc + ', cnonce="' + cnonce + '"';
    return h;
  },
};
"""

_CY_GITIGNORE = """node_modules/
cypress/screenshots/
cypress/videos/
cypress/qa-runs/
cypress/reports/
# environment-specific config (URLs + secrets stay out of git):
.env
# local DOM snapshots from an opt-in live inspection:
inspection-screens.json
# QA Studio's local record of verified cases (reused on regenerate):
.qastudio/verified-cases.json
# locators.json is COMMITTED on purpose — healed locators are shared across
# environments (identical UI). Only URLs/creds differ, and those live in .env.
# a locators.json save in progress:
locators.json.*.tmp
locators.json.lock
"""

_CY_SUPPORT_E2E = """// Loaded before every spec — registers the self-healing commands
// and the mochawesome HTML reporter.
require('cypress-mochawesome-reporter/register');
require('./healer');

// Progress window (cypress/progress.js): which test runs now, and its result.
beforeEach(function () {
  const t = this.currentTest;
  cy.task('qaProgress', { op: 'test', title: (t && t.title) || '', browser: Cypress.browser.displayName },
    { log: false });
});
afterEach(function () {
  const t = this.currentTest;
  if (!t) return;
  // A failed try that is retried is counted on its last try only.
  if (t.state === 'failed' && typeof t.currentRetry === 'function' && t.currentRetry() < t.retries()) return;
  const status = t.state === 'passed' ? 'passed' : t.state === 'failed' ? 'failed' : 'skipped';
  cy.task('qaProgress', { op: 'done', status, browser: Cypress.browser.displayName }, { log: false });
});

// A failed test whose page is a host/server error page (Heroku "Application
// error", 502/503/504, the browser's "can't be reached") is labelled "Site
// unavailable": the site was down, not the test. Only short pages are checked,
// so a real page that merely mentions an error isn't mislabelled.
function qaSiteDown(title, text) {
  const body = String(text || '');
  if (body.length > 2500) return '';
  const s = (String(title || '') + ' ' + body).toLowerCase();
  const rules = [
    ['application error', 'the page showed "Application error" (the Heroku app is down)'],
    ['502 bad gateway', 'the server answered 502 Bad Gateway'],
    ['503 service', 'the server answered 503 Service Unavailable'],
    ['service unavailable', 'the server answered Service Unavailable'],
    ['504 gateway', 'the server answered 504 Gateway Timeout'],
    ['gateway time-out', 'the server answered 504 Gateway Timeout'],
    ['internal server error', 'the server answered 500 Internal Server Error'],
    ["this site can't be reached", 'the browser could not reach the site'],
    ['this site can\u2019t be reached', 'the browser could not reach the site'],
    ['this page isn\u2019t working', 'the site returned a server error page'],
    ["this page isn't working", 'the site returned a server error page'],
    ['err_name_not_resolved', 'the site address could not be resolved (DNS)'],
    ['err_connection_refused', 'the site refused the connection'],
    ['err_connection_timed_out', 'the connection to the site timed out'],
  ];
  for (const [needle, why] of rules) if (s.includes(needle)) return why;
  return '';
}

// Step labels for the detailed report ("Step N · …", logged by every spec).
let qaSteps = [];
beforeEach(() => { qaSteps = []; });
Cypress.on('log:added', (attrs) => {
  const msg = String((attrs && attrs.message) || '').replace(/\\*\\*/g, '').trim();
  if (attrs && attrs.name === 'log' && /^Step \\d+ /.test(msg)) qaSteps.push(msg);
});
afterEach(function () {
  const t = this.currentTest;
  cy.task('qaSteps', { spec: Cypress.spec.relative, title: t.title, steps: qaSteps.slice() }, { log: false });
  // Passed tests get a screenshot too (QA_SCREENSHOTS=failed turns this off).
  if (t.state === 'passed' && String(Cypress.env('QA_SCREENSHOTS') || 'all') !== 'failed') {
    cy.screenshot('passed -- ' + t.title.replace(/[\\/:*?"<>|]/g, ''), { capture: 'viewport', log: false });
  }
});

Cypress.on('fail', (err) => {
  try {
    const doc = cy.state('document');
    let text = doc && doc.body ? doc.body.innerText : '';
    if (doc) doc.querySelectorAll('iframe').forEach((f) => {
      try { text += ' ' + f.contentDocument.body.innerText; } catch (e) { /* cross-origin */ }
    });
    const why = qaSiteDown(doc ? doc.title : '', text);
    if (why) err.message = 'Site unavailable \u2014 ' + why
      + '. The site was down or erroring, not a test problem.\\n\\n' + err.message;
  } catch (e) { /* keep the original error */ }
  throw err;
});
"""

_CY_HARVEST_HEAD = """// DOM harvester — returns a compact list of interactable elements for the AI
// healer. Body is QA Studio's shared harvest script; `document` is the app's.
function __qaHarvest(document) {
"""
_CY_HARVEST_TAIL = """
}
module.exports = { harvest: function (doc) { return __qaHarvest(doc); } };
"""

_CY_HEALER_JS = """// Self-healing Cypress commands. Resolution per step key:
//   1) locator saved in locators.json (via cy.task)  2) the generated seed
//   3) ask the AI (cy.task -> Node) to pick one from the live DOM, then save it.
const { harvest } = require('./harvest');

// Frame scope: healEnterFrame points every later heal command inside that
// (same-origin) iframe; each test starts back on the main page.
let __frame = null;
beforeEach(() => { __frame = null; });
// Shadow DOM locator "host >>> inner" (see toSelector): find it in the app's
// document, or chain .shadow().find() for Cypress commands.
const SHADOW_PREFIX = 'shadow::';
function _shadowFind(doc, chain) {
  const parts = String(chain).split(' >>> ');
  let roots = [doc];
  for (let i = 0; i < parts.length - 1; i++) {
    const next = [];
    roots.forEach((r) => r.querySelectorAll(parts[i]).forEach((h) => { if (h.shadowRoot) next.push(h.shadowRoot); }));
    roots = next;
  }
  const out = [];
  roots.forEach((r) => r.querySelectorAll(parts[parts.length - 1]).forEach((e) => out.push(e)));
  return out;
}
function _jq(sel) {
  if (typeof sel === 'string' && sel.indexOf(SHADOW_PREFIX) === 0) {
    const doc = Cypress.$('html').get(0).ownerDocument;
    return Cypress.$(_shadowFind(doc, sel.slice(SHADOW_PREFIX.length)));
  }
  if (!__frame) return Cypress.$(sel);
  const f = Cypress.$(__frame).get(0);
  const doc = f && f.contentDocument;
  return doc ? Cypress.$(doc).find(sel) : Cypress.$();
}
// Every heal command queries through here (frame- and shadow-aware).
function _q(selector) {
  if (typeof selector === 'string' && selector.indexOf(SHADOW_PREFIX) === 0) {
    const parts = selector.slice(SHADOW_PREFIX.length).split(' >>> ');
    let c = cy.get(parts[0]);
    for (let i = 1; i < parts.length; i++) c = c.shadow().find(parts[i]);
    return c;
  }
  if (!__frame) return cy.get(selector);
  // The target may be the frame's <body> itself (a rich-text editor such as
  // TinyMCE: <body id="tinymce" contenteditable>), which find() never returns.
  return cy.get(__frame, { log: false }).its('0.contentDocument.body')
    .should('not.be.empty').then((body) => (Cypress.$(body).is(selector)
      ? cy.wrap(body) : cy.wrap(body).find(selector)));
}
function _scopeDoc() {
  if (!__frame) return cy.document({ log: false });
  return cy.get(__frame, { log: false }).its('0.contentDocument');
}
function _scopeBody(opts) {
  if (!__frame) return cy.get('body', opts);
  return cy.get(__frame, { log: false }).its('0.contentDocument.body').then(cy.wrap);
}
const _CY_KEYS = { ENTER: '{enter}', ESCAPE: '{esc}', SPACE: ' ', ARROWUP: '{uparrow}',
  ARROWDOWN: '{downarrow}', ARROWLEFT: '{leftarrow}', ARROWRIGHT: '{rightarrow}',
  BACKSPACE: '{backspace}', DELETE: '{del}', HOME: '{home}', END: '{end}',
  PAGEUP: '{pageup}', PAGEDOWN: '{pagedown}' };

function toSelector(spec) {
  const by = (spec && spec.by) || 'css';
  const v = (spec && spec.value) || '';
  switch (by) {
    case 'id':        return '[id="' + v + '"]';   // valid even when the id starts with a digit
    case 'name':      return '[name=\"' + v + '\"]';
    case 'className': return '.' + v;
    case 'tagName':   return v;
    case 'linkText':
    case 'text':      return ':contains(' + JSON.stringify(v) + ')';
    case 'xpath':     return null; // xpath needs a plugin; fall through to heal
    case 'shadow':    return SHADOW_PREFIX + v;
    case 'css':
    case 'cssSelector':
    default:          return v;
  }
}
// Poll for a locator for up to tries x 250ms (a one-shot jQuery check sent
// not-yet-rendered elements straight to AI healing). Yields true/false.
function _poll(spec, tries) {
  if (_has(spec)) return cy.wrap(true, { log: false });
  if (tries <= 0) return cy.wrap(false, { log: false });
  return cy.wait(250, { log: false }).then(() => _poll(spec, tries - 1));
}

function _has(spec) {
  const sel = toSelector(spec);
  if (!sel) return false;
  try { return _jq(sel).length > 0; } catch (e) { return false; }
}

// Login-page language — must match the generated tests' expected-message
// language; the frozen APP_LOGIN_URL can carry a stale ui_locales=… (see the
// Playwright auth.js comment for the live failure this caused).
function _withLocale(url) {
  const loc = Cypress.env('APP_UI_LOCALE') || '__UI_LOCALE__';
  if (!loc || !/^https?:/.test(url)) return url;
  if (/[?&]ui_locales=/.test(url)) {
    return url.replace(/([?&])ui_locales=[^&]*/, '$1ui_locales=' + loc);
  }
  return url + (url.includes('?') ? '&' : '?') + 'ui_locales=' + loc;
}

Cypress.Commands.add('openLoginPage', () => {
  cy.visit(_withLocale(Cypress.env('APP_LOGIN_URL') || Cypress.env('APP_BASE_URL') || '/'));
});
Cypress.Commands.add('performLogin', () => {
  cy.openLoginPage();
  // Same breadth as QA Studio's explorer login: also '#user-name'-style fields.
  cy.get('#username, #user-name, input[type=email], input[name=email], input[name=username], '
         + 'input[name*=user], input[id*=user]').first()
    .clear().type(Cypress.env('APP_USER') || ' ');
  cy.get('#password, input[type=password]').first()
    .clear().type(Cypress.env('APP_PASS') || ' ', { log: false });
  cy.get('#kc-login, button[type=submit], input[type=submit]').first().click();
});

// Log in ONCE and cache the session (cookies/localStorage) across tests AND
// spec files. Cypress's test isolation wipes browser state before every test,
// so a login performed in one test never survives to the next — every
// app-page test calls cy.loginSession() first, which restores the cached
// session instantly instead of re-typing credentials each time.
// App cases start here: base URL, logging in first when it shows the login
// form (some sites' root IS the login page even with a session).
Cypress.Commands.add('openAppHome', () => {
  cy.visit(Cypress.env('APP_BASE_URL') || '/');
  cy.get('body', { log: false }).then(($body) => {
    // A real login form also has a username/email field (a page with only a
    // password input, e.g. a form under test, is not a login).
    if ($body.find('input[type=password]:visible').length
        && $body.find('#username, #user-name, input[type=email], input[name=email], '
                      + 'input[name=username], input[name*=user], input[id*=user]').length) {
      cy.performLogin();
    }
  });
});

Cypress.Commands.add('loginSession', () => {
  // No test account (APP_USER empty) = a site without a login: nothing to restore.
  if (!Cypress.env('APP_USER')) return;
  cy.session('qa-studio-user', () => { cy.performLogin(); },
             { cacheAcrossSpecs: true });
});

Cypress.Commands.add('healSelector', (key, seed, intent) => {
  return cy.task('loadLocators', null, { log: false }).then((store) => {
    const cached = store && store[key];
    // Same order as the Selenium/Playwright healers: saved (short probe when
    // a seed exists) -> seed (full wait) -> PROVEN fallbacks -> AI last.
    const steps = [];
    if (cached) steps.push([cached, seed ? 8 : 48]);
    if (seed) steps.push([seed, 48]);
    for (const fb of (seed && seed.fallbacks) || []) steps.push([fb, 8]);
    // Cypress has no XPath: skip those locators at once instead of polling
    // them for 12 s; the proven CSS fallbacks behind them are used instead.
    for (let i = steps.length - 1; i >= 0; i--) if (!toSelector(steps[i][0])) steps.splice(i, 1);
    if (steps.length && steps[0][1] < 48) steps[0][1] = 48;
    const tryNext = (i) => {
      if (i >= steps.length) {
        // Lazy-loaded further down the page: scroll until it shows, then AI.
        return _scrollSearch(steps.map((st) => st[0]), 0)
          .then((sel) => (sel ? sel : _healWithAI(key, intent)));
      }
      return _poll(steps[i][0], steps[i][1]).then((ok) =>
        ok ? toSelector(steps[i][0]) : tryNext(i + 1));
    };
    return tryNext(0);
  });
});

// One scroll step down the page (or its main scrolling panel); reset = top.
function _scrollStep(reset) {
  return _scopeDoc().then((doc) => doc.defaultView.eval('(' + SCROLL_PAGE_FN + ')')(reset, doc));
}
// Scroll down until one of the locators matches; yields its selector, or ''.
function _scrollSearch(specs, n) {
  if (!specs.length) return cy.wrap('', { log: false });
  if (n >= SCROLL_SEARCH_STEPS) return _scrollStep(true).then(() => '');
  return _scrollStep(false).then((moved) => cy.wait(moved === 'end' ? 1100 : 350, { log: false }).then(() => {
    const hit = specs.find((sp) => _has(sp));
    if (hit) { Cypress.log({ name: 'wait', message: 'found after scrolling the page' }); return toSelector(hit); }
    if (moved !== 'end') return _scrollSearch(specs, n + 1);
    return _scrollStep(false).then((again) => (again === 'end'
      ? _scrollStep(true).then(() => '') : _scrollSearch(specs, n + 1)));
  }));
}
// Before an action: centred, shown, enabled, not moving and (for clicks) not
// covered. Never fails - after the limit the action runs anyway.
function _ready(sel, verb) {
  const mode = readyMode(verb);
  if (!mode) return cy.wrap(sel, { log: false });
  const start = Date.now();
  let last = null;
  const poll = () => _q(sel).first({ log: false }).then(($el) => {
    const el = $el.get(0);
    let raw = 'ok|';
    try { raw = String(el.ownerDocument.defaultView.eval('(' + READY_FN + ')')(el, mode)); } catch (e) { return; }
    const state = raw.split('|')[0], box = raw.slice(state.length + 1);
    if (state === 'gone' || (state === 'ok' && box === last)) return;
    last = state === 'ok' ? box : null;
    const limit = state === 'disabled' ? READY.disabled : state === 'covered' ? READY.covered : READY.wait;
    if (Date.now() - start > limit) {
      Cypress.log({ name: 'wait', message: 'element still ' + state + ' - acting anyway' });
      return;
    }
    return cy.wait(150, { log: false }).then(poll);
  });
  return poll().then(() => sel);
}

function _healWithAI(key, intent) {
    return _scopeDoc().then((doc) => {
      let dom = '[]';
      try { dom = JSON.stringify(harvest(doc)); } catch (e) {}
      return cy.task('pickLocator', { intent: intent, dom: dom }, { log: false }).then((picked) => {
        if (picked && _has(picked)) {
          return cy.task('saveLocator', { key: key, spec: picked }, { log: false }).then(() => {
            cy.log('[heal] ' + key + ' -> ' + picked.by + '=' + picked.value);
            return toSelector(picked);
          });
        }
        throw new Error('Could not resolve step ' + key +
          '. Set QA_AI_API_KEY to enable AI healing.');
      });
    });
}

// Data-driven runs: {{name}} placeholders take the current data row's value;
// {{random.email}}, {{random.number}}, {{random.text}}, {{today}}, {{today+3d}}
// and {{now}} are generated once per test. Unknown placeholders stay as written.
function qaDataKey(n) { return String(n || '').toLowerCase().replace(/[^0-9a-z]+/g, '_').replace(/^_+|_+$/g, ''); }
function qaDataFill(text, values, cache) {
  if (text == null || String(text).indexOf('{{') === -1) return text;
  const vals = {};
  Object.keys(values || {}).forEach((k) => { vals[qaDataKey(k)] = values[k]; });
  return String(text).replace(/\\{\\{\\s*([^{}]+?)\\s*\\}\\}/g, (all, raw) => {
    const key = qaDataKey(raw);
    if (key === 'app_user') return process.env.APP_USER || all;
    if (key === 'app_pass') return process.env.APP_PASS || all;
    if (Object.prototype.hasOwnProperty.call(vals, key)) return String(vals[key]);
    const g = String(raw).toLowerCase().replace(/\\s+/g, '');
    if (cache[g] != null) return cache[g];
    let v = null;
    if (g === 'random.email') v = 'qa.' + Date.now() + Math.floor(Math.random() * 1e4) + '@example.com';
    else if (g === 'random.number') v = String(100000 + Math.floor(Math.random() * 900000));
    else if (g === 'random.text') v = 'QA ' + Math.random().toString(36).slice(2, 8);
    else if (g === 'now') v = String(Date.now());
    else {
      const m = g.match(/^today(?:([+-])(\\d+)d)?$/);
      if (m) { const d = new Date(); d.setDate(d.getDate() + (m[1] === '-' ? -1 : 1) * Number(m[2] || 0)); v = d.toISOString().slice(0, 10); }
    }
    if (v == null) return all;
    cache[g] = v;
    return v;
  });
}
// The spec sets the row with qaUseData(row.values) at the start of each test.
let __qaDataCache = {};
globalThis.qaUseData = (values) => { Cypress.env('QA_DATA', values || {}); __qaDataCache = {}; };
globalThis.qaFill = (text) => qaDataFill(text, Cypress.env('QA_DATA') || {}, __qaDataCache);

// Visible option/suggestion by text (custom dropdowns, autocomplete lists):
// the same script QA Studio's live inspection used, run in the app's window.
const PICK_FN = __PICK_OPTION_FN__;
const SCROLL_MAX = __SCROLL_MAX__;
const DROP_FN = __DROP_TARGET_FN__;
const TABLE_FN = __TABLE_CELL_FN__;
// Page-level checks (URL, title, counts, sort, pattern) and remember-and-reuse.
const PAGE_FN = __PAGE_CHECK_FN__;
const REMEMBER_FN = __REMEMBER_FN__;
// "Close X if it is shown" (pop-ups, ads, cookie banners).
const DISMISS_FN = __DISMISS_FN__;
// Wait-and-scroll before every action, pagination and storage steps (the same
// scripts QA Studio's inspection uses).
const READY_FN = __READY_FN__;
const SCROLL_PAGE_FN = __SCROLL_PAGE_FN__;
const PAGINATE_FN = __PAGINATE_FN__;
const STORAGE_FN = __STORAGE_FN__;
const SCROLL_SEARCH_STEPS = __SCROLL_SEARCH_STEPS__;
const PAGINATE_MAX = __PAGINATE_MAX__;
const READY = __READY_LIMITS__;
// click: enabled + not covered; input: enabled; view: shown; null: no wait.
function readyMode(verb) {
  const v = String(verb || 'click');
  if (['upload', 'frame', 'navigate', 'wait', 'scroll_to'].includes(v)) return null;
  if (['type', 'clear', 'key_press'].includes(v)) return 'input';
  return v === 'set_value' ? 'view' : 'click';
}
function _n(s) { return String(s == null ? '' : s).trim().replace(/ +/g, ' ').toLowerCase(); }
function cookieOk(value, spec) {
  const p = String(spec || '').split('|');
  const op = p[1] || 'exists', want = _n(p.slice(2).join('|'));
  if (op === 'absent') return value == null;
  if (value == null) return false;
  let v = String(value);
  try { v = decodeURIComponent(v); } catch (e) {}
  v = _n(v);
  return op === 'exists' ? true : op === 'is' ? v === want : v.indexOf(want) >= 0;
}
function _findOption(doc, text) {
  return doc.defaultView.eval('(' + PICK_FN + ')')(String(text));
}
function _pickOption(text) {
  return cy.document({ log: false, timeout: 8000 })
    .should((doc) => { expect(_findOption(doc, text), 'option "' + text + '"').to.exist; })
    .then((doc) => { cy.wrap(_findOption(doc, text), { log: false }).click({ force: true }); });
}
// Infinite scroll / lazy load: scroll down until the step's element exists.
function _scrollTo(key, seed, intent) {
  return cy.task('loadLocators', null, { log: false }).then((store) => {
    const specs = [store && store[key], seed].concat((seed && seed.fallbacks) || []).filter(Boolean);
    const step = (i) => {
      const hit = specs.find((s) => _has(s));
      if (hit) return _q(toSelector(hit)).first().scrollIntoView();
      if (i >= SCROLL_MAX) {
        return cy.healSelector(key, seed, intent).then((sel) => _q(sel).first().scrollIntoView());
      }
      return cy.window({ log: false })
        .then((w) => { w.scrollTo(0, w.document.documentElement.scrollHeight); })
        .wait(1200, { log: false }).then(() => step(i + 1));
    };
    return step(0);
  });
}

Cypress.Commands.add('healAct', (key, verb, seed, intent, value) => {
  if (verb === 'navigate' || verb === 'wait') return;
  if (verb === 'scroll_to') return _scrollTo(key, seed, intent);
  cy.healSelector(key, seed, intent).then((sel) => _ready(sel, verb)).then((sel) => {
    if (verb === 'type') {
      _q(sel).first().clear();
      if (value) _q(sel).first().type(String(value));
    } else if (verb === 'select') {
      _q(sel).first().then(($el) => {
        const opts = String(value == null ? '' : value).split(';').map((o) => o.trim()).filter(Boolean);
        if ($el.is('select') && opts.length > 1) cy.wrap($el).select(opts);   // multi-select
        else if ($el.is('select')) cy.wrap($el).select(String(value));
        else {
          // Custom dropdown: open the trigger, then pick the option by its text.
          cy.wrap($el).scrollIntoView().click({ force: true });
          _pickOption(String(value));
        }
      });
    } else if (verb === 'drag') {
      // Drop target = the element showing `value`. HTML5 draggable items get
      // drag events with a shared DataTransfer; others get mouse events.
      _q(sel).first().then(($src) => {
        cy.document({ log: false, timeout: 8000 })
          .should((doc) => { expect(doc.defaultView.eval('(' + DROP_FN + ')')(String(value)), 'drop target').to.exist; })
          .then((doc) => {
            const tgt = doc.defaultView.eval('(' + DROP_FN + ')')(String(value));
            if ($src.attr('draggable') === 'true') {
              const dt = new doc.defaultView.DataTransfer();
              cy.wrap($src).trigger('dragstart', { dataTransfer: dt });
              cy.wrap(tgt).trigger('dragenter', { dataTransfer: dt })
                .trigger('dragover', { dataTransfer: dt }).trigger('drop', { dataTransfer: dt });
              cy.wrap($src).trigger('dragend', { dataTransfer: dt });
            } else {
              const r = tgt.getBoundingClientRect();
              const x = r.left + r.width / 2, y = r.top + r.height / 2;
              cy.wrap($src).trigger('mousedown', { which: 1, button: 0, force: true });
              cy.wrap(doc.body).trigger('mousemove', { which: 1, clientX: x, clientY: y, pageX: x, pageY: y, force: true })
                .trigger('mouseup', { which: 1, clientX: x, clientY: y, pageX: x, pageY: y, force: true });
            }
          });
      });
    } else if (verb === 'autocomplete') {
      const raw = String(value == null ? '' : value);
      const cut = raw.indexOf('=>');
      const typed = (cut >= 0 ? raw.slice(0, cut) : raw).trim();
      const pick = (cut >= 0 ? raw.slice(cut + 2) : raw).trim() || typed;
      _q(sel).first().click({ force: true }).clear().type(typed || pick);
      _pickOption(pick);
    } else if (verb === 'double_click') {
      _q(sel).first().dblclick({ force: true });
    } else if (verb === 'right_click') {
      _q(sel).first().rightclick({ force: true });
    } else if (verb === 'set_value') {
      _q(sel).first().then(($el) => {
        const node = $el.get(0);
        const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value');
        if (d && d.set) d.set.call(node, String(value)); else node.value = String(value);
        node.dispatchEvent(new Event('input', { bubbles: true }));
        node.dispatchEvent(new Event('change', { bubbles: true }));
      });
    } else if (verb === 'upload') {
      _q(sel).first().selectFile(String(value), { force: true });
    } else if (verb === 'key_press') {
      // 'ENTER' -> '{enter}', 'CONTROL+A' -> '{ctrl}a'; keys Cypress can't type
      // (Tab…) fire a keydown event instead.
      const parts = String(value || 'ENTER').toUpperCase().split('+').map((p) => p.trim());
      const mods = { CONTROL: '{ctrl}', CTRL: '{ctrl}', SHIFT: '{shift}', ALT: '{alt}', META: '{meta}', CMD: '{meta}' };
      const k = parts.pop();
      const prefix = parts.map((p) => mods[p] || '').join('');
      const typed = _CY_KEYS[k] || (k.length === 1 ? k.toLowerCase() : '');
      if (typed) _q(sel).first().type(prefix + typed, { force: true });
      else _q(sel).first().trigger('keydown', { key: k.charAt(0) + k.slice(1).toLowerCase(),
        ctrlKey: parts.includes('CONTROL') || parts.includes('CTRL'), shiftKey: parts.includes('SHIFT'),
        altKey: parts.includes('ALT'), metaKey: parts.includes('META') || parts.includes('CMD') });
    } else if (verb === 'clear') {
      _q(sel).first().clear({ force: true });
    } else if (verb === 'hover') {
      // Cypress has no native hover: fire the events a pointer would.
      _q(sel).first().scrollIntoView().trigger('mouseover').trigger('mouseenter');
    } else {
      _q(sel).first().scrollIntoView().click({ force: true });
    }
  });
});

Cypress.Commands.add('healAssertVisible', (key, seed, intent) => {
  cy.healSelector(key, seed, intent).then((sel) => {
    _q(sel).first().should('be.visible');
  });
});

Cypress.Commands.add('healAssertChecked', (key, seed, intent) => {
  cy.healSelector(key, seed, intent).then((sel) => _q(sel).first().should('be.checked'));
});

Cypress.Commands.add('healAssertEnabled', (key, seed, intent, enabled) => {
  cy.healSelector(key, seed, intent).then((sel) =>
    _q(sel).first().should(enabled ? 'be.enabled' : 'be.disabled'));
});

Cypress.Commands.add('healAssertSelected', (key, seed, intent, expected) => {
  cy.healSelector(key, seed, intent).then((sel) =>
    _q(sel).first().find('option:selected').should('contain.text', expected));
});

Cypress.Commands.add('healAssertHidden', (seed) => {
  // Do not self-heal a disappearance check: a replacement locator could match
  // a different visible element after the target was correctly removed.
  // (An element that is already gone is the PASS case, so don't require it.)
  const sel = seed ? toSelector(seed) : '';
  if (!sel) throw new Error('Missing verified locator for hidden assertion.');
  // Removal is often async (a loading bar first): retry up to 10 s.
  const poll = (left) => _scopeBody().then(($body) => {
    const n = $body.find(sel).filter(':visible').length;
    if (n === 0 || left <= 0) { expect(n, sel + ' is not visible').to.equal(0); return; }
    return cy.wait(250, { log: false }).then(() => poll(left - 1));
  });
  return poll(40);
});

// Arabic-aware normalization — same rationale as the Playwright healer's
// normText: on-screen copy and AI keywords differ in diacritics/alef/teh-
// marbuta/yaa variants, which exact substring matching can't survive.
function _cyNormText(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/[ً-ْٰـ]/g, '')
    .replace(/[أإآ]/g, 'ا')
    .replace(/ة/g, 'ه')
    .replace(/ى/g, 'ي');
}

Cypress.Commands.add('healAssertText', (keywords) => {
  _scopeBody({ timeout: 8000 }).should(($b) => {
    const t = _cyNormText($b.text());
    const ok = keywords.some((k) => k && t.indexOf(_cyNormText(k)) !== -1);
    expect(ok, 'page contains one of: ' + keywords.join(', ')).to.equal(true);
  });
});

// ── browser-context steps ──
Cypress.Commands.add('healEnterFrame', (key, seed, intent) => {
  __frame = null;   // resolve the iframe on the main page
  cy.healSelector(key, seed, intent).then((sel) => { __frame = sel; });
});
// Cypress answers alerts itself; a confirm's OK/Cancel is set before the click.
Cypress.Commands.add('prepareDialog', (accept, text) => {
  cy.once('window:confirm', () => accept);
  if (text != null) {
    // A JavaScript prompt: answer it (Cypress has no native prompt handling).
    cy.window({ log: false }).then((w) => { cy.stub(w, 'prompt').returns(accept ? String(text) : null); });
  }
});
// Leave every iframe: later steps run on the main page again.
Cypress.Commands.add('exitFrame', () => { __frame = null; });
// Cypress keeps one tab, so "back to the original window" = previous page.
Cypress.Commands.add('backToOriginalWindow', () => { __frame = null; cy.go('back'); });
// In a table row containing `row`, the column headed `column` shows `expected`.
// URL / title / row or text count / column sort / text pattern.
Cypress.Commands.add('healAssertPage', (check, value) => {
  if (check === 'cookie') {
    // Read through Cypress, so HttpOnly cookies count too; retried for 10 s.
    const name = String(value).split('|')[0].trim();
    const poll = (left) => cy.getCookie(name, { log: false }).then((c) => {
      const ok = cookieOk(c ? c.value : null, value);
      if (ok || left <= 0) { expect(ok, 'cookie ' + value).to.equal(true); return; }
      return cy.wait(300, { log: false }).then(() => poll(left - 1));
    });
    return poll(33);
  }
  _scopeDoc().should((doc) => {
    const ok = doc.defaultView.eval('(' + PAGE_FN + ')')(String(check), String(value), doc, doc.defaultView.location);
    expect(ok, check + ' ' + value).to.equal(true);
  });
});
// Back / forward / refresh, and "close X if it is shown" (pop-ups, ads, cookie
// banners): nothing shown within 4 s is not a failure.
function _paginate(what, left) {
  return _scopeDoc().then((doc) => {
    const got = doc.defaultView.eval('(' + PAGINATE_FN + ')')(String(what), doc);
    if (got || left <= 0) return String(got || '');
    return cy.wait(300, { log: false }).then(() => _paginate(what, left - 1));
  });
}
Cypress.Commands.add('healPageAction', (verb, value) => {
  const filled = String(qaFill(value) || '');
  if (verb === 'go_to_page' || (verb === 'next_page' && !filled.trim())) {
    const what = verb === 'go_to_page' ? filled.trim() : 'next';
    return _paginate(what, 13).then((got) => {
      if (!got) throw new Error('No "' + what + '" control in the pagination');
      if (got === 'end') throw new Error('Already on the last page');
      Cypress.log({ name: 'page', message: got });
    }).wait(800, { log: false });
  }
  if (verb === 'next_page') {
    const target = filled.trim();
    const step = (n) => _scopeDoc().then((doc) => {
      if (_n(doc.body ? doc.body.innerText : '').indexOf(_n(target)) >= 0) {
        Cypress.log({ name: 'page', message: 'found "' + target + '" on page ' + (n + 1) });
        return;
      }
      if (n >= PAGINATE_MAX) throw new Error('"' + target + '" was not found within ' + PAGINATE_MAX + ' pages');
      return _paginate('next', 13).then((got) => {
        if (!got || got === 'end') throw new Error('"' + target + '" was not found on any page (' + (n + 1) + ' checked)');
      }).wait(1000, { log: false }).then(() => step(n + 1));
    });
    return step(0);
  }
  if (verb === 'set_cookie') {
    const i = filled.indexOf('=');
    return cy.setCookie((i < 0 ? filled : filled.slice(0, i)).trim(), i < 0 ? '' : filled.slice(i + 1).trim());
  }
  if (verb === 'delete_cookies') return filled.trim() ? cy.clearCookie(filled.trim()) : cy.clearCookies();
  if (verb === 'set_storage' || verb === 'clear_storage') {
    const p = filled.split('|'), set = verb === 'set_storage';
    return cy.window({ log: false }).then((w) => {
      w.eval('(' + STORAGE_FN + ')')(set ? 'set' : 'clear', p[0] || (set ? 'local' : 'all'), p[1] || '', p.slice(2).join('|'), w);
    });
  }
  if (verb === 'auth_open') {
    // Ask the page which scheme it wants first, then answer it: Basic through
    // cy.visit's auth, Digest with a header computed in Node (cy-tasks.js).
    // Credentials never go in the URL: a "user:pass@" URL made cy.visit wait
    // forever on the browser's own sign-in prompt. The password is never logged.
    const p = filled.split('|');
    const user = p[1] || '', pass = p[2] || '';
    return cy.url({ log: false }).then((cur) => {
      const plain = new URL(p[0].trim(), /^https?:/i.test(cur) ? cur : (Cypress.config('baseUrl') || undefined)).toString();
      cy.request({ url: plain, failOnStatusCode: false, log: false, auth: { username: user, password: pass },
                   timeout: 30000 }).then((res) => {
        const www = String((res.headers || {})['www-authenticate'] || '');
        if (res.status === 401 && /^\\s*digest/i.test(www)) {
          cy.task('qaDigestHeader', { url: plain, method: 'GET', user, pass, challenge: www }, { log: false })
            .then((h) => cy.visit(plain, { headers: { Authorization: h }, log: false, timeout: 30000 }));
        } else {
          cy.visit(plain, { auth: { username: user, password: pass }, log: false, timeout: 30000 });
        }
        Cypress.log({ name: 'auth', message: 'opened ' + plain + ' with HTTP authentication' });
      });
    });
  }
  if (verb === 'go_back') return cy.go('back');
  if (verb === 'go_forward') return cy.go('forward');
  if (verb === 'refresh') return cy.reload();
  const tryClose = (left) => cy.document({ log: false }).then((doc) => {
    const got = doc.defaultView.eval('(' + DISMISS_FN + ')')(String(value || ''), doc);
    if (got) { Cypress.log({ name: 'close', message: got }); return; }
    if (left <= 0) { Cypress.log({ name: 'close', message: 'nothing to close (not shown)' }); return; }
    cy.wait(300, { log: false }).then(() => tryClose(left - 1));
  });
  return tryClose(13);
});
// Reads the first page text matching `pattern`; later steps use it as {{name}}
// (the spec runs those steps inside cy.then, after this value is known).
Cypress.Commands.add('healRemember', (pattern, name) => {
  const read = (doc) => doc.defaultView.eval('(' + REMEMBER_FN + ')')(String(pattern), doc);
  _scopeDoc().should((doc) => {
    expect(read(doc), 'page text matching ' + pattern).to.not.equal('');
  }).then((doc) => {
    const values = Object.assign({}, Cypress.env('QA_DATA') || {});
    values[name] = read(doc);
    Cypress.env('QA_DATA', values);
    Cypress.log({ name: 'remember', message: name + ' = ' + values[name] });
  });
});
Cypress.Commands.add('healAssertTableCell', (row, column, expected) => {
  _scopeDoc().should((doc) => {
    const ok = doc.defaultView.eval('(' + TABLE_FN + ')')(String(row), String(column), String(expected));
    expect(ok, 'row "' + row + '" / column "' + column + '" shows "' + expected + '"').to.equal(true);
  });
});
// A finished download whose name contains `name` (cypress/downloads), within 20 s.
Cypress.Commands.add('healAssertDownloaded', (name) => {
  const check = (n) => cy.task('findDownload', name, { log: false }).then((ok) => {
    if (ok) return;
    if (n <= 0) throw new Error('File "' + name + '" was not downloaded');
    return cy.wait(1000, { log: false }).then(() => check(n - 1));
  });
  return check(20);
});
Cypress.Commands.add('healAssertValue', (key, seed, intent, expected) => {
  cy.healSelector(key, seed, intent).then((sel) => _q(sel).first().should(($el) => {
    // An input's value, or the text a custom dropdown shows.
    // A native <select> shows its selected option's text, not its value.
    const shown = $el.is('select')
      ? $el.find('option:selected').map((i, o) => o.text + ' ' + o.value).get().join(' ')
      : ($el.is('input,textarea') ? $el.val() : $el.text());
    expect(_cyNormText(shown), 'shown value').to.contain(_cyNormText(expected));
  }));
});
Cypress.Commands.add('healAssertInvalid', (key, seed, intent) => {
  cy.healSelector(key, seed, intent).then((sel) => _q(sel).first().should(($el) => {
    const node = $el.get(0);
    expect(node.checkValidity ? node.checkValidity() : true, 'browser validation').to.eq(false);
  }));
});
// Cypress drives one tab: a link or window.open that would open a new tab
// opens in the same tab instead, and the test continues there.
Cypress.Commands.add('prepareSameTabWindow', () => {
  cy.window({ log: false }).then((w) => {
    cy.stub(w, 'open').callsFake((url) => { w.location.href = url; return w; });
  });
  cy.document({ log: false }).then((d) => {
    d.querySelectorAll('a[target]').forEach((a) => a.removeAttribute('target'));
  });
});
"""

_CY_README = """# QA Studio — self-healing Cypress tests

Generated by QA Studio. Locators live in `locators.json` (committed): it is seeded
at generation time, and when a step's locator fails at RUNTIME the browser healer
asks the Node side (via `cy.task`) to have your AI provider pick the right element
from the live DOM, then writes the verified locator back into `locators.json` — so
the AI is asked at most once per step and every later run reuses it.

## Any environment, no regeneration
The app URLs and credentials come from `.env` (git-ignored) or real environment
variables, never from the generated code. Copy `.env.example` to `.env`, fill it in,
and the same project runs against dev / test / staging / prod. Because the UI is
identical across environments, `locators.json` is shared (committed).

## Run
Needs Node.js 18+ (https://nodejs.org). QA Studio already ran `npm install` here
when Node was available (that also downloads the Cypress app).

- **VS Code:** Terminal → Run Task → **Run tests** (installs anything missing first).
- **IntelliJ / WebStorm:** pick the **Run tests** configuration (top-right) and Run.
- **Terminal:** `npm install` once, then `npm test` (headless) or `npm run open`.

`.env` already holds the app URLs (and the test account, if you chose to save it
in QA Studio). Otherwise set `APP_USER` / `APP_PASS` there or as environment
variables. `QA_AI_API_KEY` is only needed for runtime self-healing.

The browser is visible while tests run (`HEADLESS=true` or `npm run test:headless`
hides it). After every run the HTML report opens (`OPEN_REPORT=false` turns that
off) and is emailed when `REPORT_EMAIL_TO`, `SMTP_USER` and `SMTP_PASS` (a Gmail
App Password) are set in `.env`.

Browsers: `BROWSER=` in `.env` — `chrome`, `edge`, `firefox`, `electron`, a list
(`chrome,firefox`) or `all` (empty = Cypress's Electron). Or `npm run test:chrome` /
`test:edge` / `test:firefox` / `test:all` (IntelliJ: **Run tests (Edge)** etc.). With
more than one browser the runs go at the same time (`PARALLEL=false` in `.env`: one after
another) and one report covers them all. `PROGRESS=off` in `.env` hides the progress window.
Each spec's video (`VIDEO=on`, `retain-on-failure` or `off`) plays in
`qa-studio-detailed.html`. Firefox doesn't support `chromeWebSecurity: false`, so a
case that crosses sites may need `cy.origin()` there.

## Note on locators
`xpath` seeds fall through to AI healing (Cypress has no native XPath). Most seeds
are CSS/id/name/text and resolve directly.
"""


def _emit_cy_intent(lines, key, intent, seed_fn, seeds, app_page=False):
    """Append the Cypress JS for one intent (mirrors _emit_pw_intent)."""
    role = intent.get("role")
    target = intent.get("target", "")
    ij = {"target": target, "keywords": intent.get("keywords", []),
          "kind": intent.get("kind", "any"), "verb": intent.get("verb", "")}
    by, val, _known = seed_fn(intent, app_page=app_page)
    seed = None if val == "TODO_RESOLVE_AT_RUNTIME" else {"by": by, "value": val}
    seed = _with_fallbacks(seed, intent, xpath_ok=False)

    def _record():
        if seed is not None:
            seeds[key] = {"by": by, "value": val,
                          "source": "inspection" if intent.get("live_locator") else "seed"}

    if role == "precondition":
        lines.append("    // precondition (no UI action): %s" %
                     (str(target)[:70].replace("\n", " ")))
        return
    if role == "assertion" and (intent.get("check") or "") == "table":
        row, col, want = (str(intent.get("value") or "").split("|") + ["", "", ""])[:3]
        lines.append("    cy.healAssertTableCell(%s, %s, %s);"
                     % tuple(json.dumps(x.strip(), ensure_ascii=False) for x in (row, col, want)))
        return
    if role == "assertion" and (intent.get("check") or "") == "download":
        lines.append("    cy.healAssertDownloaded(%s);"
                     % json.dumps(str(intent.get("value") or ""), ensure_ascii=False))
        return
    if role == "assertion" and (intent.get("check") or "") in _E_PAGE_CHECKS:
        lines.append("    cy.healAssertPage(%s, %s);"
                     % (json.dumps(intent["check"]), json.dumps(str(intent.get("value") or ""), ensure_ascii=False)))
        return
    if role == "action" and (intent.get("verb") or "") in _E_PAGE_ACTIONS \
            and intent.get("verb") != "remember":
        lines.append("    cy.healPageAction(%s, %s);"
                     % (json.dumps(intent["verb"]), json.dumps(str(intent.get("value") or ""), ensure_ascii=False)))
        return
    if role == "action" and (intent.get("verb") or "") == "remember":
        # Later steps read {{name}} when they run, so they go inside cy.then.
        lines.append("    cy.healRemember(%s, %s);"
                     % (json.dumps(str(intent.get("value") or ""), ensure_ascii=False),
                        json.dumps(str(intent.get("remember_as") or "value"), ensure_ascii=False)))
        lines.append("    cy.then(() => {")
        return
    if role == "assertion":
        kind = (intent.get("kind") or "").lower()
        kws = [k for k in (intent.get("keywords") or []) if k] or ([target] if target else [])
        if intent.get("assert_before_action"):
            lines.append("    cy.healAssertHidden(%s);" % json.dumps(seed, ensure_ascii=False))
            _record()
            return
        check = (intent.get("check") or "").lower()
        if check == "checked":
            lines.append("    cy.healAssertChecked(%s, %s, %s);"
                         % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                            json.dumps(ij, ensure_ascii=False)))
            _record()
            return
        if check in ("enabled", "disabled"):
            lines.append("    cy.healAssertEnabled(%s, %s, %s, %s);"
                         % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                            json.dumps(ij, ensure_ascii=False),
                            "true" if check == "enabled" else "false"))
            _record()
            return
        if check == "invalid":
            lines.append("    cy.healAssertInvalid(%s, %s, %s);"
                         % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                            json.dumps(ij, ensure_ascii=False)))
            _record()
            return
        if check == "value":
            want = str(intent.get("value") or intent.get("expected") or "")
            lines.append("    cy.healAssertValue(%s, %s, %s, %s);"
                         % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                            json.dumps(ij, ensure_ascii=False), json.dumps(want, ensure_ascii=False)))
            _record()
            return
        if check in ("selected", "selected_value", "value_selected"):
            expected = (intent.get("value") or (kws[0] if kws else "") or
                        intent.get("expected") or target)
            lines.append("    cy.healAssertSelected(%s, %s, %s, %s);"
                         % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                            json.dumps(ij, ensure_ascii=False), json.dumps(expected, ensure_ascii=False)))
            _record()
            return
        if (seed is None and not intent.get("inspection_unresolved") and
                (kind in ("text", "message", "menu", "validation", "error")
                 or not kind)):
            lines.append("    cy.healAssertText(%s);" % json.dumps(kws, ensure_ascii=False))
            return
        _todo = "  // TODO verify locator (resolved at runtime)" if seed is None else ""
        lines.append("    cy.healAssertVisible(%s, %s, %s);%s"
                     % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                        json.dumps(ij, ensure_ascii=False), _todo))
        _record()
        return
    verb = intent.get("verb") or "click"
    value = intent.get("value", "")
    if verb == "navigate" and str(value).lower().startswith(("http://", "https://")):
        lines.append("    cy.visit(%s);" % json.dumps(value, ensure_ascii=False))
        return
    if verb in ("dialog_accept", "dialog_dismiss"):
        lines.append("    // browser dialog %s was answered by prepareDialog before the click"
                     % ("dismiss" if verb == "dialog_dismiss" else "accept"))
        return
    if verb == "window_switch":
        lines.append("    // new window opened in this tab (Cypress drives a single tab)")
        return
    if verb == "frame_exit":
        lines.append("    cy.exitFrame();")
        return
    if verb in ("window_back", "window_close"):
        lines.append("    cy.backToOriginalWindow();")
        return
    if verb == "frame":
        _todo = "  // TODO verify locator (resolved at runtime)" if seed is None else ""
        lines.append("    cy.healEnterFrame(%s, %s, %s);%s"
                     % (json.dumps(key), json.dumps(seed, ensure_ascii=False),
                        json.dumps(ij, ensure_ascii=False), _todo))
        _record()
        return
    _todo = "  // TODO verify locator (resolved at runtime)" if seed is None else ""
    lines.append("    cy.healAct(%s, %s, %s, %s, %s);%s"
                 % (json.dumps(key), json.dumps(verb),
                    json.dumps(seed, ensure_ascii=False),
                    json.dumps(ij, ensure_ascii=False), _js_value(value, _cy_env),
                    _todo))
    _record()


def _cy_spec(story, cases, seed_fn, seeds):
    """Emit a Cypress spec (cypress/e2e/story-<id>.cy.js) for one story."""
    sid = str(story.get("id", "0"))
    L = []
    L.append("describe(%s, () => {"
             % json.dumps("Story %s — %s" % (sid, story.get("title", ""))))
    for ci, c in enumerate(cases):
        bucket = c.get("bucket", 3)
        explicit_login_flow = bool(c.get("explicit_login_flow"))
        L.append("")
        rows = _case_data_rows(c)
        if rows:
            # Data-driven: one test per data row, named after the row.
            L.append("  for (const row of %s) {" % json.dumps(rows, ensure_ascii=False))
            L.append("  it(%s + row.name, () => {"
                     % json.dumps(c.get("title", "case %d" % ci) + " · ", ensure_ascii=False))
            L.append("    qaUseData(row.values);")
        else:
            L.append("  it(%s, () => {" % json.dumps(c.get("title", "case %d" % ci)))
        case_start = len(L)
        as_authored = _login_steps_as_authored(c)
        if as_authored:
            # Verified login steps run as written (test isolation = logged out).
            if not _starts_with_absolute_navigate(c):
                L.append("    cy.openLoginPage();")
            explicit_login_flow = False
        elif explicit_login_flow:
            L.append("    cy.performLogin();")
        elif bucket < 2:
            L.append("    cy.openLoginPage();")
        elif bucket == 2:
            L.append("    cy.performLogin();")
        else:
            # App-page case: restore the cached login session FIRST (Cypress
            # test isolation wipes state before every test — without this,
            # visit() lands on the login redirect and none of the case's
            # elements exist; see the identical Playwright fix in _pw_spec).
            L.append("    cy.loginSession();")
            # A case that opens its own absolute URL first starts there: visiting
            # the base URL first would make that URL a second origin, which
            # Cypress refuses without cy.origin().
            first = next((i for i in c.get("intents", []) if i.get("role") == "action"), {})
            if not (first.get("verb") == "navigate" and
                    str(first.get("value") or "").lower().startswith(("http://", "https://"))):
                L.append("    cy.openAppHome();")
        intents = c.get("intents", [])
        for ii, intent in enumerate(intents):
            if (bucket == 2 and not as_authored and intent.get("role") == "action") or (
                    explicit_login_flow and _is_login_transition_action(intent)):
                continue
            # Dialog answers and same-tab windows must be set up BEFORE the
            # click that triggers them (same rule as the Playwright output).
            _next = intents[ii + 1] if ii + 1 < len(intents) else {}
            import engine as _E
            _label = _E._intent_step_label(intent)
            if _label:
                # Named step in the Cypress command log, the video and the report's code view.
                L.append("    cy.log(%s);" % json.dumps("**" + _label + "**", ensure_ascii=False))
            if intent.get("role") == "action" and _next.get("role") == "action":
                if _next.get("verb") == "dialog_accept" and _next.get("value"):
                    L.append("    cy.prepareDialog(true, %s);"
                             % json.dumps(str(_next.get("value")), ensure_ascii=False))
                elif _next.get("verb") == "dialog_accept":
                    L.append("    cy.prepareDialog(true);")
                elif _next.get("verb") == "dialog_dismiss":
                    L.append("    cy.prepareDialog(false);")
                elif _next.get("verb") == "window_switch":
                    L.append("    cy.prepareSameTabWindow();")
            _emit_cy_intent(L, "%s.%d.%d" % (sid, ci, ii), intent, seed_fn, seeds,
                            app_page=(bucket == 3 or explicit_login_flow))
        # Close the cy.then blocks opened after each remember step.
        L.extend(["    });"] * sum(1 for ln in L[case_start:] if ln == "    cy.then(() => {"))
        L[case_start:] = _wrap_data_literals(L[case_start:], "qaFill")
        L.append("  });")
        if rows:
            L.append("  }")
    L.append("});")
    return "\n".join(L) + "\n", "story-%s.cy.js" % ("".join(ch for ch in sid if ch.isalnum()) or "0")


def build_cypress_project(out_dir, sequenced, cfg, seed_fn, harvest_js,
                          cb=None, should_stop=None, orig_tcs=None):
    """Write a full self-healing Cypress (JavaScript) project from the IR."""
    cb = cb or (lambda *a, **k: None)
    should_stop = should_stop or (lambda: False)
    orig_tcs = orig_tcs or {}
    e2e_dir = os.path.join(out_dir, "cypress", "e2e")
    support_dir = os.path.join(out_dir, "cypress", "support")
    for d in (e2e_dir, support_dir):
        os.makedirs(d, exist_ok=True)
    written = []
    m = _load_manifest(out_dir)
    m.setdefault("stories", {})
    m["manifest_version"] = 1
    m["target"] = "cypress"

    prov = cfg.get("ai_provider", "anthropic")
    aibase = cfg.get("ai_base_url", "https://api.anthropic.com")
    model = cfg.get("ai_model", "claude-sonnet-4-6")

    cb("Writing self-healing Cypress framework (tasks bridge, AI · %s)…" % prov, "dim")
    _wif(os.path.join(out_dir, "package.json"), _CY_PKG_JSON, written, out_dir)
    _wif(os.path.join(out_dir, "cypress.config.js"), _CY_CONFIG, written, out_dir)
    _wif(os.path.join(out_dir, "cy-tasks.js"),
         _CY_TASKS_JS.replace("__PROVIDER__", prov)
                     .replace("__AI_BASE__", aibase).replace("__MODEL__", model),
         written, out_dir)
    _wif(os.path.join(out_dir, ".gitignore"), _CY_GITIGNORE, written, out_dir)
    _wif(os.path.join(out_dir, ".env.example"), _PW_ENV_EXAMPLE, written, out_dir)
    _wif(os.path.join(out_dir, "README.md"), _CY_README, written, out_dir)
    _wif(os.path.join(support_dir, "e2e.js"), _CY_SUPPORT_E2E, written, out_dir)
    _wif(os.path.join(support_dir, "harvest.js"),
         _CY_HARVEST_HEAD + (harvest_js or "return [];") + _CY_HARVEST_TAIL, written, out_dir)
    _wif(os.path.join(support_dir, "healer.js"),
         _with_batch2_js(_CY_HEALER_JS.replace("__UI_LOCALE__", cfg.get("ui_locale", "ar"))),
         written, out_dir)

    _prepare_env(out_dir, cfg, written)
    _write_ide_files(out_dir, "cypress", written)
    _wif(os.path.join(out_dir, "cypress", "report-mailer.js"), _report_mailer_js(), written, out_dir)
    _wif(os.path.join(out_dir, "cypress", "run-browsers.js"), _CY_RUN_BROWSERS_JS, written, out_dir)
    _wif(os.path.join(out_dir, "cypress", "progress.js"), _PROGRESS_JS, written, out_dir)
    _wif(os.path.join(out_dir, "cypress", "QaProgressWindow.java"),
         progress_window_java(), written, out_dir)
    _copy_logo(os.path.join(out_dir, "cypress", "qa-logo.png"), written, out_dir)
    # Upload steps send this file (path relative to the project root).
    os.makedirs(os.path.join(out_dir, "fixtures"), exist_ok=True)
    _wif(os.path.join(out_dir, "fixtures", "qastudio-upload.txt"),
         "QA Studio generated automation fixture.\n", written, out_dir)

    seeds = {}
    specs_owned = set()
    _todo_total = 0
    for entry in sequenced:
        if should_stop():
            break
        story = entry["story"]
        if not entry.get("cases"):
            continue
        sid = str(story.get("id"))
        cb("  generating spec for story %s (%d case(s))"
           % (story.get("id"), len(entry["cases"])), "dim")
        js, spec = _cy_spec(story, entry["cases"], seed_fn, seeds)
        _todo_total += js.count(", null,")   # null seed = resolved at runtime (TODO)
        specs_owned.add(spec)
        path = os.path.join(e2e_dir, spec)
        prior = m["stories"].get(sid) or {}
        wrote, chash = _guarded_write(path, js, prior.get("hash"), cb)
        if wrote:
            written.append(os.path.relpath(path, out_dir))
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        m["stories"][sid] = {
            "spec": spec,
            "hash": chash,
            "provider": prov if wrote else prior.get("provider", prov),
            "model": model if wrote else prior.get("model", model),
            "generatedAt": stamp if wrote else prior.get("generatedAt", stamp),
            "test_cases": {str(tc.get("id")): (tc.get("title", "") or "")
                           for tc in orig_tcs.get(sid, [])},
        }
        _save_manifest(out_dir, m)

    _merge_seed_locators(out_dir, seeds, cb)
    owned = specs_owned | {r.get("spec") for r in m["stories"].values() if r.get("spec")}
    _prune_specs(e2e_dir, owned, cb)
    _save_manifest(out_dir, m)
    cb("TODO_LIVE: %d" % _todo_total, "meta")   # reconcile the live counter to the emitted total
    if _todo_total:
        cb("TODO: %d locator(s) to resolve at runtime "
           "(the rest are seeded)." % _todo_total, "warn")
    else:
        cb("Every locator is seeded — none left for runtime self-healing.", "ok")
    cb("Wrote %d files, %d spec(s) this run." % (len(written), len(specs_owned)), "ok")
    return written


def build(target, out_dir, sequenced, cfg, seed_fn, harvest_js,
          cb=None, should_stop=None, orig_tcs=None):
    """Dispatch to the emitter for `target`. 'selenium' is handled by engine
    directly; this module owns the JS targets."""
    if target == "playwright":
        return build_playwright_project(out_dir, sequenced, cfg, seed_fn, harvest_js,
                                        cb=cb, should_stop=should_stop, orig_tcs=orig_tcs)
    if target == "cypress":
        return build_cypress_project(out_dir, sequenced, cfg, seed_fn, harvest_js,
                                     cb=cb, should_stop=should_stop, orig_tcs=orig_tcs)
    raise ValueError("Unknown or non-JS target: %r" % (target,))
