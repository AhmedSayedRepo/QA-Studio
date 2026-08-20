"""_sync_to_install.py — copy changed .py files from dev folder to install folder.
Run after any code change: python _sync_to_install.py
"""
import os, shutil, glob, hashlib

SRC = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(os.environ["LOCALAPPDATA"], "QA Studio")

if not os.path.isdir(DST):
    print(f"Install folder not found: {DST}")
    raise SystemExit(1)

def _digest(p):
    # Compare by CONTENT, not mtime: an app self-update can stamp the installed
    # files with a newer timestamp than your edits, which an mtime check would
    # wrongly treat as "already up to date" and skip the real change.
    try:
        with open(p, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None

synced = []
# Copy .py sources PLUS assets the app reads at runtime (VERSION drives the footer
# version; icons/backgrounds for anything not embedded). Without VERSION here the
# installed app keeps showing its old version even after a code sync.
patterns = ["*.py", "VERSION", "*.png", "*.jpg", "*.ico"]
seen = set()
for pat in patterns:
    for src_path in glob.glob(os.path.join(SRC, pat)):
        fname = os.path.basename(src_path)
        if fname in seen:
            continue
        seen.add(fname)
        # Keep developer/release helpers out of the installed runtime folder.
        if (fname.startswith("_sync") or fname.startswith("patch_")
                or fname == "_check_release_notes.py"):
            continue
        dst_path = os.path.join(DST, fname)
        if (not os.path.exists(dst_path)) or _digest(src_path) != _digest(dst_path):
            shutil.copy2(src_path, dst_path)
            synced.append(fname)
            if fname.endswith(".py"):
                pyc_glob = os.path.join(DST, "__pycache__",
                                        fname[:-3] + ".cpython-*.pyc")
                for pyc in glob.glob(pyc_glob):
                    try: os.remove(pyc)
                    except: pass

# Package SUBDIRECTORIES. The glob above is top-level only, so packages such as
# tracker/ (backend adapters) and perf/ (the Performance screen) would otherwise
# be absent from the installed copy and fail at import time. Running `python
# main.py` from this dev folder hides that problem because the packages are here.
# Sync each package dir recursively. Add any future package here.
PACKAGE_DIRS = ["tracker", "perf"]
for pkg in PACKAGE_DIRS:
    src_dir = os.path.join(SRC, pkg)
    if not os.path.isdir(src_dir):
        continue
    for root, _dirs, files in os.walk(src_dir):
        if "__pycache__" in root:
            continue
        rel = os.path.relpath(root, SRC)
        dst_root = os.path.join(DST, rel)
        os.makedirs(dst_root, exist_ok=True)
        for fname in files:
            if not fname.endswith(".py"):
                continue
            sp = os.path.join(root, fname)
            dp = os.path.join(dst_root, fname)
            if (not os.path.exists(dp)) or _digest(sp) != _digest(dp):
                shutil.copy2(sp, dp)
                synced.append(os.path.join(rel, fname))
                # Drop this package's stale bytecode too — the top-level loop
                # above clears its own __pycache__, but subpackages have their
                # own, and copy2 preserves the source mtime, so without this the
                # installed .pyc could shadow the fresh .py on first launch.
                for pyc in glob.glob(os.path.join(dst_root, "__pycache__",
                                                  fname[:-3] + ".cpython-*.pyc")):
                    try: os.remove(pyc)
                    except Exception: pass

if synced:
    print("Synced:", ", ".join(synced))
else:
    print("Already up to date.")
