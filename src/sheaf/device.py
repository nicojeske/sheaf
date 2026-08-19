"""Finding the scanner.

The device string ``canon_dr:libusb:001:022`` encodes bus:device and changes on
replug and reboot, so it is discovered on every launch and never persisted.
``scanimage -f`` also lists unrelated devices — this machine reports
``v4l:/dev/video0`` alongside the scanner — so filtering on the backend prefix
is required, not merely defensive.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .caps import BACKEND_PREFIX, USB_ID

LIST_TIMEOUT = 30.0

#: USBDEVFS_RESET, i.e. _IO('U', 20). Resetting the port is the only way to
#: revive the scanner after scanimage is killed mid-scan: it stays visible in
#: lsusb but the backend can no longer open or even enumerate it. The logind ACL
#: on the device node makes this work without root. Verified.
_USBDEVFS_RESET = 0x5514

_SYSFS_USB = Path("/sys/bus/usb/devices")


class ScanimageMissing(RuntimeError):
    """scanimage is not installed or not on PATH."""


@dataclass(frozen=True, slots=True)
class DeviceLookup:
    device: str | None
    error: str | None = None
    #: Plugged in on USB but not openable by the backend — recoverable with
    #: :func:`reset_usb_device`.
    wedged: bool = False

    @property
    def found(self) -> bool:
        return self.device is not None


def scanimage_path() -> str | None:
    return shutil.which("scanimage")


def usb_device_node() -> Path | None:
    """The /dev/bus/usb path of the scanner, found by USB id via sysfs."""
    vendor, product = USB_ID.split(":")
    try:
        entries = sorted(_SYSFS_USB.iterdir())
    except OSError:
        return None
    for entry in entries:
        try:
            if (entry / "idVendor").read_text().strip() != vendor:
                continue
            if (entry / "idProduct").read_text().strip() != product:
                continue
            bus = int((entry / "busnum").read_text())
            dev = int((entry / "devnum").read_text())
        except (OSError, ValueError):
            continue
        return Path(f"/dev/bus/usb/{bus:03d}/{dev:03d}")
    return None


def usb_present() -> bool:
    """True when the scanner is plugged in, whether or not SANE can open it."""
    return usb_device_node() is not None


def reset_usb_device() -> bool:
    """Reset the scanner's USB port, the software equivalent of replugging it.

    Used to recover a scanner left wedged by a killed scan. Returns False if the
    device is absent or the reset was refused.
    """
    node = usb_device_node()
    if node is None:
        return False
    try:
        fd = os.open(node, os.O_WRONLY)
    except OSError:
        return False
    try:
        fcntl.ioctl(fd, _USBDEVFS_RESET, 0)
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


def find_device() -> DeviceLookup:
    """Look up the scanner. Blocking — call this off the UI thread."""
    program = scanimage_path()
    if program is None:
        raise ScanimageMissing(
            "scanimage was not found on PATH. Install sane-backends."
        )

    try:
        proc = subprocess.run(
            [program, "-f", "%d%n"],
            capture_output=True,
            text=True,
            timeout=LIST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return DeviceLookup(None, "Listing scanners timed out.")

    device = parse_device_list(proc.stdout)
    if device is not None:
        return DeviceLookup(device)

    detail = proc.stderr.strip().splitlines()
    message = detail[-1] if detail else None
    if usb_present():
        message = (
            "The scanner is plugged in but the driver cannot open it. "
            "It is probably wedged after an interrupted scan; resetting it "
            "should help."
        )
    return DeviceLookup(None, message, wedged=usb_present())


def parse_device_list(stdout: str) -> str | None:
    """Pick the canon_dr entry out of ``scanimage -f '%d%n'`` output."""
    for line in stdout.splitlines():
        name = line.strip()
        if name.startswith(BACKEND_PREFIX):
            return name
    return None
