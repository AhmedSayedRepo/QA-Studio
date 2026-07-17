"""secure_store_mobile.py — OS-keychain-backed credential persistence for the
Android/iOS build. This is the mobile secure-storage backend MOBILE_PLAN.md's
Phase 0 originally scoped ("AES key held in OS keychain on mobile") but never
actually built — store.py stayed DPAPI/Fernet/base64-only on every platform,
including mobile, where the minimal build (no `cryptography`, see
build-apk.yml) meant every save silently fell to base64 — not real
encryption. Desktop is completely untouched by this module: it's only ever
imported by store.py, and every call here is a no-op unless
platform_caps.is_mobile() AND the flet_secure_storage package actually
imports (only true on the mobile build).

Backed by flet-secure-storage (https://pypi.org/project/flet-secure-storage),
Flet's own first-party wrapper around Flutter's `flutter_secure_storage`
plugin: genuine Android Keystore / iOS Keychain backed encryption, not a
Python-side cipher store.py has to manage itself.

WHY THIS ISN'T A DROP-IN SYNCHRONOUS REPLACEMENT
flet_secure_storage's get/set/... are all `async def` (Flet's Service
pattern). store.py's save()/load() are called synchronously from ~20 sites
across main.py, many directly inside plain (non-async) on_click/on_change
handlers — which Flet's own dispatcher calls directly, inline, on the
event-loop thread (confirmed against the installed flet package:
base_control.py's event trigger calls a plain sync handler with a bare
`event_handler(e)`, not via an executor). Blocking that thread on an awaited
result would deadlock the whole app. So nothing here ever blocks:
  - the first read (and any later per-user switch) is fire-and-forget via
    page.run_task(...); when it lands, a registered callback re-populates the
    caller's state and re-renders — the same "can't know synchronously, show
    something now, refresh when the real answer lands" shape main.py already
    uses for Supabase session restore (_restore_session_async).
  - save() updates an in-memory cache immediately (so a save() followed by a
    load() within the same session is always consistent — same contract the
    desktop file-backed store already gives) and dispatches the real
    OS-keychain write fire-and-forget right after.
Every dispatch goes through page.run_task — Flet's own sanctioned bridge,
already proven in this codebase (main._open_url) — never a hand-rolled
run_coroutine_threadsafe.

NOT YET VERIFIED ON-DEVICE: this is the first time the mobile build pulls in
a native Flutter plugin (every prior dependency was pure-Python). Treat the
first build+install after this change as a probe, same posture
build-apk.yml already documents for the original first APK build.
"""
import json

_storage = None            # fss.SecureStorage instance, created once by init()
_page = None                # live Page ref
_key = "qa_studio_creds"    # secure-storage key; set_user() makes this per-uid
_cache = None                # None until the first successful read for `_key` lands
_on_ready = None             # callback re-invoked after every (re)bootstrap
_bio_reverted = False        # set True when a biometric-gated read failed and
                              # require_biometric got auto-turned back off —
                              # main.py polls/consumes this to toast the user
_bio_required = False         # snapshot of require_biometric at init() time —
                              # main.py's login-gate check (_on_secure_creds_
                              # ready) uses this to know whether a gate was
                              # even in play for THIS launch
_bio_gate_passed = False      # True once this launch's biometric/PIN check
                              # has actually succeeded (or immediately, if
                              # biometrics wasn't required — nothing to gate)


def available():
    """True only where flet_secure_storage actually imports — i.e. only ever
    on the mobile build; desktop's requirements.txt never includes it."""
    try:
        import flet_secure_storage  # noqa: F401
        return True
    except Exception:
        return False


def init(page, on_ready=None):
    """Call once, early (mobile only) — see main.py's QAStudio.__init__,
    right where store.load() is first called. Registers on_ready (invoked
    every time a (re)bootstrap completes, including later set_user() calls)
    and kicks off the first read."""
    global _storage, _page, _on_ready, _bio_required, _bio_gate_passed
    if not available():
        return
    try:
        import flet_secure_storage as fss
    except Exception:
        return
    _page = page
    _on_ready = on_ready
    try:
        # enforce_biometrics: opt-in via Settings (mobile_prefs.require_biometric,
        # read synchronously below — see mobile_prefs.py). Left False by
        # default: this feature exists so credentials DON'T need re-entering
        # each launch — requiring a fingerprint/PIN prompt on every read
        # would defeat that, and the plugin THROWS on any device with no
        # biometric/PIN enrolled if forced on. Keystore-backed encryption
        # still applies either way; this only gates whether unlocking it
        # additionally needs a biometric/PIN check.
        try:
            import mobile_prefs as _mp
            _want_bio = bool(_mp.get("require_biometric", False))
        except Exception:
            _want_bio = False
        _bio_required = _want_bio
        _bio_gate_passed = not _want_bio   # nothing to gate → treat as passed
        _storage = fss.SecureStorage(
            android_options=fss.AndroidOptions(
                # reset_on_error=True is flet_secure_storage's own recovery
                # path for an Android-Keystore-invalidated encryption key
                # (device lock reset, factory reset, etc.) — but newly
                # ENABLING enforce_biometrics on an already-populated vault
                # is itself one of the documented triggers for exactly that
                # invalidation (Android regenerates the AES key's auth
                # requirements the moment setUserAuthenticationRequired(true)
                # is added, which invalidates ciphertext written under the
                # OLD, non-biometric key). Toggling the Settings switch used
                # to walk straight into reset_on_error silently wiping every
                # stored credential on the very next launch — reported live
                # as "biometrics enabled and clears the setup credentials,
                # next login without asking for biometrics at any stage"
                # (nothing was left to unlock, so the prompt never had
                # anything to guard). Off whenever biometrics is being
                # enforced: a failed decrypt now surfaces as a caught
                # exception in _bootstrap() below (existing OS-keystore data
                # preserved, see the auto-revert handling there) instead of
                # an irreversible wipe.
                reset_on_error=not _want_bio,
                migrate_on_algorithm_change=True,
                enforce_biometrics=_want_bio),
            ios_options=fss.IOSOptions())
    except Exception:
        _storage = None
        return
    # CRITICAL: SecureStorage (like every flet_secure_storage/flet control
    # deriving from Service) does nothing until it's actually attached to
    # the page — Service._invoke_method raises "Control must be added to
    # the page first" if self.page is unset, which only happens once the
    # control is mounted. Constructing it bare (the original bug here) left
    # `page` never set, so the very first get()/set() call below would have
    # raised on a real device — the sandboxed FakePage test harness used to
    # verify this module doesn't model that requirement, which is how this
    # slipped through. Same page.services.append(...) + page.update()
    # pattern this app already uses for page.overlay (see dialogs.py).
    try:
        page.services.append(_storage)
        page.update()
    except Exception:
        _storage = None
        return
    try:
        page.run_task(_bootstrap)
    except Exception:
        pass


def set_user(uid):
    """Mirrors store.set_user(): point at this signed-in user's own slot so
    multiple accounts on one device never share credentials. Re-bootstraps
    for the new key; on_ready fires again once that lands."""
    global _key, _cache
    _key = f"qa_studio_creds_{uid}" if uid else "qa_studio_creds"
    _cache = None
    if _storage is not None and _page is not None:
        try:
            _page.run_task(_bootstrap)
        except Exception:
            pass


async def _bootstrap():
    global _cache, _bio_reverted, _bio_gate_passed
    key = _key
    try:
        raw = await _storage.get(key)
        data = json.loads(raw) if raw else None
        if data is None:
            data = _migrate_legacy_file()
            if data:
                await _storage.set(key, json.dumps(data))
        if _key == key:      # a later set_user() may have moved on already
            _cache = data if data is not None else {}
        if _bio_required:
            # A biometric-gated get() just returned successfully, which on
            # Android only happens after the user actually passes the native
            # fingerprint/PIN/Face prompt — this is the real "login gate"
            # signal main.py's _on_secure_creds_ready() waits on before it's
            # willing to silently restore a cached Supabase session.
            _bio_gate_passed = True
    except Exception:
        if _key == key:
            _cache = _cache or {}
        # A biometric-gated read can fail for reasons that will keep
        # failing on every future launch too — no biometric/PIN actually
        # enrolled on this device (flet_secure_storage throws in that case
        # per its own docs), or the Keystore key invalidation described in
        # init()'s AndroidOptions comment. Rather than leave the app stuck
        # silently retrying a broken read forever, auto-revert the
        # preference so the next launch goes back to a working, unprotected
        # read — main.py surfaces this via consume_bio_revert() so the user
        # actually sees why the toggle turned itself back off.
        try:
            import mobile_prefs as _mp
            if _mp.get("require_biometric", False):
                _mp.set("require_biometric", False)
                _bio_reverted = True
        except Exception:
            pass
    if _on_ready:
        try:
            _on_ready()
        except Exception:
            pass


def consume_bio_revert():
    """One-shot flag: True exactly once, the first time main.py checks after
    a biometric-gated read failed and the preference got auto-reverted.
    Calling this clears it, so a later unrelated re-render doesn't re-toast
    the same event."""
    global _bio_reverted
    was = _bio_reverted
    _bio_reverted = False
    return was


def bio_required():
    """True if 'Require biometric/PIN unlock' was on when init() ran THIS
    launch — a snapshot, not a live re-read, so it can't change mid-session
    out from under the login-gate check that consumes it."""
    return _bio_required


def bio_gate_passed():
    """True once this launch's biometric/PIN check has actually succeeded —
    or immediately, if biometrics wasn't required (nothing to gate). Not
    reset on set_user()'s later per-account re-bootstraps: a user who has
    already unlocked the device once this launch shouldn't be re-prompted
    just for switching accounts (biometrics protects the DEVICE'S vault
    access, not each individual account's own slot within it)."""
    return _bio_gate_passed


def _migrate_legacy_file():
    """One-time pickup of whatever store.py's file-based fallback already
    saved before this module existed — reads it via store's own decrypt
    logic, then deletes that file so a weaker-security copy doesn't keep
    sitting on disk once the keychain-backed copy exists."""
    try:
        import store as _store, os
        d = _store._load_from_file()
        if d.get("pat") or d.get("gmail") or d.get("keys"):
            try:
                os.remove(_store.CRED_FILE)
            except Exception:
                pass
            return d
    except Exception:
        pass
    return None


def load():
    """Best-effort synchronous read: the real cached value once a bootstrap
    for the CURRENT key has landed, else None (caller falls back to its own
    default/legacy path). Never blocks."""
    return _cache


def save(d):
    """Update the cache immediately, then fire off the real write. Never
    blocks. Returns False (no-op) if init() hasn't run / isn't available —
    caller (store.py) falls back to its existing file-based path."""
    global _cache
    if _storage is None or _page is None:
        return False
    _cache = dict(d)
    try:
        _page.run_task(_storage.set, _key, json.dumps(d))
    except Exception:
        pass
    return True
