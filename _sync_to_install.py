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
for src_path in glob.glob(os.path.join(SRC, "*.py")):
    fname = os.path.basename(src_path)
    if fname.startswith("_sync") or fname.startswith("patch_"):
        continue
    dst_path = os.path.join(DST, fname)
    if (not os.path.exists(dst_path)) or _digest(src_path) != _digest(dst_path):
        shutil.copy2(src_path, dst_path)
        synced.append(fname)
        # remove stale pyc
        pyc_glob = os.path.join(DST, "__pycache__", fname.replace(".py", "") + ".cpython-*.pyc")
        for pyc in glob.glob(pyc_glob):
            try: os.remove(pyc)
            except: pass

if synced:
    print("Synced:", ", ".join(synced))
else:
    print("Already up to date.")
