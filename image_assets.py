"""Validated square-image selection for QA Studio identity assets.

The FilePicker is a Flet service in 0.85, so it must be attached to
``page.services`` before it can open a native picker.  Keeping that detail and
the image validation here makes organization logos and profile pictures follow
the same safety and sizing contract on Windows and mobile.
"""
import base64
import io
import math
import warnings

import flet as ft
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_BYTES = 2 * 1024 * 1024
MIN_PIXELS = 128
MAX_PIXELS = 2048
# The final identity asset is at most 2048px square.  Do not decode an
# arbitrarily large source just to shrink it: a crafted image can claim an
# enormous canvas while remaining a small file (a decompression bomb).
MAX_SOURCE_DIMENSION = 4096
MAX_SOURCE_TOTAL_PIXELS = 16_777_216
_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}


def _safe_open_image(data):
    """Open one supported, single-frame raster without trusting its header.

    Pillow is intentionally used as a decoder, not as a type detector.  The
    header is bounded before ``load()`` can allocate pixels and a successful
    decode is later re-encoded, which removes source metadata and any bytes
    appended to an otherwise valid image container.
    """
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > MAX_BYTES:
        return None, "size" if data else "empty"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                image_format = str(probe.format or "").upper()
                width, height = probe.size
                frames = int(getattr(probe, "n_frames", 1) or 1)
                if image_format not in _FORMATS:
                    return None, "format"
                if frames != 1:
                    return None, "format"
                if (min(width, height) < MIN_PIXELS
                        or max(width, height) > MAX_SOURCE_DIMENSION
                        or width * height > MAX_SOURCE_TOTAL_PIXELS):
                    return None, "dimensions"
                probe.verify()
            # ``verify`` invalidates the decoder by design. Re-open, then
            # fully load only after the header bounds above have been checked.
            with Image.open(io.BytesIO(data)) as decoded:
                if str(decoded.format or "").upper() != image_format:
                    return None, "format"
                decoded.load()
                return ImageOps.exif_transpose(decoded).copy(), image_format
    except (Image.DecompressionBombError, Image.DecompressionBombWarning,
            UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None, "format"


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
    decoded, reason = _safe_open_image(data)
    if decoded is None:
        return None, reason
    width, height = decoded.size
    side = min(width, height)
    left, top = (width - side) // 2, (height - side) // 2
    normalized = decoded.crop((left, top, left + side, top + side))
    if side > MAX_PIXELS:
        normalized = normalized.resize((MAX_PIXELS, MAX_PIXELS), Image.Resampling.LANCZOS)
    # Identity assets are always emitted as WebP.  The source's format,
    # metadata and any trailing/polyglot bytes never leave the desktop app.
    if normalized.mode not in ("RGB", "RGBA"):
        normalized = normalized.convert("RGBA" if "transparency" in normalized.info else "RGB")
    encoded = io.BytesIO()
    normalized.save(encoded, format="WEBP", quality=88, method=4)
    output = encoded.getvalue()
    if len(output) > MAX_BYTES:
        return None, "size"
    return {
        "image_base64": base64.b64encode(output).decode("ascii"),
        "mime_type": "image/webp",
        "extension": "webp",
    }, ""


def transform_validated_square_image(payload, zoom=1.0, rotation_degrees=0,
                                     offset_x=0.0, offset_y=0.0,
                                     preview_max_pixels=None):
    """Transform a previously validated square payload.

    ``preview_max_pixels`` produces a fast visual-only rendition for slider
    adjustments. The caller saves from the same validated source with it set
    to ``None``, which keeps the final upload at the original validated size.
    Avoiding file validation and a full 2048px WebP encode for every slider
    release makes interactive rotation responsive on ordinary Windows PCs.
    """
    try:
        if not isinstance(payload, dict) or not payload.get("image_base64"):
            return None, "format"
        if payload.get("mime_type") != "image/webp" or payload.get("extension") != "webp":
            return None, "format"
        source = base64.b64decode(payload["image_base64"], validate=True)
        image, reason = _safe_open_image(source)
        if image is None:
            return None, reason

        side = min(image.size)
        zoom = max(0.5, min(float(zoom), 2.0))
        crop_side = max(MIN_PIXELS, min(side, int(round(side / max(1.0, zoom)))))
        # ``offset_*`` is normalized to -1…1 and moves the crop from one
        # permitted edge to the other.  That makes drag-to-position independent
        # of source resolution and keeps every saved crop inside real pixels.
        offset_x = max(-1.0, min(float(offset_x), 1.0))
        offset_y = max(-1.0, min(float(offset_y), 1.0))
        x_room = max(0, image.width - crop_side)
        y_room = max(0, image.height - crop_side)
        left = int(round((x_room / 2) * (1 + offset_x)))
        top = int(round((y_room / 2) * (1 + offset_y)))
        image = image.crop((left, top, left + crop_side, top + crop_side))
        target_side = min(side, int(preview_max_pixels or side))
        if zoom < 1.0:
            # Zooming out cannot reveal pixels beyond an already-square source.
            # Keep the complete image, scale it down, and centre it on a
            # transparent WebP canvas that fits naturally in the avatar circle.
            inner_side = max(1, int(round(target_side * zoom)))
            image = image.resize((inner_side, inner_side), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (target_side, target_side), (0, 0, 0, 0))
            offset = (target_side - inner_side) // 2
            if image.mode != "RGBA":
                image = image.convert("RGBA")
            canvas.paste(image, (offset, offset), image)
            image = canvas
        elif crop_side != target_side:
            image = image.resize((target_side, target_side), Image.Resampling.LANCZOS)

        # PIL's clockwise direction is negative degrees. Rotate into an
        # expanded transparent canvas, then take the largest centred square
        # that lies entirely inside the rotated photo. Scaling that safe crop
        # back to the avatar size keeps every degree-based edit filled to the
        # circular edge — no black/transparent corner wedges.
        rotation = max(-180.0, min(float(rotation_degrees), 180.0))
        if rotation:
            radians = math.radians(abs(rotation) % 90.0)
            safe_side = max(1, int(round(target_side / (math.cos(radians) + math.sin(radians)))))
            if image.mode != "RGBA":
                image = image.convert("RGBA")
            rotated = image.rotate(-rotation, resample=Image.Resampling.BICUBIC,
                                   expand=True, fillcolor=(0, 0, 0, 0))
            left = max(0, (rotated.width - safe_side) // 2)
            top = max(0, (rotated.height - safe_side) // 2)
            image = rotated.crop((left, top, left + safe_side, top + safe_side))
            image = image.resize((target_side, target_side), Image.Resampling.LANCZOS)

        # WebP retains alpha where present and has a much better chance of
        # remaining below the upload size limit than a re-encoded PNG.
        encoded = io.BytesIO()
        image.save(encoded, format="WEBP", quality=(80 if preview_max_pixels else 88),
                   method=(0 if preview_max_pixels else 4))
        output = encoded.getvalue()
        if len(output) > MAX_BYTES:
            return None, "size"
        return {
            "image_base64": base64.b64encode(output).decode("ascii"),
            "mime_type": "image/webp",
            "extension": "webp",
        }, ""
    except (TypeError, ValueError, OSError, UnidentifiedImageError):
        return None, "format"


def transform_square_image(data, zoom=1.0, rotation_degrees=0,
                           offset_x=0.0, offset_y=0.0):
    """Validate one source image then return its edited full-quality payload."""
    normalized_payload, reason = validate_square_image(data)
    if normalized_payload is None:
        return None, reason
    return transform_validated_square_image(normalized_payload, zoom, rotation_degrees,
                                            offset_x, offset_y)


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
