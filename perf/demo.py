"""perf/demo.py - offline smoke test for the performance core.

Extracts a PerfScenario from sample functional steps, emits a real Apache JMeter
project (.jmx + CSV + run scripts) to a temp folder, prints a summary and the
first lines of the plan, and reports whether JMeter is installed so you could run
it. Nothing here touches the app or the network - it's a hands-on way to SEE what
the feature produces before it's wired into a screen.

Run from the repo root:
    python -m perf.demo

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

import os
import tempfile

from perf import DataSource, HeuristicExtractor, LoadProfile, get_target

SAMPLE_STEPS = [
    {"action": "Log in with {{email}} and {{password}}", "expected": "200 OK"},
    {"action": "Open the dashboard at https://app.example.com/dashboard", "expected": "Welcome"},
    {"action": "Search for {{term}}", "expected": "results are shown"},
    {"action": "Log out", "expected": "204"},
]


def main() -> None:
    # 1) extract a scenario from a functional test case (heuristic = no AI/no key)
    scenario = HeuristicExtractor().extract(
        case_id="TC-101", title="Search journey", steps=SAMPLE_STEPS, story_id="US-42")

    print("=" * 64)
    print(f"Scenario: {scenario.title}  (id={scenario.id}, story={scenario.story_id})")
    print(f"Variables (a CSV would supply): {scenario.variables}")
    for i, r in enumerate(scenario.requests, 1):
        print(f"  {i}. {r.method:6} {r.url}")
    print("=" * 64)

    # 2) a data-driven CSV (a real one would be user-uploaded); password is sensitive
    out = tempfile.mkdtemp(prefix="qastudio_perf_")
    csv_path = os.path.join(out, "users.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("email,password,term\n")
        f.write("a@ex.com,pw1,laptops\n")
        f.write("b@ex.com,pw2,phones\n")
    data = DataSource(csv_path=csv_path, columns=["email", "password", "term"],
                      sensitive_columns=["password"])

    # 3) emit a JMeter project
    profile = LoadProfile(users=20, ramp_up_s=15, duration_s=120,
                          thresholds={"p95_ms": 800, "error_rate": 0.01})
    target = get_target("jmeter")
    paths = target.emit([scenario], profile, out, data=data)

    print(f"Emitted JMeter project -> {paths.root}")
    print(f"  plan     : {paths.entry}")
    print(f"  data csv : {paths.data_csv}  (password column stripped)")
    print("  --- first lines of plan.jmx ---")
    with open(paths.entry, encoding="utf-8") as f:
        head = f.read(900)
    print("  " + head.replace("\n", "\n  "))
    print("  ...")

    # 4) is JMeter runnable here?
    ok, msg = target.preflight()
    print("=" * 64)
    print(f"JMeter preflight: {'OK - ' if ok else 'NOT READY - '}{msg}")
    if ok:
        print("To actually run the load test against a real target host:")
        print(f'    cd "{paths.root}" && jmeter -n -t plan.jmx -l results.jtl -e -o report')
        print("(set the {{host}} variable / point URLs at your environment first)")
    print("=" * 64)


if __name__ == "__main__":
    main()
