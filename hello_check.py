"""hello_check.py - read-only Windows Hello diagnostic (prints no secrets).

    py -3.12 hello_check.py            (availability only)
    py -3.12 hello_check.py --verify   (also shows the real prompt)
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

if "--verify" in sys.argv:
    print("Asking Windows Hello (owned by this console window)...")
    ok, reason = d.verify("QA Studio - Windows Hello test", hwnd=d.foreground_window())
    print("Verify      :", ok, reason)
    print("Detail      :", d.last_reason or "-")
