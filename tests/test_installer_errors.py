"""The installer explains a failed `pip install` (network, a package with no
build for this Python, anything else) instead of a generic 'check your
internet', and says which Python it is using."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import installer  # noqa: E402


class PipFailureMessage(unittest.TestCase):
    def test_no_build_on_another_python(self):
        out = ["Collecting pythonnet==3.1.0", "  error: subprocess-exited-with-error",
               "ERROR: Failed building wheel for pythonnet"]
        with mock.patch.object(installer.sys, "version_info", (3, 14, 0, "final", 0)):
            msg = installer._pip_failure(out)
        self.assertIn("'pythonnet'", msg)
        self.assertIn("Python 3.14", msg)
        self.assertIn("Python 3.12", msg)

    def test_network(self):
        out = ["WARNING: Retrying after connection broken by 'ProxyError('Cannot connect to proxy.')'",
               "ERROR: No matching distribution found for flet==0.85.3"]
        msg = installer._pip_failure(out)
        self.assertIn("network, proxy or firewall", msg)
        self.assertIn("ProxyError", msg)

    def test_other_error_is_quoted(self):
        msg = installer._pip_failure(["ERROR: Could not install packages due to an OSError: [WinError 5] "
                                      "Access is denied"])
        self.assertIn("WinError 5", msg)
        self.assertNotIn("internet", msg)

    def test_worker_uses_it(self):
        import inspect
        src = inspect.getsource(installer._work)
        self.assertIn("_pip_failure(out)", src)
        self.assertIn("_WANT_PY", src)
        self.assertNotIn("Check your internet connection", src)

    def test_locked_package(self):
        msg = installer._pip_failure(["ERROR: Could not restore 'c:\\py312\\site-packages\\pythonnet\\' from "
                                      "'C:\\py312\\site-packages\\~~thonnet'. Check both paths"])
        self.assertIn("close this window", msg)

    def test_window_packages_are_pinned(self):
        with open(os.path.join(ROOT, "install.bat"), encoding="utf-8") as f:
            self.assertIn('pythonnet -c "%DEST%\\requirements.txt"', f.read())
        import inspect
        self.assertIn('["-c", REQ_FILE]', inspect.getsource(installer._ensure_pywebview))


class InstallBatPicksPython312(unittest.TestCase):
    def test_only_312_is_used(self):
        with open(os.path.join(ROOT, "install.bat"), encoding="utf-8") as f:
            bat = f.read()
        find = bat[bat.index(":find_python\n") if ":find_python\n" in bat else bat.index(":find_python\r\n"):]
        self.assertNotIn("Python313", find)
        self.assertNotIn("Python311", find)
        self.assertNotIn('"PY=py -3"', find)
        self.assertIn("py -3.12 --version", find)
        self.assertIn("sys.version_info[:2]==(3,12)", find)


if __name__ == "__main__":
    unittest.main()
