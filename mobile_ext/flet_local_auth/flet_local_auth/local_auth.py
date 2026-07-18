"""Python side of the flet_local_auth extension — a Flet Service that bridges
to the Flutter `local_auth` plugin. Modeled on flet_secure_storage's
SecureStorage service (same Flet 0.85 `@control` + Service + _invoke_method
pattern), so it attaches to the page the same way:

    import flet_local_auth as fla
    auth = fla.LocalAuth()
    page.services.append(auth)
    page.update()
    ok = await auth.authenticate(reason="Unlock QA Studio")

Every method is async because it round-trips to the Flutter runtime; the
underlying _invoke_method passes timeout=None, so `authenticate` waits as long
as the user takes at the native prompt instead of timing out.
"""
from flet.controls.base_control import control
from flet.controls.services.service import Service


@control("LocalAuth")
class LocalAuth(Service):
    """Biometric / device-credential authentication gate.

    Wraps `local_auth`'s LocalAuthentication. Does NOT persist anything — it
    only asks the OS to verify the user and returns the result, so it can never
    invalidate a Keystore key or wipe stored credentials.
    """

    async def authenticate(
        self,
        reason: str = "Authenticate to continue",
        biometric_only: bool = False,
    ) -> bool:
        """Prompt the OS biometric / device-credential dialog.

        Returns True only if the user passed. Any error (no enrollment,
        cancel, lockout) resolves to False on the Dart side rather than
        throwing, so callers can treat False uniformly as "not authenticated".
        `biometric_only=False` lets the OS fall back to PIN/pattern/password.
        """
        return bool(
            await self._invoke_method(
                "authenticate",
                {"reason": reason, "biometric_only": biometric_only},
            )
        )

    async def is_device_supported(self) -> bool:
        """True if the device can perform any local authentication at all
        (biometric hardware OR a device passcode enrolled)."""
        return bool(await self._invoke_method("is_device_supported"))

    async def can_check_biometrics(self) -> bool:
        """True if biometric hardware is present and usable (may still be True
        with no fingerprints enrolled — check enrollment via authenticate)."""
        return bool(await self._invoke_method("can_check_biometrics"))
