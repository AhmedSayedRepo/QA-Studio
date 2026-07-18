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

# Biometric LOGIN-GATE sentinel. A fixed (NOT per-user) entry written under an
# enforce_biometrics storage when the toggle is enabled. Reading it back at
# launch is what actually pops the OS fingerprint/PIN prompt — deliberately
# decoupled from the per-user creds vault, which (a) we don't know the key for
# until AFTER sign-in, and (b) we no longer put behind biometrics at all, so it
# can never be Android-Keystore-invalidated and wiped by toggling the setting.
# The OLD design gated on reading the creds vault itself: at launch that vault
# is the default (empty) slot, so the read returned nothing, never prompted,
# and silently let the user in — exactly the reported "biometrics not working
# for login".
_SENTINEL_KEY = "qa_studio_bio_gate"
_SENTINEL_VAL = "1"

# CORRECT-ARCHITECTURE gate: the flet_local_auth extension (Android
# BiometricPrompt / iOS LocalAuthentication via the official `local_auth`
# plugin). When bundled, it REPLACES the sentinel-storage gate below — auth
# becomes a pure identity check that never persists a key, so it can't be
# Keystore-invalidated or wipe creds (the failure mode the sentinel design
# still technically carries). None until init() attaches it; stays None (→
# sentinel fallback) on desktop or any build without the extension.
_local_auth = None


def _local_auth_available():
    """True only where the flet_local_auth extension actually imports — the
    mobile build that bundles it. Desktop never installs it, so this is False
    there and the whole local_auth path is skipped."""
    try:
        import flet_local_auth  # noqa: F401
        return True
    except Exception:
        return False


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
        # The CREDS vault is NEVER put behind biometrics anymore. Enforcing
        # biometrics on it was the direct cause of the recurring "enabling
        # biometrics wiped my saved credentials" bug: adding
        # setUserAuthenticationRequired(true) invalidates the Android Keystore
        # key the existing ciphertext was written under, so the next read
        # either threw (creds unreadable) or, under reset_on_error, silently
        # wiped them. Biometrics is now a pure LOGIN GATE driven by a separate
        # sentinel entry (see _bio_gate_check), so the creds vault stays plain
        # Keystore-encrypted and ALWAYS readable (reset_on_error=True) — it can
        # never be invalidated or cleared by toggling the setting.
        _storage = fss.SecureStorage(
            android_options=fss.AndroidOptions(
                reset_on_error=True,
                migrate_on_algorithm_change=True,
                enforce_biometrics=False),
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
    # Attach the decoupled local_auth gate if the extension is bundled. This is
    # the preferred authenticator; _bio_gate_check()/apply_biometric_setting()
    # use it instead of the sentinel-storage read whenever it's present.
    global _local_auth
    if _local_auth_available():
        try:
            import flet_local_auth as _fla
            _local_auth = _fla.LocalAuth()
            page.services.append(_local_auth)
            page.update()
        except Exception:
            _local_auth = None
    try:
        page.run_task(_bootstrap)
    except Exception:
        pass
    # When the login gate is on, read the biometric SENTINEL — THIS is what
    # pops the OS fingerprint/PIN prompt at launch. Kept separate from the
    # creds bootstrap above so the prompt fires regardless of whether this
    # device has any creds saved yet (a returning user on a fresh reinstall
    # has a cached Supabase session but an empty creds vault — the old code
    # read that empty vault, never prompted, and silently signed them in).
    if _want_bio:
        try:
            page.run_task(_bio_gate_check)
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
    """Read the per-user CREDS vault (plain Keystore-encrypted, no biometrics).
    The login gate lives entirely in _bio_gate_check() now, so this never
    prompts and never risks wiping creds."""
    global _cache
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
    except Exception:
        if _key == key:
            _cache = _cache or {}
    if _on_ready:
        try:
            _on_ready()
        except Exception:
            pass


async def _bio_gate_check():
    """Launch-time LOGIN GATE: read the biometric sentinel under an
    enforce_biometrics storage. On Android this get() only returns after the
    user passes the native fingerprint/PIN/Face prompt, so a successful read of
    the expected value is proof the gate was cleared. Anything else — nothing
    enrolled (the plugin throws), a cancelled prompt, or a missing sentinel —
    reverts the setting via _revert_bio() so the app can never get stuck unable
    to sign in. Re-fires on_ready when done so main.py's _on_secure_creds_
    ready() can complete the now-authorized silent session restore."""
    global _bio_gate_passed
    # PREFERRED: decoupled local_auth check (no storage, no key to invalidate).
    if _local_auth is not None:
        try:
            ok = bool(await _local_auth.authenticate(
                reason="Unlock QA Studio"))
            if ok:
                _bio_gate_passed = True
            else:
                # Cancelled / no enrollment / lockout — fall through to
                # email+password rather than a stuck launch.
                _revert_bio()
        except Exception:
            _revert_bio()
        if _on_ready:
            try:
                _on_ready()
            except Exception:
                pass
        return
    # FALLBACK (no extension bundled): sentinel-storage read. Requires the
    # FragmentActivity host patch to actually prompt (see build-apk.yml).
    try:
        import flet_secure_storage as fss
        gate = fss.SecureStorage(
            android_options=fss.AndroidOptions(
                reset_on_error=False, migrate_on_algorithm_change=True,
                enforce_biometrics=True),
            ios_options=fss.IOSOptions())
        try:
            _page.services.append(gate)
            _page.update()
        except Exception:
            pass
        val = await gate.get(_SENTINEL_KEY)   # ← native biometric prompt here
        if val == _SENTINEL_VAL:
            _bio_gate_passed = True
        else:
            # Sentinel absent/blank: can't confirm a real check happened
            # (never written, or biometrics got un-enrolled since). Fail safe.
            _revert_bio()
    except Exception:
        _revert_bio()
    if _on_ready:
        try:
            _on_ready()
        except Exception:
            pass


def _revert_bio():
    """Turn the login gate back OFF after a failed/blocked biometric check so
    the user falls through to ordinary email+password sign-in instead of a
    permanently stuck launch. _bio_gate_passed is set True too: the gate no
    longer blocks (there's nothing protecting the vault now), so the normal
    silent restore may proceed; main.py surfaces the revert via
    consume_bio_revert() so the user sees why the toggle turned itself off."""
    global _bio_reverted, _bio_gate_passed
    try:
        import mobile_prefs as _mp
        if _mp.get("require_biometric", False):
            _mp.set("require_biometric", False)
    except Exception:
        pass
    _bio_reverted = True
    _bio_gate_passed = True


def apply_biometric_setting(want_bio, on_done=None):
    """Turn the biometric/PIN LOGIN GATE on or off, effective immediately.

    Enabling writes a small SENTINEL entry under an enforce_biometrics storage
    — the write triggers the OS prompt right now, which both confirms a
    biometric/PIN is actually enrolled (the plugin throws otherwise) and lands
    the entry that _bio_gate_check() reads at every future launch to re-prompt.
    Disabling removes the sentinel. The per-user creds vault is NEVER touched
    either way (it's no longer behind biometrics — see init()), so toggling
    this can never lose saved credentials, which was the whole recurring bug.

    on_done(ok: bool, err: str|None) fires on the event loop; the mobile_prefs
    flag is written ONLY on success, so a cancelled/failed prompt leaves
    everything unchanged. Desktop / no-secure-storage: just persists the pref."""
    if not available() or _page is None:
        try:
            import mobile_prefs as _mp
            _mp.set("require_biometric", bool(want_bio))
        except Exception:
            pass
        if on_done:
            on_done(True, None)
        return

    want_bio = bool(want_bio)

    async def _do():
        global _bio_required, _bio_gate_passed
        # PREFERRED: local_auth. Enabling just prompts once to confirm the user
        # can authenticate (proves enrollment) — nothing is persisted in secure
        # storage, so there's no key to invalidate and creds are never touched.
        # Disabling only flips the pref. This removes the entire wipe-risk class.
        if _local_auth is not None:
            try:
                if want_bio:
                    ok = bool(await _local_auth.authenticate(
                        reason="Confirm to enable biometric unlock"))
                    if not ok:
                        raise RuntimeError("authentication was not completed")
                _bio_required = want_bio
                _bio_gate_passed = True
                try:
                    import mobile_prefs as _mp
                    _mp.set("require_biometric", want_bio)
                except Exception:
                    pass
                if on_done:
                    on_done(True, None)
            except Exception as ex:
                if on_done:
                    on_done(False, str(ex)[:140] or "biometric change failed")
            return
        # FALLBACK (no extension): sentinel-storage round-trip.
        try:
            import flet_secure_storage as fss
            gate = fss.SecureStorage(
                android_options=fss.AndroidOptions(
                    reset_on_error=False, migrate_on_algorithm_change=True,
                    enforce_biometrics=want_bio),
                ios_options=fss.IOSOptions())
            try:
                _page.services.append(gate)
                _page.update()
            except Exception:
                pass
            if want_bio:
                # Prompts biometrics NOW (also proves enrollment), then verifies
                # the sentinel reads back before committing the preference.
                await gate.set(_SENTINEL_KEY, _SENTINEL_VAL)
                back = await gate.get(_SENTINEL_KEY)
                if back != _SENTINEL_VAL:
                    raise RuntimeError("verification read came back empty")
            else:
                # Best-effort remove; the pref flip below is what actually
                # disables the gate, so a failed delete is harmless.
                for _m in ("remove", "delete"):
                    _fn = getattr(gate, _m, None)
                    if _fn:
                        try:
                            await _fn(_SENTINEL_KEY)
                            break
                        except Exception:
                            pass
            _bio_required = want_bio
            _bio_gate_passed = True
            try:
                import mobile_prefs as _mp
                _mp.set("require_biometric", want_bio)
            except Exception:
                pass
            if on_done:
                on_done(True, None)
        except Exception as ex:
            # Nothing committed, pref untouched, creds never involved — the
            # toggle simply didn't take.
            if on_done:
                on_done(False, str(ex)[:140] or "biometric change failed")

    try:
        _page.run_task(_do)
    except Exception as ex:
        if on_done:
            on_done(False, str(ex)[:140])


def rearm_gate():
    """Re-arm the login gate on SIGN-OUT so biometrics guards the next sign-in,
    not just the process launch. main.py's _sign_out() clears the Supabase
    session (so a re-login needs typed credentials anyway) but nothing reset
    THIS launch's already-passed gate — _bio_gate_passed stayed True from the
    initial unlock, so a cached-session silent restore right after logout could
    slip back in with no fresh biometric check. Reported as "biometrics not
    working ... when logging out". Resetting _bio_gate_passed to its armed state
    means bio_gate_passed() reads False again until the user re-verifies, so
    _on_secure_creds_ready()'s gated restore can't auto-sign them back in. No-op
    unless biometrics is actually required (nothing to re-arm otherwise)."""
    global _bio_gate_passed
    if not _bio_required:
        return
    _bio_gate_passed = False


def reprompt(on_result=None):
    """Re-run the biometric prompt MID-SESSION — e.g. when the app is resumed
    after being backgrounded/closed while the user was signed in. Cold starts
    are already gated at launch by init()'s _bio_gate_check; this covers the
    resume case, where __init__ never re-runs. Calls on_result(ok: bool).

    No-op that reports success (stays signed in) when biometrics isn't required
    or there's no local_auth authenticator attached — so the ordinary,
    non-biometric session is never disrupted."""
    if not _bio_required or _local_auth is None or _page is None:
        if on_result:
            on_result(True)
        return

    async def _do():
        ok = True
        try:
            ok = bool(await _local_auth.authenticate(reason="Unlock QA Studio"))
        except Exception:
            ok = False
        if on_result:
            try:
                on_result(ok)
            except Exception:
                pass

    try:
        _page.run_task(_do)
    except Exception:
        if on_result:
            on_result(True)


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
