"""Automation: the 'choose a file / pick a plan' note sits inside the
Test-case source card, not floating between cards with a light-theme border."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SetupHintPlacement(unittest.TestCase):
    def test_hint_inside_source_card(self):
        with open(os.path.join(ROOT, "automation.py"), encoding="utf-8") as f:
            src = f.read()
        card_src = src[src.index("source_card = card(ft.Column(["):src.index("framework_card = card(")]
        self.assertIn("*([ft.Container(height=12), setup_hint] if setup_hint else [])", card_src)
        left = src[src.index("left = ft.Column(["):]
        self.assertNotIn("[setup_hint]", left[:400])
        self.assertNotIn("#EAD9A8", src)


if __name__ == "__main__":
    unittest.main()
