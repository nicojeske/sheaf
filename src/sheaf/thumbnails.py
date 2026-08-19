"""Turning scanned PNGs into textures off the UI thread.

This is the GTK edge of image handling; the pixel work itself lives in
imaging.py, which stays free of gi so it can be tested without a display.

Two sizes are produced, and the difference matters. Card thumbnails are fitted
into a fixed box so that every card is the same size whatever the page's aspect
ratio — a till receipt is nearly 1:5, and letting the card follow that makes the
grid unusable. The preview instead decodes at the scan's own resolution (capped
only to keep a 600 dpi page from costing hundreds of megabytes), because fitting
*both* dimensions of a 927 x 3648 receipt into a square box — which is what this
module used to do — throws away three quarters of the width and the result looks
like a fax.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib  # noqa: E402

from .imaging import Box  # noqa: E402

#: Fixed box every card thumbnail is fitted into, in logical pixels. Portrait,
#: because every source this scanner offers is portrait.
THUMBNAIL_BOX = (210, 280)

#: Ceiling on preview pixels. A 216 x 356 mm page at 600 dpi is 43 MP, which as
#: an RGB texture is 129 MB; halving that is still far more detail than a
#: display can show at once.
PREVIEW_MAX_PIXELS = 24_000_000

#: --batch-print announces a file as soon as it is closed, but give a slow
#: filesystem a second chance rather than showing a broken page.
_RETRY_DELAY = 0.25


@dataclass(frozen=True, slots=True)
class Decoded:
    """A texture plus the size it was decoded *from*.

    ``size`` is the page in its own pixels once crop and rotation are applied,
    which is what "100%" means in the preview and what the card caption turns
    into millimetres. The texture itself may be smaller — a thumbnail always is
    — so it cannot answer either question.
    """

    texture: Gdk.Texture
    size: tuple[int, int]


def load_async(
    path: Path,
    box: tuple[int, int] | None,
    on_ready: Callable[[Decoded | None], None],
    *,
    rotation: int = 0,
    crop: Box | None = None,
) -> None:
    """Decode ``path`` in a thread, then call ``on_ready`` on the main loop."""

    def work() -> None:
        decoded = load(path, box, rotation=rotation, crop=crop)
        if decoded is None:
            import time

            time.sleep(_RETRY_DELAY)
            decoded = load(path, box, rotation=rotation, crop=crop)
        GLib.idle_add(on_ready, decoded)

    threading.Thread(target=work, name="thumbnail", daemon=True).start()


def detect_crop_async(
    path: Path,
    on_ready: Callable[[Box | None], None],
    *,
    dpi: int,
    margin_px: int,
) -> None:
    """Find the content box in a thread, then call ``on_ready`` on the main loop."""
    from . import imaging

    def work() -> None:
        box = imaging.detect_content_box(path, dpi=dpi, margin_px=margin_px)
        GLib.idle_add(on_ready, box)

    threading.Thread(target=work, name="autocrop", daemon=True).start()


def load(
    path: Path,
    box: tuple[int, int] | None,
    *,
    rotation: int = 0,
    crop: Box | None = None,
) -> Decoded | None:
    """Decode ``path``, or None if it cannot be read.

    ``box`` is the maximum width and height to fit into; None means the scan's
    own resolution, subject to :data:`PREVIEW_MAX_PIXELS`. ``crop`` and
    ``rotation`` are applied for display only — the scanned file is never
    rewritten, so both are free and reversible.
    """
    from PIL import Image

    from . import imaging

    try:
        image = imaging.render(path, crop=crop, rotation=rotation)
        size = image.size
        if box is not None:
            image.thumbnail(box, Image.Resampling.LANCZOS)
        else:
            _limit_pixels(image)
    except (OSError, ValueError):
        return None
    texture = _texture(image)
    return None if texture is None else Decoded(texture=texture, size=size)


def _limit_pixels(image) -> None:
    """Shrink ``image`` in place if it is larger than the preview ceiling."""
    from PIL import Image

    pixels = image.width * image.height
    if pixels <= PREVIEW_MAX_PIXELS:
        return
    scale = (PREVIEW_MAX_PIXELS / pixels) ** 0.5
    image.thumbnail(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )


def _texture(image) -> Gdk.Texture | None:
    """Wrap a decoded image as a texture.

    Uploaded as raw bytes rather than re-encoded to PNG and handed to
    ``Gdk.Texture.new_from_bytes``: at preview resolution that round-trip is the
    single most expensive step in opening a page, and it buys nothing.
    """
    if image.mode not in ("L", "RGB"):
        image = image.convert("L" if image.mode == "1" else "RGB")
    grayscale = image.mode == "L"
    fmt = Gdk.MemoryFormat.G8 if grayscale else Gdk.MemoryFormat.R8G8B8
    stride = image.width * (1 if grayscale else 3)
    try:
        return Gdk.MemoryTexture.new(
            image.width, image.height, fmt, GLib.Bytes.new(image.tobytes()), stride
        )
    except (GLib.Error, TypeError):
        return _png_texture(image)


def _png_texture(image) -> Gdk.Texture | None:
    """Fallback for a GDK that will not take the raw format above."""
    import io

    buffer = io.BytesIO()
    try:
        image.save(buffer, format="PNG")
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(buffer.getvalue()))
    except (OSError, GLib.Error):
        return None
