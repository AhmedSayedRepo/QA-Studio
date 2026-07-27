"""run_contract.py — run the tracker parity suite against a REAL backend.

    py -3.12 run_contract.py "MyScratchProject"          # Azure (default)
    py -3.12 run_contract.py "PROJ" --backend jira_zephyr

WHY THIS WRAPPER EXISTS
`python -m tracker.contract azure "<Project>"` does not work on its own.
`engine.AZURE_ORG` / `engine.AZURE_PAT` are module globals that start EMPTY and
are only populated at runtime by the app calling `engine.set_credentials()`.
Run the suite standalone and it fails immediately on the org guard, which looks
like a bug in the suite rather than what it is: no credentials loaded.

So this script does the one thing the app does at startup — reads the saved
credentials via `store.load()` and pushes them into the engine — then runs the
suite. No secrets are printed; only which fields were found.

⚠️  THIS WRITES TO YOUR TRACKER. Use a SCRATCH project.
    The suite creates a test plan, a suite/folder, and a test case. It deletes
    the test case it created, but it does NOT delete the plan or folder — those
    are left behind on purpose (deleting a plan is destructive enough that the
    suite shouldn't do it unattended). Expect one "QA Studio contract HHMMSS"
    plan per run; clean them up manually.

Requires Python 3.12+ — engine.py uses PEP 701 f-strings that older versions
cannot parse.
"""
from __future__ import annotations

import sys


def _fail(msg, hint=""):
    print(f"\n  ERROR: {msg}")
    if hint:
        print(f"  {hint}")
    return 2


def _select_cred_file(store, user_id=""):
    """Point store at the right per-user credential file.

    Returns None on success, or an int exit code to bubble up.

    The app writes creds_{uid}.dat per signed-in account. Standalone we can't
    authenticate to learn the uid, so: honour --user if given, auto-select when
    exactly one per-user file exists, and otherwise list them and let the user
    choose rather than silently defaulting to the shared file (the failure that
    produced a misleading 401).
    """
    import glob
    import os

    if user_id:
        store.set_user(user_id)
        return None

    per_user = sorted(glob.glob(os.path.join(store.CRED_DIR, "creds_*.dat")))
    if not per_user:
        return None                      # only the shared file exists — use it

    def uid_of(path):
        return os.path.basename(path)[len("creds_"):-len(".dat")]

    if len(per_user) == 1:
        uid = uid_of(per_user[0])
        store.set_user(uid)
        print(f"\n  using per-user credentials for: {uid}")
        return None

    print(f"\n  {len(per_user)} per-user credential files found. Pick one with --user:\n")
    for path in per_user:
        try:
            size = os.path.getsize(path)
            when = __import__("datetime").datetime.fromtimestamp(
                os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size, when = 0, "?"
        print(f"    --user {uid_of(path)}    ({size} bytes, modified {when})")
    print("\n  The most recently modified one is usually the account you're signed into.")
    return 1


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    if sys.version_info < (3, 12):
        return _fail(
            f"Python {sys.version_info.major}.{sys.version_info.minor} is too old.",
            "engine.py needs 3.12+. Try:  py -3.12 run_contract.py \"MyProject\"")

    read_only = "--read-only" in argv
    list_only = argv[0] in ("--list", "-l")
    project = "" if list_only else argv[0]
    backend_name = "azure"
    if "--backend" in argv:
        try:
            backend_name = argv[argv.index("--backend") + 1]
        except IndexError:
            return _fail("--backend needs a value (azure | jira_zephyr | fake).")

    user_id = ""
    if "--user" in argv:
        try:
            user_id = argv[argv.index("--user") + 1]
        except IndexError:
            return _fail("--user needs a value (see the list this script prints).")

    # 1) Load saved credentials, exactly as the app does at startup.
    #
    # CRITICAL: credentials are stored PER-USER. store.set_user(uid) repoints
    # load()/save() at creds_{uid}.dat so that two accounts on one device never
    # share a PAT — main.py does this right after sign-in. A script that skips
    # set_user() silently reads the SHARED pre-sign-in creds.dat instead, which
    # typically holds a stale PAT and fails with a 401 that looks like "your
    # token is bad" rather than "you're reading the wrong file".
    try:
        import store
    except Exception as exc:
        return _fail(f"Could not import store: {exc}")

    chosen = _select_cred_file(store, user_id)
    if isinstance(chosen, int):
        return chosen

    try:
        creds = store.load() or {}
    except Exception as exc:
        return _fail(f"Could not read saved credentials: {exc}",
                     "Open QA Studio once and complete Setup first.")

    try:
        import backend_setup
        backend_setup.defaults(creds)
    except Exception:
        creds.setdefault("backend", "azure")

    print(f"\n  credentials  : {store.CRED_FILE if hasattr(store, 'CRED_FILE') else 'saved store'}")
    print(f"  backend      : {backend_name}")
    if not list_only:
        print(f"  project      : {project}")

    # 2) Seed the engine globals (Azure) — the step the bare module invocation
    #    is missing. Values are never printed, only their presence.
    if backend_name == "azure":
        org = (creds.get("org") or "").strip()
        pat = (creds.get("pat") or "").strip()
        print(f"  org          : {'set (' + org + ')' if org else 'MISSING'}")
        print(f"  PAT          : {'set (%d chars)' % len(pat) if pat else 'MISSING'}")
        if not org or not pat:
            return _fail("Azure organization and/or PAT are not saved.",
                         "Open QA Studio → Setup, fill them in, and Save. Then re-run.")
        try:
            import engine as E
            E.set_credentials(org=org, pat=pat)
        except SyntaxError as exc:
            return _fail(f"engine.py failed to parse: {exc}",
                         "This is the 3.12-only f-string issue — use py -3.12.")
        except Exception as exc:
            return _fail(f"Could not seed engine credentials: {exc}")
    else:
        for label, key in (("site", "jira_site"), ("email", "jira_email"),
                           ("jira token", "jira_token"), ("zephyr token", "zephyr_token")):
            val = (creds.get(key) or "").strip()
            shown = val if key in ("jira_site", "jira_email") else ("set (%d chars)" % len(val) if val else "")
            print(f"  {label:13}: {shown or 'MISSING'}")

    # --list is READ-ONLY: show the real project names and stop. Nothing is
    # created. Use this to find the exact name to pass as the project argument.
    if list_only:
        import tracker
        backend = tracker.get_backend(creds, name=backend_name, config=creds)
        try:
            projects = backend.fetch_projects()
        except Exception as exc:
            return _fail(f"Could not list projects: {exc}")
        print(f"\n  {len(projects)} project(s) — pass one of these as the argument:\n")
        for proj in projects:
            print(f"    {proj.name}")
        print("\n  Then:  py -3.12 run_contract.py \"<name>\"")
        print("  Pick a SCRATCH project — the suite leaves a test plan behind.")
        return 0

    if read_only:
        print("\n  read-only: nothing will be created, updated or deleted.")
    else:
        print("\n  ⚠️  This writes to the tracker: it creates a plan, a suite and a")
        print("      test case. The case is removed from its suite; the PLAN and")
        print("      SUITE are LEFT BEHIND (nothing is plan-deleted). Prefer a")
        print("      SCRATCH project, or clean up the 'QA Studio contract …' plan")
        print("      by hand afterward. (Use --read-only to verify without writing.)")
        try:
            if input("  Continue? [y/N] ").strip().lower() not in ("y", "yes"):
                print("  Aborted.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return 1

    # 3) Run the suite.
    import tracker
    from tracker.contract import run_contract

    backend = tracker.get_backend(creds, name=backend_name, config=creds)
    results, ok = run_contract(backend, project=project, read_only=read_only)

    print(f"\ntracker contract suite — backend: {backend.name}\n" + "─" * 62)
    for res in results:
        print(res)
    print("─" * 62)
    passed = sum(1 for r in results if r.ok and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    failed = sum(1 for r in results if not r.ok)
    print(f"{passed} passed · {skipped} skipped · {failed} failed")
    if failed:
        print("\n  Failures above are the DTO conversions to fix — each names the\n"
              "  method and what it returned. This is the suite doing its job.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
