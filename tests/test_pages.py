"""The page model: duplex labelling, rotation metadata, reordering."""

from __future__ import annotations

from pathlib import Path

from sheaf.pages import PageStore


def store(count: int, *, duplex: bool = False) -> PageStore:
    pages = PageStore()
    for i in range(count):
        pages.add(Path(f"/tmp/p{i:04d}.png"), duplex=duplex)
    return pages


def test_simplex_pages_are_numbered_sequentially():
    assert [p.label for p in store(3)] == ["Page 1", "Page 2", "Page 3"]


def test_duplex_pages_are_labelled_by_sheet_and_side():
    # ADF Duplex returns two images per sheet, front then back.
    assert [p.label for p in store(4, duplex=True)] == [
        "Sheet 1 (front)",
        "Sheet 1 (back)",
        "Sheet 2 (front)",
        "Sheet 2 (back)",
    ]


def test_rotation_wraps_and_is_metadata_only():
    page = store(1)[0]
    page.rotate(90)
    page.rotate(90)
    assert page.rotation == 180
    page.rotate(180)
    assert page.rotation == 0
    page.rotate(-90)
    assert page.rotation == 270


def test_moving_a_page_reorders_the_model():
    pages = store(3)
    first = pages[0]
    assert pages.move(first, 2)
    assert [p.index for p in pages] == [2, 3, 1]


def test_moving_beyond_the_ends_does_nothing():
    pages = store(3)
    assert not pages.move(pages[0], -1)
    assert not pages.move(pages[2], 1)
    assert [p.index for p in pages] == [1, 2, 3]


def test_removing_a_page_leaves_the_rest_in_order():
    pages = store(3)
    pages.remove(pages[1])
    assert [p.index for p in pages] == [1, 3]


def test_empty_selection_means_all_pages_in_current_order():
    pages = store(3)
    assert pages.selection([]) == list(pages)


def test_selection_follows_page_order_not_click_order():
    pages = store(3)
    chosen = pages.selection([pages[2], pages[0]])
    assert [p.index for p in chosen] == [1, 3]
