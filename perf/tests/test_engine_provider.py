"""Provider-wire compatibility tests that do not make network calls."""

import unittest
from unittest import mock
import sys
import types

# The compact verification runtime used in the desktop workspace does not ship
# requests. Engine only touches it while making a real HTTP call, which these
# tests replace, so provide a harmless import stub for this no-network suite.
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.exceptions = types.SimpleNamespace(
        SSLError=RuntimeError, ConnectionError=RuntimeError, Timeout=RuntimeError)
    adapters_stub = types.ModuleType("requests.adapters")
    adapters_stub.HTTPAdapter = type("HTTPAdapter", (), {})
    sys.modules["requests"] = requests_stub
    sys.modules["requests.adapters"] = adapters_stub

import engine


_RESPONSE = {
    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
}


class TestOpenAICompatibleTokenLimits(unittest.TestCase):
    def test_gpt5_uses_completion_token_limit(self):
        sent = []
        with mock.patch.object(engine, "_http_post_json",
                               side_effect=lambda _u, _h, payload, _t:
                               sent.append(dict(payload)) or _RESPONSE):
            text, _usage = engine._openai_compat_http(
                {"model": "gpt-5-mini", "api_key": "test"}, "hello", [], 321, 10)
        self.assertEqual(text, "ok")
        self.assertEqual(sent[0]["max_completion_tokens"], 321)
        self.assertNotIn("max_tokens", sent[0])

    def test_explicit_provider_hint_retries_once_with_alternate_limit(self):
        sent = []

        def post(_url, _headers, payload, _timeout):
            sent.append(dict(payload))
            if len(sent) == 1:
                raise RuntimeError(
                    "HTTP 400 from provider: Unsupported parameter: 'max_tokens' "
                    "is not supported with this model. Use 'max_completion_tokens' instead.")
            return _RESPONSE

        with mock.patch.object(engine, "_http_post_json", side_effect=post):
            text, _usage = engine._openai_compat_http(
                {"model": "newly-released-model", "api_key": "test"}, "hello", [], 123, 10)
        self.assertEqual(text, "ok")
        self.assertIn("max_tokens", sent[0])
        self.assertIn("max_completion_tokens", sent[1])
        self.assertNotIn("max_tokens", sent[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
