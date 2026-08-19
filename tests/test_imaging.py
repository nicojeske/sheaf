"""App-side crop-to-content.

The cases that matter are the ones swcrop gets wrong: a margin of zero, and a
speck of dust at the edge of the sheet dragging the crop out to the full width
of the paper path.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from sheaf import imaging

PAPER = 247
INK = 30
DPI = 300
#: One millimetre at 300 dpi: the granularity the content box is found at.
BLOCK = 12


def receipt(
    size: tuple[int, int] = (2551, 4199),
    *,
    content: tuple[int, int, int, int] = (700, 300, 1650, 3200),
) -> Image.Image:
    """A full-width scan of a narrow till roll, as the Receipt preset produces."""
    image = Image.new("L", size, PAPER)
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = content
    for y in range(top, bottom, 40):
        draw.line((left, y, right, y), fill=INK, width=2)
    return image


def test_content_box_finds_the_ink_not_the_paper():
    # Blocks are a millimetre, so the box lands within one block of the ink —
    # always outside it, never inside.
    left, top, right, bottom = imaging.content_box(receipt(), dpi=DPI)
    assert 700 - BLOCK <= left <= 700
    assert 300 - BLOCK <= top <= 300
    assert 1651 <= right <= 1651 + BLOCK
    # The last line is drawn at y=3180 and is 2 px thick.
    assert 3181 <= bottom <= 3181 + BLOCK


def test_a_speck_at_the_edge_does_not_drag_the_crop_out():
    # A bounding box of dark pixels would return left=5 here and hand back the
    # full 216 mm width, which is exactly the failure this module exists for.
    image = receipt()
    ImageDraw.Draw(image).rectangle((5, 2000, 9, 2004), fill=0)
    left, _, _, _ = imaging.content_box(image, dpi=DPI)
    assert left >= 700 - BLOCK


def test_margin_expands_the_box_on_all_four_sides():
    tight = imaging.content_box(receipt(), dpi=DPI)
    padded = imaging.content_box(receipt(), dpi=DPI, margin_px=50)
    assert padded == (tight[0] - 50, tight[1] - 50, tight[2] + 50, tight[3] + 50)


def test_a_blank_page_is_left_alone():
    assert imaging.content_box(Image.new("L", (500, 800), 250), dpi=DPI) is None


def test_a_page_too_dark_to_read_is_left_alone():
    # An inverted or entirely black scan: there is no paper level to measure
    # against, so guessing a crop would be worse than not cropping.
    assert imaging.content_box(Image.new("L", (500, 800), 5), dpi=DPI) is None


def test_margin_beyond_the_scan_is_padded_with_the_paper_colour(tmp_path: Path):
    # This is the swcrop case: the driver already trimmed to the ink, so there
    # is no whitespace left to keep and the margin has to be added instead.
    # Image.crop would fill the overhang with black.
    path = tmp_path / "tight.png"
    Image.new("L", (200, 400), PAPER).save(path)

    rendered = imaging.render(path, crop=(-20, -20, 220, 420))
    assert rendered.size == (240, 440)
    assert rendered.getpixel((0, 0)) == PAPER
    assert rendered.getpixel((239, 439)) == PAPER


def test_render_crops_before_rotating(tmp_path: Path):
    path = tmp_path / "page.png"
    receipt((600, 1200), content=(100, 200, 500, 1000)).save(path)

    upright = imaging.render(path, crop=(100, 200, 500, 1000))
    assert upright.size == (400, 800)
    # Rotation swaps the axes of the *cropped* page, not of the file on disk.
    turned = imaging.render(path, crop=(100, 200, 500, 1000), rotation=90)
    assert turned.size == (800, 400)


def test_render_without_a_crop_returns_the_scan_unchanged(tmp_path: Path):
    path = tmp_path / "page.png"
    original = receipt((300, 500))
    original.save(path)
    assert imaging.render(path).tobytes() == original.tobytes()


def test_an_unreadable_file_yields_no_box(tmp_path: Path):
    path = tmp_path / "truncated.png"
    path.write_bytes(b"not a png")
    assert imaging.detect_content_box(path, dpi=DPI) is None


def test_colour_scans_are_padded_per_band(tmp_path: Path):
    path = tmp_path / "colour.png"
    Image.new("RGB", (100, 100), (250, 244, 236)).save(path)
    rendered = imaging.render(path, crop=(-10, -10, 110, 110))
    assert rendered.getpixel((0, 0)) == (250, 244, 236)


def test_margin_and_size_convert_through_dpi():
    assert imaging.margin_px(4.0, 300) == 47
    assert imaging.margin_px(0.0, 300) == 0
    width, height = imaging.size_mm((927, 3648), 300)
    assert round(width, 1) == 78.5
    assert round(height, 1) == 308.9


def test_a_page_of_nothing_but_dust_is_left_alone():
    image = Image.new("L", (1200, 1600), PAPER)
    draw = ImageDraw.Draw(image)
    for x, y in ((30, 40), (600, 900), (1100, 1500)):
        draw.rectangle((x, y, x + 4, y + 4), fill=0)
    assert imaging.content_box(image, dpi=DPI) is None


def test_content_running_to_the_edge_turns_the_margin_into_padding(tmp_path: Path):
    # What swcrop hands us: a page already trimmed to the ink. There is no
    # whitespace to keep, so the whole margin has to be added on.
    path = tmp_path / "swcropped.png"
    image = Image.new("L", (400, 900), PAPER)
    ImageDraw.Draw(image).rectangle((0, 0, 399, 899), outline=INK, width=3)
    image.save(path)

    margin = imaging.margin_px(4.0, DPI)
    box = imaging.detect_content_box(path, dpi=DPI, margin_px=margin)
    assert box == (-margin, -margin, 400 + margin, 900 + margin)

    rendered = imaging.render(path, crop=box)
    assert rendered.size == (400 + 2 * margin, 900 + 2 * margin)
    assert rendered.getpixel((0, 0)) == PAPER
