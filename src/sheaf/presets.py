"""Built-in and user presets.

The Receipt presets are the reason this app exists: scan the full 355 mm strip
and let the driver's swcrop trim it down to the actual receipt, which NAPS2
cannot do because its SANE driver drops Bool options.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import caps
from .settings import ScanSettings

RECEIPT_WIDTH_MM = 80.0
"""Standard thermal till-roll width.

Deliberately *not* used by the default receipt preset. An 80 mm scan window is
centred in the 216 mm paper path, so a till roll fed a few millimetres off centre
has its left or right edge cut off before swcrop ever runs — verified on a real
receipt, which lost the first character of every line. Scanning full width and
letting swcrop find the edges is what actually auto-sizes reliably. See NOTES.md.
"""


@dataclass(frozen=True, slots=True)
class Preset:
    name: str
    settings: ScanSettings
    builtin: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "settings": self.settings.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Preset | None:
        name = data.get("name")
        if not isinstance(name, str) or not name:
            return None
        raw = data.get("settings")
        if not isinstance(raw, dict):
            return None
        return cls(name=name, settings=ScanSettings.from_dict(raw), builtin=False)


RECEIPT_MARGIN_MM = 4.0
"""Margin the app-side crop keeps around a receipt.

``--swcrop`` alone leaves none whatsoever — it trims to the ink and stops — so
the receipt presets pair it with the app's own crop, which re-finds the edges
and pads them out to this. See imaging.py and NOTES.md §8d.
"""


def _receipt(page_width: float, scan_width: float) -> ScanSettings:
    return ScanSettings(
        source="ADF Front",
        mode="Gray",
        resolution=300,
        page_width=page_width,
        page_height=caps.RECEIPT_STRIP_HEIGHT_MM,
        left=0.0,
        top=0.0,
        width=scan_width,
        height=caps.RECEIPT_STRIP_HEIGHT_MM,
        swcrop=True,
        swdeskew=True,
        swdespeck=2,
        swskip=2.0,
        autocrop=True,
        autocrop_margin_mm=RECEIPT_MARGIN_MM,
    )


def _a4(source: str) -> ScanSettings:
    return ScanSettings(
        source=source,
        mode="Gray",
        resolution=300,
        page_width=210.0,
        page_height=297.0,
        left=0.0,
        top=0.0,
        width=210.0,
        height=297.0,
        swdeskew=True,
    )


BUILTIN_PRESETS: tuple[Preset, ...] = (
    Preset(
        name="Receipt (auto-size)",
        settings=_receipt(caps.MAX_PAGE_WIDTH_MM, caps.MAX_PAGE_WIDTH_MM),
        builtin=True,
    ),
    Preset(
        name="Receipt (80 mm window)",
        settings=_receipt(RECEIPT_WIDTH_MM, RECEIPT_WIDTH_MM),
        builtin=True,
    ),
    Preset(name="A4 document", settings=_a4("ADF Front"), builtin=True),
    Preset(name="A4 duplex", settings=_a4(caps.DUPLEX_SOURCE), builtin=True),
)

DEFAULT_PRESET_NAME = BUILTIN_PRESETS[0].name

