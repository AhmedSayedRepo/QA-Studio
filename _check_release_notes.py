"""Validate exact-version notes for release.bat's advisory reminder."""

from __future__ import annotations

import sys
from pathlib import Path

import release_notes
import strings


SUPPORTED_LANGUAGES = ("en", "ar", "fr", "tr", "es", "de", "nl")
HELP_KEYS = (
    "help_whats_new_title",
    "help_whats_new_blurb",
    "help_whats_new_d0",
    "help_whats_new_d1",
    "help_whats_new_p0",
    "help_whats_new_p1",
)


def _current_release_version() -> str:
    """The Help guide has one current-release page, unlike update popups.

    Historical popup notes must remain verifiable after a later release is
    prepared, but they do not own the live Help page's shared strings.
    """
    try:
        return release_notes.normalize_version(
            Path(__file__).with_name("VERSION").read_text(encoding="utf-8"))
    except OSError:
        return ""


def validate(version: str) -> list[str]:
    version = release_notes.normalize_version(version)
    errors: list[str] = []
    note_keys = release_notes.keys_for(version)
    if not note_keys:
        errors.append(f"No popup release notes are registered for {version}.")
        return errors

    verify_help_page = version == _current_release_version()
    for language in SUPPORTED_LANGUAGES:
        values = strings._STRINGS.get(language, {})
        for key in (*note_keys, *(HELP_KEYS if verify_help_page else ())):
            if not str(values.get(key, "")).strip():
                errors.append(f"{language}: missing {key}")
        authored_for = str(values.get("help_whats_new_version", "")).strip()
        if verify_help_page and authored_for != version:
            errors.append(
                f"{language}: Help What's New is authored for "
                f"{authored_for or 'no version'}, not {version}."
            )
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
