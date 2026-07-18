# QA Studio — Biometrics, Keyboard & Backdrop Fix Plan

_Concrete diffs and root-cause plan for the three still-open mobile issues._
_Written 2026-07-18 after tracing `secure_store_mobile.py`, `main.py`, `login.py`, and `.github/workflows/build-apk.yml`._

---

## TL;DR

The biometric feature has **never been a Python bug** — the Python logic in `secure_store_mobile.py` is sound. It fails because the **APK is built without the three Android prerequisites a biometric prompt structurally requires**. No rebuild of the current code will make the prompt appear. Fix the build first; optionally move to the correct `local_auth` architecture second.

| # | Issue | Root cause | Fix layer |
|---|-------|-----------|-----------|
| 1a | No biometric prompt at login | Host `MainActivity` is `FlutterActivity`, not `FlutterFragmentActivity` | **Build (CI)** |
| 1b | No biometric prompt at login | `USE_BIOMETRIC` permission missing from manifest | **Build (pyproject)** |
| 1c | `enforce_biometrics=True` throws / no-ops | `minSdk` not pinned ≥ 28 | **Build (pyproject)** |
| 1d | Recurring "biometrics wiped my creds" | Auth bound to storage key (Keystore invalidation) | **Architecture** |
| 1e | Logout → re-login skips biometrics | `_sign_out` never re-arms the gate | **Python** |
| 2 | Password field won't reopen keyboard | `GestureDetector.on_tap` loses the Flutter gesture arena to the `TextField` | **Python** |
| 3 | Backdrop nearly invisible on phone | Mobile `contain` fit shrinks the landscape art to a hidden center band | **Python** |

---

## Issue 1 — Biometrics

### Why it can't work today

`secure_store_mobile._bio_gate_check()` calls `gate.get(sentinel)` on a `SecureStorage` built with `enforce_biometrics=True`, expecting that read to pop the OS fingerprint/Face prompt. For that prompt to appear on Android, **all three** of these must be true — and none are set by `build-apk.yml`:

1. **The host Activity must extend `FlutterFragmentActivity`.** Android's `BiometricPrompt` attaches to a `FragmentManager`; a plain `FlutterActivity` has none. Flet's generated `MainActivity` extends `FlutterActivity` by default. Result: the plugin's biometric path either throws (→ your `_revert_bio()` silently turns the toggle back off) or returns nothing → user is signed straight in. **This is the primary blocker.**
2. **`android.permission.USE_BIOMETRIC`** must be in the manifest. The build passes no permissions.
3. **`minSdk ≥ 28`.** `enforce_biometrics=True` requires API 28+; Flet's default is lower and isn't pinned.

### Build fixes

Flet 0.85 reads build config from `pyproject.toml` (`[tool.flet.*]`). Create/extend it in the staged mobile dir. Since `build-apk.yml` synthesizes the mobile bundle in `/tmp/mobile`, add a step that writes this **before** the `flet build apk` step.

**a) Permissions + minSdk — `pyproject.toml` (clean, fully supported):**

```toml
[tool.flet.android.permission]
"android.permission.USE_BIOMETRIC" = true
"android.permission.USE_FINGERPRINT" = true   # pre-API-28 fallback prompt

[tool.flet.android]
min_sdk_version = 28
```

Add to `build-apk.yml`, in the "stage the app" step (right after the `requirements.txt` printf):

```yaml
          # Biometric prompt prerequisites (see BIOMETRICS_FIX_PLAN.md):
          # USE_BIOMETRIC permission + minSdk 28 for enforce_biometrics.
          cat >> /tmp/mobile/pyproject.toml <<'TOML'

          [tool.flet.android.permission]
          "android.permission.USE_BIOMETRIC" = true
          "android.permission.USE_FINGERPRINT" = true

          [tool.flet.android]
          min_sdk_version = 28
          TOML
```

> If `flet build` for 0.85.3 doesn't yet generate a `pyproject.toml` in the staged dir, create it there in full (project name/org already come from the `--project`/`--org` CLI flags, which override the file). Verify with `--verbose` that the generated `AndroidManifest.xml` contains `<uses-permission android:name="android.permission.USE_BIOMETRIC"/>`.

**b) FragmentActivity — the hard one.** Flet does **not** expose `MainActivity` for editing (tracked upstream as flet-dev/flet#6550, "Access to AndroidManifest.xml for more complex entries"). `flet build apk` generates the Flutter project and runs Gradle atomically, so there's no clean seam to drop in a `MainActivity.kt`. Two options:

- **Option A (recommended long-term): don't rely on storage-bound biometrics at all** — use `local_auth` (see "Correct architecture" below). `local_auth` *also* needs `FlutterFragmentActivity`, so this doesn't dodge the requirement, but it's the right dependency to invest the effort in.
- **Option B (patch step in CI):** split the build so you can patch the generated activity between generation and Gradle. `flet build apk` supports `--flutter-args`, but not a pre-Gradle hook. The practical patch is to run `flet build` once (it will produce the Flutter project under `build/flutter/`), `sed`-replace `FlutterActivity` → `FlutterFragmentActivity` in the generated `MainActivity.kt`, then re-run the Gradle assemble. This is brittle across Flet versions — pin Flet and re-verify on every bump.

Sketch of the patch (verify the generated path against your `--verbose` output):

```yaml
      - name: Patch MainActivity to FlutterFragmentActivity
        working-directory: /tmp/mobile
        run: |
          MA=$(find build -name 'MainActivity.kt' | head -n1)
          echo "Patching $MA"
          sed -i 's/import io.flutter.embedding.android.FlutterActivity/import io.flutter.embedding.android.FlutterFragmentActivity/' "$MA"
          sed -i 's/: FlutterActivity/: FlutterFragmentActivity/' "$MA"
          grep -n 'FlutterFragmentActivity' "$MA"   # fail loudly if the pattern moved
```

> Because `flet build apk` compiles in one pass, you likely need to invoke the underlying Flutter generate/build in two phases (or apply the patch via a Flet build template if 0.85.3 supports `--template`). Treat this as the item to prototype first — it's the difference between the prompt appearing or not.

### Correct architecture — decouple auth from storage

The current design gates login on a **storage read**. That's the source of the credential-wipe bug: turning on `enforce_biometrics` adds `setUserAuthenticationRequired(true)` to the Keystore key, invalidating any ciphertext written under the old key.

Every well-built mobile app separates the two concerns:

| Concern | Current (wrong) | Correct |
|--------|-----------------|---------|
| **Authentication** ("prove it's you") | side effect of `secure_storage.get(sentinel)` with `enforce_biometrics=True` | explicit `local_auth.authenticate()` — the official Flutter-team plugin; does nothing but return `true`/`false` |
| **Storage** (creds at rest) | same plugin, biometric-bound → fragile | plain Keystore/Keychain, `enforce_biometrics=False`, **always readable** |
| **Wipe risk** | high (key invalidation) | none — auth never touches the vault |
| **Prompt reliability** | never fires (activity/perm/minSdk) | fires |

**Target flow:**

```
launch / logout→login / unlock-required action
        │
        ▼
   local_auth.authenticate()   ──fail/cancel──▶  stay on email+password screen
        │ success
        ▼
   read creds from plain secure storage (always decryptable)
        │
        ▼
   acquire_silent() → sign in
```

`secure_store_mobile.py` already got *halfway* here (it split the sentinel gate from the creds vault). The remaining change: replace the sentinel `get()`/`set()` with a real `local_auth` call, and set the creds vault to plain encryption permanently (it already is: `enforce_biometrics=False`). This removes `_bio_gate_check`, `apply_biometric_setting`'s sentinel round-trip, and every wipe-risk code path.

Flet 0.85.3 has no first-party `local_auth` wrapper, so this needs a small Flutter plugin dependency added to the mobile `requirements.txt` (same posture as `flet-secure-storage`) plus a thin Python service wrapper. If that's more than you want to take on now, **Option B build patch + the current sentinel design will work** — the three build fixes are what unblock it either way.

### 1e — Logout doesn't re-arm the gate (pure Python, do this regardless)

`main.py:846 _sign_out()` sets `self.user = None` and re-renders, but never re-invokes the biometric check. The gate only ever runs once, at `init()` via `_bio_gate_check`. So after logout, signing back in within the same session skips biometrics entirely. When you move to `local_auth`, call `authenticate()` from `_sign_out`'s re-login path (or gate the next `acquire_silent()` on it). With the current design, set a flag on logout that forces `_biometric_login_gate_active()` to re-run the sentinel read before the next silent restore.

---

## Issue 2 — Password field won't reopen the keyboard

**Current code** (`login.py:260-283`): wraps each `TextField` in `ft.GestureDetector(content=tf, on_tap=_refocus)`, where `_refocus` does `blur()` → 50 ms → `focus()`.

**Why it's unreliable:** in Flutter's gesture arena, a `TextField` (EditableText) has its own tap recognizer and **almost always wins the tap** over a parent `GestureDetector` — so the parent's `on_tap` frequently never fires. Your own roadmap entry admits this ("depends on GestureDetector winning the tap over the TextField in Flet 0.85.3"). Scrolling the field first biases the arena even further toward the field/scrollable, which is exactly the reported "scroll, dismiss keyboard, then taps do nothing."

**Fix:** use **`on_tap_down`** instead of `on_tap`. Down events are dispatched to the detector *before* the arena resolves, so they fire even when the `TextField` later claims the gesture. Keep the blur→focus cycle.

```python
# login.py, in _field(), replace the GestureDetector wrap:
if platform_caps.is_mobile():
    def _refocus(e, _tf=tf):
        async def _cycle():
            try: _tf.blur()
            except Exception: pass
            try:
                import asyncio; await asyncio.sleep(0.05)
            except Exception: pass
            try: _tf.focus()
            except Exception: pass
        try:
            app.page.run_task(_cycle)
        except Exception:
            try: _tf.focus()
            except Exception: pass
    # on_tap_down fires pre-arena, so it lands even when the TextField
    # wins the tap for cursor placement (the reason on_tap was flaky).
    field_ctl = ft.GestureDetector(content=tf, on_tap_down=_refocus)
```

Fully robust route (if `on_tap_down` still misses on-device): a tiny Flutter-side call to `SystemChannels.textInput.invokeMethod('TextInput.show')` on tap — but try `on_tap_down` first; it needs no plugin. **Verify on a real device.**

---

## Issue 3 — Backdrop nearly invisible on mobile

**Current code** (`login.py:176`): mobile uses `contain` fit on the 2160×1215 (16:9 landscape) art. On a tall portrait phone, `contain` shrinks the whole image into a short horizontal band in the vertical center; the dark base fills top/bottom, and the frosted card sits directly over the band. Net result (matches the screenshot): mostly dark, with only thin circuit slivers at the far left/right edges. The immersive art is effectively lost.

This was a deliberate flip from `cover` to kill a "too zoomed in" complaint (roadmap #37), but the cost is an empty-looking backdrop. On a phone, `cover` fills the screen and the crop is acceptable — the card covers the cropped center anyway.

**Fix (choose one):**

- **Simplest — use `cover` on mobile too.** In `_bg()`, drop the mobile-only `contain` branch:
  ```python
  # login.py _bg(): was  _fit = (_contain if (_mob_bg and _contain) else _cover)
  _fit = _cover
  _fit_str = "cover"
  ```
  Then bump the mobile layer scale back toward 1.3 (login.py:592) so parallax headroom is preserved, and retune the tilt strengths (login.py:694) since `cover` no longer leaves vertical bands to move within.
- **Best-looking — ship a portrait crop of the art** (e.g. 1080×2160 centered on the circuit corridor) as a mobile-specific asset, keep `cover`. No dark bands, no heavy crop.

Either way the backdrop will actually read on the phone instead of showing as a dark frame around the card.

---

## Suggested order

1. **Build fixes (1a/1b/1c)** — nothing else matters until the prompt can appear. Prototype the FragmentActivity patch first; it's the make-or-break item.
2. **Logout re-arm (1e)**, **keyboard (2)**, **backdrop (3)** — small, self-contained Python changes; ship alongside.
3. **Correct architecture (1d)** — migrate to `local_auth` when you have room; it retires the whole wipe-risk class permanently.

## Verification checklist (on-device — sandbox can't exercise native prompts)

- [ ] Generated `AndroidManifest.xml` contains `USE_BIOMETRIC` and `minSdkVersion="28"`.
- [ ] Decompiled/patched `MainActivity` extends `FlutterFragmentActivity`.
- [ ] Enable "Require biometric/PIN unlock" → prompt appears immediately.
- [ ] Fully close + reopen → prompt appears at launch; creds retained.
- [ ] Log out → log back in → biometric challenge fires.
- [ ] Cancel the prompt → falls through to email+password (never silently in).
- [ ] Password field: scroll, dismiss keyboard, tap field → keyboard reopens first tap.
- [ ] Login backdrop fills the screen (no dark frame).

---

## Sources

- [flutter_secure_storage — pub.dev](https://pub.dev/packages/flutter_secure_storage)
- [Flet — SecureStorage service](https://flet.dev/docs/services/securestorage/)
- [Flet — Packaging app for Android (permissions, minSdk)](https://flet.dev/docs/publish/android/)
- [Flet issue #6550 — Access to AndroidManifest.xml for complex entries](https://github.com/flet-dev/flet/issues/6550)
- [FlutterFragmentActivity — Flutter API](https://api.flutter.dev/javadoc/io/flutter/embedding/android/FlutterFragmentActivity.html)
- [local_auth — official Flutter biometric plugin (Flutter Gems)](https://fluttergems.dev/biometric-local-auth/)
