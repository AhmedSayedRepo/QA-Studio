"""Stamp prepared release-note keys with release.bat's selected version.

This intentionally does nothing when no draft exists. release.bat will still
publish, while its existing advisory check reminds the releaser that the popup
will have no exact notes for that version.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import release_notes


SOURCE = Path(__file__).with_name("release_notes.py")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not VERSION_RE.fullmatch(argv[1].strip()):
        print("Usage: python _stamp_release_notes.py VERSION")
        return 2

    version = release_notes.normalize_version(argv[1])
    pending = tuple(release_notes.PENDING_RELEASE_NOTE_KEYS)
    if not pending:
        print("[INFO] No prepared release-note draft to stamp.")
        return 0
    if release_notes.keys_for(version):
        print(f"[WARN] Release notes already exist for {version}; draft was not changed.")
        return 0

    source = SOURCE.read_text(encoding="utf-8")
    registry_match = re.search(
        r"RELEASE_NOTE_KEYS\s*=\s*(\{.*?\})\n\n# Add the keys",
        source,
        flags=re.DOTALL,
    )
    if not registry_match:
        print("[WARN] Could not find the release-note registry; draft was not changed.")
        return 1

    registry = ast.literal_eval(registry_match.group(1))
    registry[version] = pending
    rendered = "RELEASE_NOTE_KEYS = " + repr(registry)
    source = source[:registry_match.start()] + rendered + source[registry_match.end(1):]
    source = re.sub(
        r"PENDING_RELEASE_NOTE_KEYS\s*=\s*\(.*?\)\n",
        "PENDING_RELEASE_NOTE_KEYS = ()\n",
        source,
        count=1,
        flags=re.DOTALL,
    )
    SOURCE.write_text(source, encoding="utf-8")
    print(f"[OK] Stamped {len(pending)} release-note item(s) for {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
