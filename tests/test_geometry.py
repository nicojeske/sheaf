"""Geometry snapping — the fixed-point grid is easy to get subtly wrong."""

from __future__ import annotations

import pytest

from sheaf import caps, geometry


def test_grid_is_fixed_point_quantised_not_plain_1_1200_inch():
    # 25.4/1200 = 0.021166... but the backend advertises 0.0211639, which is
    # round(65536 * 25.4/1200) / 65536. Using the former is off by half a step.
    assert caps.GRID_MM == pytest.approx(0.0211639, abs=1e-7)
    assert caps.GRID_MM != pytest.approx(25.4 / 1200, abs=1e-9)


def test_maxima_match_the_firmware_reported_units():
    # Long Document Mode firmware (NOTES.md §13): the device reports
    # "max width: 10208 (8.51 in) / max length: 47244 (39.37 in)".
    assert caps.MAX_PAGE_WIDTH_UNITS == 10208
    assert caps.MAX_PAGE_HEIGHT_UNITS == 47244
    assert caps.MAX_PAGE_WIDTH_MM == pytest.approx(216.042, abs=0.001)
    assert caps.MAX_PAGE_HEIGHT_MM == pytest.approx(999.869, abs=0.001)


def test_stock_firmware_height_is_recorded_for_reference():
    # Unpatched firmware reports 16800 (355.554 mm); kept so the difference
    # between stock and patched devices stays explicit.
    assert caps.STOCK_MAX_PAGE_HEIGHT_UNITS == 16800
    assert caps.STOCK_MAX_PAGE_HEIGHT_UNITS * caps.GRID_MM == pytest.approx(
        355.554, abs=0.001
    )


@pytest.mark.parametrize("mm", [0.5, 12.3, 80.0, 210.0, 297.0, 355.5])
def test_snapped_values_land_exactly_on_the_grid(mm):
    snapped = geometry.snap_page_height(mm)
    units = snapped / caps.GRID_MM
    assert units == pytest.approx(round(units), abs=1e-9)


@pytest.mark.parametrize("mm", [355.554, 400.0, 1e6])
def test_snapping_never_exceeds_the_maximum(mm):
    assert geometry.snap_page_height(mm) <= caps.MAX_PAGE_HEIGHT_MM
    assert geometry.to_units(geometry.snap_page_height(mm)) <= caps.MAX_PAGE_HEIGHT_UNITS


@pytest.mark.parametrize("mm", [216.042, 300.0])
def test_snapping_width_never_exceeds_the_maximum(mm):
    assert geometry.to_units(geometry.snap_page_width(mm)) <= caps.MAX_PAGE_WIDTH_UNITS


def test_negative_values_clamp_to_zero():
    assert geometry.snap(-10.0, max_units=1000) == 0.0


def test_zero_page_dimension_is_rejected():
    # --page-height 0 collapses the scan area to -y 0..0mm rather than meaning
    # "automatic". It must never reach the command line.
    with pytest.raises(geometry.GeometryError):
        geometry.snap_positive_page(
            0.0, max_units=caps.MAX_PAGE_HEIGHT_UNITS, name="page-height"
        )
    with pytest.raises(geometry.GeometryError):
        geometry.snap_positive_page(
            0.001, max_units=caps.MAX_PAGE_HEIGHT_UNITS, name="page-height"
        )


def test_format_number_keeps_full_precision():
    # A value rounded for display does not survive the round trip through
    # scanimage's fixed-point conversion, which is what causes the
    # "rounded value of" warnings.
    text = geometry.format_number(geometry.snap_page_height(355.554))
    assert text == "355.55419921875"
    assert float(text) == geometry.snap_page_height(355.554)


def test_swskip_snaps_to_its_own_grid_and_clamps_below_the_maximum():
    assert geometry.snap_swskip(2.0) == pytest.approx(2.00012, abs=1e-5)
    assert geometry.snap_swskip(100.0) <= 100.0
    assert geometry.snap_swskip(150.0) <= 100.0
    assert geometry.snap_swskip(-5.0) == 0.0
