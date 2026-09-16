"""Assembling pages into a PDF.

The case that matters: a page removed earlier in the session frees up its
index, a later scan reuses it, and two live pages then share the same
``page.index``. ``_materialise`` used to name its rendered temp file after
that index, so the second page silently overwrote the first in the shared
tmpdir and the PDF ended up with one page twice and the other missing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PIL import Image

from sheaf.export import ExportError, document_dpi, export_pdf
from sheaf.pages import Page

PAGE_PATTERN = re.compile(rb"/Type\s*/Page[^s]")


def _png(path: Path, shade: int) -> Path:
    Image.new("L", (80, 120), shade).save(path)
    return path


def test_materialise_names_do_not_collide_when_page_index_repeats(tmp_path: Path):
    # Simulates: scan a,b,c; delete b; scan d. d's Page.index (3) matches c's,
    # because PageStore.add sizes the new index off len(pages). Both need
    # rendering (rotation set) so both go through _materialise.
    c = Page(path=_png(tmp_path / "c.png", 30), index=3, rotation=90)
    d = Page(path=_png(tmp_path / "d.png", 200), index=3, rotation=90)

    from sheaf.export import _materialise

    workdir = tmp_path / "work"
    workdir.mkdir()
    first = _materialise(c, workdir, 1)
    second = _materialise(d, workdir, 2)

    assert first != second
    assert first.exists() and second.exists()


def test_export_pdf_keeps_every_page_after_an_index_collision(tmp_path: Path):
    c = Page(path=_png(tmp_path / "c.png", 30), index=3, rotation=90)
    d = Page(path=_png(tmp_path / "d.png", 200), index=3, rotation=90)
    a = Page(path=_png(tmp_path / "a.png", 10), index=1)

    destination = tmp_path / "out.pdf"
    export_pdf([a, c, d], destination, dpi=300)

    data = destination.read_bytes()
    assert len(PAGE_PATTERN.findall(data)) == 3


def test_document_dpi_takes_the_first_page():
    pages = [
        Page(path=Path("/tmp/a.png"), index=1, dpi=300),
        Page(path=Path("/tmp/b.png"), index=2, dpi=600),
    ]
    assert document_dpi(pages) == 300


def test_document_dpi_falls_back_when_empty():
    assert document_dpi([]) == 300


def test_export_pdf_raises_on_no_pages(tmp_path: Path):
    with pytest.raises(ExportError):
        export_pdf([], tmp_path / "out.pdf", dpi=300)
