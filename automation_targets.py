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
        if key not in data:
            data[key] = {"by": seed["by"], "value": seed["value"], "source": "seed"}
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
  // A rich HTML report is written to ./playwright-report on EVERY run (pass or
  // fail), listing every test with status, steps, screenshots and traces. Open it
  // with `npx playwright show-report` (or `npm run report`).
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: process.env.APP_BASE_URL || '',
    headless: true,
    screenshot: 'on',              // capture a screenshot for EVERY test (not only failures)
    video: 'retain-on-failure',    // keep a video when a test fails
    trace: 'retain-on-failure',
    actionTimeout: 15000,
  },
});
"""

_PW_GITIGNORE = """node_modules/
test-results/
playwright-report/
# environment-specific config (URLs + secrets stay out of git):
.env
# locators.json is COMMITTED on purpose — the UI is identical across environments,
# so healed locators are shared. Only URLs/creds differ, and those live in .env.
"""

_PW_ENV_EXAMPLE = """# Copy to .env (git-ignored) and fill in for your environment.
# Real environment variables of the same name override these.
APP_BASE_URL=https://your-app.example.com
APP_LOGIN_URL=https://your-login.example.com/realms/.../protocol/openid-connect/auth
APP_USER=
APP_PASS=
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
      const j = await resp.json();
      text = j && j.content && j.content[0] && j.content[0].text;
    } else {
      resp = await fetch(BASE.replace(/\\/$/, '') + '/chat/completions', {
        method: 'POST',
        headers: { 'content-type': 'application/json',
                   'authorization': 'Bearer ' + KEY },
        body: JSON.stringify({ model: MODEL, temperature: 0,
          messages: [{ role: 'system', content: SYS },
                     { role: 'user', content: user }] }),
      });
      const j = await resp.json();
      text = j && j.choices && j.choices[0] && j.choices[0].message &&
             j.choices[0].message.content;
    }
    return _extractJson(text);
  } catch (e) {
    console.log('[heal] AI call failed: ' + e.message);
    return null;
  }
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

class Healer {
  constructor(page) { this.page = page; this.store = load(); }

  _loc(spec) { return this.page.locator(toSelector(spec)).first(); }

  async _ok(spec) {
    try { return (await this.page.locator(toSelector(spec)).count()) > 0; }
    catch (e) { return false; }
  }

  async resolve(key, seed, intent) {
    const cached = this.store[key];
    if (cached && await this._ok(cached)) return this._loc(cached);
    if (seed && await this._ok(seed)) return this._loc(seed);
    if (HEAL) {
      console.log('[heal] resolving ' + key + ' via AI (' + PROVIDER + ')');
      let dom = '[]';
      try { dom = JSON.stringify(await this.page.evaluate(HARVEST_EXPR)); } catch (e) {}
      const picked = await pickLocator(intent, dom);
      if (picked && await this._ok(picked)) {
        this.store[key] = { by: picked.by, value: picked.value, source: 'healed',
                            resolvedAt: new Date().toISOString(), provider: PROVIDER };
        save(this.store);
        console.log('[heal] ' + key + ' -> ' + picked.by + '=' + picked.value);
        return this._loc(picked);
      }
    }
    throw new Error('Could not resolve step ' + key +
      '. Set QA_AI_API_KEY to enable AI healing.');
  }

  async act(key, verb, seed, intent, value) {
    if (verb === 'navigate' || verb === 'wait') return;
    const el = await this.resolve(key, seed, intent);
    try { await el.scrollIntoViewIfNeeded(); } catch (e) {}
    switch (verb) {
      case 'type':   await el.fill(value == null ? '' : String(value)); break;
      case 'select':
      case 'hover':
      case 'click':
      default:       await el.click(); break;
    }
  }

  async assertVisible(key, seed, intent) {
    try { return await (await this.resolve(key, seed, intent)).isVisible(); }
    catch (e) { return false; }
  }

  // Verify by PAGE TEXT (no locator, no AI): true if the page contains ANY keyword.
  async assertText(keywords) {
    const end = Date.now() + 8000;
    do {
      let t = '';
      try { t = (await this.page.locator('body').innerText()).toLowerCase(); }
      catch (e) {}
      for (const k of keywords) {
        if (k && t.includes(String(k).toLowerCase())) return true;
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

async function openLoginPage(page) {
  await page.goto(process.env.APP_LOGIN_URL || process.env.APP_BASE_URL || '/');
}

async function performLogin(page) {
  await openLoginPage(page);
  const user = page.locator('#username, input[name=username], input[type=email]').first();
  const pass = page.locator('#password, input[type=password]').first();
  try { await user.fill(process.env.APP_USER || ''); } catch (e) {}
  try { await pass.fill(process.env.APP_PASS || ''); } catch (e) {}
  await page.locator('#kc-login, button[type=submit], input[type=submit]').first().click();
  try { await page.waitForLoadState('networkidle', { timeout: 12000 }); } catch (e) {}
}
module.exports = { openLoginPage, performLogin };
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
1. `npm install`
2. `npx playwright install`   (first time — downloads browsers)
3. Copy `.env.example` to `.env` and set `APP_BASE_URL`, `APP_LOGIN_URL`,
   `APP_USER`, `APP_PASS`, and `QA_AI_API_KEY` (to enable self-healing).
4. `npm test`

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
        "# AI self-healing — paste a key to enable auto-repair of locators.\n"
        "QA_AI_API_KEY=\n"
        "QA_AI_PROVIDER=%s\n"
        "QA_AI_BASE_URL=%s\n"
        "QA_AI_MODEL=%s\n"
        % (cfg.get("base_url", ""), cfg.get("login_url", ""),
           cfg.get("ai_provider", "anthropic"),
           cfg.get("ai_base_url", "https://api.anthropic.com"),
           cfg.get("ai_model", "claude-sonnet-4-6")))


def _spec_name(sid):
    safe = "".join(ch if (ch.isalnum()) else "" for ch in str(sid)) or "0"
    return "story-%s.spec.js" % safe


def _emit_pw_intent(lines, key, intent, seed_fn, seeds):
    """Append the Playwright JS for one intent. Mirrors engine._emit_intent: keyed
    steps record a known seed into `seeds` for locators.json pre-seeding."""
    role = intent.get("role")
    target = intent.get("target", "")
    ij = {"target": target, "keywords": intent.get("keywords", []),
          "kind": intent.get("kind", "any"), "verb": intent.get("verb", "")}
    by, val, _known = seed_fn(intent)
    seed = None if val == "TODO_RESOLVE_AT_RUNTIME" else {"by": by, "value": val}

    def _record():
        if seed is not None:
            seeds[key] = {"by": by, "value": val}

    if role == "precondition":
        lines.append("    // precondition (no UI action): %s" %
                     (str(target)[:70].replace("\n", " ")))
        return
    if role == "assertion":
        kind = (intent.get("kind") or "").lower()
        kws = [k for k in (intent.get("keywords") or []) if k] or ([target] if target else [])
        if seed is None and (kind in ("text", "message", "menu", "validation", "error")
                             or not kind):
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
    _todo = "  // TODO verify locator (resolved at runtime)" if seed is None else ""
    lines.append("    await heal.act(%s, %s, %s, %s, %s);%s"
                 % (json.dumps(key), json.dumps(verb),
                    json.dumps(seed, ensure_ascii=False),
                    json.dumps(ij, ensure_ascii=False), json.dumps(value, ensure_ascii=False),
                    _todo))
    _record()


def _pw_spec(story, cases, seed_fn, seeds):
    """Emit a Playwright spec file for one story from compiled intents."""
    sid = str(story.get("id", "0"))
    L = []
    L.append("const { test, expect } = require('../fixtures');")
    L.append("const { openLoginPage, performLogin } = require('../support/auth');")
    L.append("")
    L.append("test.describe(%s, () => {"
             % json.dumps("Story %s — %s" % (sid, story.get("title", ""))))
    for ci, c in enumerate(cases):
        bucket = c.get("bucket", 3)
        L.append("")
        L.append("  test(%s, async ({ page, heal }) => {"
                 % json.dumps(c.get("title", "case %d" % ci)))
        if bucket < 2:
            L.append("    await openLoginPage(page);")
        elif bucket == 2:
            L.append("    await performLogin(page);")
        else:
            L.append("    await page.goto(process.env.APP_BASE_URL || '/');")
        for ii, intent in enumerate(c.get("intents", [])):
            if bucket == 2 and intent.get("role") == "action":
                continue
            _emit_pw_intent(L, "%s.%d.%d" % (sid, ci, ii), intent, seed_fn, seeds)
        L.append("  });")
    L.append("});")
    return "\n".join(L) + "\n", _spec_name(sid)


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
    for d in (tests_dir, support_dir):
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
    _wif(os.path.join(out_dir, ".gitignore"), _PW_GITIGNORE, written, out_dir)
    _wif(os.path.join(out_dir, ".env.example"), _PW_ENV_EXAMPLE, written, out_dir)
    _wif(os.path.join(out_dir, "README.md"), _PW_README, written, out_dir)
    _wif(os.path.join(out_dir, "fixtures.js"), _PW_FIXTURES, written, out_dir)
    _wif(os.path.join(support_dir, "locators.js"), _PW_LOCATORS_JS, written, out_dir)
    _wif(os.path.join(support_dir, "aiClient.js"),
         _PW_AICLIENT_JS.replace("__PROVIDER__", prov)
                        .replace("__AI_BASE__", aibase).replace("__MODEL__", model),
         written, out_dir)
    _wif(os.path.join(support_dir, "harvest.js"),
         _PW_HARVEST_HEAD + (harvest_js or "return [];") + _PW_HARVEST_TAIL, written, out_dir)
    _wif(os.path.join(support_dir, "healer.js"), _PW_HEALER_JS, written, out_dir)
    _wif(os.path.join(support_dir, "auth.js"), _PW_AUTH_JS, written, out_dir)

    # .env holds real environment values — written ONCE so a re-run never clobbers edits.
    envp = os.path.join(out_dir, ".env")
    if not os.path.exists(envp):
        with open(envp, "w", encoding="utf-8") as f:
            f.write(_pw_env_real(cfg))
        written.append(".env")

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
    cb("TODO: %d locator(s) to resolve at runtime "
       "(the rest are seeded)." % _todo_total, "warn")
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
  try {
    let text;
    if (PROVIDER === 'anthropic') {
      const r = await fetch(BASE.replace(/\\/$/, '') + '/v1/messages', {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-api-key': KEY,
                   'anthropic-version': '2023-06-01' },
        body: JSON.stringify({ model: MODEL, max_tokens: 300, system: SYS,
                               messages: [{ role: 'user', content: user }] }),
      });
      const j = await r.json();
      text = j && j.content && j.content[0] && j.content[0].text;
    } else {
      const r = await fetch(BASE.replace(/\\/$/, '') + '/chat/completions', {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'authorization': 'Bearer ' + KEY },
        body: JSON.stringify({ model: MODEL, temperature: 0,
          messages: [{ role: 'system', content: SYS }, { role: 'user', content: user }] }),
      });
      const j = await r.json();
      text = j && j.choices && j.choices[0] && j.choices[0].message &&
             j.choices[0].message.content;
    }
    return _extractJson(text);
  } catch (e) { return null; }
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
function _has(spec) {
  const sel = toSelector(spec);
  if (!sel) return false;
  try { return Cypress.$(sel).length > 0; } catch (e) { return false; }
}

Cypress.Commands.add('openLoginPage', () => {
  cy.visit(Cypress.env('APP_LOGIN_URL') || Cypress.env('APP_BASE_URL') || '/');
});
Cypress.Commands.add('performLogin', () => {
  cy.openLoginPage();
  cy.get('#username, input[name=username], input[type=email]').first()
    .clear().type(Cypress.env('APP_USER') || ' ');
  cy.get('#password, input[type=password]').first()
    .clear().type(Cypress.env('APP_PASS') || ' ', { log: false });
  cy.get('#kc-login, button[type=submit], input[type=submit]').first().click();
});

Cypress.Commands.add('healSelector', (key, seed, intent) => {
  return cy.task('loadLocators', null, { log: false }).then((store) => {
    const cached = store && store[key];
    if (cached && _has(cached)) return toSelector(cached);
    if (seed && _has(seed)) return toSelector(seed);
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
  });
});

Cypress.Commands.add('healAct', (key, verb, seed, intent, value) => {
  if (verb === 'navigate' || verb === 'wait') return;
  cy.healSelector(key, seed, intent).then((sel) => {
    if (verb === 'type') {
      cy.get(sel).first().clear();
      if (value) cy.get(sel).first().type(String(value));
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

Cypress.Commands.add('healAssertText', (keywords) => {
  cy.get('body', { timeout: 8000 }).should(($b) => {
    const t = ($b.text() || '').toLowerCase();
    const ok = keywords.some((k) => k && t.indexOf(String(k).toLowerCase()) !== -1);
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
1. `npm install`
2. Copy `.env.example` to `.env` and set `APP_BASE_URL`, `APP_LOGIN_URL`,
   `APP_USER`, `APP_PASS`, and `QA_AI_API_KEY` (to enable self-healing).
3. `npm test`   (headless)  ·  `npm run open`  (interactive)

## Note on locators
`xpath` seeds fall through to AI healing (Cypress has no native XPath). Most seeds
are CSS/id/name/text and resolve directly.
"""


def _emit_cy_intent(lines, key, intent, seed_fn, seeds):
    """Append the Cypress JS for one intent (mirrors _emit_pw_intent)."""
    role = intent.get("role")
    target = intent.get("target", "")
    ij = {"target": target, "keywords": intent.get("keywords", []),
          "kind": intent.get("kind", "any"), "verb": intent.get("verb", "")}
    by, val, _known = seed_fn(intent)
    seed = None if val == "TODO_RESOLVE_AT_RUNTIME" else {"by": by, "value": val}

    def _record():
        if seed is not None:
            seeds[key] = {"by": by, "value": val}

    if role == "precondition":
        lines.append("    // precondition (no UI action): %s" %
                     (str(target)[:70].replace("\n", " ")))
        return
    if role == "assertion":
        kind = (intent.get("kind") or "").lower()
        kws = [k for k in (intent.get("keywords") or []) if k] or ([target] if target else [])
        if seed is None and (kind in ("text", "message", "menu", "validation", "error")
                             or not kind):
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
    _todo = "  // TODO verify locator (resolved at runtime)" if seed is None else ""
    lines.append("    cy.healAct(%s, %s, %s, %s, %s);%s"
                 % (json.dumps(key), json.dumps(verb),
                    json.dumps(seed, ensure_ascii=False),
                    json.dumps(ij, ensure_ascii=False), json.dumps(value, ensure_ascii=False),
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
        L.append("")
        L.append("  it(%s, () => {" % json.dumps(c.get("title", "case %d" % ci)))
        if bucket < 2:
            L.append("    cy.openLoginPage();")
        elif bucket == 2:
            L.append("    cy.performLogin();")
        else:
            L.append("    cy.visit(Cypress.env('APP_BASE_URL') || '/');")
        for ii, intent in enumerate(c.get("intents", [])):
            if bucket == 2 and intent.get("role") == "action":
                continue
            _emit_cy_intent(L, "%s.%d.%d" % (sid, ci, ii), intent, seed_fn, seeds)
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
    _wif(os.path.join(support_dir, "healer.js"), _CY_HEALER_JS, written, out_dir)

    envp = os.path.join(out_dir, ".env")
    if not os.path.exists(envp):
        with open(envp, "w", encoding="utf-8") as f:
            f.write(_pw_env_real(cfg))
        written.append(".env")

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
    cb("TODO: %d locator(s) to resolve at runtime "
       "(the rest are seeded)." % _todo_total, "warn")
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
