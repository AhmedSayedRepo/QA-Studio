"""Automation from a phone through Remote Runs: the app queues a kind='automation'
row (options in params, secrets in Supabase Vault), the GitHub Actions worker
loads the cases (Azure selection or uploaded file), clones the Git repo, runs
the headless inspection -> replay -> generation pipeline and pushes."""
import base64
import importlib
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

GIT = shutil.which("git")


def _worker():
    os.environ.setdefault("RUN_ID", "00000000-0000-0000-0000-000000000000")
    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
    import run_worker
    return importlib.reload(run_worker)


@unittest.skipUnless(GIT, "git not installed")
class CloneForPush(unittest.TestCase):
    def _git(self, *args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))

    def test_existing_branch_is_cloned(self):
        d = tempfile.mkdtemp()
        bare, src = os.path.join(d, "r.git"), os.path.join(d, "src")
        self._git("init", "--bare", "-b", "main", bare)
        self._git("clone", bare, src)
        with open(os.path.join(src, "README.md"), "w") as f:
            f.write("x")
        self._git("add", ".", cwd=src)
        self._git("commit", "-m", "first", cwd=src)
        self._git("push", "origin", "HEAD:main", cwd=src)
        out = os.path.join(d, "proj")
        ok, msg = E.git_clone_for_push(out, bare, "", "main")
        self.assertTrue(ok, msg)
        self.assertTrue(os.path.isfile(os.path.join(out, "README.md")))

    def test_empty_repo_starts_an_empty_folder(self):
        d = tempfile.mkdtemp()
        bare = os.path.join(d, "r.git")
        self._git("init", "--bare", "-b", "main", bare)
        out = os.path.join(d, "proj")
        ok, msg = E.git_clone_for_push(out, bare, "", "main")
        self.assertTrue(ok, msg)

    def test_unreachable_repo_fails_without_leaking_the_token(self):
        ok, msg = E.git_clone_for_push(os.path.join(tempfile.mkdtemp(), "p"),
                                       "/nonexistent/repo.git", "SECRET-TOKEN", "main")
        self.assertFalse(ok)
        self.assertNotIn("SECRET-TOKEN", msg)


class Pipeline(unittest.TestCase):
    PAYLOAD = [{"story": {"id": 1, "title": "S"}, "test_cases": [{"id": 7, "title": "T", "steps": []}]}]

    def test_runs_headless_and_generates(self):
        calls = {}
        with mock.patch.object(E, "classify_selection", return_value=(set(), set(), set(), set())), \
                mock.patch.object(E, "split_cached_cases", side_effect=lambda p, *a, **k: (p, 0)), \
                mock.patch.object(E, "explore_and_map", side_effect=lambda w, login, site, **k: (
                    calls.update(explore=(login, site, k["headless"])) or {"stories_payload": w, "stats": {"verified": 4}})), \
                mock.patch.object(E, "write_inspection_screens", return_value="r.json"), \
                mock.patch.object(E, "apply_inspection_screens"), \
                mock.patch.object(E, "replay_verify", side_effect=lambda w, login, site, **k: (
                    calls.update(replay=k["headless"]) or {"passed": 1, "failed": 0})), \
                mock.patch.object(E, "write_verification_report"), \
                mock.patch.object(E, "save_verified_cases"), \
                mock.patch.object(E, "generate_and_push_selfhealing", side_effect=lambda *a, **k: (
                    calls.update(gen=k) or ["a", "b"])), \
                mock.patch.object(E, "apply_run_browsers"):
            res = E.run_automation_pipeline("/tmp/p", self.PAYLOAD, {
                "site_url": "https://x.io/app", "login_user": "u", "login_pass": "pw",
                "otp_secret": "JBSWY3DPEHPK3PXP", "target": "playwright"})
        self.assertEqual(res["status"], "generated")
        login, site, headless = calls["explore"]
        self.assertTrue(headless and calls["replay"])
        self.assertEqual((login["user"], login["password"], login["otp_secret"]), ("u", "pw", "JBSWY3DPEHPK3PXP"))
        self.assertEqual(calls["gen"]["target"], "playwright")
        self.assertNotIn("env_pass", calls["gen"]["login"])          # no secrets in the pushed project
        self.assertEqual(res["stats"]["Files"], 2)

    def test_blocked_generation_is_reported(self):
        with mock.patch.object(E, "classify_selection", return_value=(set(), set(), set(), set())), \
                mock.patch.object(E, "split_cached_cases", side_effect=lambda p, *a, **k: (p, 1)), \
                mock.patch.object(E, "explore_and_map", return_value={"stats": {}}), \
                mock.patch.object(E, "write_inspection_screens"), mock.patch.object(E, "apply_inspection_screens"), \
                mock.patch.object(E, "replay_verify", return_value=None), \
                mock.patch.object(E, "write_verification_report"), \
                mock.patch.object(E, "generate_and_push_selfhealing", return_value=None):
            res = E.run_automation_pipeline("/tmp/p", self.PAYLOAD, {"site_url": "https://x.io"})
        self.assertEqual(res["status"], "blocked")

    def test_remote_run_skips_local_installs(self):
        with mock.patch.dict(os.environ, {"QA_REMOTE_RUN": "1"}):
            self.assertTrue(E._remote_run())
        src = open(os.path.join(ROOT, "engine.py"), encoding="utf-8").read()
        self.assertIn("if not _remote_run():\n                automation_targets.install_dependencies", src)
        self.assertIn("if not _remote_run():\n            maven_prefetch", src)


class AzurePayload(unittest.TestCase):
    def test_loads_like_the_screen(self):
        story = types.SimpleNamespace(id=11, fields={"System.Title": "Login"})
        with mock.patch.object(E, "connect_azure_sdk"), \
                mock.patch.object(E, "discover_suites_for_stories", return_value={11: 99}), \
                mock.patch.object(E, "fetch_stories", return_value=[story]), \
                mock.patch.object(E, "fetch_test_cases_for_suite", return_value=[{"workItem": {"id": 5, "name": "Case"}}]), \
                mock.patch.object(E, "fetch_test_case_detail", return_value=("Case", [{"action": "a", "expected": ""}])), \
                mock.patch.object(E, "fetch_test_case_data", return_value=[]):
            out = E.load_automation_payload("P", [{"id": 11, "title": "Login", "plan_id": 3}])
        self.assertEqual(out[0]["story"]["id"], 11)
        self.assertEqual(out[0]["test_cases"][0]["steps"][0]["action"], "a")


class Worker(unittest.TestCase):
    def test_automation_run_end_to_end_with_stubs(self):
        W = _worker()
        csv_text = ("Test Case ID,Test Case Title,Step,Action,Expected Result\r\n"
                    "T1,Open,1,Open https://x.io,\"\"\"Hi\"\" is shown\"\r\n")
        run = {"kind": "automation", "created_by": "u1", "email_recipients": ["a@b.c"],
               "params": {"target": "cypress", "site_url": "https://x.io", "git_url": "https://github.com/me/r.git",
                          "git_branch": "qa", "login_user": "tester",
                          "file": {"name": "cases.csv", "content_b64": base64.b64encode(csv_text.encode()).decode()}}}
        seen = {}
        fake = types.SimpleNamespace(
            git_clone_for_push=lambda d, url, tok, br, cb=None: (seen.update(clone=(url, tok, br)) or (True, "")),
            run_automation_pipeline=lambda proj, payload, opts, **k: (
                seen.update(payload=payload, opts=opts) or {"status": "generated", "summary": "ok", "stats": {}}),
            push_to_git=lambda proj, url, tok, br, message="", cb=None: (seen.update(push=(url, br)) or (True, "")))
        with mock.patch.object(W, "_sb", return_value=[{"git_token": "GT", "app_pass": "PW", "otp_secret": "OS"}]), \
                mock.patch.object(W, "_cb"):
            res = W._run_automation(fake, run, lambda *a: None)
        self.assertEqual(res["status"], "generated")
        self.assertEqual(seen["clone"], ("https://github.com/me/r.git", "GT", "qa"))
        self.assertEqual(seen["push"], ("https://github.com/me/r.git", "qa"))
        self.assertEqual((seen["opts"]["login_pass"], seen["opts"]["otp_secret"], seen["opts"]["target"]),
                         ("PW", "OS", "cypress"))
        self.assertEqual(seen["payload"][0]["test_cases"][0]["title"], "Open")
        self.assertEqual(os.environ.get("QA_REMOTE_RUN"), "1")

    def test_no_git_token_stops_with_a_clear_message(self):
        W = _worker()
        run = {"kind": "automation", "created_by": "u1",
               "params": {"git_url": "https://github.com/me/r.git", "selection": []}}
        fake = types.SimpleNamespace(load_automation_payload=lambda *a, **k: [])
        with mock.patch.object(W, "_sb", return_value=[{"git_token": ""}]):
            with self.assertRaises(SystemExit) as ctx:
                W._run_automation(fake, run, lambda *a: None)
        self.assertIn("access token", str(ctx.exception))

    def test_file_runs_need_no_azure_pat(self):
        src = open(os.path.join(ROOT, "run_worker.py"), encoding="utf-8").read()
        self.assertIn("need_azure=not _auto_file", src)
        self.assertIn('if run["kind"] == "automation":', src)
        wf = open(os.path.join(ROOT, ".github", "workflows", "remote-run.yml"), encoding="utf-8").read()
        self.assertIn("selenium==4.45.0", wf)
        self.assertIn("git config --global user.email", wf)


class AppSide(unittest.TestCase):
    def test_enqueue_row_has_options_but_no_secrets(self):
        import auth_supabase as auth
        posted = {}

        class _Resp:
            status_code = 201

            def json(self):
                return [{"id": "run-1"}]

        class _Client:
            def post(self, url, headers=None, json=None, timeout=None):
                posted.update(url=url, row=json)
                return _Resp()
        with mock.patch.object(auth, "configured", return_value=True), \
                mock.patch.object(auth, "access_token", return_value="tok"), \
                mock.patch.object(auth, "user_id", return_value="u1"), \
                mock.patch.object(auth, "resolve_current_org_project", return_value="p-uuid"), \
                mock.patch.object(auth, "current_org_id", return_value="org"), \
                mock.patch.object(auth, "_client", return_value=_Client()):
            ok, rid = auth.enqueue_automation_run("Proj", {"target": "selenium",
                                                           "selection": [{"id": 5, "plan_id": 9}]}, ["a@b.c"])
        self.assertEqual((ok, rid), (True, "run-1"))
        row = posted["row"]
        self.assertEqual((row["kind"], row["plan_id"], row["story_ids"], row["project_id"]),
                         ("automation", 9, [5], "p-uuid"))
        self.assertNotIn("token", json.dumps(row).lower())

    def test_phone_params_carry_the_upload(self):
        import ast
        tree = ast.parse(open(os.path.join(ROOT, "main.py"), encoding="utf-8").read())
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_remote_automation_params")
        ns = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "main.py", "exec"), ns)
        app = types.SimpleNamespace(
            _auto_target="playwright", creds={"auto_browsers": ["chrome", "firefox"]},
            auto_site_url=" https://x.io ", auto_login_url="", auto_login_user="u",
            auto_git_url="https://github.com/me/r.git", auto_git_branch="", auto_allow_unverified=False,
            auto_only_automation=False, _auto_selected=[],
            _auto_upload={"name": "c.csv", "content": b"abc", "data": b"d", "data_name": "c-data.csv"})
        p = ns["_remote_automation_params"](app, True)
        self.assertEqual((p["target"], p["git_branch"], p["site_url"]), ("playwright", "main", "https://x.io"))
        self.assertEqual(base64.b64decode(p["file"]["content_b64"]), b"abc")
        self.assertEqual(p["file"]["data_name"], "c-data.csv")
        self.assertNotIn("password", json.dumps(p).lower())

    def test_screen_is_offered_on_phones(self):
        import platform_caps
        self.assertTrue(platform_caps.has_automation_screen())
        main_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
        self.assertEqual(main_src.count("not platform_caps.has_automation_screen()"), 2)
        self.assertIn("if platform_caps.automation_is_remote():\n            self._start_remote_automation()", main_src)
        ui = open(os.path.join(ROOT, "automation.py"), encoding="utf-8").read()
        self.assertIn('strings.t("auto_run_remote")', ui)
        self.assertIn("*([] if remote else [local_card])", ui)
        import strings as S
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            for k in ("auto_run_remote", "auto_remote_note", "auto_remote_need_token", "rr_kind_automation",
                      "help_automation_d14", "upd_note_remote_automation"):
                self.assertTrue(S._STRINGS[lang].get(k), (lang, k))


if __name__ == "__main__":
    unittest.main()
