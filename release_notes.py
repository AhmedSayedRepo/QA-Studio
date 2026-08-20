"""Version-specific release-note metadata.

Release notes must never fall back to another version.  When a release has no
authored entry, the updater still reports a successful update but omits the
"What's new" card instead of presenting stale highlights.
"""


RELEASE_NOTE_KEYS = {
    "3.7.1": (
        "upd_note_tenants",
        "upd_note_access",
        "upd_note_audit",
        "upd_note_experience",
    ),
    "3.7.8": (
        "upd_note_378_update",
        "upd_note_378_setup",
    ),
}


def normalize_version(version):
    return str(version or "").strip().lower().removeprefix("v")


def keys_for(version):
    """Return only notes authored for *version*; never use a fallback."""
    return RELEASE_NOTE_KEYS.get(normalize_version(version), ())

