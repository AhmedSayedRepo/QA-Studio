"""hello_check.py - read-only Windows Hello diagnostic (prints no secrets).

    py -3.12 hello_check.py
"""
import sys
import importlib.util

print("Python      :", sys.version.split()[0], sys.executable)
print("Platform    :", sys.platform)
for mod in ("winrt", "winrt.windows.foundation",
            "winrt.windows.security.credentials.ui"):
    try:
        ok = importlib.util.find_spec(mod) is not None
    except Exception:
        ok = False
    print("%-40s %s" % (mod, "installed" if ok else "MISSING"))

import desktop_hello as d
print("Available   :", d.available(refresh=True))
print("Reason      :", d.last_reason or "-")
print("Enabled     :", d.enabled())
