"""Building the scanimage command line.

Argument *order* is load-bearing and is the single most important thing in this
module. scanimage applies options in argv order, and the canon_dr backend
derives the range of ``-t``/``-y`` from the current ``page-height`` (and
``-l``/``-x`` from ``page-width``). Emitting ``-y 355`` while page-height is
still its 279.364 default silently clamps the scan to 279 mm. This is the same
ordering bug that breaks tall pages in NAPS2 (cyanfish/naps2#281).

So geometry is always emitted as::

    --page-width W --page-height H -l L -t T -x X -y Y
"""

from __future__ import annotations

from .geometry import format_number
from .settings import ScanSettings

SCANIMAGE = "scanimage"

#: Filename pattern handed to --batch. %04d is substituted by scanimage.
BATCH_PATTERN = "p%04d.png"


def build_argv(
    device: str,
    settings: ScanSettings,
    batch_target: str,
    *,
    program: str = SCANIMAGE,
) -> list[str]:
    """Return the full scanimage argv for a batch scan.

    ``batch_target`` is the ``--batch`` output pattern, e.g.
    ``/tmp/scan-xyz/p%04d.png``. ``settings`` is snapped onto the device grid
    here, so callers may pass raw user input.
    """
    s = settings.snapped()

    argv = [
        program,
        "-d",
        device,
        "--format=png",
        f"--batch={batch_target}",
        "--batch-print",
        "--progress",
        # Standard
        "--source",
        s.source,
        "--mode",
        s.mode,
        "--resolution",
        str(s.resolution),
        # Geometry — page dimensions first, always. See module docstring.
        "--page-width",
        format_number(s.page_width),
        "--page-height",
        format_number(s.page_height),
        "-l",
        format_number(s.left),
        "-t",
        format_number(s.top),
        "-x",
        format_number(s.width),
        "-y",
        format_number(s.height),
    ]

    # Enhancement. --threshold is [inactive] outside Lineart; sending it then is
    # at best noise, so it is omitted entirely.
    if s.brightness:
        argv += ["--brightness", str(s.brightness)]
    if s.contrast:
        argv += ["--contrast", str(s.contrast)]
    if s.threshold_active:
        argv += ["--threshold", str(s.threshold)]

    # Advanced. Booleans are emitted only when enabled — "no" is the device
    # default, and omitting them keeps the echoed command readable.
    if s.swcrop:
        argv.append("--swcrop=yes")
    if s.swdeskew:
        argv.append("--swdeskew=yes")
    if s.swdespeck:
        argv += ["--swdespeck", str(s.swdespeck)]
    if s.swskip:
        argv += ["--swskip", format_number(s.swskip)]
    if s.rollerdeskew:
        argv.append("--rollerdeskew=yes")
    if s.df_thickness:
        argv.append("--df-thickness=yes")
    if s.df_length:
        argv.append("--df-length=yes")
    if s.stapledetect:
        argv.append("--stapledetect=yes")
    if s.dropout_front != "None":
        argv += ["--dropout-front", s.dropout_front]
    if s.dropout_back != "None":
        argv += ["--dropout-back", s.dropout_back]
    if s.buffermode:
        argv.append("--buffermode=yes")

    return argv


def display_command(argv: list[str]) -> str:
    """Render argv as a copy-pasteable shell command.

    Geometry values carry full float precision so scanimage stays silent about
    rounding; that is faithful to what the app actually runs, so it is shown
    verbatim rather than prettified.
    """
    import shlex

    return shlex.join(argv)


def probe_argv(device: str, settings: ScanSettings, *, program: str = SCANIMAGE) -> list[str]:
    """argv that applies the settings but does not scan (``-n``).

    Used to verify that the backend accepts the geometry without complaint.
    """
    argv = build_argv(device, settings, "unused-%04d.png", program=program)
    # Drop the batch/output options and add -n.
    filtered = [
        a
        for a in argv
        if not a.startswith("--batch") and a not in ("--format=png", "--progress")
    ]
    return filtered + ["-n"]

