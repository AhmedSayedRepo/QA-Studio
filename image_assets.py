"""Validated square-image selection for QA Studio identity assets.

The FilePicker is a Flet service in 0.85, so it must be attached to
``page.services`` before it can open a native picker.  Keeping that detail and
the image validation here makes organization logos and profile pictures follow
the same safety and sizing contract on Windows and mobile.
"""
import base64
import io

import flet as ft
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_BYTES = 2 * 1024 * 1024
MIN_PIXELS = 128
MAX_PIXELS = 2048
MAX_SOURCE_PIXELS = 8192
_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}


def _file_picker(app, attr="_qa_file_picker"):
    """Return one Flet FilePicker service for ordinary files and images.

    Flet 0.85 exposes file dialogs asynchronously through a page service. This
    avoids the former separate native-dialog roots, which could steal focus,
    block the UI, and were unavailable on mobile.
    """
    picker = getattr(app, attr, None)
    if picker is None:
        picker = ft.FilePicker()
        setattr(app, attr, picker)
        app.page.services.append(picker)
        app.page.update()
    return picker


def choose_file(app, title, extensions, on_ready, on_error=None):
    """Choose one local file with the shared asynchronous Flet picker."""
    async def _choose():
        try:
            files = await _file_picker(app).pick_files(
                dialog_title=title,
                file_type=ft.FilePickerFileType.CUSTOM if extensions else ft.FilePickerFileType.ANY,
                allowed_extensions=list(extensions or []), allow_multiple=False, with_data=False)
            path = str(getattr(files[0], "path", "") or "") if files else ""
            if path:
                on_ready(path)
        except Exception:
            if callable(on_error):
                on_error()
    try:
        app.page.run_task(_choose)
    except Exception:
        if callable(on_error):
            on_error()


def choose_directory(app, title, on_ready, on_error=None):
    """Choose a directory using the same Flet service as image selection."""
    async def _choose():
        try:
            path = await _file_picker(app).get_directory_path(dialog_title=title)
            if path:
                on_ready(str(path))
        except Exception:
            if callable(on_error):
                on_error()
    try:
        app.page.run_task(_choose)
    except Exception:
        if callable(on_error):
            on_error()


def choose_save_path(app, title, filename, extensions, on_ready, on_error=None):
    """Ask for an export destination through Flet's asynchronous service."""
    async def _choose():
        try:
            path = await _file_picker(app).save_file(
                dialog_title=title, file_name=filename,
                file_type=ft.FilePickerFileType.CUSTOM if extensions else ft.FilePickerFileType.ANY,
                allowed_extensions=list(extensions or []))
            if path:
                on_ready(str(path))
        except Exception:
            if callable(on_error):
                on_error()
    try:
        app.page.run_task(_choose)
    except Exception:
        if callable(on_error):
            on_error()


def validate_square_image(data):
    """Return a square, transport-ready image payload or ``(None, reason)``.

    Standard photos are accepted.  They are center-cropped to a square and
    downscaled to 2048px when needed, which gives avatars and logos a stable
    fit without asking people to pre-edit an otherwise valid image.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        return None, "empty"
    if len(data) > MAX_BYTES:
        return None, "size"
    try:
        with Image.open(io.BytesIO(data)) as probe:
            image_format = str(probe.format or "").upper()
            width, height = probe.size
            probe.verify()
        # Re-open after verify(): Pillow intentionally invalidates the first
        # decoder to make callers prove they did not use corrupt pixels.
        with Image.open(io.BytesIO(data)) as decoded:
            decoded.load()
            decoded = ImageOps.exif_transpose(decoded)
            width, height = decoded.size
            if min(width, height) < MIN_PIXELS or max(width, height) > MAX_SOURCE_PIXELS:
                return None, "dimensions"
            side = min(width, height)
            left, top = (width - side) // 2, (height - side) // 2
            normalized = decoded.crop((left, top, left + side, top + side))
            if side > MAX_PIXELS:
                normalized = normalized.resize((MAX_PIXELS, MAX_PIXELS), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, ValueError):
        return None, "format"
    if image_format not in _FORMATS:
        return None, "format"
    mime, extension = _FORMATS[image_format]
    # Re-encode the normalized crop. JPEG cannot keep an alpha channel.
    if image_format == "JPEG" and normalized.mode not in ("RGB", "L"):
        normalized = normalized.convert("RGB")
    encoded = io.BytesIO()
    save_options = {"format": image_format}
    if image_format in ("JPEG", "WEBP"):
        save_options.update({"quality": 90, "optimize": True})
    normalized.save(encoded, **save_options)
    output = encoded.getvalue()
    if len(output) > MAX_BYTES:
        return None, "size"
    return {
        "image_base64": base64.b64encode(output).decode("ascii"),
        "mime_type": mime,
        "extension": extension,
    }, ""


def choose_square_image(app, title, on_ready, on_error):
    """Open the platform picker and validate one image on the Flet UI loop."""
    async def _choose():
        try:
            picker = _file_picker(app)
            files = await picker.pick_files(
                dialog_title=title,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["jpg", "jpeg", "png", "webp"],
                allow_multiple=False,
                with_data=True,
            )
            if not files:
                return
            payload, reason = validate_square_image(getattr(files[0], "bytes", None))
            if payload is None:
                on_error(reason)
                return
            on_ready(payload)
        except Exception as ex:
            on_error("picker")

    try:
        app.page.run_task(_choose)
    except Exception:
        on_error("picker")
