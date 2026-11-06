"""Automation activity rail view model (no Flet): per-case groups with a
status, auto-collapse of finished cases, and the All / Issues filter."""
import unittest

import log_view as V


def L(msg, tone="dim"):
    return {"msg": msg, "tone": tone}


RUN = [
    L("Launching Chrome…"),                                         # 0 general
    L("▸ Story 7 — Imported file", "story"),                       # 1 general
    L("  walking 'Login works'  [interaction · app-page]  (3 steps)", "case"),  # 2 header A
    L("    → type: Username field"),                               # 3
    L("Opening login page…"),                                       # 4 still A (login helper)
    L("    ✓ assertion → \"Products\" (new)", "ok"),               # 5
    L("  walking 'Checkout'  [interaction · app-page]  (8 steps)", "case"),     # 6 header B
    L("    ? assertion target not found on page", "warn"),         # 7
    L("  walking 'Logout'  [interaction · app-page]  (2 steps)", "case"),       # 8 header C
    L("      click #logout_sidebar_link", "ok"),                   # 9
    L("Exploration done — 41 exact, 0 from snapshots, 2 still need a guess.", "ok"),  # 10 general
    L("Generation blocked: live inspection did not verify every required locator.", "err"),  # 11
]


class GroupingTests(unittest.TestCase):
    def test_cases_start_at_walking_lines_and_end_at_the_next_case_or_run_summary(self):
        m = V.build(RUN)
        self.assertEqual(m["group"], [None, None, 0, 0, 0, 0, 1, 1, 2, 2, None, None])
        self.assertEqual(m["headers"], {0: 2, 1: 6, 2: 8})
        self.assertEqual(m["titles"], {0: "Login works", 1: "Checkout", 2: "Logout"})

    def test_status_is_the_worst_tone_inside_the_case(self):
        m = V.build(RUN + [L("    ✗ broke", "err")])   # lands after 'Generation blocked' -> general
        self.assertEqual(m["status"], {0: "ok", 1: "warn", 2: "ok"})
        m2 = V.build(RUN[:8] + [L("    x", "err")])
        self.assertEqual(m2["status"][1], "err")

    def test_a_story_line_also_closes_the_open_case(self):
        m = V.build(RUN[:4] + [L("▸ Story 8 — next", "story"), L("    after")])
        self.assertEqual(m["group"][4:], [None, None])


class VisibilityTests(unittest.TestCase):
    def test_while_running_finished_cases_collapse_and_the_active_one_stays_open(self):
        vis = V.visibility(RUN[:10], running=True)
        self.assertEqual(vis, [True, True, True, False, False, False, True, False, True, True])

    def test_after_the_run_every_case_collapses_to_its_header(self):
        vis = V.visibility(RUN, running=False)
        self.assertEqual([i for i, v in enumerate(vis) if v], [0, 1, 2, 6, 8, 10, 11])

    def test_user_toggle_overrides_auto_collapse(self):
        vis = V.visibility(RUN, running=False, toggles={1: True})
        self.assertTrue(vis[7])
        vis2 = V.visibility(RUN[:10], running=True, toggles={2: False})
        self.assertFalse(vis2[9])

    def test_issues_filter_shows_only_problem_lines_and_their_case_headers(self):
        vis = V.visibility(RUN, running=False, issues_only=True)
        self.assertEqual([i for i, v in enumerate(vis) if v], [6, 7, 11])


class ResultBannerTests(unittest.TestCase):
    def test_banner_kinds(self):
        self.assertEqual(V.banner_tone("generated"), "ok")
        self.assertEqual(V.banner_tone("blocked"), "err")
        self.assertEqual(V.banner_tone("failed"), "err")
        self.assertEqual(V.banner_tone("stopped"), "warn")
        self.assertEqual(V.banner_tone("nothing"), "ok")


if __name__ == "__main__":
    unittest.main()
