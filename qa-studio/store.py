"""store.py — local credential persistence (base64, in the user profile).

Security note: credentials are stored base64-encoded (NOT encrypted) in the
user's home profile, which is OS-protected per-user. On POSIX systems the file
is locked to owner-only (0600). base64 is obfuscation, not encryption — anyone
with read access to your user account can decode it. Treat the host machine as
the trust boundary.
"""
import os, json, base64, stat

CRED_DIR  = os.path.join(os.path.expanduser("~"), ".qa_tool")
CRED_FILE = os.path.join(CRED_DIR, "creds.dat")

def load():
    try:
        with open(CRED_FILE, "rb") as f:
            d = json.loads(base64.b64decode(f.read()).decode("utf-8"))
    except Exception:
        d = {}
    d.setdefault("keys", {})
    d.setdefault("pat", "")
    d.setdefault("gmail", "")
    return d

def save(d):
    try:
        os.makedirs(CRED_DIR, exist_ok=True)
        # Restrict the directory to the owner (POSIX). No-op on Windows, where
        # the user profile is already access-controlled per account.
        try:
            os.chmod(CRED_DIR, stat.S_IRWXU)  # 0700
        except Exception:
            pass
        # Write to a temp file then atomically replace, so a crash mid-write
        # can't leave a half-written/corrupt credentials file.
        tmp = CRED_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(base64.b64encode(json.dumps(d).encode("utf-8")))
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except Exception:
            pass
        os.replace(tmp, CRED_FILE)
        try:
            os.chmod(CRED_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except Exception:
            pass
        return True
    except Exception:
        return False