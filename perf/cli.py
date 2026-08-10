"""perf/cli.py - headless runner for the performance pipeline.

Test the WHOLE flow from a terminal before there's any GUI - extract a scenario
from test cases, emit a JMeter project, and (optionally) run it - using your real
AI key and JMeter install, with zero risk to the app.

Examples (run from the repo root):
    py -3 -m perf.cli --sample --out perf_out --users 20 --duration 60
    py -3 -m perf.cli --cases cases.json --ai --data users.csv --p95 800 --run

`--cases` JSON is a list of:
    {"id":"TC-1","title":"Login","story_id":"US-9",
     "steps":[{"action":"Log in with {{email}}","expected":"200"}]}

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import service
from .models import DataSource, LoadProfile


def _sample_cases() -> List[dict]:
    return [{
        "id": "TC-101", "title": "Search journey", "story_id": "US-42",
        "steps": [
            {"action": "Log in with {{email}} and {{password}}", "expected": "200 OK"},
            {"action": "Open the dashboard", "expected": "Welcome"},
            {"action": "Search for {{term}}", "expected": "results are shown"},
            {"action": "Log out", "expected": "204"},
        ],
    }]


def _ai_callable(verbose: bool = True):
    """Wire the app's engine.ai_complete if importable/configured. On any failure
    the AIExtractor degrades to the heuristic, so this never blocks a run."""
    try:
        import engine  # noqa
        if verbose:
            print("[ai] using engine.ai_complete (falls back to heuristic if the model/key is unavailable)")
        return lambda p: engine.ai_complete(p, tag="perf")
    except Exception as e:
        if verbose:
            print(f"[ai] engine unavailable ({e}); using the offline heuristic extractor")
        return None


def _load_data(path: Optional[str]) -> Optional[DataSource]:
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        header = [h.strip() for h in f.readline().strip().split(",") if h.strip()]
    return DataSource(csv_path=path, columns=header)


def _print_result(r) -> None:
    gate = {True: "PASS", False: "FAIL", None: "n/a"}[r.threshold_pass]
    print("-" * 60)
    print(f"RESULT [{gate}]  samples={r.samples}  errors={r.errors} "
          f"({r.error_rate * 100:.2f}%)  throughput={r.throughput_rps:.1f} req/s")
    print(f"  latency ms  p50={r.p50_ms:.0f}  p90={r.p90_ms:.0f}  "
          f"p95={r.p95_ms:.0f}  p99={r.p99_ms:.0f}  avg={r.avg_ms:.0f}")
    for rs in r.per_request:
        print(f"    {rs.label:34.34}  n={rs.samples:<5} err={rs.errors:<4} "
              f"p95={rs.p95_ms:.0f}ms")
    print("-" * 60)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="perf.cli", description="QA Studio performance pipeline (headless)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--cases", help="JSON file: list of test-case dicts")
    src.add_argument("--sample", action="store_true", help="use a built-in sample test case")
    ap.add_argument("--out", default="perf_out", help="output project dir (default: perf_out)")
    ap.add_argument("--target", default="jmeter", help="load tool (default: jmeter)")
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--ramp", type=int, default=15)
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--ai", action="store_true", help="use the AI extractor (needs engine + a key)")
    ap.add_argument("--data", help="CSV of data-driven inputs (columns map to variables)")
    ap.add_argument("--p95", type=float, help="threshold: p95 latency ms")
    ap.add_argument("--error-rate", type=float, help="threshold: max error rate (0-1)")
    ap.add_argument("--run", action="store_true", help="actually run JMeter (needs it installed)")
    args = ap.parse_args(argv)

    cases = _sample_cases() if args.sample else json.load(open(args.cases, encoding="utf-8"))
    ai = _ai_callable() if args.ai else None
    thresholds = {}
    if args.p95:
        thresholds["p95_ms"] = args.p95
    if args.error_rate:
        thresholds["error_rate"] = args.error_rate
    profile = LoadProfile(users=args.users, ramp_up_s=args.ramp,
                          duration_s=args.duration, thresholds=thresholds)
    data = _load_data(args.data)

    scenarios, target, paths = service.build_and_emit(
        cases, profile, args.out, target_name=args.target, ai_complete=ai, data=data)

    print(f"Extracted {len(scenarios)} scenario(s); "
          f"{sum(s.request_count for s in scenarios)} request(s).")
    for s in scenarios:
        print(f"  [{s.id}] {s.title} - {s.request_count} req, vars={s.variables}")
    print(f"Emitted -> {paths.entry}")
    if paths.data_csv:
        print(f"Data CSV -> {paths.data_csv} (sensitive columns stripped)")

    ok, msg = target.preflight()
    print(f"{args.target} preflight: {'OK' if ok else 'NOT READY'} - {msg}")

    if not args.run:
        print("Not running (pass --run to execute). Open the emitted project to inspect it.")
        return 0
    if not ok:
        print("Cannot run: tool not available.", file=sys.stderr)
        return 2
    result = service.run(target, paths, profile,
                         on_event=lambda ev: print(f"[run] {ev.get('msg', '')}"))
    _print_result(result)
    return 0 if (result.threshold_pass in (True, None)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
