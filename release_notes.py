"""Version-specific release-note metadata.

Authored notes are version-specific. When an exact entry has not been prepared,
the updater uses a short localized release summary rather than showing stale
highlights or omitting the update popup completely.
"""


RELEASE_NOTE_KEYS = {'3.7.1': ('upd_note_tenants', 'upd_note_access', 'upd_note_audit', 'upd_note_experience'), '3.7.9': ('upd_note_379_openai', 'upd_note_379_performance'), '3.8.0': ('upd_note_380_handoff', 'upd_note_380_setup'), '3.8.2': ('upd_note_381_story_picker', 'upd_note_381_activity'), '3.8.4': ('upd_note_org_help', 'upd_note_invite_email', 'upd_note_saved_accounts'), '3.8.5': ('upd_note_385_identity_editor', 'upd_note_385_identity_help'), '3.8.7': ('upd_note_audit_filters', 'upd_note_org_manager_audit', 'upd_note_usage_cost_coverage'), '3.8.8': ('upd_note_audit_filters', 'upd_note_org_manager_audit', 'upd_note_usage_cost_coverage', 'upd_note_release_notes_after_restart'), '3.8.9': ('upd_note_389_mobile_layout',), '3.8.10': ('upd_note_3810_mobile_followups',), '3.9.1': ('upd_note_391_landscape_orientation',), '3.9.4': ('upd_note_failure_analysis', 'upd_note_automation_waits'), '3.9.5': ('upd_note_ai_model_refresh',), '3.9.6': ('upd_note_verified_locators', 'upd_note_regenerate', 'upd_note_automation_cases', 'upd_note_automation_filter_tips', 'upd_note_js_ready_to_run', 'upd_note_automation_fixes'), '3.9.7': ('upd_note_browser_context', 'upd_note_copy_ai_prompt', 'upd_note_more_field_types', 'upd_note_run_report', 'upd_note_reuse_verified', 'upd_note_optional_account', 'upd_note_branded_report', 'upd_note_run_fixes'), '3.9.8': ('upd_note_components', 'upd_note_existing_choice', 'upd_note_batch3', 'upd_note_run_from_ide', 'upd_note_report_steps', 'upd_note_data_driven', 'upd_note_detailed_report', 'upd_note_retry_site_down', 'upd_note_398_fixes'), '3.9.9': ('upd_note_components', 'upd_note_existing_choice', 'upd_note_batch3', 'upd_note_run_from_ide', 'upd_note_report_steps', 'upd_note_data_driven', 'upd_note_detailed_report', 'upd_note_retry_site_down', 'upd_note_398_fixes')}

# Add the keys for the next release here while implementing its features.
# release.bat stamps this draft with the version entered at its prompt, so
# release-note text never has to guess a version number in advance.
PENDING_RELEASE_NOTE_KEYS = ()

FALLBACK_RELEASE_NOTE_KEYS = ("upd_note_release_general",)


def normalize_version(version):
    return str(version or "").strip().lower().removeprefix("v")


def exact_keys_for(version):
    """Return notes authored specifically for *version*."""
    return RELEASE_NOTE_KEYS.get(normalize_version(version), ())


def keys_for(version):
    """Return exact notes, or the localized no-stale-notes release summary."""
    return exact_keys_for(version) or FALLBACK_RELEASE_NOTE_KEYS
