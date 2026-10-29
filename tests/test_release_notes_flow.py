"""release.bat's Python steps: stamp the pending draft, then check it.

Runs the real _stamp_release_notes.py / _check_release_notes.py against a
CRLF copy of release_notes.py + strings.py (the Windows checkout format), so
the flow release.bat performs is exercised end to end without touching the
repo's own files.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANGS = ("en", "ar", "fr", "tr", "es", "de", "nl")
NEXT_VERSION = "99.0.0"


def _copy_crlf(name, dst_dir):
    with open(os.path.join(ROOT, name), "rb") as fh:
        data = fh.read().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    with open(os.path.join(dst_dir, name), "wb") as fh:
        fh.write(data)


def _run(script, cwd):
    return subprocess.run([sys.executable, script, NEXT_VERSION], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8")


class ReleaseNoteDraftTests(unittest.TestCase):
    def test_automation_wait_and_hover_fix_is_in_the_next_release_draft(self):
        import release_notes
        self.assertIn("upd_note_automation_waits",
                      release_notes.PENDING_RELEASE_NOTE_KEYS)

    def test_every_pending_note_is_translated_in_all_seven_languages(self):
        import release_notes
        import strings
        for key in release_notes.PENDING_RELEASE_NOTE_KEYS:
            for lang in LANGS:
                self.assertTrue(str(strings._STRINGS[lang].get(key, "")).strip(),
                                "%s missing in %s" % (key, lang))


class ReleaseBatPythonStepsTests(unittest.TestCase):
    def test_stamp_then_check_passes_and_registers_the_draft(self):
        import release_notes
        pending = tuple(release_notes.PENDING_RELEASE_NOTE_KEYS)
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("release_notes.py", "strings.py",
                         "_stamp_release_notes.py", "_check_release_notes.py"):
                _copy_crlf(name, tmp)
            stamp = _run("_stamp_release_notes.py", tmp)
            self.assertEqual(stamp.returncode, 0, stamp.stdout + stamp.stderr)
            check = _run("_check_release_notes.py", tmp)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            probe = subprocess.run(
                [sys.executable, "-c",
                 "import release_notes as r;"
                 "print(r.exact_keys_for('%s'));print(r.PENDING_RELEASE_NOTE_KEYS)"
                 % NEXT_VERSION],
                cwd=tmp, capture_output=True, text=True, encoding="utf-8")
            stamped, left = probe.stdout.strip().splitlines()
        self.assertEqual(stamped, repr(pending))
        self.assertEqual(left, "()")
        self.assertIn("upd_note_automation_waits", stamped)


if __name__ == "__main__":
    unittest.main()
