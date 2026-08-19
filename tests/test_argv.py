"""Command line construction. Option order is the whole point here."""

from __future__ import annotations

import pytest

from sheaf import caps
from sheaf.argv import build_argv, display_command
from sheaf.geometry import GeometryError
from sheaf.presets import BUILTIN_PRESETS
from sheaf.settings import ScanSettings

DEVICE = "canon_dr:libusb:001:022"
BATCH = "/tmp/scan/p%04d.png"


def argv_for(settings: ScanSettings) -> list[str]:
    return build_argv(DEVICE, settings, BATCH)


def test_page_width_precedes_x_and_page_height_precedes_y():
    # The backend derives the -l/-x range from page-width and the -t/-y range
    # from page-height, and scanimage applies options in argv order. Emitting
    # -y before --page-height silently clamps a 355 mm scan to 279.364 mm.
    argv = argv_for(ScanSettings())
    assert argv.index("--page-width") < argv.index("-x")
    assert argv.index("--page-height") < argv.index("-y")
    assert argv.index("--page-width") < argv.index("-l")
    assert argv.index("--page-height") < argv.index("-t")


def test_geometry_is_emitted_in_one_contiguous_ordered_block():
    argv = argv_for(ScanSettings())
    order = ["--page-width", "--page-height", "-l", "-t", "-x", "-y"]
    positions = [argv.index(flag) for flag in order]
    assert positions == sorted(positions)
    # flag/value pairs, so each is two apart from the next
    assert positions == list(range(positions[0], positions[0] + 12, 2))


def test_tall_scan_survives_argv_construction():
    settings = ScanSettings(page_height=355.554, height=355.554)
    argv = argv_for(settings)
    assert argv[argv.index("-y") + 1] == "355.55419921875"
    assert argv[argv.index("--page-height") + 1] == "355.55419921875"


def test_zero_page_height_is_refused():
    with pytest.raises(GeometryError):
        argv_for(ScanSettings(page_height=0.0))


def test_threshold_only_sent_in_lineart():
    assert "--threshold" not in argv_for(ScanSettings(mode="Gray", threshold=120))
    assert "--threshold" not in argv_for(ScanSettings(mode="Color", threshold=120))
    lineart = argv_for(ScanSettings(mode="Lineart", threshold=120))
    assert lineart[lineart.index("--threshold") + 1] == "120"


def test_batch_and_progress_options_present():
    argv = argv_for(ScanSettings())
    assert f"--batch={BATCH}" in argv
    assert "--batch-print" in argv  # gives us page filenames as they arrive
    assert "--progress" in argv
    assert "--format=png" in argv  # lossless; PDF assembly happens later


def test_disabled_booleans_are_omitted_entirely():
    argv = argv_for(ScanSettings())
    for flag in ("--swcrop", "--swdeskew", "--rollerdeskew", "--buffermode"):
        assert not any(a.startswith(flag) for a in argv)
    assert "--swdespeck" not in argv
    assert "--swskip" not in argv


def test_enabled_booleans_use_explicit_yes():
    argv = argv_for(ScanSettings(swcrop=True, swdeskew=True, buffermode=True))
    assert "--swcrop=yes" in argv
    assert "--swdeskew=yes" in argv
    assert "--buffermode=yes" in argv


def test_dropout_only_sent_when_not_none():
    assert "--dropout-front" not in argv_for(ScanSettings())
    argv = argv_for(ScanSettings(dropout_front="Enhance Red"))
    assert argv[argv.index("--dropout-front") + 1] == "Enhance Red"


def test_receipt_preset_argv_snapshot():
    preset = BUILTIN_PRESETS[0]
    assert preset.name == "Receipt (auto-size)"
    assert display_command(argv_for(preset.settings)) == (
        "scanimage -d canon_dr:libusb:001:022 --format=png "
        "--batch=/tmp/scan/p%04d.png --batch-print --progress "
        "--source 'ADF Front' --mode Gray --resolution 300 "
        "--page-width 216.04150390625 --page-height 355.55419921875 "
        "-l 0.0 -t 0.0 -x 216.04150390625 -y 355.55419921875 "
        "--swcrop=yes --swdeskew=yes --swdespeck 2 --swskip 2.0001220703125"
    )


def test_default_receipt_preset_scans_full_width():
    # An 80 mm window is centred in the 216 mm paper path, so an off-centre
    # till roll gets clipped before swcrop can see its edges. Verified on paper.
    default = BUILTIN_PRESETS[0].settings
    assert default.page_width == pytest.approx(caps.MAX_PAGE_WIDTH_MM)
    assert default.width == pytest.approx(caps.MAX_PAGE_WIDTH_MM)
    assert default.swcrop


@pytest.mark.parametrize("preset", BUILTIN_PRESETS, ids=lambda p: p.name)
def test_builtin_presets_are_valid(preset):
    argv = argv_for(preset.settings)
    assert argv[argv.index("--source") + 1] in caps.SOURCES
    assert argv[argv.index("--mode") + 1] in caps.MODES
    assert int(argv[argv.index("--resolution") + 1]) in caps.RESOLUTIONS
    # every geometry value on the grid and within range
    for flag, limit in (
        ("--page-width", caps.MAX_PAGE_WIDTH_MM),
        ("--page-height", caps.MAX_PAGE_HEIGHT_MM),
        ("-x", caps.MAX_PAGE_WIDTH_MM),
        ("-y", caps.MAX_PAGE_HEIGHT_MM),
    ):
        value = float(argv[argv.index(flag) + 1])
        assert 0 < value <= limit


def test_receipt_presets_enable_the_options_naps2_cannot_reach():
    for preset in BUILTIN_PRESETS[:2]:
        assert preset.settings.swcrop
        assert preset.settings.swdeskew
        assert preset.settings.page_height == pytest.approx(caps.MAX_PAGE_HEIGHT_MM)
