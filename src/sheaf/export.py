"""Assembling scanned PNGs into a PDF, or copying them out as images."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .pages import Page


class ExportError(RuntimeError):
    pass


def export_pdf(pages: Sequence[Page], destination: Path, dpi: int) -> Path:
    """Assemble ``pages`` into a PDF at ``destination``.

    The scans stay lossless: img2pdf embeds the PNG data as-is rather than
    re-encoding. The physical page size comes from an explicit dpi rather than
    the PNG's pHYs chunk, so a PDF page always measures what was actually
    scanned — or, once a page has been cropped, what is left of it.
    """
    import img2pdf

    if not pages:
        raise ExportError("There are no pages to save.")

    with tempfile.TemporaryDirectory(prefix="scanner-pdf-") as tmp:
        sources = [_materialise(page, Path(tmp)) for page in pages]
        layout = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("wb") as fh:
                img2pdf.convert([str(p) for p in sources], layout_fun=layout, outputstream=fh)
        except Exception as exc:  # img2pdf raises a variety of types
            raise ExportError(f"Could not write the PDF: {exc}") from exc
    return destination


def export_images(pages: Sequence[Page], directory: Path, *, stem: str = "scan") -> list[Path]:
    """Write each page out as a PNG, applying its crop and rotation."""
    if not pages:
        raise ExportError("There are no pages to save.")

    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for position, page in enumerate(pages, start=1):
        target = directory / f"{stem}-{position:04d}.png"
        if _needs_rendering(page):
            _render_to(page, target)
        else:
            shutil.copy2(page.path, target)
        written.append(target)
    return written


def _needs_rendering(page: Page) -> bool:
    """Whether the scan on disk is already what should be exported."""
    return bool(page.rotation) or page.effective_crop is not None


def _materialise(page: Page, workdir: Path) -> Path:
    """The file to hand to img2pdf: the original unless it needs transforming."""
    if not _needs_rendering(page):
        return page.path
    target = workdir / f"{page.index:04d}.png"
    _render_to(page, target)
    return target


def _render_to(page: Page, target: Path) -> None:
    from . import imaging

    rendered = imaging.render(page.path, crop=page.effective_crop, rotation=page.rotation)
    rendered.save(target, format="PNG")
