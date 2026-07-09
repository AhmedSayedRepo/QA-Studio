"""store.py — local credential persistence.

At rest, the credential blob is encrypted with **Windows DPAPI** (per-user,
machine-bound) when available, so API keys / Azure PAT / Gmail app password are
NOT recoverable by simply reading the file. On non-Windows, or if DPAPI is
unavailable, it falls back to **Fernet symmetric encryption** (AES-128-CBC +
HMAC, via the `cryptography` package already used elsewhere in this app) keyed
by a random, locally-generated key file with restrictive permissions — a real
encryption fallback, not the plain base64 "obfuscation" this used previously
(base64 is trivially reversible by anyone who can read the file; it provided
no confidentiality at all). Base64 is now only used as a last-resort fallback
if `cryptography` itself is unavailable, and legacy base64 files are still
read transparently and upgraded on the next save.

All DPAPI/Fernet access is lazy and fully guarded, so importing this module
never fails on a non-Windows host and any crypto error degrades gracefully.
"""
import os, json, base64, ctypes, stat
import ctypes.wintypes as wintypes

CRED_DIR  = os.path.join(os.path.expanduser("~"), ".qa_tool")
_DEFAULT_FILE = os.path.join(CRED_DIR, "creds.dat")
# Active credential file. Switched to a per-user file via set_user() so different
# signed-in accounts on the same device don't share keys / PAT / prefs.
CRED_FILE = _DEFAULT_FILE

import re as _re


def set_user(user_id):
    """Point load()/save() at a per-user credential file. Pass None/'' for the
    shared default file (before sign-in, or when auth is unconfigured)."""
    global CRED_FILE
    uid = (str(user_id).strip() if user_id else "")
    if uid:
        safe = _re.sub(r"[^A-Za-z0-9._-]", "_", uid)[:80]
        CRED_FILE = os.path.join(CRED_DIR, f"creds_{safe}.dat")
    else:
        CRED_FILE = _DEFAULT_FILE
    return CRED_FILE

_DPAPI_MAGIC = b"DPAPI1\n"   # marks a DPAPI-encrypted file (vs legacy base64)


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_ok():
    return os.name == "nt" and hasattr(ctypes, "windll")


_CRYPT = {}
def _crypt_fn(name):
    """Return a properly-typed crypt32 function (argtypes set so 64-bit pointers
    aren't truncated). Windows only — callers must gate on _dpapi_ok()."""
    if name not in _CRYPT:
        fn = getattr(ctypes.windll.crypt32, name)
        fn.restype = wintypes.BOOL
        fn.argtypes = [ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR,
                       ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                       wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
        _CRYPT[name] = fn
    return _CRYPT[name]


def _dpapi(name, data):
    inb = _DATA_BLOB(len(data),
                     ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                 ctypes.POINTER(ctypes.c_char)))
    outb = _DATA_BLOB()
    # CRYPTPROTECT_LOCAL_MACHINE is NOT set -> per-user protection.
    if not _crypt_fn(name)(ctypes.byref(inb), None, None, None, None, 0,
                           ctypes.byref(outb)):
        raise OSError(f"{name} failed")
    try:
        return ctypes.string_at(ctypes.cast(outb.pbData, ctypes.c_void_p), outb.cbData)
    finally:
        try:
            ctypes.windll.kernel32.LocalFree(ctypes.cast(outb.pbData, ctypes.c_void_p))
        except Exception:
            pass


_FERNET_MAGIC = b"FERN1\n"    # marks a Fernet-encrypted file (non-Windows fallback)
_KEY_FILE = os.path.join(CRED_DIR, ".store_key")


def _fernet_key():
    """Load (or create) a random local key for the Fernet fallback. Stored
    separately from the ciphertext file, with owner-only permissions where the
    platform supports it, so reading the credentials file alone isn't enough
    to decrypt it — an attacker needs both files.

    SECURITY: the loaded key is validated (not just checked for non-empty)
    before being trusted. A key file that exists but is corrupt/truncated/
    tampered-with would otherwise make `Fernet(key)` raise inside _encrypt(),
    which previously fell through silently to the base64 "fallback" — writing
    every credential from that point on in effectively plaintext, with no
    warning. Validating here means a bad key file is treated as ABSENT (we
    regenerate a fresh one) rather than as a reason to quietly disable
    encryption."""
    from cryptography.fernet import Fernet
    os.makedirs(CRED_DIR, exist_ok=True)
    try:
        with open(_KEY_FILE, "rb") as f:
            key = f.read().strip()
        if key:
            Fernet(key)   # raises if not a valid Fernet key — don't trust it silently
            return key
    except Exception:
        pass
    key = Fernet.generate_key()
    fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    try:
        os.chmod(_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)   # 0600, best-effort
    except Exception:
        pass
    return key


def _fernet_ok():
    try:
        import cryptography.fernet  # noqa: F401
        return True
    except Exception:
        return False


def _encrypt(plain):
    if _dpapi_ok():
        try:
            return _DPAPI_MAGIC + _dpapi("CryptProtectData", plain)
        except Exception:
            pass
    if _fernet_ok():
        try:
            from cryptography.fernet import Fernet
            return _FERNET_MAGIC + Fernet(_fernet_key()).encrypt(plain)
        except Exception:
            pass
    return base64.b64encode(plain)            # last-resort fallback only


def _decrypt(raw):
    if raw.startswith(_DPAPI_MAGIC):
        return _dpapi("CryptUnprotectData", raw[len(_DPAPI_MAGIC):])
    if raw.startswith(_FERNET_MAGIC):
        from cryptography.fernet import Fernet
        return Fernet(_fernet_key()).decrypt(raw[len(_FERNET_MAGIC):])
    return base64.b64decode(raw)              # legacy base64 file


def load():
    try:
        with open(CRED_FILE, "rb") as f:
            d = json.loads(_decrypt(f.read()).decode("utf-8"))
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
        blob = _encrypt(json.dumps(d).encode("utf-8"))
        with open(CRED_FILE, "wb") as f:
            f.write(blob)
        return True
    except Exception:
        return False
