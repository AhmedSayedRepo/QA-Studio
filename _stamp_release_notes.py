"""Stamp prepared release-note keys with release.bat's selected version.

Two jobs, both writing RELEASE_NOTE_KEYS in release_notes.py:

1. Carry-over. Notes stamped onto a version that was never published (a release
   that aborted after stamping, then re-ran under a newer number) would never be
   shown. Any such orphan between the previous PUBLISHED release and the version
   being released is moved onto the version being released, ahead of its own
   notes. Published versions come from the remote's vX.Y.Z tags; if they can't
   be read, nothing is moved (a warning says so).
2. Stamp the pending draft (PENDING_RELEASE_NOTE_KEYS) onto the version and
   clear it.

Usage: python _stamp_release_notes.py VERSION [--published 3.9.1,3.9.2]
(--published overrides tag discovery; used by tests.)
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import release_notes


SOURCE = Path(__file__).with_name("release_notes.py")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
TAG_RE = re.compile(r"refs/tags/v(\d+\.\d+\.\d+)$")


def _vkey(version):
    return tuple(int(p) for p in version.split("."))


def carry_over(registry, target, published):
    """Return (new_registry, moved_versions).

    Orphans are registry versions that are not published and sit strictly
    between the newest published version below `target` and `target`. Their
    keys are merged (oldest first, de-duplicated) in front of target's own
    keys. With `published` unknown (None) nothing moves."""
    if published is None:
        return dict(registry), []
    tkey = _vkey(target)
    below = [_vkey(v) for v in published if VERSION_RE.fullmatch(v) and _vkey(v) < tkey]
    floor = max(below) if below else (0, 0, 0)
    moved = sorted((v for v in registry
                    if v != target and VERSION_RE.fullmatch(v) and v not in published
                    and floor < _vkey(v) < tkey), key=_vkey)
    if not moved:
        return dict(registry), []
    merged = []
    for v in moved + [target]:
        for key in registry.get(v, ()):
            if key not in merged:
                merged.append(key)
    new = {v: keys for v, keys in registry.items() if v not in moved}
    new[target] = tuple(merged)
    return new, moved


def published_versions():
    """vX.Y.Z tags on origin, or None if they can't be read."""
    try:
        out = subprocess.run(["git", "ls-remote", "--tags", "origin"],
                             capture_output=True, text=True, timeout=30,
                             cwd=str(SOURCE.parent))
    except Exception:
        return None
    if out.returncode != 0:
        return None
    found = set()
    for line in out.stdout.splitlines():
        m = TAG_RE.search(line.strip())
        if m:
            found.add(m.group(1))
    return found


def _parse_args(argv):
    args = [a.strip() for a in argv[1:]]
    published = None
    if "--published" in args:
        i = args.index("--published")
        if i + 1 >= len(args):
            return None, None
        published = {v.strip() for v in args[i + 1].split(",") if v.strip()}
        del args[i:i + 2]
    if len(args) != 1 or not VERSION_RE.fullmatch(args[0]):
        return None, None
    return release_notes.normalize_version(args[0]), published


def main(argv: list[str]) -> int:
    version, published = _parse_args(argv)
    if not version:
        print("Usage: python _stamp_release_notes.py VERSION [--published 3.9.1,3.9.2]")
        return 2
    if published is None:
        published = published_versions()
        if published is None:
            print("[WARN] Could not read published tags from origin; "
                  "notes of skipped versions were not carried over.")

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

    registry, moved = carry_over(registry, version, published)
    for v in moved:
        print(f"[OK] Carried release notes from unpublished {v} over to {version}.")

    pending = tuple(release_notes.PENDING_RELEASE_NOTE_KEYS)
    stamped = 0
    if pending:
        current = list(registry.get(version, ()))
        for key in pending:
            if key not in current:
                current.append(key)
                stamped += 1
        registry[version] = tuple(current)
    else:
        print("[INFO] No prepared release-note draft to stamp.")

    if not moved and not pending:
        return 0

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
    if pending:
        print(f"[OK] Stamped {stamped} release-note item(s) for {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
