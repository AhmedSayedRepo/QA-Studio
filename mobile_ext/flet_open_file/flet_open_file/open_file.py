"""Python side of the flet_open_file extension — a Flet Service that opens a
local file with the OS default handler.

Used by the in-app updater: opening a .apk this way makes Android hand it
straight to the package installer, skipping the share-sheet app chooser that
ft.Share produced. Modeled on flet_local_auth / flet_secure_storage (same Flet
0.85 @control + Service + _invoke_method pattern).

NOTE: the system "Do you want to install this update?" dialog and the one-time
"Allow from this source" grant are Android security guarantees and CANNOT be
bypassed — this only removes the chooser step in front of them.
"""
from flet.controls.base_control import control
from flet.controls.services.service import Service


@control("OpenFile")
class OpenFile(Service):
    """Opens a local file path with the platform's default handler."""

    async def open(self, path: str) -> str:
        """Open `path`. Returns the plugin's result type as a string
        ("done", "noAppToOpen", "permissionDenied", "fileNotFound", "error").
        Never raises — failures come back as a string so the caller can fall
        back to the share sheet."""
        return str(await self._invoke_method("open", {"path": path}) or "")
