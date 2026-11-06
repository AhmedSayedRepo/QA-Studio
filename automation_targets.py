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
import os, json, time, hashlib

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
    "pretest": "playwright install chromium",
    "test": "playwright test",
    "test:headed": "playwright test --headed",
    "report": "playwright show-report"
  },
  "engines": { "node": ">=18" },
  "devDependencies": { "@playwright/test": "^1.48.0" },
  "dependencies": { "dotenv": "^16.4.5" }
}
"""

_PW_CONFIG = """// Playwright config. baseURL and everything environment-specific come from the
// environment (.env, git-ignored) or real env vars — nothing is baked in, so the
// same project runs against any environment with no regeneration.
require('dotenv').config();
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  timeout: 45000,
  expect: { timeout: 10000 },
  fullyParallel: false,
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
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: process.env.APP_BASE_URL || '',
    storageState: '.auth/state.json',   // every test starts logged in (see global-setup.js)
    headless: true,
    screenshot: 'on',              // capture a screenshot for EVERY test (not only failures)
    video: 'retain-on-failure',    // keep a video when a test fails
    trace: 'retain-on-failure',
    actionTimeout: 15000,
  },
});
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
# locators.json is COMMITTED on purpose — the UI is identical across environments,
# so healed locators are shared. Only URLs/creds differ, and those live in .env.
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
"""

_PW_FIXTURES = """// Extends Playwright's test with a `heal` fixture — a self-healing locator
// resolver available to every spec as ({ page, heal }).
const base = require('@playwright/test');
const { Healer } = require('./support/healer');

const test = base.test.extend({
  heal: async ({ page }, use) => { await use(new Healer(page)); },
});

module.exports = { test, expect: base.expect };
"""

_PW_LOCATORS_JS = """// Load/save the committed locators.json and translate a stored {by,value} into a
// Playwright selector string.
const fs = require('fs');
const path = require('path');
const FILE = path.join(process.cwd(), 'locators.json');

function load() {
  try { return JSON.parse(fs.readFileSync(FILE, 'utf8')); } catch (e) { return {}; }
}
function save(store) {
  try { fs.writeFileSync(FILE, JSON.stringify(store, null, 2)); } catch (e) {}
}
function toSelector(spec) {
  const by = (spec && spec.by) || 'css';
  const v = (spec && spec.value) || '';
  switch (by) {
    case 'id':          return '#' + v;
    case 'name':        return '[name=\"' + v + '\"]';
    case 'className':   return '.' + v;
    case 'tagName':     return v;
    case 'linkText':
    case 'text':        return 'text=' + v;
    case 'xpath':       return 'xpath=' + v;
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

class Healer {
  constructor(page) { this.page = page; this.scope = page; this.store = load(); this.pendingPopup = null; }

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
        save(this.store);
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

  async act(key, verb, seed, intent, value) {
    if (verb === 'navigate' || verb === 'wait') return;
    const el = await this.resolve(key, seed, intent);
    try { await el.scrollIntoViewIfNeeded(); } catch (e) {}
    switch (verb) {
      case 'type':   await el.fill(value == null ? '' : String(value)); break;
      case 'select':
        try { await el.selectOption({ label: String(value) }); break; }
        catch (e) { await el.click(); break; } // custom dropdown trigger
      case 'hover':  await el.hover(); break;
      case 'upload': await el.setInputFiles(String(value)); break;
      case 'key_press': await el.press(String(value || 'Enter')); break;
      case 'click':
      default:       await el.click(); break;
    }
  }

  async enterFrame(key, seed, intent) {
    const frame = await (await this.resolve(key, seed, intent)).contentFrame();
    if (!frame) throw new Error('Could not enter frame for ' + key);
    this.scope = frame;
  }

  prepareDialog(action) {
    this.page.once('dialog', async dialog => {
      if (action === 'dismiss') await dialog.dismiss(); else await dialog.accept();
    });
  }

  prepareWindow() { this.pendingPopup = this.page.context().waitForEvent('page'); }

  async switchWindow() {
    if (!this.pendingPopup) throw new Error('No popup was prepared before window switch');
    this.page = await this.pendingPopup;
    await this.page.waitForLoadState('domcontentloaded');
    await this.page.waitForFunction(() =>
      location.href !== 'about:blank' && !!(document.body && document.body.innerText.trim()));
    this.scope = this.page;
    this.pendingPopup = null;
  }

  async assertVisible(key, seed, intent) {
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
    try { return (await (await this.resolve(key, seed, intent)).isEnabled()) === enabled; }
    catch (e) { console.log('[heal] assertEnabled ' + key + ' failed: ' + e.message); return false; }
  }

  async assertSelected(key, seed, intent, expected) {
    try {
      const text = await (await this.resolve(key, seed, intent)).locator('option:checked').textContent();
      return normText(text).includes(normText(expected));
    } catch (e) { console.log('[heal] assertSelected ' + key + ' failed: ' + e.message); return false; }
  }

  async assertHidden(key, seed) {
    // Absence is not a healing problem: retrying an AI locator search after a
    // successful removal could bind a different, still-visible product. Test
    // only the exact locator captured immediately before the removing action.
    if (!seed || !seed.by || !seed.value) return false;
    try { return !(await this._loc(seed).isVisible()); }
    catch (e) { return true; }
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
    loginForm = true;
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


def _pw_env(name):
    return "(process.env.%s || '')" % name


def _cy_env(name):
    return "(Cypress.env('%s') || '')" % name


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
        L.append(indent + "  test(%s, async ({ page, heal }) => {"
                 % json.dumps(c.get("title", "case %d" % ci)))
        body = []
        if explicit_login_flow:
            body.append("    await performLogin(page);")
        elif bucket < 2:
            body.append("    await openLoginPage(page);")
        elif bucket == 2:
            body.append("    await performLogin(page);")
        else:
            body.append("    await openAppHome(page);")
        intents = c.get("intents", [])
        for ii, intent in enumerate(intents):
            if (bucket == 2 and intent.get("role") == "action") or (
                    explicit_login_flow and _is_login_transition_action(intent)):
                continue
            # Browser dialogs and popups are delivered by Playwright as events.
            # Register the listener before the preceding click, never after the
            # event has already fired.  Context actions themselves are emitted
            # as explicit no-locator transitions below.
            next_intent = intents[ii + 1] if ii + 1 < len(intents) else {}
            if intent.get("role") == "action" and next_intent.get("role") == "action":
                next_verb = next_intent.get("verb")
                if next_verb == "dialog_accept":
                    body.append("    heal.prepareDialog('accept');")
                elif next_verb == "dialog_dismiss":
                    body.append("    heal.prepareDialog('dismiss');")
                elif next_verb == "window_switch":
                    body.append("    heal.prepareWindow();")
            _emit_pw_intent(body, "%s.%d.%d" % (sid, ci, ii), intent, seed_fn,
                            seeds, app_page=(bucket == 3 or explicit_login_flow))
        L.extend(indent + ln for ln in body)
        L.append(indent + "  });")

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


def _env_fill(path, values):
    """Fill keys in .env that are missing or EMPTY; never overwrite a value the
    user already set. Returns the keys filled."""
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
            if not val.strip():
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


def _prepare_env(out_dir, cfg, written):
    """Create .env once, then fill any empty URL/account values from cfg."""
    envp = os.path.join(out_dir, ".env")
    if not os.path.exists(envp):
        with open(envp, "w", encoding="utf-8") as f:
            f.write(_pw_env_real(cfg))
        written.append(".env")
    _env_fill(envp, {"APP_BASE_URL": cfg.get("base_url", ""),
                     "APP_LOGIN_URL": cfg.get("login_url", ""),
                     "APP_USER": cfg.get("app_user", ""),
                     "APP_PASS": cfg.get("app_pass", "")})


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
    _wif(os.path.join(support_dir, "healer.js"), _PW_HEALER_JS, written, out_dir)
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
    "test": "cypress run",
    "open": "cypress open",
    "report": "echo Open cypress/reports/index.html for the HTML report"
  },
  "engines": { "node": ">=18" },
  "devDependencies": {
    "cypress": "^13.15.0",
    "cypress-mochawesome-reporter": "^3.8.2"
  },
  "dependencies": { "dotenv": "^16.4.5" }
}
"""

_CY_CONFIG = """// Cypress config. baseUrl + credentials come from the environment (.env,
// git-ignored) or real env vars — nothing is baked in, so the same project runs
// against any environment with no regeneration. setupNodeEvents registers the
// Node-side tasks the browser healer uses for locator persistence + AI healing.
require('dotenv').config();
const { defineConfig } = require('cypress');
const tasks = require('./cy-tasks');

module.exports = defineConfig({
  // A visual HTML report (cypress/reports/index.html) is generated on EVERY run,
  // pass or fail, listing every spec/test with status, steps, screenshots and charts.
  reporter: 'cypress-mochawesome-reporter',
  reporterOptions: {
    reportDir: 'cypress/reports',
    charts: true,
    reportPageTitle: 'QA Studio — Cypress report',
    embeddedScreenshots: true,
    inlineAssets: true,
    overwrite: false,
    saveAllAttempts: true,
  },
  e2e: {
    baseUrl: process.env.APP_BASE_URL || undefined,
    specPattern: 'cypress/e2e/**/*.cy.js',
    supportFile: 'cypress/support/e2e.js',
    defaultCommandTimeout: 12000,
    screenshotOnRunFailure: true,
    video: true,
    setupNodeEvents(on, config) {
      require('cypress-mochawesome-reporter/plugin')(on);   // merges + builds the HTML report
      on('task', {
        loadLocators: tasks.loadLocators,
        saveLocator: tasks.saveLocator,
        pickLocator: tasks.pickLocator,
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
function _save(s) { try { fs.writeFileSync(FILE, JSON.stringify(s, null, 2)); } catch (e) {} }
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
    const s = _load();
    s[key] = { by: spec.by, value: spec.value, source: 'healed',
               resolvedAt: new Date().toISOString(), provider: PROVIDER };
    _save(s);
    return null;
  },
  async pickLocator({ intent, dom }) { return (await _pick(intent, dom)) || null; },
};
"""

_CY_GITIGNORE = """node_modules/
cypress/screenshots/
cypress/videos/
cypress/reports/
# environment-specific config (URLs + secrets stay out of git):
.env
# local DOM snapshots from an opt-in live inspection:
inspection-screens.json
# locators.json is COMMITTED on purpose — healed locators are shared across
# environments (identical UI). Only URLs/creds differ, and those live in .env.
"""

_CY_SUPPORT_E2E = """// Loaded before every spec — registers the self-healing commands
// and the mochawesome HTML reporter.
require('cypress-mochawesome-reporter/register');
require('./healer');
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

function toSelector(spec) {
  const by = (spec && spec.by) || 'css';
  const v = (spec && spec.value) || '';
  switch (by) {
    case 'id':        return '#' + v;
    case 'name':      return '[name=\"' + v + '\"]';
    case 'className': return '.' + v;
    case 'tagName':   return v;
    case 'linkText':
    case 'text':      return ':contains(' + JSON.stringify(v) + ')';
    case 'xpath':     return null; // xpath needs a plugin; fall through to heal
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
  try { return Cypress.$(sel).length > 0; } catch (e) { return false; }
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
    if ($body.find('input[type=password]:visible').length) cy.performLogin();
  });
});

Cypress.Commands.add('loginSession', () => {
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
    const tryNext = (i) => {
      if (i >= steps.length) return _healWithAI(key, intent);
      return _poll(steps[i][0], steps[i][1]).then((ok) =>
        ok ? toSelector(steps[i][0]) : tryNext(i + 1));
    };
    return tryNext(0);
  });
});

function _healWithAI(key, intent) {
    return cy.document({ log: false }).then((doc) => {
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

Cypress.Commands.add('healAct', (key, verb, seed, intent, value) => {
  if (verb === 'navigate' || verb === 'wait') return;
  cy.healSelector(key, seed, intent).then((sel) => {
    if (verb === 'type') {
      cy.get(sel).first().clear();
      if (value) cy.get(sel).first().type(String(value));
    } else if (verb === 'select') {
      cy.get(sel).first().then(($el) => {
        if ($el.is('select')) cy.wrap($el).select(String(value));
        else cy.wrap($el).scrollIntoView().click({ force: true });
      });
    } else if (verb === 'hover') {
      // Cypress has no native hover: fire the events a pointer would.
      cy.get(sel).first().scrollIntoView().trigger('mouseover').trigger('mouseenter');
    } else {
      cy.get(sel).first().scrollIntoView().click({ force: true });
    }
  });
});

Cypress.Commands.add('healAssertVisible', (key, seed, intent) => {
  cy.healSelector(key, seed, intent).then((sel) => {
    cy.get(sel).first().should('be.visible');
  });
});

Cypress.Commands.add('healAssertChecked', (key, seed, intent) => {
  cy.healSelector(key, seed, intent).then((sel) => cy.get(sel).first().should('be.checked'));
});

Cypress.Commands.add('healAssertEnabled', (key, seed, intent, enabled) => {
  cy.healSelector(key, seed, intent).then((sel) =>
    cy.get(sel).first().should(enabled ? 'be.enabled' : 'be.disabled'));
});

Cypress.Commands.add('healAssertSelected', (key, seed, intent, expected) => {
  cy.healSelector(key, seed, intent).then((sel) =>
    cy.get(sel).first().find('option:selected').should('contain.text', expected));
});

Cypress.Commands.add('healAssertHidden', (seed) => {
  // Do not self-heal a disappearance check: a replacement locator could match
  // a different visible element after the target was correctly removed.
  const sel = seed && _has(seed) ? toSelector(seed) : '';
  if (!sel) throw new Error('Missing verified locator for hidden assertion.');
  cy.get('body').then(($body) => {
    expect($body.find(sel).filter(':visible').length, sel + ' is not visible').to.equal(0);
  });
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
  cy.get('body', { timeout: 8000 }).should(($b) => {
    const t = _cyNormText($b.text());
    const ok = keywords.some((k) => k && t.indexOf(_cyNormText(k)) !== -1);
    expect(ok, 'page contains one of: ' + keywords.join(', ')).to.equal(true);
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
        L.append("  it(%s, () => {" % json.dumps(c.get("title", "case %d" % ci)))
        if explicit_login_flow:
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
            L.append("    cy.openAppHome();")
        for ii, intent in enumerate(c.get("intents", [])):
            if (bucket == 2 and intent.get("role") == "action") or (
                    explicit_login_flow and _is_login_transition_action(intent)):
                continue
            _emit_cy_intent(L, "%s.%d.%d" % (sid, ci, ii), intent, seed_fn, seeds,
                            app_page=(bucket == 3 or explicit_login_flow))
        L.append("  });")
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
         _CY_HEALER_JS.replace("__UI_LOCALE__", cfg.get("ui_locale", "ar")),
         written, out_dir)

    _prepare_env(out_dir, cfg, written)
    _write_ide_files(out_dir, "cypress", written)

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
