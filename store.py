"""store.py — local credential persistence.

At rest, the credential blob is encrypted with **Windows DPAPI** (per-user,
machine-bound) when available, so API keys / Azure PAT / Gmail app password are
NOT recoverable by simply reading the file. On non-Windows or if DPAPI is
unavailable it falls back to base64 obfuscation (the previous behavior). Legacy
base64 files are read transparently and upgraded to DPAPI on the next save.

All DPAPI access is lazy and fully guarded, so importing this module never fails
on a non-Windows host and any crypto error degrades gracefully to base64.
"""
import os, json, base64, ctypes
import ctypes.wintypes as wintypes

CRED_DIR  = os.path.join(os.path.expanduser("~"), ".qa_tool")
CRED_FILE = os.path.join(CRED_DIR, "creds.dat")

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


def _encrypt(plain):
    if _dpapi_ok():
        try:
            return _DPAPI_MAGIC + _dpapi("CryptProtectData", plain)
        except Exception:
            pass
    return base64.b64encode(plain)            # fallback / non-Windows


def _decrypt(raw):
    if raw.startswith(_DPAPI_MAGIC):
        return _dpapi("CryptUnprotectData", raw[len(_DPAPI_MAGIC):])
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
