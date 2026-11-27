"""Public releases-only repo: installs, update checks, self-update and the
landing page download from it, so the source repo can be private."""
import hashlib
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402


class _Resp:
    def __init__(self, status=200, text="", headers=None, body=b""):
        self.status_code, self.text = status, text
        self.headers = headers or {}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, n):
        yield self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %s" % self.status_code)


class Channel(unittest.TestCase):
    def test_latest_tag_from_redirect(self):
        loc = "https://github.com/AhmedSayedRepo/QA-Studio-Releases/releases/tag/v4.0.10"
        with mock.patch.object(E.requests, "head", return_value=_Resp(302, headers={"Location": loc})):
            self.assertEqual(E.dist_latest_tag(), "v4.0.10")
        with mock.patch.object(E.requests, "head", return_value=_Resp(404)):
            self.assertIsNone(E.dist_latest_tag())

    def test_update_check_prefers_public_version_asset(self):
        urls = []

        def get(url, **k):
            urls.append(url)
            return _Resp(200, "99.0.0")
        with mock.patch.object(E.requests, "get", side_effect=get):
            res = E.check_for_update()
        self.assertEqual(res["remote"], "99.0.0")
        self.assertIn("QA-Studio-Releases/releases/latest/download/VERSION", urls[0])

    def test_mobile_marker_from_public_repo_carries_its_apk_url(self):
        with mock.patch.object(E.requests, "get", return_value=_Resp(200, "99.0.0")):
            res = E.check_mobile_update()
        self.assertTrue(res["apk_url"].endswith(
            "QA-Studio-Releases/releases/download/android-apk/qa-studio.apk"))

    def _fetch(self, body, sums_text):
        d = tempfile.mkdtemp()
        zp = os.path.join(d, "src.zip")

        def get(url, **k):
            if url.endswith("SHA256SUMS"):
                return _Resp(200, sums_text)
            return _Resp(200, body=body)
        with mock.patch.object(E, "dist_latest_tag", return_value="v9.9.9"), \
                mock.patch.object(E.requests, "get", side_effect=get):
            return E._fetch_dist_source(zp, lambda *a, **k: None)

    def test_verified_zip_is_accepted(self):
        body = b"zipbytes"
        h = hashlib.sha256(body).hexdigest()
        self.assertEqual(self._fetch(body, h + "  qa-studio-src.zip\n"), ("ok", "v9.9.9"))

    def test_checksum_mismatch_aborts_and_never_falls_back(self):
        res = self._fetch(b"tampered", "0" * 64 + "  qa-studio-src.zip\n")
        self.assertEqual(res[0], "abort")
        with mock.patch.object(E, "_fetch_dist_source", return_value=res), \
                mock.patch.object(E, "_resolve_branch_sha") as fallback:
            ok, msg = E._apply_update_zip(lambda *a, **k: None)
        self.assertFalse(ok)
        fallback.assert_not_called()

    def test_missing_releases_repo_falls_back_to_source_repo(self):
        with mock.patch.object(E, "dist_latest_tag", return_value=None):
            self.assertIsNone(E._fetch_dist_source(os.path.join(tempfile.mkdtemp(), "z"),
                                                   lambda *a, **k: None))
        with mock.patch.object(E, "_fetch_dist_source", return_value=None), \
                mock.patch.object(E, "_resolve_branch_sha",
                                  side_effect=RuntimeError("private")) as fallback:
            ok, msg = E._apply_update_zip(lambda *a, **k: None)
        fallback.assert_called_once()
        self.assertFalse(ok)


class ReleaseFiles(unittest.TestCase):
    def _read(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8", errors="replace") as f:
            return f.read()

    def test_install_bat_downloads_public_zip_first(self):
        s = self._read("install.bat")
        pub = s.index("%DIST%/releases/latest/download/qa-studio-src.zip")
        self.assertLess(pub, s.index("archive/refs/heads/%BRANCH%.zip"))

    def test_release_publishes_public_downloads(self):
        self.assertIn('call "%~dp0publish_dist.bat" "%VER%" "%MSG%"', self._read("release.bat"))
        p = self._read("publish_dist.bat")
        for asset in ("qa-studio-src.zip", "VERSION", "SHA256SUMS", "install.bat"):
            self.assertIn("dist\\" + asset, p)
        self.assertIn("git archive --format=zip --prefix=QA-Studio/", p)

    def test_landing_links_use_public_repo_and_vercel_serves_only_landing(self):
        html = self._read("index.html")
        self.assertNotIn("github.com/AhmedSayedRepo/QA-Studio/releases/", html)
        self.assertNotIn("api.github.com/repos/AhmedSayedRepo/QA-Studio/", html)
        build = self._read("vercel-build.sh")
        self.assertIn("cp index.html public/", build)
        self.assertNotIn("cp -r", build)
        self.assertNotIn("*.py", build)


if __name__ == "__main__":
    unittest.main()
