"""App-side crop-to-content, and rendering a page for display or export.

``--swcrop`` trims to the exact ink boundary and leaves **no margin at all**.
On a real receipt that produced a page whose text ran right up to x=0 (see
NOTES.md §8), which looks wrong on paper and gives OCR nothing to work with.
This module does the same job in the app, where the box can be padded by a
margin the user picks — and where the result is metadata, so it can be switched
off again per page without rescanning.

Detection deliberately does not take the bounding box of dark pixels. One speck
of dust at the edge of the sheet would move that box out to the full width of
the paper path, and on a full-width receipt scan that means no crop at all.
Instead the ink mask is reduced to a grid of one-millimetre blocks, and only
connected patches of at least a few blocks count as content. Dust is a patch of
one or two blocks and drops out; text, rules and barcodes are large connected
patches and survive. Working in millimetres rather than pixels is what makes the
same thresholds hold at 100 and at 600 dpi, which is why detection needs to know
the resolution.

The result is accurate to a millimetre and rounds outwards, which is the right
direction to be wrong in: a crop a millimetre too generous is invisible, one a
millimetre too tight shaves the digit off a total — the bug swcrop already has.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL.Image import Image

#: left, top, right, bottom in source pixels. May fall outside the image, in
#: which case rendering pads with the paper colour rather than clipping.
Box = tuple[int, int, int, int]

#: A pixel counts as content when it is this much darker than the paper. Gray
#: scans of white paper land around 245-250, not 255, so an absolute cutoff
#: would be either blind or hysterical depending on the sheet.
CONTENT_CONTRAST = 24

#: Below this the paper itself is too dark to tell from ink — a fully black or
#: inverted scan. Cropping is skipped rather than guessed at.
MIN_PAPER_LEVEL = 40

#: Side of the blocks the ink mask is reduced to. One millimetre is finer than
#: any margin worth setting and coarser than any speck worth keeping.
BLOCK_MM = 1.0

#: Fraction of a block that must be ink before the block counts as content.
#: Two percent of a square millimetre is about three pixels at 300 dpi, so a
#: hairline rule or a single row of small text still registers.
MIN_BLOCK_INK = 0.02

#: Blocks a connected patch of ink needs before it counts as content rather than
#: dust. A speck up to a millimetre across covers at most four blocks — 2x2, when
#: it straddles a boundary in both directions at once — so five is the smallest
#: threshold that reliably excludes one. The cost is that a genuinely isolated
#: mark smaller than about 1.5 mm is treated as dust; a word, a rule or a number
#: is an order of magnitude larger.
MIN_CONTENT_BLOCKS = 5

#: Percentile of the whole page taken as the unprinted paper level. Not the
#: border, which was the first implementation and got this exactly backwards:
#: a page swcrop has already trimmed has ink *on* its border, so sampling there
#: reported the paper as black and gave up. Even a dense page of text is mostly
#: paper, so a high percentile finds it whatever is printed.
PAPER_PERCENTILE = 0.85


def margin_px(margin_mm: float, dpi: int) -> int:
    """Millimetres of margin as whole pixels at ``dpi``."""
    return max(0, round(margin_mm / 25.4 * dpi))


def size_mm(size: tuple[int, int], dpi: int) -> tuple[float, float]:
    """A pixel size as millimetres at ``dpi``.

    What the card caption and the preview subtitle report, so that a receipt
    coming out at 78 x 309 mm instead of 216 x 356 is visible without measuring
    the exported PDF.
    """
    width, height = size
    return (width / dpi * 25.4, height / dpi * 25.4)


def detect_content_box(path: Path, *, dpi: int, margin_px: int = 0) -> Box | None:
    """Content box of the scan at ``path``, or None if there is nothing to crop.

    None means "leave this page alone": a blank page, a scan too dark to
    analyse, or a file that cannot be read. Never an exception — this runs on a
    worker thread for a page the user can already see.
    """
    from PIL import Image

    try:
        with Image.open(path) as image:
            image.load()
            return content_box(image, dpi=dpi, margin_px=margin_px)
    except (OSError, ValueError):
        return None


def content_box(image: Image, *, dpi: int, margin_px: int = 0) -> Box | None:
    """Content box of an already-decoded image. See :func:`detect_content_box`."""
    from PIL import Image as ImageModule

    gray = image.convert("L")
    width, height = gray.size
    if not width or not height:
        return None

    paper = _paper_level(gray)
    cutoff = paper - CONTENT_CONTRAST
    if paper < MIN_PAPER_LEVEL or cutoff <= 0:
        return None

    # 255 where there is ink, then averaged down to one value per block: the
    # block's ink coverage, computed in C by the resampler.
    mask = gray.point(lambda value: 255 if value <= cutoff else 0)
    block = max(1, round(BLOCK_MM / 25.4 * dpi))
    columns = max(1, width // block)
    rows = max(1, height // block)
    coverage = mask.resize((columns, rows), ImageModule.Resampling.BOX).tobytes()

    inked = [value >= MIN_BLOCK_INK * 255 for value in coverage]
    indices = _content_blocks(inked, columns, rows)
    if not indices:
        return None  # blank page, or nothing but dust

    xs = [i % columns for i in indices]
    ys = [i // columns for i in indices]
    # Whole blocks, so the box always rounds outwards from the ink. The blocks
    # are width/columns wide rather than `block` wide — the two differ by a
    # fraction of a pixel because the page is not a whole number of blocks — and
    # using the wrong one here would bias the box inwards.
    left, right = _extent(min(xs), max(xs), columns, width)
    top, bottom = _extent(min(ys), max(ys), rows, height)
    return (left - margin_px, top - margin_px, right + margin_px, bottom + margin_px)


def render(path: Path, *, crop: Box | None = None, rotation: int = 0) -> Image:
    """Open ``path`` and apply the page's crop and rotation, in that order.

    Crop first: the box is in the coordinates of the file on disk, which is
    never rewritten, so it stays valid however often the page is rotated.
    """
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        result = _cropped(image, crop) if crop is not None else image.copy()
        if rotation:
            # expand=True keeps the whole image when rotating by 90/270.
            result = result.rotate(-rotation, expand=True)
        return result


# -- internals ---------------------------------------------------------------
def _cropped(image: Image, box: Box) -> Image:
    """Crop to ``box``, padding with the paper colour where it overhangs.

    ``Image.crop`` also accepts a box outside the image but fills the overhang
    with black, which is the opposite of what a margin should look like.
    """
    from PIL import Image as ImageModule

    left, top, right, bottom = box
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return image.copy()
    canvas = ImageModule.new(image.mode, (width, height), _paper_fill(image))
    canvas.paste(image, (-left, -top))
    return canvas


def _extent(first: int, last: int, blocks: int, length: int) -> tuple[int, int]:
    """Pixel range spanned by blocks ``first``..``last``, rounded outwards."""
    import math

    return (
        max(0, math.floor(first * length / blocks)),
        min(length, math.ceil((last + 1) * length / blocks)),
    )


def _content_blocks(inked: list[bool], columns: int, rows: int) -> list[int]:
    """Indices of inked blocks belonging to a patch of a real size.

    This is the whole defence against dust: an 8-connected flood fill, keeping
    only patches of at least :data:`MIN_CONTENT_BLOCKS`. The grid is one cell per
    square millimetre of page, so this is a few thousand cells at most however
    high the resolution.
    """
    seen = [False] * len(inked)
    content: list[int] = []
    for start, is_inked in enumerate(inked):
        if not is_inked or seen[start]:
            continue
        seen[start] = True
        patch = [start]
        pending = [start]
        while pending:
            index = pending.pop()
            x, y = index % columns, index // columns
            for ny in range(max(0, y - 1), min(rows, y + 2)):
                for nx in range(max(0, x - 1), min(columns, x + 2)):
                    neighbour = ny * columns + nx
                    if inked[neighbour] and not seen[neighbour]:
                        seen[neighbour] = True
                        patch.append(neighbour)
                        pending.append(neighbour)
        if len(patch) >= MIN_CONTENT_BLOCKS:
            content.extend(patch)
    return content


def _paper_level(gray: Image) -> int:
    """Brightness of the unprinted paper, as a high percentile of the page."""
    return _percentile(gray.histogram(), PAPER_PERCENTILE)


def _paper_fill(image: Image):
    """Fill value for padded margins, matched to the scan's own paper colour.

    Pure white would leave a visible seam: gray scans of white paper sit around
    245, not 255. Measured per band, because paper scans slightly warm.
    """
    if image.mode == "1":
        return 1
    bands = [_percentile(band.histogram(), PAPER_PERCENTILE) for band in image.split()]
    return bands[0] if len(bands) == 1 else tuple(bands)


def _percentile(histogram: list[int], fraction: float) -> int:
    """Value at ``fraction`` of the way through a 256-bin histogram."""
    total = sum(histogram)
    if not total:
        return 255
    target = total * fraction
    running = 0
    for value, count in enumerate(histogram):
        running += count
        if running >= target:
            return value
    return 255
