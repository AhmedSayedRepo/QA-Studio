"""store.py — local credential persistence (base64, in the user profile)."""
import os, json, base64

CRED_DIR  = os.path.join(os.path.expanduser("~"), ".qa_tool")
CRED_FILE = os.path.join(CRED_DIR, "creds.dat")

def load():
    try:
        with open(CRED_FILE, "rb") as f:
            d = json.loads(base64.b64decode(f.read()).decode("utf-8"))
    except Exception:
        d = {}
    d.setdefault("keys", {})
    d.setdefault("models", {})
    d.setdefault("pat", "")
    d.setdefault("gmail", "")
    return d

def save(d):
    try:
        os.makedirs(CRED_DIR, exist_ok=True)
        with open(CRED_FILE, "wb") as f:
            f.write(base64.b64encode(json.dumps(d).encode("utf-8")))
        return True
    except Exception:
        return False
