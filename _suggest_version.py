"""Suggest the next release version for release.bat.

Base = the latest PUBLISHED release, not the VERSION file: an aborted release
run leaves VERSION already bumped (e.g. 3.9.3 while 3.9.2 is the latest
release), and bumping that again would skip a number (that's how v3.9.3 never
existed). A "latest release" ABOVE the VERSION file is treated as a stray
(e.g. old v4.6.x tags) and ignored.

Usage: python _suggest_version.py VERSION_FILE_VALUE LATEST_RELEASE_TAG
Prints the suggestion, or nothing when neither input is an x.y.z version.
"""

from __future__ import annotations

import re
import sys

_V = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _parse(value):
    m = _V.match(str(value or "").strip())
    return tuple(int(p) for p in m.groups()) if m else None


def suggest(version_file, latest_release):
    cur, last = _parse(version_file), _parse(latest_release)
    base = last if last and (cur is None or last <= cur) else cur
    if base is None:
        return ""
    return "%d.%d.%d" % (base[0], base[1], base[2] + 1)


if __name__ == "__main__":
    args = sys.argv[1:] + ["", ""]
    print(suggest(args[0], args[1]))
