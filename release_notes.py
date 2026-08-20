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
    "3.7.9": (
        "upd_note_379_openai",
        "upd_note_379_performance",
    ),
    "3.8.0": (
        "upd_note_380_handoff",
        "upd_note_380_setup",
    ),
    "3.8.2": (
        "upd_note_381_story_picker",
        "upd_note_381_activity",
    ),
}

# Add the keys for the next release here while implementing its features.
# release.bat stamps this draft with the version entered at its prompt, so
# release-note text never has to guess a version number in advance.
PENDING_RELEASE_NOTE_KEYS = ()


def normalize_version(version):
    return str(version or "").strip().lower().removeprefix("v")


def keys_for(version):
    """Return only notes authored for *version*; never use a fallback."""
    return RELEASE_NOTE_KEYS.get(normalize_version(version), ())
