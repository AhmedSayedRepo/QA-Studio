"""Regression checks for identity-image input hardening.

Run with:
    python -m unittest tests.test_image_assets_security
"""
import base64
import io
import pathlib
import sys
import types
import unittest

from PIL import Image


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The validator itself does not need Flet; use a tiny import shim so this
# security regression suite remains runnable in a minimal CI Python runtime.
if "flet" not in sys.modules:
    sys.modules["flet"] = types.SimpleNamespace()

import image_assets  # noqa: E402


def png_bytes(size=(256, 256)):
    out = io.BytesIO()
    Image.new("RGB", size, "#1178b8").save(out, format="PNG")
    return out.getvalue()


class IdentityImageSecurityTests(unittest.TestCase):
    def test_normalizes_supported_raster_to_webp(self):
        payload, reason = image_assets.validate_square_image(png_bytes())
        self.assertEqual(reason, "")
        self.assertEqual(payload["mime_type"], "image/webp")
        self.assertEqual(payload["extension"], "webp")
        self.assertTrue(base64.b64decode(payload["image_base64"], validate=True).startswith(b"RIFF"))

    def test_rejects_svg_script_payload(self):
        payload, reason = image_assets.validate_square_image(
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
        self.assertIsNone(payload)
        self.assertEqual(reason, "format")

    def test_reencoding_strips_appended_script_bytes(self):
        source = png_bytes() + b"<script>malicious()</script>"
        payload, reason = image_assets.validate_square_image(source)
        self.assertEqual(reason, "")
        output = base64.b64decode(payload["image_base64"], validate=True)
        self.assertNotIn(b"<script>", output)
        self.assertNotIn(b"malicious", output)

    def test_editor_refuses_tampered_payload_metadata(self):
        payload, _ = image_assets.validate_square_image(png_bytes())
        payload["mime_type"] = "image/png"
        transformed, reason = image_assets.transform_validated_square_image(payload)
        self.assertIsNone(transformed)
        self.assertEqual(reason, "format")


if __name__ == "__main__":
    unittest.main()
