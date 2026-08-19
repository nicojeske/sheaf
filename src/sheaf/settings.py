"""The scan settings model — plain data, no GTK, no scanimage knowledge."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from . import caps, geometry

#: Millimetres of margin the app-side crop may keep. Not a device limit — a
#: margin larger than this is a border, not a margin.
AUTOCROP_MARGIN_RANGE = (0.0, 25.0)


@dataclass(frozen=True, slots=True)
class ScanSettings:
    """Everything the user can choose.

    Mostly in backend terms, one field per ``scanimage`` option. Geometry is
    stored in millimetres exactly as the user sees it and snapped onto the
    device grid by :meth:`snapped` before argv is built. The last block is the
    exception: those fields are applied by the app after the scan and never
    reach argv.
    """

    source: str = "ADF Front"
    mode: str = "Gray"
    resolution: int = 300

    page_width: float = caps.MAX_PAGE_WIDTH_MM
    page_height: float = 279.364
    left: float = 0.0
    top: float = 0.0
    width: float = caps.MAX_PAGE_WIDTH_MM
    height: float = 279.364

    brightness: int = 0
    contrast: int = 0
    threshold: int = 128

    df_thickness: bool = False
    df_length: bool = False
    rollerdeskew: bool = False
    swdeskew: bool = False
    swdespeck: int = 0
    swcrop: bool = False
    swskip: float = 0.0
    stapledetect: bool = False
    dropout_front: str = "None"
    dropout_back: str = "None"
    buffermode: bool = False

    # -- app-side post-processing ----------------------------------------
    # Not backend options: these are applied by the app to the scanned image
    # and are deliberately absent from argv. They exist because --swcrop trims
    # to the ink with no margin at all; see imaging.py.
    autocrop: bool = False
    autocrop_margin_mm: float = 4.0

    @property
    def is_duplex(self) -> bool:
        return self.source == caps.DUPLEX_SOURCE

    @property
    def threshold_active(self) -> bool:
        """--threshold is [inactive] outside Lineart, so we must not send it."""
        return self.mode == caps.THRESHOLD_MODE

    def snapped(self) -> ScanSettings:
        """Return a copy with all geometry on the device grid.

        Raises :class:`geometry.GeometryError` for a zero page dimension.
        """
        page_width = geometry.snap_positive_page(
            self.page_width, max_units=caps.MAX_PAGE_WIDTH_UNITS, name="page-width"
        )
        page_height = geometry.snap_positive_page(
            self.page_height, max_units=caps.MAX_PAGE_HEIGHT_UNITS, name="page-height"
        )
        # -x/-y are bounded by the page dimensions, which is exactly why
        # page-width/page-height are emitted first.
        width_units = geometry.to_units(page_width)
        height_units = geometry.to_units(page_height)
        return replace(
            self,
            page_width=page_width,
            page_height=page_height,
            left=geometry.snap(self.left, max_units=width_units),
            top=geometry.snap(self.top, max_units=height_units),
            width=geometry.snap(self.width, max_units=width_units),
            height=geometry.snap(self.height, max_units=height_units),
            swskip=geometry.snap_swskip(self.swskip),
            swdespeck=_clamp_int(self.swdespeck, caps.SWDESPECK_RANGE),
            brightness=_clamp_int(self.brightness, caps.BRIGHTNESS_RANGE),
            contrast=_clamp_int(self.contrast, caps.CONTRAST_RANGE),
            threshold=_clamp_int(self.threshold, caps.THRESHOLD_RANGE),
            autocrop_margin_mm=_clamp_float(self.autocrop_margin_mm, AUTOCROP_MARGIN_RANGE),
        )

    # -- serialisation for config.toml ------------------------------------
    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScanSettings:
        """Build from persisted data, ignoring unknown or malformed keys."""
        fields = {f.name: f.type for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key not in fields:
                continue
            kwargs[key] = value
        try:
            return cls(**kwargs)
        except TypeError:
            return cls()


def _clamp_int(value: int, bounds: tuple[int, int]) -> int:
    lo, hi = bounds
    return min(max(int(value), lo), hi)


def _clamp_float(value: float, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return min(max(float(value), lo), hi)
