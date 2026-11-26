"""Batch 11a: phone and tablet screens - the Screen setting (DEVICE / device=)
and 'Switch to an iPhone 14 screen' steps, for inspection, replay and the
generated Selenium, Playwright and Cypress tests."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402
import automation_targets as A  # noqa: E402
import release_notes  # noqa: E402
import strings  # noqa: E402

NODE = shutil.which("node")
SEED = lambda *a, **k: ("css", "#x", True)  # noqa: E731


class DeviceSpec(unittest.TestCase):
    def test_presets_aliases_custom_and_landscape(self):
        s = E.device_spec("iPhone 14")
        self.assertEqual((s["width"], s["height"], s["dpr"], s["mobile"]), (390, 844, 3, True))
        self.assertEqual(E.device_spec("mobile")["name"], "iPhone 14")
        self.assertEqual(E.device_spec("tablet")["name"], "iPad")
        land = E.device_spec("iPad landscape")
        self.assertEqual((land["width"], land["height"], land["name"]), (1180, 820, "iPad landscape"))
        self.assertEqual(E.device_spec("iPhone 14|landscape")["width"], 844)
        self.assertEqual(E.device_spec("375x812")["width"], 375)
        for v in ("", "desktop", "Desktop", "nonsense phone 9"):
            self.assertIsNone(E.device_spec(v), v)


class DeviceSteps(unittest.TestCase):
    def test_phrasings(self):
        cases = {
            "Switch to an iPhone 14 screen": "iPhone 14",
            "Use an iPad screen in landscape mode": "iPad|landscape",
            "Rotate the phone to landscape": "|landscape",
            "Rotate to portrait": "|portrait",
            "Resize the window to 1024x768": "1024x768",
            "Switch back to desktop": "desktop",
            "View it on mobile": "iPhone 14",
            "Test it on a Galaxy S20": "Galaxy S20",
        }
        for text, value in cases.items():
            self.assertEqual(E._page_action_in(text), ("device", value), text)

    def test_not_device(self):
        for text in ("Use the search box", "Switch to the Settings tab", "Open the menu", 'Click "Mobile"'):
            got = E._page_action_in(text)
            self.assertFalse(got and got[0] == "device", text)

    def test_labels(self):
        self.assertEqual(E._page_action_label("device", "iPhone 14"), "switch to the iPhone 14 screen (390×844)")
        self.assertEqual(E._page_action_label("device", "|landscape"), "rotate the screen to landscape")
        self.assertEqual(E._page_action_label("device", "desktop"), "switch back to the desktop window")

    def test_inspection_uses_cdp(self):
        calls = []

        class D:
            def execute_cdp_cmd(self, cmd, args):
                calls.append((cmd, args))
        state = {"value": "", "current": ""}
        E._emulate_device(D(), "Pixel 7", state)
        E._emulate_device(D(), "|landscape", state)
        metrics = [a for c, a in calls if c == "Emulation.setDeviceMetricsOverride"]
        self.assertEqual((metrics[0]["width"], metrics[1]["width"]), (412, 915))
        self.assertTrue(any(c == "Emulation.setUserAgentOverride" for c, _ in calls))
        E._emulate_device(D(), "desktop", state)
        self.assertEqual(calls[-2][0], "Emulation.clearDeviceMetricsOverride")

    def test_run_setting_reaches_inspection_chrome(self):
        class O:
            def __init__(self):
                self.opts = {}

            def add_experimental_option(self, k, v):
                self.opts[k] = v
        E._set_run_device({"device": "iPhone SE"})
        try:
            o = O()
            E._apply_run_device_options(o)
            self.assertEqual(o.opts["mobileEmulation"]["deviceMetrics"]["width"], 375)
        finally:
            E._set_run_device({})
        o = O()
        E._apply_run_device_options(o)
        self.assertEqual(o.opts, {})

    def test_prompt_mentions_it(self):
        self.assertIn("Switch to an iPhone 14 screen", E.EXTERNAL_CASE_PROMPT)


class Emitted(unittest.TestCase):
    def test_every_framework(self):
        it = {"role": "action", "verb": "device", "target": "x", "value": "iPad|landscape"}
        j, p, c = [], [], []
        E._emit_intent(j, "k1", it)
        A._emit_pw_intent(p, "k1", it, SEED, {})
        A._emit_cy_intent(c, "k1", it, SEED, {})
        self.assertIn('heal.pageAction("device", "iPad|landscape")', "\n".join(j))
        self.assertIn('heal.pageAction("device"', "\n".join(p))
        self.assertIn('cy.healPageAction("device", "iPad|landscape")', "\n".join(c))

    def test_runtimes(self):
        healer = E._sh_healer("com.qastudio")
        self.assertIn('case "device": emulate(value); break;', healer)
        self.assertIn("Emulation.setDeviceMetricsOverride", healer)
        dev = E._devices_java("com.qastudio")
        self.assertIn('T.put("iphone 14", new Object[]{"iPhone 14", 390, 844, 3.0,', dev)
        df = E._driver_factory("com.qastudio")
        self.assertIn('Devices.spec(Config.setting("DEVICE", ""))', df)
        self.assertEqual(df.count('options.setExperimentalOption("mobileEmulation", Devices.mobileEmulation(dev))'), 2)
        pw = A._with_batch2_js(A._PW_HEALER_JS)
        cy = A._with_batch2_js(A._CY_HEALER_JS)
        cfg = A._with_batch2_js(A._CY_CONFIG)
        self.assertIn("if (verb === 'device')", pw)
        self.assertIn("if (verb === 'device')", cy)
        self.assertIn("config.viewportWidth = dev.width", cfg)
        self.assertIn("DEVICE: process.env.DEVICE || ''", cfg)
        for text in (pw, cy, cfg, A._with_batch2_js(A._PW_BROWSERS_JS)):
            self.assertNotIn("__DEVICES_JSON__", text)

    @unittest.skipUnless(NODE, "node not installed")
    def test_playwright_projects_follow_device(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "browsers.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(A._with_batch2_js(A._PW_BROWSERS_JS).replace("require('dotenv').config();", ""))
        js = ("const b = require(%s); console.log(JSON.stringify(b.projects().map((p) => "
              "[p.name, p.use.viewport || null, p.use.isMobile === true])));" % json.dumps(path))
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=60,
                           env=dict(os.environ, DEVICE="iPhone 14 landscape", BROWSER="chrome,firefox", GRID=""))
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(got, [["Chrome", {"width": 844, "height": 390}, True],
                               ["Firefox", {"width": 844, "height": 390}, False]])

    @unittest.skipUnless(NODE, "node not installed")
    def test_generated_js_parses(self):
        d = tempfile.mkdtemp()
        for name, text in (("healer.js", A._with_batch2_js(A._PW_HEALER_JS)),
                           ("cy-healer.js", A._with_batch2_js(A._CY_HEALER_JS).replace("__UI_LOCALE__", "en")),
                           ("cypress.config.js", A._with_batch2_js(A._CY_CONFIG)),
                           ("browsers.js", A._with_batch2_js(A._PW_BROWSERS_JS))):
            path = os.path.join(d, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            r = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, name + ": " + r.stderr)


class RunSetting(unittest.TestCase):
    def test_written_into_the_project(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ".env"), "w") as f:
            f.write("BROWSER=chrome\n")
        self.assertEqual(E.apply_run_device(d, "playwright", "iPad landscape"), "iPad landscape")
        with open(os.path.join(d, ".env"), encoding="utf-8") as f:
            self.assertIn("DEVICE=iPad landscape", f.read())
        E.apply_run_device(d, "playwright", "")
        with open(os.path.join(d, ".env"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("DEVICE=\n", text)
        self.assertEqual(text.count("DEVICE="), 1)
        with open(os.path.join(d, "config.properties"), "w") as f:
            f.write("browser=chrome\n")
        E.apply_run_device(d, "selenium", "Pixel 7")
        with open(os.path.join(d, "config.properties"), encoding="utf-8") as f:
            self.assertIn("device=Pixel 7", f.read())

    def test_ui_main_and_worker_pass_it(self):
        with open(os.path.join(ROOT, "automation.py"), encoding="utf-8") as f:
            ui = f.read()
        with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
            app = f.read()
        with open(os.path.join(ROOT, "run_worker.py"), encoding="utf-8") as f:
            worker = f.read()
        self.assertIn('app.creds["auto_device"] = val', ui)
        self.assertEqual(app.count('"device": self.creds.get("auto_device") or ""'), 3)
        self.assertIn("E.apply_run_device(project_dir,", app)
        self.assertIn('"device": p.get("device") or ""', worker)


class Texts(unittest.TestCase):
    def test_strings_and_notes(self):
        # Pending until release.bat stamps it into a version (4.0.6 and later).
        released = {k for keys in release_notes.RELEASE_NOTE_KEYS.values() for k in keys}
        self.assertIn("upd_note_batch11_devices", set(release_notes.PENDING_RELEASE_NOTE_KEYS) | released)
        for lang in ("en", "ar", "fr", "tr", "es", "de", "nl"):
            for k in ("auto_screen", "auto_screen_desktop", "auto_screen_landscape", "auto_screen_note",
                      "help_automation_d16", "help_automation_p16", "upd_note_batch11_devices"):
                self.assertTrue(strings._STRINGS[lang].get(k), lang + " " + k)

    def test_sample_cases_parse(self):
        import csv
        with open(os.path.join(ROOT, "samples", "batch11-devices-test-cases.csv"), encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        acts = [E._page_action_in(r["Action"]) for r in rows]
        self.assertEqual(sum(1 for a in acts if a and a[0] == "device"), 6)


if __name__ == "__main__":
    unittest.main()
