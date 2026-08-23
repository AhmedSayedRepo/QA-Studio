"""Validate exact-version notes for release.bat's advisory reminder."""

from __future__ import annotations

import sys

import release_notes
import strings


SUPPORTED_LANGUAGES = ("en", "ar", "fr", "tr", "es", "de", "nl")
def validate(version: str) -> list[str]:
    version = release_notes.normalize_version(version)
    errors: list[str] = []
    note_keys = release_notes.exact_keys_for(version)
    if not note_keys:
        errors.append(f"No popup release notes are registered for {version}.")
        return errors

    for language in SUPPORTED_LANGUAGES:
        values = strings._STRINGS.get(language, {})
        for key in note_keys:
            if not str(values.get(key, "")).strip():
                errors.append(f"{language}: missing {key}")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not release_notes.normalize_version(argv[1]):
        print("Usage: python _check_release_notes.py VERSION")
        return 2
    version = release_notes.normalize_version(argv[1])
    errors = validate(version)
    if errors:
        print(f"[ABORT] Release notes for {version} are not ready:")
        for error in errors:
            print(f"  - {error}")
        print("Update release_notes.py and all seven languages in strings.py, then retry.")
        return 1
    print(f"[OK] Release notes for {version} are complete in all seven languages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
