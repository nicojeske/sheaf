"""The model of scanned pages.

Rotation *and* cropping are metadata only. The scanned PNG is never rewritten —
both are applied once, at export time, so repeated rotates cannot degrade the
image, undo is free, and an auto-crop the user does not like can simply be
switched off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .imaging import Box


@dataclass(slots=True)
class Page:
    path: Path
    #: Sequence number as produced by scanimage --batch (1-based).
    index: int
    rotation: int = 0
    #: True when this page is the back of a sheet from an ADF Duplex scan.
    duplex: bool = False
    #: Resolution this page was scanned at. Needed to turn a margin in
    #: millimetres into pixels, and to size the PDF page.
    dpi: int = 300
    #: Content box found by imaging.detect_content_box, margin already included,
    #: or None when nothing was detected or app-side cropping is off.
    crop: Box | None = None
    #: Lets the user see the page uncropped without losing the detected box.
    crop_enabled: bool = True

    @property
    def effective_crop(self) -> Box | None:
        return self.crop if self.crop_enabled else None

    @property
    def sheet(self) -> int:
        """1-based sheet number. ADF Duplex yields two images per sheet."""
        if self.duplex:
            return (self.index + 1) // 2
        return self.index

    @property
    def side(self) -> str | None:
        if not self.duplex:
            return None
        return "front" if self.index % 2 == 1 else "back"

    @property
    def label(self) -> str:
        if self.duplex:
            return f"Sheet {self.sheet} ({self.side})"
        return f"Page {self.index}"

    def rotate(self, degrees: int) -> None:
        self.rotation = (self.rotation + degrees) % 360


@dataclass(slots=True)
class PageStore:
    """Ordered list of scanned pages."""

    pages: list[Page] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.pages)

    def __iter__(self):
        return iter(self.pages)

    def __getitem__(self, i: int) -> Page:
        return self.pages[i]

    def add(self, path: Path, *, duplex: bool, dpi: int = 300) -> Page:
        page = Page(path=path, index=len(self.pages) + 1, duplex=duplex, dpi=dpi)
        self.pages.append(page)
        return page

    def remove(self, page: Page) -> None:
        """Drop a page from the model.

        The temporary file is left on disk until the session ends, so nothing is
        destroyed by a misclick.
        """
        if page in self.pages:
            self.pages.remove(page)

    def move(self, page: Page, offset: int) -> bool:
        """Move a page by ``offset`` positions. Returns True if it moved."""
        try:
            current = self.pages.index(page)
        except ValueError:
            return False
        target = current + offset
        if not 0 <= target < len(self.pages):
            return False
        self.pages.insert(target, self.pages.pop(current))
        return True

    def clear(self) -> None:
        self.pages.clear()

    def selection(self, selected: list[Page]) -> list[Page]:
        """Selecting nothing means all pages, in current order."""
        if not selected:
            return list(self.pages)
        chosen = set(id(p) for p in selected)
        return [p for p in self.pages if id(p) in chosen]
