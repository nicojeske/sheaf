"""Snapping millimetre geometry onto the scanner's fixed-point grid.

The backend quantises every geometry option to ``GRID_MM`` (1387/65536 mm).
If we hand scanimage a value that is not exactly on that grid it silently
adjusts it and prints ``rounded value of page-height from X to Y`` on stderr.
Snapping here — and formatting with full float precision in argv.py — keeps
scanimage entirely silent, which was verified on the device.
"""

from __future__ import annotations

from .caps import (
    GRID_MM,
    MAX_PAGE_HEIGHT_UNITS,
    MAX_PAGE_WIDTH_UNITS,
)


class GeometryError(ValueError):
    """A geometry value cannot be used with this backend."""


def to_units(mm: float) -> int:
    """Millimetres to whole grid units, rounded to nearest."""
    return round(mm / GRID_MM)


def from_units(units: int) -> float:
    """Grid units back to millimetres."""
    return units * GRID_MM


def snap(mm: float, *, max_units: int) -> float:
    """Snap ``mm`` onto the grid and clamp it into ``0..max_units``.

    Clamping happens in grid units, so the result can never land above the
    maximum through rounding up.
    """
    units = min(max(to_units(mm), 0), max_units)
    return from_units(units)


def snap_page_width(mm: float) -> float:
    return snap(mm, max_units=MAX_PAGE_WIDTH_UNITS)


def snap_page_height(mm: float) -> float:
    return snap(mm, max_units=MAX_PAGE_HEIGHT_UNITS)


def snap_positive_page(mm: float, *, max_units: int, name: str) -> float:
    """Snap a page dimension, rejecting zero.

    ``--page-height 0`` looks like it might mean "auto" or "unlimited". It does
    not: it collapses the scan area to ``-y 0..0mm``, i.e. nothing at all.
    Verified on the device. The same holds for ``--page-width``.
    """
    value = snap(mm, max_units=max_units)
    if to_units(value) == 0:
        raise GeometryError(
            f"{name} must be greater than zero — {name} 0 collapses the scan "
            f"area to nothing rather than meaning 'automatic'."
        )
    return value


def format_number(value: float) -> str:
    """Render a snapped value for argv.

    Full ``repr`` precision is deliberate. scanimage parses the string into a
    double and converts to 16.16 fixed point; a rounded-off decimal such as
    "355.554" does not convert back to the same fixed-point value and triggers
    the "rounded value of" warning, whereas "355.55419921875" is silent.
    """
    return repr(float(value))


def snap_swskip(percent: float) -> float:
    """Snap a --swskip percentage onto its own fixed-point grid.

    As with millimetres, the ceiling is clamped in grid units so that rounding
    can never push the value above the advertised maximum (snapping 100% up to
    100.0061 makes the backend clamp it back down and complain).
    """
    from .caps import SWSKIP_GRID, SWSKIP_RANGE

    lo, hi = SWSKIP_RANGE
    max_units = int(hi / SWSKIP_GRID)
    units = min(max(round(percent / SWSKIP_GRID), int(lo)), max_units)
    return units * SWSKIP_GRID
