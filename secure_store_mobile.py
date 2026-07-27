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
_bio_gate_done = False        # True once this launch's biometric/PIN check has
                              # RESOLVED — passed, failed, or cancelled. Distinct
                              # from _bio_gate_passed: lets the caller tell "still
                              # waiting on the prompt" from "resolved, not passed",
                              # so a branded 'Signing you in…' hold can clear the
                              # instant the gate settles instead of guessing.

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
    global _storage, _page, _on_ready, _bio_required, _bio_gate_passed, _bio_gate_done
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
        _bio_gate_done = not _want_bio     # nothing to gate → already resolved
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
    # Read the session from its own FIXED key, independently of the per-user
    # creds bootstrap above — set_user() must never move or hide the session
    # (that slot mismatch is what let a logged-out user come back signed in).
    try:
        page.run_task(_bootstrap_session)
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
                # Await the vault write, and only THEN drop the legacy file —
                # never the other way round (see _migrate_legacy_file).
                await _storage.set(key, json.dumps(data))
                _delete_legacy_file()
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
    """Launch-time LOGIN GATE — the ONLY place biometrics is challenged.

    Prefers local_auth (a real biometric prompt); falls back to the legacy
    sentinel read only on builds without the extension. Re-fires on_ready when
    done so main.py's _on_secure_creds_ready() can complete the now-authorized
    silent session restore.

    Failing or cancelling NEVER disables the feature and NEVER grants access —
    it just leaves the gate closed, so the user signs in with email+password
    this once and biometrics still guards the next launch."""
    global _bio_gate_passed, _bio_gate_done
    # INVARIANT (learned the hard way): a failed/cancelled gate must leave
    # _bio_gate_passed FALSE and the preference ON. The old code called
    # _revert_bio() here, which BOTH disabled biometrics permanently AND set
    # _bio_gate_passed = True — so the app signed in with no biometric check at
    # all and never prompted again ("closing the entire app and relaunching
    # doesn't trigger bio"). The user is never locked out by failing here,
    # because the email+password screen is always the fallback.
    #
    # PREFERRED: decoupled local_auth check (no storage, no key to invalidate).
    if _local_auth is not None:
        try:
            ok = await _authenticate("Unlock QA Studio")
        except Exception:
            ok = False
        _bio_gate_passed = bool(ok)
        _bio_gate_done = True
        if _on_ready:
            try:
                _on_ready()
            except Exception:
                pass
        return
    # local_auth is the configured mechanism but isn't attached this launch
    # (import or page.services attach failed). Do NOT fall through to the
    # sentinel read: enabling via local_auth never writes a sentinel, so that
    # read would find nothing and look like a failed check — which is exactly
    # what silently disabled biometrics before. Leave the gate closed and let
    # the user sign in with credentials this once; biometrics still works on a
    # launch where the extension attaches.
    if _local_auth_available():
        _bio_gate_passed = False
        _bio_gate_done = True
        if _on_ready:
            try:
                _on_ready()
            except Exception:
                pass
        return
    # LEGACY FALLBACK: builds without the extension, where the sentinel IS the
    # mechanism that enabled biometrics (apply_biometric_setting wrote it).
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
        _bio_gate_passed = (val == _SENTINEL_VAL)
    except Exception:
        _bio_gate_passed = False
    _bio_gate_done = True
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
    global _bio_reverted, _bio_gate_passed, _bio_gate_done
    try:
        import mobile_prefs as _mp
        if _mp.get("require_biometric", False):
            _mp.set("require_biometric", False)
    except Exception:
        pass
    _bio_reverted = True
    _bio_gate_passed = True
    _bio_gate_done = True


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
                    ok = await _authenticate(
                        "Confirm to enable biometric unlock")
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


_prompt_active = False    # True while a native biometric prompt is on screen
_last_prompt_ts = 0.0     # monotonic time the last prompt finished


def prompt_active():
    """True while THIS app is showing a native biometric prompt.

    CRITICAL for the resume re-lock: showing the prompt pushes the app through
    its own pause → resume lifecycle cycle, which the re-lock would otherwise
    mistake for the user backgrounding the app — firing another prompt, which
    cycles again, forever. Reported live as "the bio toggle triggers
    fingerprint, after verify keeps triggering fingerprint". Lifecycle handlers
    consult this so they never react to our own prompt."""
    return _prompt_active


def seconds_since_prompt():
    """Seconds since the last native prompt finished (a huge number if none has
    run yet). Gives resume handlers a grace window, since the lifecycle events
    that TRAIL a just-finished prompt can arrive after _prompt_active clears."""
    import time as _t
    if not _last_prompt_ts:
        return 1e9
    return max(0.0, _t.monotonic() - _last_prompt_ts)


async def _authenticate(reason):
    """Single funnel for EVERY native prompt, so _prompt_active /
    _last_prompt_ts are maintained no matter which caller triggered it."""
    global _prompt_active, _last_prompt_ts
    import time as _t
    _prompt_active = True
    try:
        return bool(await _local_auth.authenticate(reason=reason))
    finally:
        _prompt_active = False
        _last_prompt_ts = _t.monotonic()


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
            ok = await _authenticate("Unlock QA Studio")
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


def bio_gate_done():
    """True once this launch's biometric/PIN check has RESOLVED — whether it
    passed, failed, or was cancelled. Unlike bio_gate_passed(), this lets the
    caller distinguish "still waiting on the native prompt" (show a branded
    hold) from "resolved but not passed" (fall through to email+password).
    Always True immediately when biometrics wasn't required."""
    return _bio_gate_done


# The session lives under its OWN FIXED storage key — never inside the
# per-user creds vault, and never keyed by uid.
#
# ROOT CAUSE this fixes ("log out, close the app, relaunch → still signed in"):
# the session used to be embedded in the per-user creds dict, whose storage key
# CHANGES with set_user(). auth.sign_in() saves the session BEFORE
# _switch_user_creds() runs (login.py), so it landed in the DEFAULT slot
# (qa_studio_creds); auth.sign_out() clears it while the user is still signed
# in, so it cleared the PER-USER slot (qa_studio_creds_<uid>). Different slots —
# the default slot's copy survived logout, and relaunch reads exactly that slot
# first and restored it.
#
# Same reasoning as the biometric sentinel above: a value you need in order to
# know WHO the user is cannot itself be stored per-user.
_SESSION_STORE_KEY = "qa_studio_session"
_session_cache = None     # dict | None — the session as last read/written
_session_ready = False    # True once the first read of the fixed key has landed


def session_available():
    """True once the fixed session key's first read has landed, i.e.
    load_session() can give a trustworthy answer. Independent of the per-user
    creds bootstrap — set_user() must never affect the session."""
    return _storage is not None and _session_ready


async def _bootstrap_session():
    """Read the session from its FIXED key. Runs once per launch, alongside (and
    independently of) the per-user creds bootstrap."""
    global _session_cache, _session_ready
    try:
        raw = await _storage.get(_SESSION_STORE_KEY)
        _session_cache = json.loads(raw) if raw else None
    except Exception:
        _session_cache = None
    _session_ready = True
    if _on_ready:
        try:
            _on_ready()
        except Exception:
            pass


def save_session(data):
    """Persist (or clear, when data is None) the Supabase session — access +
    REFRESH token — under the FIXED Keystore key.

    WHY the vault at all: auth_supabase's own file is written via
    store._encrypt(), which on the mobile build has NEITHER DPAPI
    (Windows-only) NOR Fernet (`cryptography` is excluded from the APK), so it
    degraded to base64. The diagnostics log confirmed it live — 22x "DPAPI and
    Fernet both unavailable ... NOT encrypted". A refresh token in base64 is
    effectively plaintext, which matters all the more because signing out
    deliberately KEEPS that token so biometrics can restore the session.

    WHY a fixed key: see _SESSION_STORE_KEY. Storing it in the per-user creds
    slot meant sign-in wrote it to one slot and sign-out cleared another, so
    logging out then relaunching signed the user straight back in.

    A clear does NOT require the cache to be ready — it writes straight through
    to storage, so a sign-out can never be lost to bootstrap timing.

    DURABILITY: this used to `_page.run_task(...)` the write and return True
    IMMEDIATELY — fire-and-forget. When a restore refreshed the session, GoTrue
    ROTATED the refresh token, but if the process was backgrounded/killed before
    that scheduled write flushed, the Keystore kept the OLD token → next launch
    presented the rotated-away token → 400 refresh_token_not_found → session
    wiped → sign-in screen. We now AWAIT the write (bounded) and return its real
    result, so a rotated token can't be silently dropped and callers only delete
    the legacy fallback once the vault write has actually landed."""
    global _session_cache
    import threading as _th
    if _storage is None or _page is None:
        return False
    _session_cache = data
    done = _th.Event()
    ok = {"v": False}

    async def _writer():
        try:
            if data is None:
                _fn = None
                for _m in ("remove", "delete"):
                    _fn = getattr(_storage, _m, None)
                    if _fn:
                        break
                if _fn:
                    await _fn(_SESSION_STORE_KEY)
                else:
                    # No remove API — overwrite with empty, reads back falsy.
                    await _storage.set(_SESSION_STORE_KEY, "")
            else:
                await _storage.set(_SESSION_STORE_KEY, json.dumps(data))
            ok["v"] = True
        except Exception:
            ok["v"] = False
        finally:
            done.set()

    try:
        _page.run_task(_writer)
    except Exception:
        return False
    # Block the CALLING thread (a background/handler thread — never the page's
    # asyncio loop thread, which is what runs _writer) until the write confirms.
    # Timeout is a backstop so a stuck vault can't freeze auth forever.
    confirmed = done.wait(timeout=6.0)
    if not (confirmed and ok["v"]):
        try:
            import diag_log
            diag_log.log_warn("secure_store_mobile.save_session",
                              f"vault write did not confirm (confirmed={confirmed})")
        except Exception:
            pass
    return bool(confirmed and ok["v"])


def load_session():
    """The cached session dict, or None. Only meaningful once the fixed-key
    read has landed (see session_available)."""
    return _session_cache or None


def _delete_legacy_file():
    """Drop the legacy file-backed copy — called ONLY after the Keystore write
    has been awaited and confirmed, so the credentials always exist in at least
    one place at every instant."""
    try:
        import store as _store, os
        os.remove(_store.CRED_FILE)
    except Exception:
        pass


def _migrate_legacy_file():
    """One-time pickup of whatever store.py's file-based fallback already
    saved before this module existed — reads it via store's own decrypt
    logic, then deletes that file so a weaker-security copy doesn't keep
    sitting on disk once the keychain-backed copy exists."""
    try:
        import store as _store
        d = _store._load_from_file()
        if d.get("pat") or d.get("gmail") or d.get("keys"):
            # DO NOT delete the source here. This used to os.remove() the file
            # and THEN hand the data back for an async vault write — so if the
            # process died in between, the credentials were gone from both
            # places. Process death right after a read is not hypothetical:
            # installing an app UPDATE kills the old process, which is exactly
            # when users reported "setup credentials wiped after update".
            # _bootstrap now deletes it only once the vault write has been
            # awaited and confirmed (see _delete_legacy_file).
            return d
    except Exception:
        pass
    return None


def load():
    """Best-effort synchronous read: the real cached value once a bootstrap
    for the CURRENT key has landed, else None (caller falls back to its own
    default/legacy path). Never blocks."""
    return _cache


def _is_empty_creds(d):
    """True when this payload carries NO actual secret. Used to recognise the
    'we haven't loaded anything yet' state so it can't be mistaken for 'the
    user cleared everything'."""
    if not isinstance(d, dict):
        return True
    return not (d.get("pat") or d.get("gmail") or d.get("gmail_app_pass")
                or (d.get("keys") or {}))


def save(d):
    """Update the cache immediately, then fire off the real write. Never
    blocks. Returns False (no-op) if init() hasn't run / isn't available —
    caller (store.py) falls back to its existing file-based path.

    SAFETY GUARD — this is how credentials kept getting 'wiped after an
    update': the vault is keyed per-user, so a launch that starts signed out
    (or whose bootstrap hasn't landed) reads an EMPTY slot into self.creds.
    Anything that then called store.save(self.creds) — a theme change, a
    provider pick, an onboarding flag — persisted that empty dict straight over
    the real credentials. One incidental save was enough to destroy them
    permanently.

    An empty payload landing on top of populated storage is never a legitimate
    intent, so refuse it and log it. Genuinely clearing credentials happens
    field-by-field (each writes a populated dict minus one value), which this
    never blocks."""
    global _cache
    if _storage is None or _page is None:
        return False
    # NEVER persist an empty payload. Full stop — no comparison against the
    # in-memory cache.
    #
    # The previous version of this guard only blocked "empty over POPULATED
    # _cache", which left the actual hole wide open: on a fresh process the
    # async _bootstrap has not landed yet, so _cache is None/{} — the guard saw
    # "empty over empty", allowed it, and the empty dict went straight over the
    # POPULATED vault on disk. The in-memory cache says nothing about what is
    # stored, so it was the wrong thing to compare against. Confirmed by a
    # device log where credentials were wiped and no save_blocked line was ever
    # written, across 30 app launches in 90 minutes — plenty of windows where a
    # save fired before the first read completed.
    #
    # Storing "no credentials" has no legitimate purpose: clearing one field
    # still writes a populated dict minus that value, which this never blocks.
    if _is_empty_creds(d):
        try:
            import diag_log
            diag_log.log_warn(
                "secure_store.save_blocked",
                f"refused to persist an EMPTY credentials payload (key={_key}, "
                f"cache_empty={_is_empty_creds(_cache)}) — this is the "
                "'credentials wiped' bug")
        except Exception:
            pass
        return True      # report success: the stored value remains the truth
    _cache = dict(d)
    try:
        _page.run_task(_storage.set, _key, json.dumps(d))
    except Exception:
        pass
    return True
