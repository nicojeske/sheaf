# Device notes

Things verified directly on the Canon imageFORMULA P-208II
(`canon_dr:libusb:001:022`, sane-backends 1.4.0, USB `1083:165f`), recorded
because several of them contradict or extend the original brief.

Both questions the brief left open have now been settled on real paper; one of
them contradicts the brief's guess.

---

## 1. The geometry grid is not 1/1200 inch — it is 1387/65536 mm

The brief says to round geometry as `round(mm / 25.4 * 1200) / 1200 * 25.4`. That
is wrong, and it produces a value half a step off the real grid.

The backend advertises `steps of 0.0211639`. That is not `25.4/1200`
(= 0.0211666…). It is 1/1200 inch *after quantisation into SANE's 16.16
fixed-point format*: `round(65536 * 25.4/1200) = 1387`, and `1387/65536 =
0.021163940…`.

Using `25.4/1200` makes the backend re-round every value:

```console
$ scanimage -d canon_dr:... --page-height 2.116669 -n     # 100 * 25.4/1200
scanimage: rounded value of page-height from 2.11667 to 2.11639
```

`caps.GRID_MM` is therefore `1387 / 65536`.

The firmware-reported maxima are whole numbers of these units, which confirms the
reading: `10208 * 1387/65536 = 216.0415 mm` (reported as `216.042`) and
`16800 * 1387/65536 = 355.5542 mm` (reported as `355.554`) — matching the
firmware's "max width 10208 / max length 16800" exactly.

## 2. Full float precision in argv makes scanimage completely silent

Snapping to the correct grid is not enough. scanimage parses the argument into a
`double` and converts it to 16.16 fixed point; a value rounded for display does
not survive that conversion. **Even the maxima as the backend prints them are
re-rounded**:

```console
$ scanimage -d canon_dr:... --page-height 355.554 -n
scanimage: rounded value of page-height from 355.554 to 355.554   # to itself!
$ scanimage -d canon_dr:... --page-height 355.55419921875 -n
                                                                  # silent
```

So geometry is emitted with `repr(float(value))` — full precision, e.g.
`--page-height 355.55419921875`. All four built-in presets plus offset crops,
Lineart, duplex and every advanced option now pass `scanimage -n` with **no
output on stderr at all**. That is the check to re-run after touching
`geometry.py`.

## 3. `--swskip` has the same problem, and must clamp *down* at its maximum

The brief lists `--swskip 0..100%` as if it took integers. The device reports
`in steps of 0.100006`, which is 0.1 through the same quantisation:
`round(65536 * 0.1) = 6554`, `6554/65536 = 0.1000061…`.

Snapping 2% to a plain `2` gives
`rounded value of swskip from 2 to 2.00012`; snapping onto `6554/65536` and
printing `2.0001220703125` is silent.

Edge case: snapping 100% *up* lands on 100.0061, which exceeds the advertised
maximum, so the backend clamps it back and complains. The ceiling therefore has
to be clamped in grid units (`int(100 / grid) = 999` units → 99.996%), exactly as
for millimetres.

## 4. `-x` is capped by `page-width`, not fixed at 215.872

The brief documents `-x 0..215.872mm` as a fixed range, and describes the
`page-height` → `-y` ordering dependency only. The same dependency applies to
width: raise `page-width` first and `-x` unlocks past 215.872.

```console
$ scanimage -d canon_dr:... --page-width 216.042 -x 999 -n
scanimage: rounded value of br-x from 999 to 216.042
```

Both orderings are enforced together in `argv.py`:
`--page-width W --page-height H -l L -t T -x X -y Y`.

Confirmed as documented: `--page-height 355.554 -n --help` reports
`-t 0..355.554` and `-y 0..355.554 [355.554]`, and `--page-height 0` collapses
the area to `-t 0..0mm`, `-y 0..0mm`. Zero page dimensions are rejected before
argv is built.

## 5. `--progress` writes carriage returns, not newlines

The brief says progress goes to stderr, which is true, but the format string in
the binary is:

```console
$ python3 -c "d=open('/usr/bin/scanimage','rb').read(); i=d.find(b'Progress: %3.1f%%'); print(repr(d[i:i+20]))"
b'Progress: %3.1f%%\r\x00P'
```

`\r`, no `\n`. Iterating `proc.stderr` line by line — the obvious
implementation — would therefore yield **nothing until the process exited**, and
the progress bar would never move. `scan.py` reads stderr in raw chunks and
splits on CR and LF alike. There is also a `Progress: (unknown)\r` variant, which
the app turns into a pulsing progress bar.

## 6. Empty feeder: verified

```
stderr: Scanning infinity pages, incrementing by 1, numbering from 1
stderr: Scanning page 1
stderr: scanimage: sane_start: Document feeder out of documents
stderr: Batch terminated, 0 pages scanned
exit 7, 0 pages
```

Reported as "Feeder is empty — put the pages in the feeder and press Scan."

## 7. Batch-mode end of stack exits **0**, not 7 — the brief's guess was wrong

Settled with a real receipt in the feeder:

```
Scanning page 1
Progress: 31.0%Progress: 62.0%Progress: 93.0%Progress: 100.0%
Scanned page 1. (scanner status = 5)
/tmp/.../p0001.png
Scanning page 2
scanimage: sane_start: Document feeder out of documents
Batch terminated, 1 page scanned
exit 0
```

So the exit code depends on whether anything was produced:

| Situation | stderr | Exit |
|---|---|---|
| Feeder empty from the start | `Batch terminated, 0 pages scanned` | **7** |
| Stack ran out after N ≥ 1 pages | `Batch terminated, N pages scanned` | **0** |

The brief predicted exit 7 for the second case. It is 0. `classify()` treats
both as success — exit 0 obviously, and exit-7-with-pages defensively — so the
app was correct either way, but the defensive branch turns out to be unreachable
in normal batch use.

Note the progress output in that transcript: four `Progress:` values on a single
line, which is the carriage-return behaviour from §5 seen in the wild.

## 8. `swcrop` works — but an 80 mm scan window clips the receipt

Verified on a real TEDi till receipt with the Receipt preset. swcrop did its job:

```
scanned window : 80.0 x 355.6 mm   (945 x 4199 px at 300 dpi)
output         : 78.5 x 308.9 mm   (927 x 3648 px)
```

47 mm of trailing whitespace removed and the sheet deskewed. **That is the
premise of the app confirmed.**

But the output was clipped on the left: `TOTAL` came out as `OTAL`, `Mastercard`
as `astercard` — the first character of every line was gone. Counting dark pixels
per edge column shows why:

```
leftmost 10 columns : [66, 68, 77, 69, 53, 65, 79, 93, 75, 63]   <- ink at x=0
rightmost 10 columns: [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
```

Ink runs right up to `x=0`. The receipt's left edge fell **outside** the scan
window, so the text was already missing before swcrop ran; swcrop then trimmed to
the content it could see (18 px total) and reported a clean-looking crop.

The cause is that `--page-width` centres the scan window in the 216 mm paper
path — the option is documented as "required for automatic centering of sheet-fed
scans". An 80 mm window is therefore a narrow aperture in the middle of the path,
and a till roll fed a few millimetres off centre loses an edge. The feeder does
not centre paper accurately enough for this to be safe.

**Fix applied:** the default **Receipt (auto-size)** preset now scans the full
216 mm width and lets swcrop find the edges — which is what "auto-size" should
have meant all along. swcrop trims empty borders on all four sides, so the narrow
window bought nothing and only added a way to lose data. The 80 mm variant is
kept as **Receipt (80 mm window)** for anyone who wants the smaller/faster scan
and can live with the risk.

Still worth checking on paper: that full width + swcrop crops the sides tightly
rather than leaving 216 mm of white margin. swcrop found the top and bottom
correctly, so there is good reason to expect it will, but it has not been
observed.

## 8a. Killing scanimage wedges the scanner until its USB port is reset

Not in the brief, and the most consequential thing found. If scanimage is
SIGKILLed mid-scan, the scanner is left in a state where:

- `lsusb` still shows it — `Bus 001 Device 023: ID 1083:165f`;
- `scanimage -L` does **not** list it at all;
- opening it directly fails with
  `open of device canon_dr:libusb:001:023 failed: Invalid argument`;
- no process holds it (`pgrep scanimage`, `pgrep saned` both empty);
- it does **not** recover with time — still dead after 18 s of retries.

This is reachable in ordinary use: the first implementation gave SIGTERM a 3 s
grace before escalating to SIGKILL, and scanimage only unwinds after the
in-flight page read completes — which with swcrop buffering a 355 mm page takes
much longer than 3 s. So pressing Cancel could reliably brick the scanner until
it was physically replugged.

**Two fixes:**

1. `TERMINATE_GRACE` is now 20 s. SIGKILL is a genuine last resort, not a routine
   escalation.
2. The port can be reset in software, which is exactly equivalent to replugging,
   and **needs no root** — the logind ACL on the device node is enough:

   ```python
   fcntl.ioctl(os.open("/dev/bus/usb/001/023", os.O_WRONLY), 0x5514, 0)  # USBDEVFS_RESET
   ```

   Verified: wedge it with a SIGKILL, `find_device()` reports `wedged=True`,
   `reset_usb_device()` returns True, and after ~3 s the device enumerates again.

The app uses this automatically when a scan fails to open the device or when a
cancel had to escalate to SIGKILL, and exposes it as ☰ → Reset scanner.
`device.usb_device_node()` finds the node by USB id through sysfs rather than
parsing lsusb.

## 8b. Exit code 1 is missing from the brief's table

`scanimage` exits **1** when it cannot open the device at all, regardless of the
underlying SANE status (the message says `Invalid argument`, i.e. `SANE_STATUS_INVAL`
= 4, but the exit code is 1). The brief's table has no entry for it, so the first
implementation reported a bare "scanimage exited with status 1".

It is now mapped to "Could not open the scanner — the scanner may have been
unplugged, or it moved to a different USB address", and triggers the automatic
reset-and-retry above. Exit 4 (`INVAL`) and 10 (`NO_MEM`) are mapped too.

## 8c. The device string does change, as warned

Observed within a single session: `canon_dr:libusb:001:022` became
`canon_dr:libusb:001:023`. A command copied out of the app's command expander
therefore goes stale — worth knowing when pasting one into a terminal later. The
app rediscovers the string at every launch and now also after a failed open.

## 8d. swcrop leaves no margin, and the app now adds one

Confirmed on the same receipt: swcrop trims to the ink boundary and stops. There
is no option to keep a margin — the backend exposes `--swcrop` as a plain Bool —
so the output has text in its very first pixel column. That is what made the
clipping in §8 look like a clean crop rather than a fault.

`imaging.py` therefore does the crop again in the app, where a margin can be
kept, and the receipt presets pair the two: swcrop for the driver-side
deskew-and-trim, then 4 mm of margin from the app.

Two findings from getting that right, both of which cost a rewrite:

1. **The bounding box of dark pixels is useless here.** A single 5-pixel speck at
   the edge of a full-width scan puts `left` at 5 and hands back the whole
   216 mm. Requiring each row or column to hold a few dark pixels does not fix
   it either: a 5 px speck and a column crossing one thin line of text both hold
   about five dark pixels, so no threshold separates them. What works is
   two-dimensional — reduce the ink mask to 1 mm blocks and keep only *connected
   patches* of at least five blocks. A speck covers at most four blocks (2×2,
   when it straddles a block boundary in both directions at once), and every
   piece of real content is an order of magnitude larger.

2. **The paper level cannot be sampled from the border.** The obvious estimate —
   the median of the outermost few pixel rows — is exactly wrong for a page
   swcrop has already trimmed, because such a page has ink *on* its border. It
   reported the paper as black, decided the scan was unreadable, and skipped the
   crop. The 85th percentile of the whole page is right instead: even a dense
   page of text is mostly paper.

Padding, not cropping, is what a margin means on an already-trimmed page, and
`Image.crop` fills a box that overhangs the image with **black**. The margin is
filled with the paper level measured off the page instead (per band for colour),
so there is no seam — a gray scan of white paper sits at about 245, not 255.

Cost, measured on a 2551 × 4199 page: 40 ms to detect, 15 ms to render.

## 9. Smaller findings

- **A non-scanner device shows up in `scanimage -f`.** This machine reports
  `v4l:/dev/video0` alongside `canon_dr:libusb:001:022`, so filtering on the
  `canon_dr:` prefix is required, not merely defensive.
- **The device exposes an empty `Sensors:` option group.** Nothing to expose.
- **No `scanner` group exists on this machine and the user is not in one**, yet
  `scanimage` opens the device fine. Access comes from a systemd-logind ACL on
  the USB node (`user:njeske:rw-` in `getfacl`), plus a `saned` group ACL added by
  `/usr/lib/udev/rules.d/66-saned.rules`. So the README's permissions note is
  "check the ACL", not "add yourself to a group".
- **`img2pdf` is not in the Arch repos** (AUR only), while `keyring`, `tomli-w`
  and `pytest` are. That is what forces the venv: `uv venv
  --system-site-packages` for PyGObject from the system plus `img2pdf` from PyPI.
  A plain `uv run` builds an isolated venv with no `gi` and fails.
- **`AdwToolbarView` is a final GType** and cannot be subclassed from Python
  (`RuntimeError: could not create new GType`). `SettingsSidebar` composes one
  inside a `GtkBox` instead.
- **`GtkFlowBox` has no reorder call.** Moving a page removes the card and
  re-inserts it at the new index; `ScannerWindow._cards` holds the reference that
  keeps the widget alive across that.
- **PDF output verified lossless and correctly sized.** An 80 × 200 mm grayscale
  scan at 300 dpi comes out as an 80.01 × 199.98 mm PDF page with a
  `/FlateDecode` image — no JPEG re-encoding. A page rotated 90° produces a
  199.98 × 80.01 mm page.

## 10. Paperless-ngx

Implemented from the documented API; the response field names below are what the
code reads.

- Upload returns HTTP 200 with the consumption **task UUID as a bare JSON
  string**, handled by `_parse_task_id` (which also tolerates an object with
  `task_id`).
- `GET /api/tasks/?task_id=<uuid>` is polled; `status` is
  `PENDING`/`STARTED`/`SUCCESS`/`FAILURE`, and on success the document id is read
  from **`related_document`**.
- `tags` is repeated once per id in the multipart body, not comma-joined.
- `Accept: application/json; version=10` on every request.

**Open:** confirm `related_document` against the live server. If the field is
named differently on that version, `parse_task()` in `upload/paperless.py` is the
one place to change, and the app will otherwise report "Consumed by Paperless"
without an id rather than failing.

## 11. Colour mode fringes, and `--brightness`/`--contrast` do nothing

Three findings from chasing a Color scan that looked oversaturated. None of them
is fixable in the app, and none is in the brief.

### `--brightness` and `--contrast` are accepted and then ignored

The device reports both as active — `-127..127 (in steps of 1) [0]`, not
`[inactive]` — and the backend really does build a lookup table from them. With
`SANE_DEBUG_CANON_DR=25` the values arrive:

```console
$ SANE_DEBUG_CANON_DR=25 scanimage -d canon_dr:... --brightness -100 --contrast 127 ...
[canon_dr] load_lut: start 0 0        # at sane_control_option time
[canon_dr] load_lut: start 127 -100   # at sane_start, with the real values
```

The output does not change. Two 18 × 20 mm Color probes, identical except for
brightness:

| `--brightness` | min | max | distinct levels |
|---|---|---|---|
| −100 | 52 | 240 | 94 |
| +100 | 50 | 240 | 91 |

A 200-unit swing moves the histogram by two levels. The table is built and then
not applied on this model in Color mode. `argv.py` emits both options correctly
(and omits them at 0, which is the device default), so there is nothing to fix
here — the controls are simply inert, and the sidebar's Enhancement group is
honest only in the sense that it mirrors what the backend advertises.

### Every channel hard-clips at 240

Not 255, and not a scale: **zero** pixels at 239 in any channel, across fourteen
scans from seven sessions. A blank white probe comes out uniformly `(240,240,240)`.
Between 82% and 99% of each page sits exactly at the clamp.

Harmless for paper, which should be white anyway, but everything lighter than the
clip point is flattened onto the same value, so faint pencil, highlighter and pale
tints are not recoverable afterwards. Worth knowing before reaching for an
app-side levels control: there is nothing above 240 to pull down.

### R and B are about one row apart — this is the "oversaturation"

The visible complaint was not exposure at all. Every horizontal rule and text
stroke carries a red edge on one side and a cyan edge on the other. Per-channel
row profiles over a 1900 × 2100 region of text, cross-correlated:

| pair | best shift | neighbours |
|---|---|---|
| R vs G | 0 rows | +1: 0.934, −1: 0.838 → R sits *late* |
| B vs G | 0 rows | −1: 0.937, +1: 0.842 → B sits *early* |
| **R vs B** | **+1 row** | +1: 0.976, 0: 0.954 |

So the three sensor rows land roughly half a row either side of green — a one-pass
CIS scanner's inter-line spacing, not fully compensated. On grey ink and white
paper it puts chroma where there is physically none: 2.5% of pixels above chroma
30, 99th percentile 107.

Because the offset is *sub-pixel*, a whole-pixel channel shift cannot fix it;
correcting it properly needs a half-row resample, and the offset would have to be
measured per resolution. **Gray mode avoids it entirely** — one channel, no
registration to get wrong — which is why all four built-in presets use Gray and
why receipts never showed this. Color is worth it only for genuinely coloured
originals, and the fringing is inherent even then.
