"""Tests for HAR import (perf/har.py) and JMeter HeaderManager emission."""
import json
import os
import tempfile
import unittest

from perf.har import scenarios_from_har
from perf.models import AssertionKind, LoadProfile
from perf.targets.jmeter import JMeterTarget, _headermanager
from perf.models import PerfRequest


def _write_har(entries, pages=None):
    har = {"log": {"version": "1.2", "pages": pages or [],
                   "entries": entries}}
    fd, path = tempfile.mkstemp(suffix=".har")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(har, f)
    return path


def _entry(url, method="GET", status=200, mime="application/json",
           headers=None, body=None, started="2026-08-04T10:00:00.000Z"):
    req = {"method": method, "url": url,
           "headers": headers or [{"name": "Authorization", "value": "Bearer xyz"}]}
    if body is not None:
        req["postData"] = {"mimeType": "application/json", "text": body}
    return {"startedDateTime": started, "request": req,
            "response": {"status": status, "content": {"mimeType": mime}}}


class TestHarParse(unittest.TestCase):
    def test_basic_requests_and_status_assertion(self):
        p = _write_har([
            _entry("https://api.example.com/login", method="POST", status=200,
                   body='{"u":"a"}'),
            _entry("https://api.example.com/search?q=x", status=200),
        ])
        try:
            scs = scenarios_from_har(p)
        finally:
            os.remove(p)
        self.assertEqual(len(scs), 1)
        reqs = scs[0].requests
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[0].method, "POST")
        self.assertEqual(reqs[0].body, '{"u":"a"}')
        self.assertEqual(reqs[0].assertions[0].kind, AssertionKind.STATUS)
        self.assertEqual(reqs[0].assertions[0].value, "200")

    def test_static_assets_skipped(self):
        p = _write_har([
            _entry("https://cdn.example.com/app.js", mime="text/javascript"),
            _entry("https://cdn.example.com/logo.png", mime="image/png"),
            _entry("https://api.example.com/data", mime="application/json"),
        ])
        try:
            reqs = scenarios_from_har(p)[0].requests
        finally:
            os.remove(p)
        self.assertEqual(len(reqs), 1)
        self.assertIn("/data", reqs[0].url)

    def test_domain_filter_keeps_only_matching(self):
        p = _write_har([
            _entry("https://api.example.com/a"),
            _entry("https://analytics.google.com/collect"),
            _entry("https://sub.example.com/b"),
        ])
        try:
            reqs = scenarios_from_har(p, include_domains=["example.com"])[0].requests
        finally:
            os.remove(p)
        urls = [r.url for r in reqs]
        self.assertTrue(all("example.com" in u for u in urls))
        self.assertEqual(len(reqs), 2)  # api.example.com + sub.example.com

    def test_pseudo_and_volatile_headers_dropped(self):
        p = _write_har([_entry("https://api.example.com/x", headers=[
            {"name": ":authority", "value": "api.example.com"},
            {"name": "Host", "value": "api.example.com"},
            {"name": "Content-Length", "value": "10"},
            {"name": "Authorization", "value": "Bearer keep-me"},
        ])])
        try:
            headers = scenarios_from_har(p)[0].requests[0].headers
        finally:
            os.remove(p)
        self.assertIn("Authorization", headers)
        self.assertNotIn(":authority", headers)
        self.assertNotIn("Host", headers)
        self.assertNotIn("Content-Length", headers)

    def test_think_time_from_timing_gap(self):
        p = _write_har([
            _entry("https://api.example.com/a", started="2026-08-04T10:00:00.000Z"),
            _entry("https://api.example.com/b", started="2026-08-04T10:00:02.000Z"),
        ])
        try:
            reqs = scenarios_from_har(p)[0].requests
        finally:
            os.remove(p)
        self.assertEqual(reqs[0].think_ms, 2000)  # 2s gap
        self.assertEqual(reqs[1].think_ms, 0)     # last request

    def test_no_matching_returns_empty(self):
        p = _write_har([_entry("https://other.com/a")])
        try:
            scs = scenarios_from_har(p, include_domains=["example.com"])
        finally:
            os.remove(p)
        self.assertEqual(scs, [])


class TestHeaderManagerEmit(unittest.TestCase):
    def test_headermanager_present_when_headers(self):
        req = PerfRequest(method="GET", url="https://api.example.com/x",
                          headers={"Authorization": "Bearer t"})
        xml = _headermanager(req)
        self.assertIn("HeaderManager", xml)
        self.assertIn("Authorization", xml)
        self.assertIn("Bearer t", xml)

    def test_headermanager_empty_without_headers(self):
        self.assertEqual(_headermanager(PerfRequest(url="https://x/y")), "")

    def test_emit_includes_headermanager_from_har(self):
        p = _write_har([_entry("https://api.example.com/x", headers=[
            {"name": "Authorization", "value": "Bearer secret"}])])
        try:
            scs = scenarios_from_har(p)
        finally:
            os.remove(p)
        out = tempfile.mkdtemp()
        paths = JMeterTarget().emit(scs, LoadProfile(users=1, duration_s=1), out)
        with open(paths.entry, encoding="utf-8") as f:
            jmx = f.read()
        self.assertIn("HeaderManager", jmx)
        self.assertIn("Authorization", jmx)


if __name__ == "__main__":
    unittest.main()
