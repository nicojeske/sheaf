"""Running scanimage as a subprocess and reporting progress as pages arrive."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from .argv import BATCH_PATTERN, build_argv, display_command
from .settings import ScanSettings
from .status import Outcome, ScanResult, classify

#: --progress writes "Progress: %3.1f%%\r" to stderr — carriage return, no
#: newline (verified in the scanimage binary). Reading stderr line-by-line would
#: therefore never yield a progress update until the process exited, so stderr
#: is read in chunks and split on CR as well as LF.
_PROGRESS_RE = re.compile(r"Progress:\s*([0-9]+(?:\.[0-9]+)?)%")
_UNKNOWN_PROGRESS = "Progress: (unknown)"

#: How long SIGTERM gets before SIGKILL. Generous on purpose: scanimage only
#: unwinds after the in-flight page read completes, and swcrop/swdeskew buffer a
#: whole 355 mm page. Killing it instead leaves the scanner wedged — visible in
#: lsusb but unopenable until its USB port is reset — so SIGKILL is a genuine
#: last resort here, not a routine escalation.
TERMINATE_GRACE = 20.0
_STDERR_TAIL = 20

#: scanimage prints this when the device string no longer resolves, e.g.
#: "open of device canon_dr:libusb:001:022 failed: Invalid argument".
_OPEN_FAILURE = "open of device"


class ScanJob:
    """One ``scanimage --batch`` run on a worker thread.

    Callbacks are invoked from that worker thread; the GTK layer wraps them in
    ``GLib.idle_add``. Keeping this class free of any gi import is what makes it
    testable and keeps the scan core independent of the UI.
    """

    def __init__(
        self,
        device: str,
        settings: ScanSettings,
        *,
        on_page: Callable[[Path], None],
        on_progress: Callable[[float | None], None],
        on_finished: Callable[[ScanResult], None],
        on_message: Callable[[str], None] | None = None,
        tmpdir: Path | None = None,
    ) -> None:
        self._device = device
        self._settings = settings
        self._on_page = on_page
        self._on_progress = on_progress
        self._on_finished = on_finished
        self._on_message = on_message
        self._tmpdir = tmpdir or Path(tempfile.mkdtemp(prefix="sheaf-"))
        self._proc: subprocess.Popen[bytes] | None = None
        self._cancelled = threading.Event()
        self._pages: list[Path] = []
        self._stderr_tail: list[str] = []
        self._device_open_failed = False
        self._killed = False
        self._thread: threading.Thread | None = None
        self.argv = build_argv(device, settings, str(self._tmpdir / BATCH_PATTERN))

    @property
    def settings(self) -> ScanSettings:
        return self._settings

    @property
    def command(self) -> str:
        return display_command(self.argv)

    @property
    def tmpdir(self) -> Path:
        return self._tmpdir

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr_tail)

    @property
    def killed(self) -> bool:
        """True if SIGTERM was ignored and the child had to be SIGKILLed.

        The scanner is very likely wedged afterwards and needs a USB reset.
        """
        return self._killed

    @property
    def device_open_failed(self) -> bool:
        """True when scanimage could not open the device at all.

        The USB bus:device address in the device string changes whenever the
        scanner is replugged or re-enumerated, so a string discovered at launch
        can go stale while the app is open. That is recoverable: look the device
        up again and retry.
        """
        return self._device_open_failed

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="scan-job", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Stop the scan, actually killing the child process."""
        self._cancelled.set()
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=TERMINATE_GRACE)
        except subprocess.TimeoutExpired:
            self._killed = True
            proc.kill()
        except OSError:
            pass

    # -- worker thread ----------------------------------------------------
    def _run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            self._on_finished(
                ScanResult(Outcome.ERROR, "Could not start scanimage", str(exc), -1, 0)
            )
            return

        proc = self._proc
        stderr_thread = threading.Thread(
            target=self._read_stderr, args=(proc,), name="scan-stderr", daemon=True
        )
        stderr_thread.start()

        # --batch-print writes one finished filename per line to stdout, so
        # pages can be shown as they arrive rather than after the whole batch.
        assert proc.stdout is not None
        for raw in proc.stdout:
            name = raw.decode("utf-8", "replace").strip()
            if not name:
                continue
            page = Path(name)
            self._pages.append(page)
            self._on_page(page)

        exit_code = proc.wait()
        stderr_thread.join(timeout=2.0)
        self._on_finished(
            classify(exit_code, len(self._pages), cancelled=self._cancelled.is_set())
        )

    def _read_stderr(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stderr is not None
        for chunk in _split_lines(_read_chunks(proc.stderr.fileno())):
            self._handle_stderr_line(chunk)

    def _handle_stderr_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        match = _PROGRESS_RE.search(line)
        if match:
            self._on_progress(float(match.group(1)) / 100.0)
            return
        if line.startswith(_UNKNOWN_PROGRESS[:9]) and "unknown" in line:
            self._on_progress(None)
            return
        if _OPEN_FAILURE in line:
            self._device_open_failed = True
        # Everything else is diagnostic: keep a short tail for error reporting
        # and pass it on for the log view.
        self._stderr_tail.append(line)
        del self._stderr_tail[:-_STDERR_TAIL]
        if self._on_message is not None:
            self._on_message(line)


def _read_chunks(fd: int, size: int = 4096) -> Iterator[bytes]:
    while True:
        try:
            data = os.read(fd, size)
        except OSError:
            return
        if not data:
            return
        yield data


def _split_lines(chunks: Iterator[bytes]) -> Iterator[str]:
    """Yield lines from byte chunks, treating CR and LF alike."""
    buffer = b""
    for chunk in chunks:
        buffer += chunk
        parts = re.split(rb"[\r\n]", buffer)
        buffer = parts.pop()
        for part in parts:
            yield part.decode("utf-8", "replace")
    if buffer:
        yield buffer.decode("utf-8", "replace")
