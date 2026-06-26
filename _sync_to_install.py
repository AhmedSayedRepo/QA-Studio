"""_sync_to_install.py — copy changed .py files from dev folder to install folder.
Run after any code change: python _sync_to_install.py
"""
import os, shutil, glob

SRC = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(os.environ["LOCALAPPDATA"], "QA Studio")

if not os.path.isdir(DST):
    print(f"Install folder not found: {DST}")
    raise SystemExit(1)

synced = []
for src_path in glob.glob(os.path.join(SRC, "*.py")):
    fname = os.path.basename(src_path)
    if fname.startswith("_sync") or fname.startswith("patch_"):
        continue
    dst_path = os.path.join(DST, fname)
    src_mt = os.path.getmtime(src_path)
    dst_mt = os.path.getmtime(dst_path) if os.path.exists(dst_path) else 0
    if src_mt > dst_mt:
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
