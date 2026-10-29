"""release.bat's Python steps: stamp the pending draft (carrying over notes from
versions that were never published), then check it.

The end-to-end tests run the real _stamp_release_notes.py / _check_release_notes.py
against a CRLF copy of release_notes.py + strings.py (the Windows checkout format)
in a temp dir, with their own draft, so they never depend on the repo's current
draft state and never touch the repo's files.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

import _stamp_release_notes as stamp_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANGS = ("en", "ar", "fr", "tr", "es", "de", "nl")


def _copy_crlf(name, dst_dir, transform=None):
    with open(os.path.join(ROOT, name), "rb") as fh:
        data = fh.read().replace(b"\r\n", b"\n").decode("utf-8")
    if transform:
        data = transform(data)
    with open(os.path.join(dst_dir, name), "wb") as fh:
        fh.write(data.replace("\n", "\r\n").encode("utf-8"))


def _set_release_notes(registry, pending):
    def transform(src):
        src = re.sub(r"RELEASE_NOTE_KEYS\s*=\s*\{.*?\}\n\n# Add the keys",
                     lambda m: "RELEASE_NOTE_KEYS = %r\n\n# Add the keys" % (registry,),
                     src, count=1, flags=re.S)
        return re.sub(r"PENDING_RELEASE_NOTE_KEYS\s*=\s*\(.*?\)\n",
                      lambda m: "PENDING_RELEASE_NOTE_KEYS = %r\n" % (pending,),
                      src, count=1, flags=re.S)
    return transform


def _run(cwd, *args):
    return subprocess.run([sys.executable] + list(args), cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8")


def _registry_in(cwd):
    out = _run(cwd, "-c", "import release_notes as r;"
                          "print(repr(r.RELEASE_NOTE_KEYS));print(repr(r.PENDING_RELEASE_NOTE_KEYS))")
    reg, pending = out.stdout.strip().splitlines()
    return eval(reg), eval(pending)  # noqa: S307 - our own repr output


class ReleaseNoteTranslationTests(unittest.TestCase):
    def test_every_pending_note_is_translated_in_all_seven_languages(self):
        import release_notes
        import strings
        for key in release_notes.PENDING_RELEASE_NOTE_KEYS:
            for lang in LANGS:
                self.assertTrue(str(strings._STRINGS[lang].get(key, "")).strip(),
                                "%s missing in %s" % (key, lang))

    def test_automation_wait_note_is_translated_in_all_seven_languages(self):
        import strings
        for lang in LANGS:
            self.assertTrue(strings._STRINGS[lang].get("upd_note_automation_waits"), lang)


class CarryOverTests(unittest.TestCase):
    """Notes stamped onto a version that never got published move to the
    version being released (between the previous published release and it)."""

    def test_moves_notes_of_a_skipped_version_onto_the_release(self):
        registry = {"3.9.1": ("a",), "3.9.3": ("fa", "aw")}
        new, moved = stamp_mod.carry_over(registry, "3.9.4", {"3.9.1", "3.9.2"})
        self.assertEqual(new, {"3.9.1": ("a",), "3.9.4": ("fa", "aw")})
        self.assertEqual(moved, ["3.9.3"])

    def test_orphan_notes_come_before_the_release_own_notes_without_duplicates(self):
        registry = {"3.9.3": ("fa", "aw"), "3.9.4": ("aw", "new")}
        new, _ = stamp_mod.carry_over(registry, "3.9.4", {"3.9.2"})
        self.assertEqual(new["3.9.4"], ("fa", "aw", "new"))

    def test_leaves_orphans_older_than_the_previous_published_release(self):
        registry = {"3.8.5": ("old",), "3.9.1": ("a",)}
        new, moved = stamp_mod.carry_over(registry, "3.9.5", {"3.8.6", "3.9.1", "3.9.4"})
        self.assertEqual(new, registry)
        self.assertEqual(moved, [])

    def test_ignores_stray_tags_above_the_release_and_compares_numerically(self):
        registry = {"3.9.10": ("x",)}
        new, moved = stamp_mod.carry_over(registry, "3.9.11", {"3.9.9", "4.6.3"})
        self.assertEqual(new, {"3.9.11": ("x",)})
        self.assertEqual(moved, ["3.9.10"])

    def test_without_a_known_published_list_nothing_moves(self):
        registry = {"3.9.3": ("fa",)}
        new, moved = stamp_mod.carry_over(registry, "3.9.4", None)
        self.assertEqual((new, moved), (registry, []))


class ReleaseBatPythonStepsTests(unittest.TestCase):
    FILES = ("strings.py", "_stamp_release_notes.py", "_check_release_notes.py")

    def _workspace(self, tmp, registry, pending):
        for name in self.FILES:
            _copy_crlf(name, tmp)
        _copy_crlf("release_notes.py", tmp, _set_release_notes(registry, pending))

    def test_stamp_then_check_registers_the_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._workspace(tmp, {"3.9.1": ("upd_note_391_landscape_orientation",)},
                            ("upd_note_automation_waits",))
            stamp = _run(tmp, "_stamp_release_notes.py", "3.9.5", "--published", "3.9.1,3.9.4")
            self.assertEqual(stamp.returncode, 0, stamp.stdout + stamp.stderr)
            check = _run(tmp, "_check_release_notes.py", "3.9.5")
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            registry, pending = _registry_in(tmp)
        self.assertEqual(registry["3.9.5"], ("upd_note_automation_waits",))
        self.assertEqual(pending, ())

    def test_rerun_after_an_aborted_release_carries_the_skipped_notes(self):
        # Today's incident: 3.9.3 was stamped, the release aborted, the re-run
        # published 3.9.4 with an empty draft.
        with tempfile.TemporaryDirectory() as tmp:
            self._workspace(tmp, {"3.9.3": ("upd_note_failure_analysis",
                                            "upd_note_automation_waits")}, ())
            stamp = _run(tmp, "_stamp_release_notes.py", "3.9.4", "--published", "3.9.2,4.6.3")
            self.assertEqual(stamp.returncode, 0, stamp.stdout + stamp.stderr)
            self.assertIn("3.9.3", stamp.stdout)
            check = _run(tmp, "_check_release_notes.py", "3.9.4")
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            registry, _ = _registry_in(tmp)
        self.assertNotIn("3.9.3", registry)
        self.assertEqual(registry["3.9.4"], ("upd_note_failure_analysis",
                                             "upd_note_automation_waits"))

    def test_stamp_leaves_no_stale_bytecode_for_the_rewritten_registry(self):
        # A same-size rewrite in the same second as the import (3.9.3 -> 3.9.4)
        # keeps the old .pyc "valid" (it checks whole-second mtime + size), so
        # the next step / the app would read the OLD registry. Seen on Windows.
        with tempfile.TemporaryDirectory() as tmp:
            self._workspace(tmp, {"3.9.3": ("upd_note_automation_waits",)}, ())
            stamp = _run(tmp, "_stamp_release_notes.py", "3.9.4", "--published", "3.9.2")
            self.assertEqual(stamp.returncode, 0, stamp.stdout + stamp.stderr)
            cache = os.path.join(tmp, "__pycache__")
            stale = [f for f in (os.listdir(cache) if os.path.isdir(cache) else [])
                     if f.startswith("release_notes.")]
        self.assertEqual(stale, [])


if __name__ == "__main__":
    unittest.main()
