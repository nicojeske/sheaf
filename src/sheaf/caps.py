"""Hardcoded capability model for the Canon imageFORMULA P-208II on canon_dr.

This app targets exactly one device on exactly one backend. Every range and
choice below was read off the real device with
``scanimage --help -d canon_dr:...`` and is authoritative; nothing here is
discovered at runtime and nothing else in the app should hardcode a limit.
"""

from __future__ import annotations

BACKEND_PREFIX = "canon_dr:"
USB_ID = "1083:165f"
MODEL = "CANON P-208II"

SOURCES: tuple[str, ...] = ("ADF Front", "ADF Duplex")
MODES: tuple[str, ...] = ("Lineart", "Gray", "Color")
RESOLUTIONS: tuple[int, ...] = (100, 150, 200, 240, 300, 400, 600)

DROPOUT_VALUES: tuple[str, ...] = (
    "None",
    "Red",
    "Green",
    "Blue",
    "Enhance Red",
    "Enhance Green",
    "Enhance Blue",
)

# --- Geometry grid -----------------------------------------------------------
#
# The backend advertises "steps of 0.0211639" mm. That is *not* 25.4/1200
# (= 0.0211666...); it is 1/1200 inch after quantisation into SANE's 16.16
# fixed-point format: round(65536 * 25.4/1200) = 1387, and 1387/65536 =
# 0.02116394... Using 25.4/1200 produces values half a step off and makes
# scanimage emit "rounded value of ..." on stderr. Verified on the device.
GRID_MM = 1387 / 65536

# Maxima expressed in grid units, which is how the firmware reports them
# (max width 10208, max length 47244 — see NOTES.md).
#
# The length maximum is firmware-dependent. Stock P-208II firmware reports
# 16800 (355.554 mm); this device runs the patched firmware from NOTES.md §13,
# which enables Long Document Mode and reports 47244 (999.869 mm). Both the
# advertised ceiling and the runtime feed-length enforcement follow this flag,
# so 47244 is genuinely scannable, not merely advertised.
MAX_PAGE_WIDTH_UNITS = 10208
MAX_PAGE_HEIGHT_UNITS = 47244
STOCK_MAX_PAGE_HEIGHT_UNITS = 16800  # unpatched firmware, for reference

MAX_PAGE_WIDTH_MM = MAX_PAGE_WIDTH_UNITS * GRID_MM   # 216.0415...
MAX_PAGE_HEIGHT_MM = MAX_PAGE_HEIGHT_UNITS * GRID_MM  # 999.8692...

# The strip the receipt presets scan. This is a *product* choice, not the
# hardware ceiling: the scanner fills the whole requested window regardless of
# how short the paper is, so scanning the full 1000 mm for every receipt would
# be needlessly slow and large. 16800 units (355.554 mm, US legal) is the
# length the receipt workflow was tuned against — see NOTES.md §8/§8d — and it
# stayed put when Long Document Mode raised the ceiling to 47244.
RECEIPT_STRIP_HEIGHT_MM = 16800 * GRID_MM  # 355.5542...

# -x and -y are capped by the *current* page-width / page-height rather than by
# a fixed maximum, so their ceiling is the page maximum above. This is why
# geometry must be emitted page-width/page-height first; see argv.py.
MAX_SCAN_WIDTH_MM = MAX_PAGE_WIDTH_MM
MAX_SCAN_HEIGHT_MM = MAX_PAGE_HEIGHT_MM

BRIGHTNESS_RANGE = (-127, 127)
CONTRAST_RANGE = (-127, 127)
THRESHOLD_RANGE = (0, 255)
SWDESPECK_RANGE = (0, 9)
SWSKIP_RANGE = (0.0, 100.0)
# --swskip advertises "steps of 0.100006", which is 0.1 after the same 16.16
# fixed-point quantisation as GRID_MM: round(65536 * 0.1) = 6554, and
# 6554/65536 = 0.1000061035... Snapping to a plain 0.1 makes scanimage print
# "rounded value of swskip from 2 to 2.00012". Verified on the device.
SWSKIP_GRID = 6554 / 65536

#: --threshold is reported [inactive] unless the mode is Lineart.
THRESHOLD_MODE = "Lineart"

#: ADF Duplex returns two images per sheet (front then back).
DUPLEX_SOURCE = "ADF Duplex"
