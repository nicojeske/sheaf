# Build a receipt/document scanner app for a Canon imageFORMULA P-208II

## Context

Build a small Linux desktop app for scanning documents — primarily **receipts** — from a
Canon imageFORMULA P-208II USB sheetfed scanner, with a one-click upload to
[Paperless-ngx](https://docs.paperless-ngx.com/).

Target machine: CachyOS (Arch-based), Linux 7.1.8, fish shell, Python 3.14.7, Wayland desktop.
Project directory `/home/njeske/projects/scanner` is currently empty — greenfield.

**This app targets exactly one scanner on exactly one backend (`canon_dr` via SANE).**
Do not build a driver abstraction layer, do not enumerate arbitrary SANE devices, do not
support TWAIN/WIA/eSCL. Hardcode the capability model described below — it was read off
this specific device and is authoritative.

### Why this app exists

The user currently uses NAPS2. NAPS2 has no auto page size option (for any driver), and its
SANE driver silently drops `Bool`-typed backend options — so `swcrop` and `swdeskew`, which
this scanner's backend *does* offer, are unreachable from NAPS2. Those two options are the
entire point of this app: they give proper auto-sized receipt scans. Everything else is
convenience around that.

---

## Verified hardware facts — treat as ground truth

Determined by direct inspection of the device. Do not re-derive; do verify anything you plan
to depend on that is marked *unverified*.

### Device identity

```
$ scanimage -L
device `canon_dr:libusb:001:022' is a CANON P-208II scanner
$ lsusb
Bus 001 Device 022: ID 1083:165f Canon Electronics, Inc. CANON   P-208II
```

sane-backends 1.4.0. USB VID:PID is `1083:165f`.

**The device string is not stable.** `libusb:001:022` encodes bus:device, which changes on
replug and reboot. Discover it at runtime — run `scanimage -f '%d%n'` and take the entry
whose name starts with `canon_dr:`. Never persist the full device string in config. Handle
"scanner not connected" as a first-class UI state, and offer a rescan.

### Firmware-reported limits

```
max width:  10208 (8.51 in)   →  216.042 mm
max length: 16800 (14.00 in)  →  355.554 mm
```

**355.554 mm is a hard ceiling.** The `canon_dr` backend implements no long-document mode
and no auto-length detection (`man sane-canon_dr` documents neither). Canon's Windows driver
has a Long Document Mode that exceeds this; it is unreachable on Linux. If a receipt is
longer than 355 mm, the app cannot scan it in one pass — say so clearly in the UI rather
than silently truncating.

### Complete option set

This is the full `scanimage --help -d canon_dr:...` output for the device. Every option the
app exposes must come from this list; there are no others.

```
Standard:
  --source ADF Front|ADF Duplex                       [ADF Front]
  --mode Lineart|Gray|Color                           [Gray]
  --resolution 100|150|200|240|300|400|600 dpi        [600]
Geometry:
  --page-width  0..216.042mm (step 0.0211639)         [215.872]
  --page-height 0..355.554mm (step 0.0211639)         [279.364]
  -l 0..215.872mm (step 0.0211639)                    [0]        top-left x
  -t 0..279.364mm (step 0.0211639)                    [0]        top-left y
  -x 0..215.872mm (step 0.0211639)                    [215.872]  width
  -y 0..279.364mm (step 0.0211639)                    [279.364]  height
Enhancement:
  --brightness -127..127                              [0]
  --contrast   -127..127                              [0]
  --threshold  0..255                                 [inactive]
Advanced:
  --df-thickness[=(yes|no)]                           [no]   double feed via thickness sensor
  --df-length[=(yes|no)]                              [no]   double feed via length compare
  --rollerdeskew[=(yes|no)]                           [no]   mechanical deskew (hardware)
  --swdeskew[=(yes|no)]                               [no]   digital deskew (driver)
  --swdespeck 0..9                                    [0]    remove lone dots <= N diameter
  --swcrop[=(yes|no)]                                 [no]   digitally trim empty borders
  --swskip 0..100%                                    [0]    discard pages below N% dark pixels
  --stapledetect[=(yes|no)]                           [no]   halt on stapled pages
  --dropout-front None|Red|Green|Blue|Enhance Red|Enhance Green|Enhance Blue  [None]
  --dropout-back  None|Red|Green|Blue|Enhance Red|Enhance Green|Enhance Blue  [None]
  --buffermode[=(yes|no)]                             [no]   async read into scanner memory
```

Note the device reports **no flatbed** and **no ADF Back** source, and only these six
resolutions. There is no halftone or monochrome mode despite the backend supporting them
generally — the firmware reports `halftone: 0`, `monochrome: 0`.

### Gotchas that will bite you — each one is verified

1. **`--page-height` must precede `-y` in argv, and `--page-width` must precede `-x`.**
   The backend derives the `-t`/`-y` range *from* the current `page-height`. With the default
   `page-height 279.364`, `-y` is capped at 279.364 and a request for 355 mm is silently
   clamped. Raising page-height first unlocks it:

   ```
   $ scanimage -d ... --page-height 355.554 -n --help
     -t 0..355.554mm  [0]
     -y 0..355.554mm  [355.554]
   ```

   scanimage applies options in argv order, so always emit geometry as
   `--page-width W --page-height H -l L -t T -x X -y Y`. This exact ordering bug is what
   breaks tall pages in NAPS2 ([issue #281](https://github.com/cyanfish/naps2/issues/281)).

2. **Never pass `--page-height 0`.** It looks like it might mean "auto/unlimited" — it does
   not. It collapses the scan area to `-y 0..0mm`, i.e. nothing. Verified.

3. **Round all geometry to the 0.0211639 mm grid** (= 1/1200 inch). Off-grid values make
   scanimage emit `rounded value of page-height from X to Y` on stderr. Compute as
   `round(mm / 25.4 * 1200) / 1200 * 25.4`, then clamp to the documented max.

4. **`--threshold` is `[inactive]` unless `--mode Lineart`.** Disable/hide the control in
   other modes and do not send the flag — sending it while inactive is at best noise.

5. **`--source ADF Duplex` returns two images per sheet** (front then back). Page count is
   2× sheet count; label pages accordingly.

6. **`swcrop`/`swdeskew`/`swdespeck` force the backend to buffer the whole image in RAM**
   and are explicitly documented as "somewhat simplistic" reimplementations of hardware
   features. They are noticeably slower. This is an acceptable tradeoff for receipts, but
   surface progress so the app doesn't look hung.

7. **`scanimage` exits with the raw SANE status code.** Verified: empty feeder → exit `7`.
   Map them:

   | Exit | SANE status | Meaning for the UI |
   |---|---|---|
   | 0 | GOOD | success |
   | 2 | CANCELLED | user cancelled |
   | 3 | DEVICE_BUSY | another app holds the scanner |
   | 5 | EOF | no more data |
   | 6 | JAMMED | **paper jam — tell the user to clear the feeder** |
   | 7 | NO_DOCS | feeder empty |
   | 8 | COVER_OPEN | cover open |
   | 9 | IO_ERROR | USB/comms failure |
   | 11 | ACCESS_DENIED | permissions — check udev/`scanner` group |

   *Unverified, must be confirmed with real paper:* in `--batch` mode, reaching the end of
   the stack likely also exits `7`. Treat "exit 7 **and** ≥1 page was produced" as normal
   completion, not an error. Confirm this empirically before shipping the error handling.

---

## Milestone 1 — scanning (do this first, get it working end to end)

### Stack

**Python 3.14 + GTK4 / libadwaita via PyGObject, driving `scanimage` as a subprocess.**

Rationale, so you don't second-guess it: native Wayland app matching the desktop; almost
everything is already installed; and shelling out to `scanimage` reproduces exactly the
behavior verified above, avoiding the fragility of the `python-sane` bindings. Do **not**
link libsane directly.

Already present system-wide: `gtk4 4.22.4`, `libadwaita 1.9.3`, `python-gobject 3.56.3`,
`python-pillow 12.3.0`, `python-requests 2.34.2`, `tesseract 5.5.3`, `uv`.
Not present, add as project dependencies: `img2pdf` (PDF assembly), `keyring` (token storage).

Manage the project with `uv`. PyGObject cannot be installed from PyPI reliably here — use
the system package and configure the venv accordingly (e.g. `uv venv --system-site-packages`),
or document a `--no-venv` run path. Provide a working run command in the README and verify it
actually launches before claiming done.

### Scanning pipeline

Scan with `--batch` so a whole stack goes through in one pass:

```
scanimage -d <device> --format=png --batch=<tmpdir>/p%04d.png --batch-print --progress \
  --source <src> --mode <mode> --resolution <dpi> \
  --page-width <W> --page-height <H> -l 0 -t 0 -x <X> -y <Y> \
  [--swcrop=yes] [--swdeskew=yes] [--swdespeck N] [--swskip N] \
  [--rollerdeskew=yes] [--df-thickness=yes] [--df-length=yes] [--stapledetect=yes] \
  [--brightness N] [--contrast N] [--threshold N] \
  [--dropout-front <v>] [--dropout-back <v>] [--buffermode=yes]
```

- `--batch-print` writes each finished filename to stdout — read it line by line to add
  thumbnails to the UI **as pages arrive**, not after the whole batch.
- `--progress` writes progress to stderr — feed a progress bar.
- Scan to PNG (lossless), then assemble to PDF with `img2pdf`. Do not round-trip through JPEG
  before the user has chosen output quality.
- Run the subprocess off the GTK main thread; marshal UI updates back with
  `GLib.idle_add`. A Cancel button must actually terminate the child process.

### UI

libadwaita, single window:

- **Left / sidebar:** the settings, grouped exactly as the backend groups them
  (Standard, Geometry, Enhancement, Advanced), with Advanced in a collapsed
  `AdwExpanderRow`. Every widget's range and default must match the table above.
- **Main area:** thumbnail grid of scanned pages. Per page: rotate, delete, reorder,
  and a full-size preview. Selecting nothing means "all pages".
- **Bottom bar:** `Scan`, `Save as PDF…`, `Save as images…`, and (Milestone 2)
  `Send to Paperless`.
- **Presets**, user-editable and persisted, with these built in:
  - **Receipt (auto-size)** — the flagship. `page-width 80`, `page-height 355.554`,
    `-x 80`, `-y 355.554`, mode `Gray` or `Color`, 300 dpi, `swcrop=yes`, `swdeskew=yes`,
    `swdespeck 2`, `swskip 2`. Scans the full 35.5 cm strip and lets the driver trim it
    down to the actual receipt. Verify against a real receipt that the output is tightly
    cropped and not 355 mm of whitespace.
  - **Receipt (full width)** — same but `page-width`/`-x` at 216.042/215.872, for wide slips.
  - **A4 document** — 210×297, `Gray`, 300 dpi, `swdeskew=yes`.
  - **A4 duplex** — as above with `--source ADF Duplex`.
- Show the exact `scanimage` command line somewhere inspectable (an expander or a
  "copy command" button). It makes debugging and trust enormously easier.
- Persist last-used settings and window state to
  `${XDG_CONFIG_HOME:-~/.config}/scanner/config.toml`.

### Error handling

Map every exit code from the table to a clear, actionable `AdwToast` or dialog. Never show a
raw traceback for an expected condition (empty feeder, jam, unplugged, busy). Also handle:
scanner absent at launch, scanner unplugged mid-scan, and `scanimage` missing from PATH.

---

## Milestone 2 — Paperless-ngx upload

Do not start this until Milestone 1 works. But **define its interface during Milestone 1** so
the scan core carries no upload knowledge: the scanning layer produces a PDF path plus
metadata, and an `Uploader` protocol consumes it. One implementation now (Paperless), room
for others later without touching the scan code.

### Verified API surface (paperless-ngx docs)

Upload:

```
POST {base_url}/api/documents/post_document/
Authorization: Token <token>
Accept: application/json; version=10
Content-Type: multipart/form-data
```

Fields — `document` (file, **required**) plus optional `title` (string),
`created` (`2016-04-19` or `2016-04-19 06:15:00+02:00`), `correspondent` (int id),
`document_type` (int id), `storage_path` (int id), `tags` (int id, **repeat the field** for
multiple), `archive_serial_number` (string), `custom_fields` (array of ids, or object
mapping id → value).

Returns **HTTP 200 with a consumption task UUID as the JSON body** — note this is *not* a
finished document. Poll `GET /api/tasks/?task_id={uuid}` until the task reports success or
failure; on success it carries the resulting document id. Surface that as real feedback
("Consumed as document #123") rather than a fire-and-forget toast, and surface failures too —
paperless rejects duplicates at consumption time, which is a very likely outcome when
rescanning a receipt.

Metadata pickers: `GET /api/tags/`, `/api/correspondents/`, `/api/document_types/`. These are
paginated — follow `next` until exhausted; do not assume one page. Cache the lists, with a
manual refresh.

Send `Accept: application/json; version=10` on every request (10 is current, 9 also
supported) so a server upgrade can't silently change response shapes.

### Configuration and secrets

- Base URL and default tags/correspondent/document type live in `config.toml`.
- **The API token goes in `keyring`** (libsecret), never in the config file or the repo.
  If the keyring backend is unavailable, fall back to a `0600` file and say so in the UI.
- Provide a "Test connection" button that verifies URL + token and reports clearly.
- Do not log the token. Redact it from any command/request echo in the UI.

### Upload flow

`Send to Paperless` opens a small dialog: title (default to something sensible like
`Receipt YYYY-MM-DD`), created date, correspondent, document type, tags, and which pages
to include. Then: assemble PDF → upload → poll task → report. Keep the local PDF on failure
so nothing is lost, and make retry cheap.

**Do not OCR locally.** Paperless-ngx runs OCR server-side on consumption; local `tesseract`
or `ocrmypdf` would duplicate that work for no benefit. (`tesseract` is installed, but leave
it alone.)

---

## Deliverables

1. Working app, launchable with a documented command, plus a `.desktop` file.
2. `README.md`: install, run, configure Paperless, and the udev/permissions note if needed.
3. Unit tests for the pure logic that is worth testing — argv construction (especially
   geometry ordering and the 1/1200-inch rounding), exit-code mapping, and the Paperless
   request builder. Do not write GTK integration tests.
4. A short `NOTES.md` recording anything you verified or discovered that contradicts or
   extends this brief — particularly the unverified batch-mode exit-code question.

## Working agreement

- The scanner is physically connected; you can run `scanimage` directly to verify anything.
  The user must feed paper for any test that needs it — **ask them** rather than assuming a
  scan failed.
- Verify the Receipt preset against a real receipt before calling Milestone 1 done. The whole
  app hinges on `swcrop` behaving as advertised, and that has not been tested on paper yet.
- If something in this brief turns out to be wrong, trust the device over the brief, and
  record it in `NOTES.md`.
