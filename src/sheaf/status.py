"""Mapping scanimage exit codes to something a user can act on.

scanimage exits with the raw SANE status code. Verified on the device: an empty
feeder gives exit 7 (SANE_STATUS_NO_DOCS).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Outcome(Enum):
    SUCCESS = "success"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ScanResult:
    outcome: Outcome
    title: str
    detail: str
    exit_code: int
    pages: int

    @property
    def is_error(self) -> bool:
        return self.outcome is Outcome.ERROR


# exit code -> (SANE status name, title, detail)
_CODES: dict[int, tuple[str, str, str]] = {
    0: ("GOOD", "Scan finished", ""),
    1: (
        "OPEN_FAILED",
        "Could not open the scanner",
        "The scanner may have been unplugged, or it moved to a different USB "
        "address. Look for it again and retry.",
    ),
    2: ("CANCELLED", "Scan cancelled", "The scan was stopped before it finished."),
    4: (
        "INVAL",
        "The scanner rejected a setting",
        "One of the scan options is not valid for this device.",
    ),
    10: (
        "NO_MEM",
        "Out of memory",
        "Cropping and deskew buffer the whole page. Try a lower resolution.",
    ),
    3: (
        "DEVICE_BUSY",
        "Scanner is busy",
        "Another application is holding the scanner. Close it and try again.",
    ),
    5: ("EOF", "Scan finished", "The scanner reported no more data."),
    6: (
        "JAMMED",
        "Paper jam",
        "Open the scanner, clear the feeder, then scan again.",
    ),
    7: (
        "NO_DOCS",
        "Feeder is empty",
        "Put the pages in the feeder and press Scan.",
    ),
    8: ("COVER_OPEN", "Cover is open", "Close the scanner cover and try again."),
    9: (
        "IO_ERROR",
        "Communication failed",
        "The scanner stopped responding. Check the USB cable and reconnect it.",
    ),
    11: (
        "ACCESS_DENIED",
        "No permission to use the scanner",
        "Your user is not allowed to open the device. See the permissions "
        "section of the README.",
    ),
}

NO_DOCS = 7
CANCELLED = 2
#: scanimage exits 1 when it cannot open the device at all — most often a stale
#: device string after the scanner was replugged and re-enumerated.
OPEN_FAILED = 1


def sane_status(exit_code: int) -> str:
    """The SANE status name for an exit code, or a placeholder."""
    entry = _CODES.get(exit_code)
    return entry[0] if entry else f"UNKNOWN({exit_code})"


def classify(exit_code: int, pages: int, *, cancelled: bool = False) -> ScanResult:
    """Turn an exit code plus a page count into a user-facing result.

    The important rule: in ``--batch`` mode, running out of paper is how a
    normal batch *ends*. scanimage reports that as exit 7 (NO_DOCS) whether the
    feeder was empty from the start or the stack simply ran out, so exit 7 with
    at least one page produced is a successful scan, not an error.
    """
    if cancelled or exit_code == CANCELLED:
        name, title, detail = _CODES[CANCELLED]
        return ScanResult(Outcome.CANCELLED, title, detail, exit_code, pages)

    if exit_code == NO_DOCS and pages > 0:
        return ScanResult(
            Outcome.SUCCESS,
            _pages_title(pages),
            "The feeder ran out of paper, which is how a batch ends.",
            exit_code,
            pages,
        )

    if exit_code in (0, 5) or (pages > 0 and exit_code not in _CODES):
        return ScanResult(Outcome.SUCCESS, _pages_title(pages), "", exit_code, pages)

    entry = _CODES.get(exit_code)
    if entry is None:
        return ScanResult(
            Outcome.ERROR,
            "Scanning failed",
            f"scanimage exited with status {exit_code}.",
            exit_code,
            pages,
        )

    _name, title, detail = entry
    if pages > 0:
        detail = f"{detail} {pages} page(s) were scanned before this happened.".strip()
    return ScanResult(Outcome.ERROR, title, detail, exit_code, pages)


def _pages_title(pages: int) -> str:
    if pages == 0:
        return "No pages scanned"
    if pages == 1:
        return "Scanned 1 page"
    return f"Scanned {pages} pages"
