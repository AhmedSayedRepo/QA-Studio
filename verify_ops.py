"""verify_ops.py — prove the generation ops seam changed nothing for Azure.

    py -3.12 verify_ops.py

`run_titles`/`run_steps` gained an `ops=None` parameter and now call
`_o.<something>(...)` instead of the module-level Azure functions. The whole
safety argument for that edit is one claim:

    with ops=None, every _o.X is the IDENTICAL function object that used to be
    called at that line.

If that holds, the default path is pure indirection and Azure generation cannot
have changed. This asserts it by identity (`is`), not by name or by eye — and
it runs without touching the network, so it's cheap to re-run after any future
edit to the seam.

It also checks the routing is COMPLETE: no Azure call may remain unrouted
inside either generator, or that path would ignore an injected backend and
silently write to Azure instead.
"""
from __future__ import annotations

import io
import re
import sys

EXPECTED = {
    "connect": "connect_azure_sdk",
    "discover_suites": "discover_suites_for_stories",
    "fetch_stories": "fetch_stories",
    "dedupe_suite": "dedupe_existing_suite",
    "existing_titles": "fetch_existing_titles_for_suite",
    "create_case": "create_test_case",
    "cases_for_suite": "fetch_test_cases_for_suite",
    "case_title": "fetch_test_case_title",
    "write_steps": "update_test_case_with_steps",
}


def main():
    if sys.version_info < (3, 12):
        print(f"  ERROR: needs Python 3.12+ (engine.py). Got "
              f"{sys.version_info.major}.{sys.version_info.minor}.")
        return 2

    import engine as E

    failures = []

    # 1) Identity: ops=None must bind to the exact original function objects.
    ops = E._default_ops()
    print("\n  default ops binding (ops=None):\n")
    for attr, fname in EXPECTED.items():
        bound = getattr(ops, attr, None)
        original = getattr(E, fname, None)
        same = bound is original
        print(f"    {attr:16} -> {fname:32} {'IDENTICAL' if same else 'DIFFERENT ✗'}")
        if not same:
            failures.append(f"{attr} is not {fname}")

    # 2) Signatures still accept ops, and default to None.
    import inspect
    for fn_name in ("run_titles", "run_steps"):
        sig = inspect.signature(getattr(E, fn_name))
        if "ops" not in sig.parameters:
            failures.append(f"{fn_name} has no ops parameter")
        elif sig.parameters["ops"].default is not None:
            failures.append(f"{fn_name}'s ops does not default to None")
    print(f"\n  signatures       : run_titles/run_steps accept ops=None")

    # 3) Completeness: nothing Azure-specific left unrouted in either generator.
    src = io.open("engine.py", encoding="utf-8").read().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith("def run_titles("))
    end = next(i for i, l in enumerate(src[start + 1:], start + 1)
               if l.startswith("def ") and any(src[j].startswith("def run_steps(")
                                               for j in range(start, i)))
    body = "\n".join(src[start:end])
    stray = []
    for fname in EXPECTED.values():
        # A bare call not preceded by "_o." (or a def) is an unrouted call site.
        for m in re.finditer(r"(?<![\w.])" + re.escape(fname) + r"\s*\(", body):
            before = body[max(0, m.start() - 4):m.start()]
            if not before.endswith("_o."):
                line = body[:m.start()].count("\n") + start + 1
                stray.append(f"{fname} at engine.py:{line}")
    if stray:
        failures.extend(stray)
    print(f"  routing complete : {'no unrouted Azure calls' if not stray else stray}")

    print("\n" + "─" * 62)
    if failures:
        print(f"  {len(failures)} PROBLEM(S):")
        for f in failures:
            print(f"    - {f}")
        print("\n  The seam is NOT a no-op — Azure generation may be affected.")
        return 1
    print("  PASS — with ops=None the generators call the identical functions")
    print("  they always did. Azure generation is unaffected by the seam.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
