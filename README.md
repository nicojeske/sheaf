# Sheaf

A small GTK4/libadwaita app for scanning documents and receipts on a **Canon
imageFORMULA P-208II**, with one-click upload to
[Paperless-ngx](https://docs.paperless-ngx.com/).

*sheaf* (n.) — a bundle of sheets held together; what you put in the feeder.

It exists because NAPS2 cannot reach two options that this scanner's `canon_dr`
backend does offer — `swcrop` (trim empty borders) and `swdeskew` (straighten) —
since its SANE driver silently drops `Bool`-typed backend options. Those two are
what make an auto-sized receipt scan possible: scan the full 355 mm strip and let
the driver crop it down to the actual receipt.

The app targets exactly one scanner on exactly one backend. It drives
`scanimage` as a subprocess rather than linking libsane, so what it does is
exactly reproducible on the command line — the full command is always visible in
the app and copyable.

## Requirements

Everything except two Python packages comes from the system:

```
sudo pacman -S sane gtk4 libadwaita python-gobject python-pillow python-requests uv
```

`img2pdf` and `keyring` are installed into the project venv by `run.sh`
(`img2pdf` is not in the Arch repos, only the AUR, which is why a venv is used at
all).

## Install and run

```sh
git clone <this repo> ~/projects/sheaf
cd ~/projects/sheaf
./run.sh
```

`run.sh` creates `.venv` **with `--system-site-packages`** (PyGObject cannot be
installed from PyPI reliably, so `gi`, GTK4 and libadwaita are inherited from the
system), installs the project into it, and launches the app.

A plain `uv run` will **not** work: it builds an isolated venv with no `gi`.

### Without a venv

```sh
pip install --user img2pdf keyring tomli-w
PYTHONPATH=src python3 -m sheaf
```

### Desktop entry

```sh
install -Dm644 data/dev.njeske.Sheaf.desktop ~/.local/share/applications/dev.njeske.Sheaf.desktop
```

Edit `Exec=` in that file if the project does not live in
`/home/njeske/projects/scanner`.

## Using it

The sidebar mirrors the backend's own option groups — Standard, Geometry,
Enhancement, Advanced — so every control maps one-to-one onto a `scanimage`
option. The **scanimage command** expander at the bottom always shows the exact
command that will run.

**After scanning** is the one group that is not a backend group. It holds the
app's own post-processing, which is why those controls do not appear in the
echoed command.

### Presets

| Preset | Page | Scan area | Mode | DPI | Driver options | After scanning |
|---|---|---|---|---|---|---|
| **Receipt (auto-size)** | 216 × 355.6 mm | full page | Gray | 300 | swcrop, swdeskew, swdespeck 2, swskip 2 | crop, 4 mm margin |
| Receipt (80 mm window) | 80 × 355.6 mm | full page | Gray | 300 | same | same |
| A4 document | 210 × 297 mm | full page | Gray | 300 | swdeskew | — |
| A4 duplex | 210 × 297 mm | full page | Gray | 300 | swdeskew, ADF Duplex | — |

**Receipt (auto-size)** is the default on first run. It scans the full 35.5 cm
strip and lets `swcrop` trim it down, so a 12 cm till receipt comes out as a
12 cm page rather than 35 cm of whitespace. Verified on a real receipt: 355.6 mm
in, 308.9 mm out. `swcrop` and `swdeskew` buffer the whole page in the driver, so
these scans are noticeably slower — that is the trade.

It scans the **full 216 mm width** on purpose. `--page-width` centres the scan
window in the paper path, so a narrow 80 mm window is an aperture in the middle
of it, and a till roll fed a few millimetres off centre loses an edge — on the
first real test that cost the first character of every line. Scanning full width
and letting `swcrop` find the edges avoids that entirely. **Receipt (80 mm
window)** keeps the narrow behaviour for smaller, faster scans if you are
confident about how the paper feeds.

Use **Save as preset…** to store your own; they are persisted and appear in the
same list.

### Cropping, and why there are two of them

`swcrop` trims to the ink boundary and stops. It leaves **no margin at all** —
on the first real receipt the text ran to the very first pixel column — which
looks wrong on paper and gives OCR nothing to work with.

So **Crop to content with a margin** (under *After scanning*) does the job again
in the app, where the box can be padded. It finds the content itself and keeps
the margin you set, 4 mm by default. Where the scan has no whitespace left to
keep — which is exactly what `swcrop` hands over — the margin is *added*, padded
with the paper colour sampled from the page, so the seam is invisible.

Detection ignores dust. Rather than taking the bounding box of dark pixels —
where one speck at the edge of the sheet gives back the full 216 mm width — the
page is reduced to a grid of one-millimetre blocks and only connected patches of
several blocks count as content. It errs outwards by up to a millimetre, which is
the right direction: a crop a hair too generous is invisible, one a hair too
tight shaves the digit off a total.

The two crops compose, and the app's own is the one to reach for. `swcrop` still
earns its place because it deskews and crops together in the driver; leave both
on for receipts. If you want the app to do all of it, turn `swcrop` off and raise
the margin.

Each card shows what the page actually came out as — `78 × 309 mm`, not
`216 × 356 mm` — so a crop that went wrong is visible without exporting
anything. The crop is metadata: the scanned PNG is never rewritten, so the
**Crop to content** button in the preview window switches it off and back on
freely.

### Limits worth knowing

- **355.5 mm is a hard ceiling.** The `canon_dr` backend implements no long
  document mode and no auto-length detection, so a receipt longer than 355 mm
  cannot be scanned in one pass. Canon's Windows driver can; that is unreachable
  on Linux.
- **ADF Duplex produces two images per sheet**, front then back. Pages are
  labelled "Sheet 3 (back)" accordingly.
- **Threshold only applies in Lineart mode**; the control is disabled otherwise
  and the option is not sent.
- There is no flatbed and no "ADF Back" source, and only six resolutions
  (100/150/200/240/300/400/600) — that is what the firmware reports.

### Pages

Scanned pages appear as thumbnails while the batch is still running. Each page
can be rotated, previewed, reordered or deleted. Rotation and cropping are
metadata only — the scanned PNG is never rewritten, so both cost nothing, are
reversible, and are applied once at export time.

Every card is the same size whatever shape the page is, and the thumbnail is
drawn inside it at its own aspect ratio: an A4 page nearly fills the box, a till
roll is a narrow strip down the middle. The caption underneath gives the page's
real size in millimetres.

**Preview** opens the page at the resolution it was scanned at, with zoom
(buttons, or <kbd>Ctrl</kbd>+scroll) and *Fit to window*. Fit on a 355 mm strip
leaves the text unreadably small, so zooming in is the point; 100% means one scan
pixel per screen pixel.

Selecting nothing means "all pages". Select pages to save or upload a subset.

## Paperless-ngx

Open **☰ → Paperless settings…** and enter the base URL (e.g.
`https://paperless.example.com`) and an API token, then press **Test
connection**.

The token is stored in your **keyring** via libsecret. If no keyring backend is
available it falls back to `~/.config/scanner/token` with `0600` permissions, and
the settings dialog says so rather than downgrading silently. The token is never
written to `config.toml` and never logged.

**Send to Paperless** collects a title, created date, correspondent, document
type and tags (fetched from the server, all pages of each list), assembles the
PDF, uploads it, and then **polls the consumption task** until the server reports
success or failure. Paperless answers an upload with a task UUID, not a finished
document — consumption happens afterwards and can still fail, most commonly
because a rescanned receipt is rejected as a duplicate. You get the real result
("Consumed as document #123") or the real reason for failure.

On failure the local PDF is kept (under `~/.cache/scanner/`) and the dialog
offers **Try again**, so nothing is lost.

OCR is not performed locally — Paperless-ngx runs it server-side on consumption.

## Configuration

`${XDG_CONFIG_HOME:-~/.config}/scanner/config.toml` holds window state,
last-used settings, your presets, and the Paperless URL and defaults. The
directory is still `scanner/` rather than `sheaf/`, and the keyring entry is
still `scanner-paperless`: renaming them when the app was renamed would orphan
an existing config and token for no visible gain.

Two things are deliberately **not** stored there: the API token (keyring), and
the scanner's device string. `canon_dr:libusb:001:022` encodes a USB bus address
that changes on replug and reboot, so it is discovered at every launch.

## Permissions

Access to the scanner works out of the box on this machine — no group membership
is needed. systemd-logind grants the locally logged-in user an ACL on the USB
device node:

```console
$ getfacl /dev/bus/usb/001/022 | grep njeske
user:njeske:rw-
```

If a scan ever fails with **"No permission to use the scanner"** (SANE
`ACCESS_DENIED`, exit 11), check that ACL. It applies to a local graphical
session, so it will be missing over plain SSH. The `sane` package also grants the
`saned` group access via `/usr/lib/udev/rules.d/66-saned.rules`, so
`sudo gpasswd -a $USER saned` (then log out and back in) is the fallback.

## Error handling

`scanimage` exits with the raw SANE status code; each one is mapped to a plain
message rather than a traceback.

| Exit | SANE status | Shown as |
|---|---|---|
| 0 | GOOD | Scanned N pages |
| 1 | (open failed) | Could not open the scanner — reset and retry, automatically |
| 2 | CANCELLED | Scan cancelled |
| 3 | DEVICE_BUSY | Scanner is busy — another application is holding it |
| 5 | EOF | Scan finished |
| 6 | JAMMED | Paper jam — clear the feeder |
| 7 | NO_DOCS | Feeder is empty (**or** normal end of a batch, see below) |
| 4 | INVAL | The scanner rejected a setting |
| 8 | COVER_OPEN | Cover is open |
| 10 | NO_MEM | Out of memory — try a lower resolution |
| 9 | IO_ERROR | Communication failed — check the USB cable |
| 11 | ACCESS_DENIED | No permission to use the scanner |

A `--batch` run ends when the feeder runs out. Measured on real paper: that is
exit **0** if at least one page was produced, and exit **7** only if none was —
so exit 7 means you forgot to load paper. (Exit 7 *with* pages is treated as
success too, defensively.) Scanner absent at launch, unplugged mid-scan, and
`scanimage` missing from `PATH` are all handled as first-class states.

### If the scanner "disappears"

Killing `scanimage` mid-scan leaves the P-208II visible in `lsusb` but
unopenable — `scanimage -L` stops listing it, and it does not recover on its own.
The app avoids causing this (Cancel gives `SIGTERM` 20 seconds, since the driver
only unwinds after the page in flight finishes) and recovers from it when it
happens: it resets the scanner's USB port, which is the software equivalent of
replugging and needs no root. That runs automatically after a failed open or a
cancel that had to escalate, and is available manually as **☰ → Reset scanner**.

The same recovery covers a device string that went stale because the scanner was
replugged and came back at a different USB address.

## Development

```sh
.venv/bin/python -m pytest        # 99 tests, no GTK involved
```

The pure logic is deliberately free of any `gi` import so it can be tested:
`argv.py` (option ordering), `geometry.py` (the fixed-point grid), `status.py`
(exit codes), `imaging.py` (crop detection and padding) and
`upload/paperless.py` (request building). There are no GTK integration tests.

The split that keeps that possible is worth knowing when adding to the image
side: `imaging.py` is pure Pillow, `thumbnails.py` is the GTK edge that runs it
on a worker thread and turns the result into a texture, and `preview.py` owns the
zoomable page window.

See `NOTES.md` for the device behaviour that was verified, including several
points where reality differs from the documentation.
