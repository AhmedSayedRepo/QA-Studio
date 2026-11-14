"""API steps exported as a Postman v2.1 collection (postman/): one request per
API step, the case's checks as pm.test scripts, saved response fields as
collection variables, {{OTP}} / {{today}} from a pre-request script. Run here
with a small Newman stand-in: requests go to a local server and every script
runs in Node with a pm / lodash stub."""
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402

NODE = shutil.which("node")
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

_SANDBOX = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const vars = Object.assign({}, input.vars), setLocal = {}, setCollection = {}, results = [];
const get = (o, p) => String(p).replace(/\[(\d+)\]/g, '.$1').split('.').filter(Boolean)
  .reduce((a, k) => (a == null ? undefined : a[k]), o);
const replaceIn = (t) => String(t).replace(/\{\{([^{}]+)\}\}/g, (m, k) => (k in vars ? vars[k] : m));
function expect(v) {
  const neg = { equal: (w) => { if (v === w) throw new Error('expected not ' + w); } };
  const api = {
    to: { eql: (w) => { if (JSON.stringify(v) !== JSON.stringify(w)) throw new Error(JSON.stringify(v) + ' != ' + JSON.stringify(w)); },
          equal: (w) => { if (v !== w) throw new Error(v + ' !== ' + w); },
          include: (w) => { if (String(v).indexOf(w) < 0) throw new Error(v + ' lacks ' + w); },
          not: neg, have: { status: (c) => { if (v.code !== c) throw new Error('status ' + v.code); } } } };
  return api;
}
const res = input.response || {};
const pm = {
  environment: { get: (k) => input.env[k] },
  info: { requestName: input.requestName || '' },
  collectionVariables: { get: (k) => vars[k], set: (k, v) => { vars[k] = v; setCollection[k] = v; } },
  variables: { set: (k, v) => { vars[k] = v; setLocal[k] = v; }, replaceIn },
  response: { code: res.code, text: () => res.body, json: () => JSON.parse(res.body) },
  expect, test: (name, fn) => { try { fn(); results.push([name, true, '']); } catch (e) { results.push([name, false, e.message]); } },
};
pm.response.to = { have: { status: (c) => { if (res.code !== c) throw new Error('status ' + res.code); } } };
const require2 = (m) => (m === 'lodash' ? { get } : require(m));
new Function('pm', 'require', input.script)(pm, require2);
console.log(JSON.stringify({ results, setLocal, setCollection }));
"""


def _run_script(script, vars_, env, response=None, request_name=""):
    r = subprocess.run([NODE, "-e", _SANDBOX], input=json.dumps(
        {"script": script, "vars": vars_, "env": env, "response": response, "requestName": request_name}),
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


class _Notes(http.server.BaseHTTPRequestHandler):
    notes = {}

    def log_message(self, *a):
        pass

    def _reply(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.headers.get("Authorization") != "Bearer t-1":
            return self._reply(401, {"success": False, "message": "Access token is not valid"})
        nid = "n%d" % (len(_Notes.notes) + 1)
        _Notes.notes[nid] = dict(body, id=nid)
        self._reply(201, {"success": True, "data": _Notes.notes[nid]})

    who = []

    def do_GET(self):
        _Notes.who.append(self.headers.get("X-Who"))
        nid = self.path.rsplit("/", 1)[-1]
        if nid in _Notes.notes:
            return self._reply(200, {"success": True, "data": _Notes.notes[nid]})
        self._reply(404, {"success": False, "message": "Note not found"})


def _plan(base):
    return [{"story": {"id": 101, "title": "Notes API"}, "cases": [
        {"tc": {"id": "EB03"}, "title": "Create then read a note", "data": [
            {"name": "Row 1", "values": {"token": "t-1"}}], "intents": [
            {"role": "action", "verb": "navigate", "value": base + "/notes/app"},
            {"role": "action", "verb": "api_call",
             "value": 'POST|/notes/api/notes|Authorization: Bearer {{token}}|'
                      '{"title": "Milk|2", "due": "{{today+3d}}", "code": "{{OTP}}", "by": "{{random.email}}"}'},
            {"role": "assertion", "check": "api_status", "value": "201"},
            {"role": "assertion", "check": "api_body", "value": "field|data.id|exists|"},
            {"role": "assertion", "check": "api_body", "value": "contains|milk"},
            {"role": "action", "verb": "remember", "value": "api:data.id", "remember_as": "noteId"},
            {"role": "action", "verb": "click", "target": "Save"},
            {"role": "assertion", "check": "a11y", "value": "serious"},
            {"role": "action", "verb": "api_call", "value": "GET|/notes/api/notes/{{noteId}}|X-Who: {{random.email}}|"},
            {"role": "assertion", "check": "api_status", "value": "2xx"},
            {"role": "assertion", "check": "api_body", "value": "field|data.title|is|Milk|2"},
            {"role": "assertion", "check": "api_body", "value": "field|data.nope|absent|"}]},
        {"tc": {"id": "UI1"}, "title": "UI only", "intents": [{"role": "action", "verb": "click"}]}]}]


class Export(unittest.TestCase):
    def test_structure(self):
        d = tempfile.mkdtemp()
        written = E.write_api_collection(d, _plan("https://notes.example.com"), "", "demo")
        self.assertEqual(sorted(written), sorted(["api-tests/README.md", "api-tests/" + E._POSTMAN_ENV,
                                                  "api-tests/" + E._POSTMAN_COLLECTION]))
        self.assertNotEqual(E._POSTMAN_DIR, "postman")      # Postman Local Mode owns postman/
        with open(os.path.join(d, E._POSTMAN_DIR, E._POSTMAN_COLLECTION), encoding="utf-8") as f:
            c = json.load(f)
        self.assertEqual(c["info"]["schema"], E._POSTMAN_SCHEMA)
        story = c["item"][0]
        self.assertEqual(story["name"], "Story 101 · Notes API")
        self.assertEqual([ci["name"] for ci in story["item"]], ["EB03 · Create then read a note"])  # UI-only skipped
        first, second = story["item"][0]["item"]
        self.assertEqual(first["request"]["url"], "{{baseUrl}}/notes/api/notes")
        self.assertEqual(first["request"]["body"]["raw"],
                         '{"title": "Milk|2", "due": "{{today_plus_3d}}", "code": "{{OTP}}", "by": "{{random.email}}"}')
        self.assertIn({"key": "Content-Type", "value": "application/json"}, first["request"]["header"])
        self.assertNotIn("body", second["request"])
        self.assertEqual(second["name"], '2. Send GET request to "/notes/api/notes/{{noteId}}"')
        self.assertIn({"key": "baseUrl", "value": "https://notes.example.com"}, c["variable"])
        self.assertIn({"key": "token", "value": "t-1"}, c["variable"])
        tests = "\n".join(first["event"][0]["script"]["exec"])
        self.assertNotIn("a11y", tests)
        with open(os.path.join(d, E._POSTMAN_DIR, E._POSTMAN_ENV), encoding="utf-8") as f:
            env = json.load(f)
        self.assertTrue(all(v["value"] in ("", "https://notes.example.com") for v in env["values"]))

    def test_old_export_in_postman_folder_is_retired(self):
        d = tempfile.mkdtemp()
        old = os.path.join(d, "postman")
        os.makedirs(os.path.join(old, "collections"))
        with open(os.path.join(old, E._POSTMAN_COLLECTION), "w", encoding="utf-8") as f:
            f.write('{"info": {"description": "Generated by QA Studio from ..."}}')
        with open(os.path.join(old, "README.md"), "w", encoding="utf-8") as f:
            f.write("my own notes")                               # not ours: kept
        E.write_api_collection(d, _plan("https://x.io"), "")
        self.assertFalse(os.path.exists(os.path.join(old, E._POSTMAN_COLLECTION)))
        self.assertTrue(os.path.exists(os.path.join(old, "README.md")))
        self.assertTrue(os.path.isdir(os.path.join(old, "collections")))
        self.assertTrue(os.path.isfile(os.path.join(d, "api-tests", E._POSTMAN_COLLECTION)))

    def test_nothing_without_api_steps(self):
        d = tempfile.mkdtemp()
        self.assertEqual(E.write_api_collection(d, [{"story": {}, "cases": [{"intents": []}]}], ""), [])
        self.assertFalse(os.path.exists(os.path.join(d, E._POSTMAN_DIR)))

    def test_filled_in_environment_is_kept(self):
        d = tempfile.mkdtemp()
        E.write_api_collection(d, _plan("https://x.io"), "")
        path = os.path.join(d, E._POSTMAN_DIR, E._POSTMAN_ENV)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"mine": true}')
        E.write_api_collection(d, _plan("https://x.io"), "")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"mine": true}')

    def test_placeholders(self):
        self.assertEqual(E._postman_text("{{random.email}} {{APP_PASS}} {{today-2d}} {{now}} {{Order Id}}"),
                         "{{random.email}} {{APP_PASS}} {{today_minus_2d}} {{now}} {{Order Id}}")
        self.assertEqual(E._postman_url("https://other.io/a", "https://x.io", ""), "https://other.io/a")
        self.assertEqual(E._postman_url("/a", "https://x.io", "https://x.io/p/q"), "{{baseUrl}}/a")


@unittest.skipUnless(NODE, "node not installed")
class RunsLikeNewman(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Notes)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_collection_runs_and_passes(self):
        d = tempfile.mkdtemp()
        E.write_api_collection(d, _plan(self.base), "")
        with open(os.path.join(d, E._POSTMAN_DIR, E._POSTMAN_COLLECTION), encoding="utf-8") as f:
            c = json.load(f)
        env = {"OTP_SECRET": RFC_SECRET}
        vars_ = {v["key"]: v["value"] for v in c["variable"]}
        pre = "\n".join(c["event"][0]["script"]["exec"])
        all_results = []
        for item in c["item"][0]["item"][0]["item"]:
            out = _run_script(pre, vars_, env, request_name=item["name"])
            vars_.update(out["setLocal"])
            vars_.update(out["setCollection"])
            self.assertRegex(vars_["OTP"], r"^\d{6}$")
            self.assertRegex(vars_["today_plus_3d"], r"^\d{4}-\d\d-\d\d$")

            def fill(t):
                return re.sub(r"\{\{([^{}]+)\}\}", lambda m: str(vars_.get(m.group(1), m.group(0))), t)
            req = item["request"]
            data = fill(req["body"]["raw"]).encode() if "body" in req else None
            r = urllib.request.Request(fill(req["url"]), data=data, method=req["method"],
                                       headers={h["key"]: fill(h["value"]) for h in req["header"]})
            try:
                with urllib.request.urlopen(r, timeout=10) as resp:
                    code, body = resp.status, resp.read().decode()
            except urllib.error.HTTPError as e:
                code, body = e.code, e.read().decode()
            script = "\n".join(item["event"][0]["script"]["exec"])
            out = _run_script(script, vars_, env, {"code": code, "body": body})
            vars_.update(out["setCollection"])
            all_results += out["results"]
        self.assertEqual([x for x in all_results if not x[1]], [])
        self.assertEqual(len(all_results), 7)
        self.assertEqual(vars_["noteId"], "n%d" % len(_Notes.notes))
        self.assertEqual(_Notes.who, [vars_["random.email"]])        # same value all case long

    def test_failing_check_fails(self):
        out = _run_script("\n".join(E._postman_tests([("api_status", "200"), ("api_body", "field|a|is|x")], [])),
                          {}, {}, {"code": 404, "body": '{"a": "y"}'})
        self.assertEqual([ok for _, ok, _ in out["results"]], [False, False])


if __name__ == "__main__":
    unittest.main()
