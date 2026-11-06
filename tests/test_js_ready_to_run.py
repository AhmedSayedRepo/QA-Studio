"""Playwright/Cypress output opens ready to run: IDE run configs, .env values,
npm scripts that fetch browsers, and npm install after generation."""
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import automation_targets as AT
import strings

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = {"base_url": "https://app.example.com", "login_url": "https://app.example.com/login",
       "ui_locale": "en", "ai_provider": "anthropic",
       "ai_base_url": "https://api.anthropic.com", "ai_model": "m"}


def _build(target, out, cfg):
    fn = AT.build_playwright_project if target == "playwright" else AT.build_cypress_project
    return fn(out, [], cfg, lambda *a, **k: None, "return [];")


class ScaffoldTests(unittest.TestCase):
    def test_both_targets_get_ide_files_and_scripts(self):
        for target in ("playwright", "cypress"):
            with tempfile.TemporaryDirectory() as out:
                _build(target, out, CFG)
                tasks = json.load(open(os.path.join(out, ".vscode", "tasks.json")))
                labels = {t["label"]: t for t in tasks["tasks"]}
                self.assertEqual(labels["Run tests"]["dependsOn"], "Install dependencies")
                json.load(open(os.path.join(out, ".vscode", "extensions.json")))
                for name in ("Run tests", "Install dependencies", "Open report"):
                    root = ET.parse(os.path.join(out, ".run", name + ".run.xml")).getroot()
                    self.assertEqual(root.find("configuration").get("name"), name)
                run = open(os.path.join(out, ".run", "Run tests.run.xml")).read()
                self.assertIn("NpmBeforeRunTask", run)
                pkg = json.load(open(os.path.join(out, "package.json")))
                self.assertIn("pretest", pkg["scripts"])
                if target == "playwright":
                    self.assertEqual(pkg["scripts"]["postinstall"], "playwright install chromium")
                gi = open(os.path.join(out, ".gitignore")).read()
                self.assertIn(".env", gi.splitlines())

    def test_env_has_urls_and_optional_account(self):
        with tempfile.TemporaryDirectory() as out:
            _build("cypress", out, CFG)
            env = open(os.path.join(out, ".env")).read()
            self.assertIn("APP_BASE_URL=https://app.example.com", env)
            self.assertIn("APP_USER=\n", env)
            cfg = dict(CFG, app_user="standard_user", app_pass="secret_sauce")
            _build("cypress", out, cfg)
            env = open(os.path.join(out, ".env")).read()
            self.assertIn("APP_USER=standard_user", env)
            self.assertIn("APP_PASS=secret_sauce", env)

    def test_env_never_overwrites_user_values(self):
        with tempfile.TemporaryDirectory() as out:
            p = os.path.join(out, ".env")
            with open(p, "w") as f:
                f.write("APP_BASE_URL=https://staging.example.com\nAPP_USER=me\n")
            filled = AT._env_fill(p, {"APP_BASE_URL": "https://x", "APP_USER": "other",
                                      "APP_PASS": "pw"})
            env = open(p).read()
            self.assertEqual(filled, ["APP_PASS"])
            self.assertIn("APP_BASE_URL=https://staging.example.com", env)
            self.assertIn("APP_USER=me", env)
            self.assertIn("APP_PASS=pw", env)


class InstallTests(unittest.TestCase):
    def test_missing_npm_warns(self):
        logs = []
        with tempfile.TemporaryDirectory() as out, patch("shutil.which", return_value=None):
            open(os.path.join(out, "package.json"), "w").write("{}")
            self.assertFalse(AT.install_dependencies(out, cb=lambda m, t="": logs.append((m, t))))
        self.assertIn("nodejs.org", logs[-1][0])

    def test_up_to_date_is_skipped(self):
        with tempfile.TemporaryDirectory() as out:
            open(os.path.join(out, "package.json"), "w").write("{}")
            os.makedirs(os.path.join(out, "node_modules"))
            lock = os.path.join(out, "node_modules", ".package-lock.json")
            open(lock, "w").write("{}")
            os.utime(lock, (os.path.getmtime(lock) + 5,) * 2)
            with patch("shutil.which", side_effect=AssertionError("should not run")):
                self.assertTrue(AT.install_dependencies(out))

    @unittest.skipIf(sys.platform.startswith("win"), "uses a POSIX fake npm")
    def test_runs_npm_install_in_project(self):
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as bindir:
            open(os.path.join(out, "package.json"), "w").write("{}")
            npm = os.path.join(bindir, "npm")
            with open(npm, "w") as f:
                f.write('#!/bin/sh\necho "$@" > args.txt\necho done\n')
            os.chmod(npm, os.stat(npm).st_mode | stat.S_IEXEC)
            with patch("shutil.which", return_value=npm):
                self.assertTrue(AT.install_dependencies(out))
            self.assertIn("install", open(os.path.join(out, "args.txt")).read())

    def test_engine_runs_install_after_js_build(self):
        src = (ROOT / "engine.py").read_text(encoding="utf-8")
        self.assertIn("automation_targets.install_dependencies(out_dir", src)
        self.assertIn('"app_user": (login or {}).get("env_user", "")', src)


class OptInTests(unittest.TestCase):
    def test_account_only_written_when_opted_in(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('getattr(self, "auto_env_creds", False)', main)
        self.assertIn('self.creds.get("auto_env_creds", False)', main)
        self.assertIn("_flip_env_creds", (ROOT / "automation.py").read_text(encoding="utf-8"))
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            self.assertIn("auto_env_creds", strings._STRINGS[lang])
            self.assertIn("upd_note_js_ready_to_run", strings._STRINGS[lang])


if __name__ == "__main__":
    unittest.main()
