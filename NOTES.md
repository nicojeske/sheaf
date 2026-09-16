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

## 12. The 355.554 mm ceiling is a VPD field, not a backend limitation

Investigating whether long receipts could be scanned in one pass at all, before
building any flip-and-stitch workaround.

### Where the ceiling actually comes from

`sane-canon_dr`'s `init_vpd()` (backend/canon_dr.c in sane-backends 1.4.0) reads it
straight off the device and never looks at it again:

```c
s->max_y = get_IN_window_length(in) * 1200 / s->basic_y_res;
DBG(15, "  max length: %d (%2.2f in)\n",s->max_y,(float)s->max_y/1200);
```

confirmed live:

```console
$ SANE_DEBUG_CANON_DR=15 scanimage -L
[canon_dr] init_vpd:   max width: 10208 (8.51 in)
[canon_dr] init_vpd:   max length: 16800 (14.00 in)
```

There is no per-model override for P-208/P-215 in `init_model()`, and
`canon_dr.conf`'s complete option vocabulary (`buffer-size`, `padded-read`,
`extra-status`, `duplex-offset`, `inquiry-length`, `vpd-length`, `tur-timeout`,
`vendor-name`, `model-name`, `version-name`) has nothing that touches length.
Shortening `vpd-length` to see if a truncated VPD read changes the parsed value
does nothing — `max length` reports the same 16800 either way. So the ceiling
cannot be moved from configuration; it has to be patched in the backend.

### A patched backend can be side-loaded, no root, fully reversible

`libsane-dll` (the frontend-facing SANE meta-backend) resolves backend `.so`s as
`%s/libsane-%s.so.%u` and honours `LD_LIBRARY_PATH` for the `%s` directory
(confirmed via `strings` on `/usr/lib/sane/libsane-dll.so.1.4.0`). So a private
build of just `canon_dr` can shadow the system one for a single `scanimage`
invocation, with the system installation completely untouched when the variable
is unset.

Built sane-backends 1.4.0 from source (`BACKENDS="canon_dr"` only), with one
addition immediately after the `max_y` assignment above:

```c
{
  const char * sheaf_max_y_mm = getenv("SHEAF_MAX_Y_MM");
  if (sheaf_max_y_mm) {
    s->max_y = (int)(atof(sheaf_max_y_mm) / 25.4 * 1200);
    DBG(15, "  SHEAF_MAX_Y_MM override: max length -> %d (%2.2f in)\n",
        s->max_y, (float)s->max_y/1200);
  }
}
```

(autoconf-archive isn't installed; `AX_CXX_COMPILE_STDCXX_11` and
`AX_CREATE_STDINT_H` were disabled in `configure.ac` and `include/_stdint.h` was
hand-written as a two-line forward to the system `<stdint.h>` — harmless for a
C-only, canon_dr-only build on a modern glibc, and confined to the scratch build
tree, not this repo.)

### The software stack accepts the wider window

```console
$ scanimage -d canon_dr:... -n --help | grep -E 'page-height|^\s+-[ty] '
    --page-height 0..355.554mm (in steps of 0.0211639) [279.364]
    -t 0..279.364mm (in steps of 0.0211639) [0]
    -y 0..279.364mm (in steps of 0.0211639) [279.364]

$ LD_LIBRARY_PATH=<scratch>/sane-longdoc/lib/sane SHEAF_MAX_Y_MM=1000 \
    SANE_DEBUG_CANON_DR=15 scanimage -d canon_dr:... --page-height 900 -n --help
[canon_dr] init_vpd:   max length: 16800 (14.00 in)
[canon_dr] init_vpd:   SHEAF_MAX_Y_MM override: max length -> 47244 (39.37 in)
scanimage: rounded value of page-height from 900 to 899.997
    --page-height 0..999.869mm (in steps of 0.0211639) [899.997]
    -t 0..899.997mm (in steps of 0.0211639) [0]
    -y 0..899.997mm (in steps of 0.0211639) [899.997]
```

The stock backend (`LD_LIBRARY_PATH` unset) is unaffected by the patched one
existing on disk — re-ran the first command after the build with no change.

**Still open: whether the firmware itself will actually feed and image past
355 mm**, or whether it jams/truncates regardless of what the driver requests.
`man sane-canon_dr` documents no long-document mode and the backend has none, so
this is genuinely unknown until tested on paper — the software accepting the
request is necessary but not sufficient. Canon's own Windows driver has a "Long
Document Mode" reaching 1000 mm on this scanner family (documented for the
sibling P-215II; the P-208II's own published spec lists only 70–356 mm), which
makes the firmware supporting it plausible but unconfirmed. To be continued once
paper is fed.

### The firmware itself rejects it — a little past 355.554 mm, nowhere near 1000 mm

Tested on real paper (patched backend, `SHEAF_MAX_Y_MM=1000`, `--mode Gray
--resolution 300`, no `swcrop`/`swdeskew`, plain `-n`-less scans of a single
~15 cm sheet at increasing window lengths):

| `-y` requested | Result |
|---|---|
| 355.554 mm (control) | accepted, scanned normally |
| 360 mm | accepted, scanned normally, `2544 × 4252` px |
| 370 mm | accepted, scanned normally, `2544 × 4370` px |
| 380 mm | **rejected before feeding** |
| 400 mm | **rejected before feeding** |
| 500 mm | **rejected before feeding** |
| 900 mm | **rejected before feeding** |

Every rejection is immediate and identical, at `sane_start`, before the feed
motor engages (confirmed each time — the sheet sat untouched in the tray):

```console
[canon_dr] do_usb_cmd: finish ...
[canon_dr] sense_handler: start
[canon_dr] Sense=0x5, ASC=0x26, ASCQ=00, EOM=0, ILI=0, info=00000000
[canon_dr] Illegal request: invalid field in parm list
scanimage: sane_start: Invalid argument
```

`ASC 0x26` is SCSI's generic "Invalid field in parameter list" — the scanner's
own firmware is validating the SET WINDOW command's length field against an
internal ceiling and refusing it outright, independent of anything the driver
or `s->max_y` claims. So **the true hard ceiling is firmware-enforced, not
merely VPD-reported**, and sits only about 15–20 mm above the number the VPD
advertises (accepted at 370, rejected at 380 — not narrowed further; good
enough to answer the question).

This closes off the long-document idea entirely. The gap between "firmware
accepts" (~370–375 mm) and "Canon's Windows driver reaches with Long Document
Mode" (1000 mm) means that mode is not simply a longer SET WINDOW request — it
must use a different command this reverse-engineered backend does not send (or
it genuinely is not present in this firmware revision at all; the man page's
"reverse engineered from USB traces" disclaimer is relevant here). Patching
`max_y` in the backend cannot reach it. **Conclusion: no software fix, patched
backend or otherwise, gets a single-pass scan meaningfully past ~370 mm on this
device.** A receipt longer than that needs a multi-pass capture, not a longer
window.

### What actually happens today, at the stock 355.554 mm ceiling, with a real over-length receipt

Tested with the **stock** backend (`LD_LIBRARY_PATH` unset), a receipt longer
than 355.554 mm, `--mode Gray --resolution 300`, `swcrop`/`swdeskew` off:

```console
$ scanimage -d canon_dr:... --format=png --source "ADF Front" --mode Gray \
    --resolution 300 --page-width 215.872 --page-height 355.55419921875 \
    -l 0 -t 0 -x 215.872 -y 355.55419921875 > out.png
```

**Exit 0.** A full `2544 × 4200` px PNG comes back — the app sees a completed,
successful scan. But the receipt was longer than the window: physically, the
sheet is left **partially fed, LED blinking**. `scanimage` does not know this;
it asked for 355.554 mm, got exactly that back, and considers the job done. So
the failure the brief called "a hard limit... before it calls a paper jam" is
not a jam at the SANE level at all on the *first* command — it is a silent
truncation that leaves the transport physically loaded. This matters for the
UI: **exit 0 is not sufficient evidence that a page is not truncated** (see
Phase 3 of the plan — the app needs its own truncation check).

**A second scan attempt against that same, still-loaded state returns exit 6**
(`sane_start: Document feeder jammed`) — this is where the "jam" the brief
described actually surfaces, and only on the retry:

```console
$ scanimage -d canon_dr:... [same args as above]
scanimage: sane_start: Document feeder jammed
```

So there is no silent resume: once a sheet is left mid-feed, the transport
firmware itself considers it jammed on the next command, not merely paused.
**A "scan the tail in a second pass without touching the paper" design does not
work** — confirmed, not assumed.

No USB wedge either way — `scanimage -L` (and a `SANE_DEBUG_CANON_DR=15`
Test-Unit-Ready probe, `wait_scanner: finish (status=0)`) succeeded throughout,
before and after manually clearing the jam by hand. This is a purely mechanical
jam, unlike the SIGKILL case in §8a — the controller stays USB-responsive; it
is the paper path that is stuck. Clearing the jam (open the ADF cover, pull the
receipt out by hand) fully restored normal operation; no USB reset was needed.

**Conclusion for the app design:** a long receipt cannot be captured by
"scan, hit the ceiling, scan again" — the user must physically remove the
receipt and re-feed it (flipped end-for-end) for a second pass. This confirms
flip-and-stitch (Phase 2B of the plan) as the only viable path, and rules out
any two-pass design that assumes the transport can be resumed in place.

## 13. Reverse-engineering the P-208II firmware for Long Document Mode

Follow-up to §12's open question — Canon's own Windows driver reaches 1000 mm
on this scanner family via "Long Document Mode"; is that reachable from Linux
at all, and is it a protocol quirk or something requiring firmware surgery?
Investigated by getting the real, Canon-published firmware and reverse
engineering it, not by guessing. **Firmware confirmed to already support a
1000 mm mode with no modification; the live command that switches it on
remains unconfirmed.**

### §12's mechanism was subtly wrong: it's `SCAN`, not `SET WINDOW`

Re-running the §12 length ladder with `SANE_DEBUG_CANON_DR=35` (full command
tracing, not just decoded fields) and **real paper loaded** shows `SET WINDOW`
for a 380 mm window completing with status GOOD — no error at all:

```
set_window: start
[... SET WINDOW CDB, 52-byte descriptor with length=380mm's worth of units ...]
stat: << 00 00 00 00
set_window: finish
calibrate_AFE: offset ... calibration_scan: start ... start_scan: start
```

The rejection appears later, right after the feed motor actually engages
(`object_position: load`, `wait_scanner: finish (status=0)`), on the **`SCAN`**
command (opcode `0x1b`):

```
start_scan: start
cmd: >> ... 1b 00 00 00 ...
out: >> ... 00                      (window id list: front only)
stat: read 0 bytes, retval 9        (USB stall)
do_usb_clear: clear halt
rs sub call >> ... in: << f0 00 05 00 00 00 00 06 00 00 00 00 26 00
Sense=0x5, ASC=0x26, ASCQ=00        (invalid field in parameter list)
```

§12 inferred the culprit from a debug level too low to see individual
commands and assumed `SET WINDOW`. This also explains why an **empty-feeder**
retest of the same ladder found *no* rejection at any length up to 380 mm —
without paper, `OBJECT POSITION` fails with "feeder out of documents" before
`SCAN` is ever reached, so the real check never fires. Any future probing of
this boundary needs paper physically loaded to mean anything.

### The unread half of the VPD page

`vpd-length` is a `canon_dr.conf` option and nothing sets it for the P-208II,
so the stock backend requests only 30 of the 48 bytes the device offers
(`INQUIRY_vpd_max_len` is `0x30` = 48, matching the device's page exactly — the
line just needs to sit **immediately above** the `usb 0x1083 0x165f` entry;
`canon_dr.c`'s `default_globals()` resets `vpd-length` after every device, so
placement is not cosmetic). Doing that and reading with
`SANE_DEBUG_CANON_DR=35` shows the full page:

```
000: 06 f0 02 00 2b 02 58 02 58 00 02 58 02 58 00 64
010: 00 64 29 d4 00 00 13 f0 00 00 20 d0 18 00 00 00
020: 00 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00
```

Bytes `0x1e`–`0x2f` are all zero except **`0x22 = 0x01`** — a single set bit in
data the backend has never even transferred. Not decoded (no reference in
`canon_dr.c` parses past `0x1c`), but recorded here in case it matters later.

### Canon really does publish this device's firmware

It is not on Canon USA/Europe/Japan's support pages, but it exists, filed
under a misleading title: **"CaptureOnTouch Lite V3.1.124.213 updater"** on
Canon's Asia support site (`asia.canon/en/support/0200710301`). Inside is
`DRP208II_P208II_FirmUpdater_Main203_Sub127_COTL31124213.exe`, an InstallShield
package whose payload is `BOWII.mot` — a 65 MB Motorola S-record image, main
firmware version **2.03.00.008** (matching `init_inquire`'s live-read
`version 2.03` and the USB `bcdDevice 2.03` exactly).

Extracting it needed two workarounds, both worth recording:

- **`unshield` can't parse this container.** The `.exe` is a modern
  ISSetupStream wrapping a compressed payload with no plain `ISc(`/`MSCF`
  cabinet signature anywhere in it — `unshield`'s classic-cabinet parser has
  nothing to grab onto.
- **Wine's own InstallShield runtime does the unpacking for free.** Running the
  installer under `wine` with `/extract_all:<target> /s` (inside
  `wine explorer /desktop=name,1x1` so nothing appears on screen) produces a
  proper `Disk1/data1.cab` + `data1.hdr` pair, which `unshield` then reads
  normally. `unshield l data1.cab` lists `BOWII.mot` directly.

The P-215II's equivalent package (`asia.canon/en/support/0200710701`) was
pulled the same way, for comparison — same generation, same toolchain, and
that model *does* ship with Long Document Mode enabled in Canon's own UI.

### What's inside: ARM, big-endian, and a VPD generator with two hardcoded ceilings

Decoded the S-records into a flat binary (main code at `0x10010000`–
`0x10064f4c`, ~340 KB; a separate 24 MB region is almost certainly the
"Auto Start" mass-storage image CaptureOnTouch Lite ships from, not
executable code) and loaded it into Ghidra as `ARM:BE:32:v7` — confirmed
correct by finding a literal `BX LR` (`E1 2F FF 1E`) and other valid ARM
opcodes at the expected addresses.

Traced the complete top-level vendor/SCSI command dispatcher
(`FUN_1001e444`, reached from the confirmed `INQUIRY` handler's caller) and
matched every opcode canon_dr already knows — `0x00,0x03,0x12,0x15,0x1a,0x1b,
0x1c,0x1d,0x24,0x25,0x28,0x2a,0x31,0xd5,0xd6,0xd8,0xe1` — plus opcodes the
backend has never touched: `0x16,0x17` (RESERVE/RELEASE UNIT), `0x3b,0x3c`
(buffer read/alloc), and, previously uncatalogued anywhere, **`0xc2`–`0xc5`,
`0xc7`, `0xd9`, `0xe0`, `0xe9`, `0xfd`, `0xfe`** — named by their own embedded
debug-log strings as `scanner_calibration`, `rescan`, `discard`,
`get_scanner_status`, `error_clear`, `sleep_control`, `get_adjust_mode`,
`run_subsidiary`, and two undifferentiated ones. None of them, on inspection,
have anything to do with document length.

The function that actually matters is the confirmed `INQUIRY` handler
(verified by its own logic: checks CDB byte `== 0xf0`, our known vendor VPD
page, exactly matching `sane-canon_dr`'s `init_vpd()`). It writes the VPD
length field from one of **two hardcoded constants**:

```c
uVar6 = DAT_1002c5dc;   // 8400   -> 355.6 mm  (355.6*600/25.4 = 8400 exactly)
uVar7 = DAT_1002c5d8;   // 23622  -> 1000.0 mm (1000*600/25.4 = 23622.05 -> 23622)
uVar9 = uVar6;                              // default: report 355.6 mm
if (iVar8 != 0) uVar9 = uVar7;              // flag set: report 1000.0 mm instead
puVar10[0x5a] = (char)((uint)uVar9 >> 8);
puVar10[0x5b] = uVar11;                     // (low byte, same selection)
```

`iVar8` comes from `FUN_10034f58()`, which is one line:

```c
undefined1 FUN_10034f58(void) { return *(byte*)(settings_base + 0x8f); }
```

and the matching setter is equally trivial:

```c
void FUN_1003527c(byte v) { *(byte*)(settings_base + 0x8f) = v; }
```

reached by a small 3-way dispatcher (selector values 1/2/3 read from another
global, each writing one byte in the same `0x8e`–`0x91` cluster). A sibling
function (`FUN_10035498`) resets that whole cluster to zero and is called
both from the mass-storage-class command handler and from one other routine —
consistent with a persistent settings block that gets reset on some event and
otherwise survives, matching the service manual's description of IC8 as an
EEPROM that "stores each setting".

**This is not a theoretical patch target. It is working, unconditional logic
in the exact, unmodified firmware this device already runs** — flip one byte
and the VPD response this device gives to any SANE frontend changes from
355.6 mm to 1000.0 mm, with no firmware modification at all.

### Independent corroboration: this is exactly what happened on a sibling model

`sane-project/backends` issue #385 (`gitlab.com/sane-project/backends/-/issues/385`,
Petr Jac, 2020) describes hitting this same wall on a **Canon DR-160II**:
Canon's Windows driver had a hidden "hardware setting" for maximum length;
after changing it once with Canon's own utility, "the scanner remembered the
setting" and long documents worked from Linux/SANE afterward too — exactly the
persistent-EEPROM-flag model the firmware confirms. He attached a partial USB
capture (`canon_usb.pcapng`), independently re-decoded here byte-for-byte: a
128-byte vendor data-out payload (canon_dr's own USB OUT framing — `byte[5]=2,
byte[6]=0xb0`, matching `do_usb_cmd`), selector `0x00030001` at `+0x04`, a
constant `600000` at `+0x0C`, and the target length in **micrometres**,
big-endian, at `+0x10`: `0x000F4240` (1,000,000 µm) and `0x002DC6C0`
(3,000,000 µm) in the two captured variants. **The command's opcode itself was
never captured** — only the data phase — so the exact CDB is still unknown.

### Still open: the live trigger

Extensive effort went into finding the actual command from both directions,
without success:

- **Static, from the firmware side:** every opcode in the complete dispatcher
  table was traced to its real handler. `MODE SELECT`/`SENSE` (`0x15`/`0x1a`) —
  the single most natural candidate, and the one thing the man page says
  canon_dr never uses — turned out to control resolution only. `SET SCAN MODE`
  (`0xd6`) only ever handles pages `0x30`/`0x32`/`0x36` (df/buffer/dropout),
  matching the backend exactly; the "known but unused" page `0x20` is not
  handled by this firmware at all. `RECEIVE/SEND DIAGNOSTIC` (`0x1c`/`0x1d`)
  are self-test, unrelated. A promising-looking embedded string,
  `"read_length_enable"`, turned out to be about paper-jam/cover/lamp sensor
  checks, not document length. The setter is reached only through an indirect
  call (thought at the time to be a small jump table Ghidra could not resolve —
  the next subsection disproves that: there is no such table, the setter's one
  caller is simply dead code).
- **Live, from the driver side:** the P-208II's own "Canon imageFORMULA
  Utility" was installed in a passed-through-USB Windows VM and its Maintenance
  page was exercised under a full USB capture (`tshark`/`usbmon`) — it issues
  only `INQUIRY`, `TEST UNIT READY`, panel-read and counter-read commands, and
  never touches anything length-related; this UI genuinely does not expose the
  control for this model, not merely hides it. The P-215II's own installer
  (which *does* expose Long Document Mode) refuses to proceed at all against
  the real hardware — it does its own device-presence check before
  installing, and our device correctly identifies as a P-208II, not a
  P-215II. Attempting to extract its driver files without running the
  installer (to force-bind them via Device Manager's "Have Disk" instead)
  hit the same InstallShield hardware-check hang even during plain
  `/extract_all` — the check appears to fire during the installer's own
  startup sequence, not merely at the "install now" step.

### Follow-up session: the trigger is not "unresolved" — it is dead code

A later session re-attacked the live trigger from the flat binary directly
(exhaustive ARM branch decoding with capstone, independent of Ghidra's xref
analysis) and settled the open question definitively. Three results, two of
which *strengthen* the premise and one of which *closes* it.

**1. The flag gates real enforcement, not just the advertised VPD max
(premise confirmed end-to-end).** §13 above only knew byte `0x8f` changed the
VPD *report*. The runtime feed-length check was now found too — in
`FUN_100235d8` (the paper-feed/scan-length monitor), which enforces the exact
Sense=0x5/ASC=0x26 rejection §13 saw at `SCAN` time:

```c
iVar6 = FUN_10028a68();          // length fed so far
iVar7 = FUN_10034f58();          // read the SAME 0x8f flag
iVar2 = DAT_1002387c;            // default limit  0x043b04d2
if (iVar7 != 0) iVar2 = DAT_10023880;   // flag set: larger limit 0x0b7d1b01
if (0 < iVar6 - (iVar2 + fed_base)) {    // over limit -> reject
    param_1[0xe] = 2; ... }
```

The two limits are `0x043b04d2` and `0x0b7d1b01` (ratio ≈ 2.72, i.e. the
short-vs-long allowance in internal accumulator units, margins included).
So flipping `0x8f` lifts both the reported ceiling (VPD, `FUN_1002c274`) and
the physically enforced one (feed monitor, `FUN_100235d8`). If the byte could
be flipped, 1000 mm really would work start to finish — not just be advertised.

**2. The #385 payload carries a 355.6 mm variant, pinning its semantics.**
Re-decoding all seven bulk-OUT packets in `firmware/canon_usb_385.pcapng`
(they are USBPcap frames: 12-byte canon_dr USB header — `byte5=2`, `byte6=0xb0`,
matching `do_usb_cmd`'s OUT framing — + 128-byte payload) shows length values
at payload `+0x10` in µm of `0x000F4240` (1000 mm), `0x002DC6C0` (3000 mm)
**and `0x00056D10` = 355600 µm = 355.6 mm** — exactly this device's stock
ceiling, which §13's original decode missed. That confirms `+0x10` is the
max-length field in micrometres and `+0x04 = 0x00030001` is the setting
selector. (New, also unrecorded before: a `0x80000000` sentinel at `+0x2c`.)
The opcode is still absent from the capture — but see result 3 for why that no
longer matters for *this* device.

**3. The setter is unreachable from anywhere in the firmware — proven, not
assumed.** Exhaustive decode of every `B`/`BL` in the 340 KB code region plus a
full-image search (all regions, incl. the 24 MB block and the `0x00104000`
boot region) for absolute address words and `MOVW`/`MOVT` synthesis shows:

- Only **three** instructions in the entire firmware touch settings offset
  `0x8f`: the getter `0x10034f5c` (`ldrb`), the setter `0x10035280` (`strb`,
  inside `FUN_1003527c`), and the cluster-reset `0x100354ac` (writes 0).
  Settings base is `0x30ff4c0c`; the long-doc flag is the byte at
  **`0x30ff4c9b`**. That single literal base is referenced exactly once
  (`0x10035678`), shared by the whole accessor family — so every reader and
  writer of the block is enumerable, and none other writes `0x8f`.
- `FUN_1003527c` (the only writer of `0x8f`) has **exactly one** caller: a
  `BL` at `0x10040950`, inside the service-menu handler `FUN_1004091c`. That
  handler reads a selector byte at `0x30ff41b1` (1/2/3) and applies the
  corresponding `0x8e`/`0x8f`/`0x90` setter — a classic two-step service
  protocol.
- **`FUN_1004091c` itself, and its whole service block, have zero callers.**
  No `B`/`BL` anywhere targets them; no word in any region equals their
  address; no `MOVW`/`MOVT` synthesises it; and the boot region physically
  cannot reach them (a `BL` from `0x00104xxx` to `0x1004091c` is a ~267 MB
  displacement, far outside ARM's ±32 MB `BL` range). The selector global
  `0x30ff41b1` is likewise referenced by a single literal, inside the same
  dead block — nothing external can even stage the selector.

So §13's hoped-for "indirect jump table Ghidra couldn't resolve" **does not
exist**. The code that writes Long Document Mode is present and correct but
wired to nothing in the P-208II's `2.03.00.008` build — it is dead code,
reachable only if some other firmware personality (not shipped here) called
it. This also kills the descriptor-spoofing plan (path 2): any command the
P-215II installer sends, spoofed or not, must ultimately reach `FUN_1003527c`
to take effect, and on *this* firmware nothing does. `READ`/`WRITE BUFFER`
(`0x3c`/`0x3b` → `FUN_1003fc78`/`FUN_1003fb48`) were also checked as a possible
raw-memory poke and are not — they are image-buffer DMA transfers, not
arbitrary memory access.

Net: on this exact P-208II firmware, Long Document Mode cannot be enabled by
*any* host command. Canon disabled it at the build level for the P-208II (code
compiled in, call site omitted), not merely hidden it in the UI. Enabling it
would require either a firmware personality/version that wires the setter, or
writing the EEPROM byte out-of-band (physical/ICSP access to IC8 — out of
scope), neither of which is a "live command."

### Solved: a 4-byte firmware patch, flashed with Canon's own updater

Since no command can *set* the flag, the fix was to stop reading it. Every
consumer of the flag — the VPD generator and the feed-length enforcer alike —
goes through the one getter, and there is exactly one `ldrb` of offset `0x8f`
in the whole image, so patching that single instruction is complete:

```
0x10034f58: e59f0718   ldr  r0,[pc,#0x718]   ; settings base (left alone)
0x10034f5c: e5d0008f   ldrb r0,[r0,#0x8f]  ->  e3a00001  mov r0,#1
```

**The firmware is not signed.** The whole image contains no SHA/MD5/RSA
constants and no CRC32 table — only S-record line checksums and one stored
section checksum. `UpdateQ.ini` (inside the updater) flashes three independent
sections:

| section | start | size | stored checksum |
|---|---|---|---|
| Main Firmware | `0x10010000` | `0x60000` | none |
| VE Sub Firmware | `0x104000` | `0xC000` | none |
| CaptureOnTouch Lite | `0x10100000` | `0x1700000` | `CheckSum2=0xb1dd0cc0` |

The patch lands in Main Firmware, which carries **no** stored checksum;
`CheckSum2` is a plain byte-sum over the COTL region and is unchanged by the
patch (verified identical before and after). So the patched image passes every
check the updater makes.

Building it: patch the 4 bytes in place in `BOWII.mot`, recompute **that one
S3 line's** checksum, and preserve CRLF line endings — the result differs from
stock in exactly 5 bytes (4 data + 1 checksum), same file size, all 1,027,138
record checksums valid. Two mistakes worth not repeating: the `ldrb` is at
`0x10034f5c`, **not** `0x10034f58` (that is the `ldr` that loads the settings
pointer); and the S3 checksum covers `count+addr+data` and is written *after*
the data, so an off-by-one there silently corrupts the last data byte.

Flashing it — the part that took the most fiddling:

- `UpdateQ.exe` extracted from the cabinet and run standalone reports **"no
  devices found"**; it needs the environment its InstallShield wrapper sets up.
  Run the **official** `DRP208II_P208II_FirmUpdater_...exe` instead.
- The wrapper extracts `BOWII.mot` + `UpdateQ.ini` to a temp folder and flashes
  *from those files*. InstallShield's CRC check happens during extraction, so
  replacing them once they are on disk (after the UI appears, before pressing
  Update) is enough. The temp folder is recreated per launch and deleted on
  exit, so the swap must be redone each run.
- `UpdateQ.exe` compares the device's version against `NewFirmwareVersion` in
  `UpdateQ.ini` and refuses with **"There is no need to upgrade the firmware"**
  when they match. Bumping `NewFirmwareVersion`/`Section.Version` to `2.04.000`
  in the extracted ini makes it proceed. (The binary also carries `force_update`
  among `silent`/`silent_w_err`/`no_union_update`/`no_bin_update`, untested.)

**Result, verified on the real device:**

```
stock:    max width: 10208 (8.51 in)   max length: 16800 (14.00 in)  -> 355.554 mm
patched:  max width: 10208 (8.51 in)   max length: 47244 (39.37 in)  -> 999.869 mm
```

`--page-height` now advertises `0..999.869mm`. More importantly the *runtime*
limit is gone too: a 500 mm window scan with an ordinary sheet loaded — the
exact case that died on `SCAN` with `Sense=0x5, ASC=0x26` in §13 — now exits 0
and returns a genuine 1696×3937 px (215.4 × 500.0 mm) image, with zero
`ASC=0x26` anywhere in the trace. Both halves of the flag do what the
disassembly said they would.

Assets: `~/scanner-longdoc/firmware/BOWII_longdoc.mot` (patched image) and
`~/scanner-longdoc/flash_kit/` (that image plus `UpdateQ.exe`/`.ini`/`.loc`
and a README). Stock `BOWII.mot` is kept alongside for reflashing back.

Caveat: this is modified firmware. `caps.py` now encodes 47244, which is right
for *this* device; a stock P-208II still reports 16800
(`caps.STOCK_MAX_PAGE_HEIGHT_UNITS`). The scanner fills whatever window it is
given regardless of paper length, so the receipt presets deliberately keep the
355.554 mm strip (`caps.RECEIPT_STRIP_HEIGHT_MM`) rather than paying for a 1 m
pass on every receipt.

### Assets, for anyone picking this up later

Everything is at `~/scanner-longdoc/` (outside this repo, `/tmp`, and any
session-specific scratch dir, so it survives reboots and new sessions):
`firmware/p208ii_mot/.../BOWII.mot` (the decoded firmware), `firmware/main_code.bin`
(the flat-binary code region), `ghidra_project/` (the analysed Ghidra project
plus every `.java` trace script used above, directly re-runnable with
`analyzeHeadless ... -postScript <name>.java`), and `captures/` (the USB traces).
The patched long-document SANE backend from §12 also still lives there and
still builds/loads correctly.

### Conclusion

**Solved.** The 1000 mm ceiling is switched by a single byte (`0x30ff4c9b`,
settings `+0x8f`) that this firmware reads in both the places that matter — the
VPD report *and* the runtime feed enforcement. No host command can *write* that
byte (its only writer is dead code), so instead the getter that *reads* it was
patched to return 1 unconditionally — a 4-byte change, flashed with Canon's own
updater, because the firmware carries no signature and the patched section has
no stored checksum.

Verified on the real device: `max length` went from 16800 (355.554 mm) to 47244
(999.869 mm), and a 500 mm scan that previously failed with `Sense=0x5,
ASC=0x26` now completes and returns a true 500 mm image.

So **long documents up to ~1000 mm now work in a single pass** on this scanner,
with stock SANE and no backend changes. Flip-and-stitch (§12, Phase 2B) is no
longer required for receipts in that range; it remains the fallback for
anything beyond 1000 mm, and for a stock, unpatched P-208II.
