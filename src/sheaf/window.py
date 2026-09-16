"""The main window: settings on the left, scanned pages on the right."""

from __future__ import annotations

import datetime
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import caps, imaging, thumbnails  # noqa: E402
from .config import Config  # noqa: E402
from .device import (  # noqa: E402
    DeviceLookup,
    ScanimageMissing,
    find_device,
    reset_usb_device,
)
from .export import ExportError, document_dpi, export_images, export_pdf  # noqa: E402
from .geometry import GeometryError  # noqa: E402
from .pages import Page, PageStore  # noqa: E402
from .presets import Preset  # noqa: E402
from .preview import PagePreview  # noqa: E402
from .scan import ScanJob  # noqa: E402
from .settings import ScanSettings  # noqa: E402
from .sidebar import SettingsSidebar  # noqa: E402
from .status import Outcome, ScanResult  # noqa: E402
from .upload.queue import UploadState  # noqa: E402

TOAST_TIMEOUT = 6
#: A USB port needs a moment to come back after a reset before it can be opened.
RESET_SETTLE = 3.0
#: Card status caption for every non-terminal upload state. DONE and FAILED
#: are worded specially (with the document id, or "failed"), so they are not
#: here.
_CARD_STATUS_TEXT = {
    UploadState.QUEUED: "Waiting to upload…",
    UploadState.EXPORTING: "Building the PDF…",
    UploadState.UPLOADING: "Uploading…",
    UploadState.CONSUMING: "Paperless is consuming…",
}
#: How much a card dims once its document has been consumed — enough to read
#: as "already filed" across a grid of thirty cards, not so much it vanishes.
_UPLOADED_CARD_OPACITY = 0.55


@dataclass(slots=True)
class _Card:
    """The widgets of one page card.

    Held rather than dug back out of the widget tree: walking siblings to find
    the caption broke every time the card grew a row.
    """

    child: Gtk.FlowBoxChild
    holder: Gtk.Stack
    picture: Gtk.Picture
    detail: Gtk.Label
    #: Upload queue state — "Uploading…", "Uploaded #123", "Upload failed".
    #: A separate line from ``detail``, which holds the page's size in mm.
    status: Gtk.Label


class SheafWindow(Adw.ApplicationWindow):
    __gtype_name__ = "SheafWindow"

    def __init__(self, application: Adw.Application, config: Config) -> None:
        super().__init__(application=application, title="Sheaf")
        #: The app, not just Adw.Application — it owns the shared uploader
        #: and the upload queue, both of which outlive any one window.
        self._app = application
        self._config = config
        self._pages = PageStore()
        self._job: ScanJob | None = None
        self._device: str | None = None
        self._tmpdirs: list[Path] = []
        self._cards: dict[int, _Card] = {}
        self._retried_after_rediscovery = False
        #: Pages from the scan run in progress, so every page of it can be
        #: kept selected as more arrive — a fresh receipt is ready to send
        #: the moment it finishes, with no need to click its card first.
        self._current_run_pages: list[Page] = []
        #: Set once the user picks "Close anyway" over uploads still in
        #: flight, so the re-entrant close() this triggers does not ask again.
        self._force_close = False
        #: Set when the user picks "Wait" instead — toast once the queue
        #: that was blocking the close actually empties.
        self._toast_when_queue_empties = False

        self.set_default_size(config.window.width, config.window.height)
        if config.window.maximized:
            self.maximize()

        self._toasts = Adw.ToastOverlay()
        self.set_content(self._toasts)

        self._split = Adw.OverlaySplitView(sidebar_width_fraction=0.32, min_sidebar_width=340)
        self._toasts.set_child(self._split)

        self._sidebar = SettingsSidebar()
        self._sidebar.connect("changed", lambda *_: self._on_settings_changed())
        self._sidebar.connect("preset-save-requested", lambda *_: self._ask_save_preset())
        self._sidebar.connect(
            "preset-delete-requested", lambda _w, name: self._delete_preset(name)
        )
        self._split.set_sidebar(self._sidebar)
        self._split.set_content(self._build_content())

        self._sidebar.set_presets(config.presets, selected=config.last_preset)
        preset = self._sidebar.current_preset()
        self._sidebar.set_settings(
            preset.settings if preset else config.settings, keep_preset=preset is not None
        )
        self._on_settings_changed()
        self._install_window_actions()

        self.connect("close-request", self._on_close)
        self.refresh_device()

    # -- layout -----------------------------------------------------------
    def _build_content(self) -> Gtk.Widget:
        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        self._sidebar_button = Gtk.ToggleButton(
            icon_name="sidebar-show-symbolic",
            tooltip_text="Show scan settings",
            active=True,
        )
        self._sidebar_button.connect(
            "toggled", lambda b: self._split.set_show_sidebar(b.get_active())
        )
        header.pack_start(self._sidebar_button)

        self._device_button = Gtk.Button(has_frame=False)
        self._device_button.connect("clicked", lambda *_: self.refresh_device())
        header.pack_start(self._device_button)

        menu = _build_menu()
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))
        header.pack_end(self._build_queue_button())
        toolbar.add_top_bar(header)

        toolbar.set_content(self._build_page_area())
        toolbar.add_bottom_bar(self._build_action_bar())
        return toolbar

    def _build_queue_button(self) -> Gtk.MenuButton:
        """The upload queue's status indicator and its popover.

        Doubles as both: the button itself is the always-visible glance ("2
        uploading", turning red on a failure) that someone feeding paper
        needs, and its popover is where the detail lives. Hidden until the
        queue has ever held an item, so it costs nothing when Paperless is
        not in use.
        """
        self._queue_icon = Gtk.Image(icon_name="cloud-upload-symbolic")
        self._queue_spinner = Adw.Spinner(width_request=16, height_request=16)
        self._queue_status_stack = Gtk.Stack()
        self._queue_status_stack.add_named(self._queue_icon, "icon")
        self._queue_status_stack.add_named(self._queue_spinner, "spinner")

        self._queue_count_label = Gtk.Label()

        content = Gtk.Box(spacing=4)
        content.append(self._queue_status_stack)
        content.append(self._queue_count_label)

        self._queue_button = Gtk.MenuButton(child=content, visible=False)
        self._queue_button.set_tooltip_text("Upload queue")

        self._queue_listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._queue_listbox.add_css_class("boxed-list")
        self._queue_rows: dict[int, Adw.ActionRow] = {}
        self._queue_empty_row = Adw.ActionRow(title="Nothing queued")
        self._queue_empty_row.add_css_class("dim-label")
        self._queue_listbox.append(self._queue_empty_row)

        scroller = Gtk.ScrolledWindow(
            child=self._queue_listbox,
            min_content_width=320,
            max_content_height=360,
            propagate_natural_height=True,
        )

        self._queue_retry_all_button = Gtk.Button(label="Retry all", visible=False)
        self._queue_retry_all_button.connect("clicked", lambda *_: self._retry_all_failed())
        clear_button = Gtk.Button(label="Clear finished")
        clear_button.connect("clicked", lambda *_: self._clear_finished_queue())
        footer = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_top=6)
        footer.append(self._queue_retry_all_button)
        footer.append(clear_button)

        popover_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6,
        )
        popover_box.append(scroller)
        popover_box.append(footer)

        popover = Gtk.Popover(child=popover_box)
        self._queue_button.set_popover(popover)
        return self._queue_button

    def _build_page_area(self) -> Gtk.Widget:
        self._stack = Gtk.Stack(vexpand=True)

        self._empty = Adw.StatusPage(
            icon_name="scanner-symbolic",
            title="No pages yet",
            description="Load the feeder and press Scan.",
        )
        self._stack.add_named(self._empty, "empty")

        self._flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.MULTIPLE,
            homogeneous=True,
            column_spacing=12,
            row_spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
            valign=Gtk.Align.START,
            max_children_per_line=8,
        )
        self._flow.connect("selected-children-changed", lambda *_: self._update_actions())
        scroller = Gtk.ScrolledWindow(child=self._flow, vexpand=True)
        self._stack.add_named(scroller, "pages")

        self._stack.set_visible_child_name("empty")
        return self._stack

    def _build_action_bar(self) -> Gtk.Widget:
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=6,
            margin_bottom=6,
            margin_start=12,
            margin_end=12,
        )

        self._command_expander = Gtk.Expander(label="scanimage command")
        command_box = Gtk.Box(spacing=6, margin_top=6)
        self._command_label = Gtk.Label(
            selectable=True,
            wrap=True,
            wrap_mode=Gtk.WrapMode.CHAR,
            xalign=0.0,
            hexpand=True,
        )
        self._command_label.add_css_class("monospace")
        self._command_label.add_css_class("dim-label")
        copy = Gtk.Button(
            icon_name="edit-copy-symbolic", tooltip_text="Copy command", valign=Gtk.Align.START
        )
        copy.connect("clicked", lambda *_: self._copy_command())
        command_box.append(self._command_label)
        command_box.append(copy)
        self._command_expander.set_child(command_box)
        box.append(self._command_expander)

        self._progress = Gtk.ProgressBar(show_text=True, visible=False)
        box.append(self._progress)

        actions = Gtk.Box(spacing=6)
        self._scan_button = Gtk.Button(label="Scan")
        self._scan_button.add_css_class("suggested-action")
        self._scan_button.connect("clicked", lambda *_: self._on_scan_clicked())
        actions.append(self._scan_button)

        self._page_count = Gtk.Label(xalign=0.0, hexpand=True)
        self._page_count.add_css_class("dim-label")
        actions.append(self._page_count)

        self._pdf_button = Gtk.Button(label="Save as PDF…")
        self._pdf_button.connect("clicked", lambda *_: self._save_pdf())
        actions.append(self._pdf_button)

        self._images_button = Gtk.Button(label="Save as images…")
        self._images_button.connect("clicked", lambda *_: self._save_images())
        actions.append(self._images_button)

        self._paperless_button = Adw.SplitButton(label="Send to Paperless")
        self._paperless_button.connect("clicked", lambda *_: self._send_to_paperless())
        paperless_menu = Gio.Menu()
        paperless_menu.append("Send with details…", "win.send-with-details")
        paperless_menu.append("Show upload queue", "win.show-upload-queue")
        paperless_menu.append("Clear uploaded pages", "win.clear-uploaded-pages")
        self._paperless_button.set_menu_model(paperless_menu)
        actions.append(self._paperless_button)

        box.append(actions)
        self._update_actions()
        return box

    # -- device -----------------------------------------------------------
    def refresh_device(self, then: Callable[[], None] | None = None) -> None:
        self._device_button.set_label("Looking for the scanner…")
        self._device_button.set_sensitive(False)

        def work() -> None:
            try:
                lookup = find_device()
            except ScanimageMissing as exc:
                GLib.idle_add(self._on_scanimage_missing, str(exc))
                return
            GLib.idle_add(self._on_device_found, lookup, then)

        threading.Thread(target=work, name="find-device", daemon=True).start()

    def _on_scanimage_missing(self, detail: str) -> None:
        self._device = None
        self._device_button.set_label("scanimage not installed")
        self._device_button.set_sensitive(True)
        self._device_button.set_tooltip_text(detail)
        self._empty.set_title("scanimage is not installed")
        self._empty.set_description(
            "Install the sane-backends package, then press the scanner button to retry."
        )
        self._update_actions()

    def _on_device_found(
        self, lookup: DeviceLookup, then: Callable[[], None] | None = None
    ) -> None:
        self._device = lookup.device
        self._device_button.set_sensitive(True)
        if lookup.found:
            self._device_button.set_label(f"{caps.MODEL} ready")
            self._device_button.set_tooltip_text(
                f"{lookup.device}\nClick to look for the scanner again"
            )
            self._empty.set_title("No pages yet")
            self._empty.set_description("Load the feeder and press Scan.")
        else:
            if lookup.wedged:
                self._device_button.set_label("Scanner needs a reset")
            else:
                self._device_button.set_label("Scanner not connected")
            self._device_button.set_tooltip_text(
                (lookup.error or "No canon_dr device was found.")
                + "\nClick to look again"
            )
            if lookup.wedged:
                self._empty.set_title("The scanner needs a reset")
                self._empty.set_description(
                    "It is plugged in but the driver cannot open it — usually the "
                    "after-effect of an interrupted scan. Use ☰ → Reset scanner."
                )
            else:
                self._empty.set_title("Scanner not connected")
                self._empty.set_description(
                    "Plug in the Canon P-208II, then click the scanner button to "
                    "look again."
                )
        self._update_actions()
        self._on_settings_changed()  # the command echo embeds the device string
        if then is not None and lookup.found:
            then()

    def _recover_and_retry(self) -> None:
        """Reset the USB port, then look the scanner up again and rescan once.

        Covers both ways a device string goes bad while the app is open: the
        scanner was replugged and re-enumerated at a new bus address, or it was
        left wedged by a killed scan.
        """

        def work() -> None:
            reset_usb_device()
            # The port takes a moment to come back after a reset.
            time.sleep(RESET_SETTLE)
            GLib.idle_add(self.refresh_device, self._start_scan)

        threading.Thread(target=work, name="reset-scanner", daemon=True).start()

    def reset_scanner(self, *, quiet: bool = False) -> None:
        """Reset the scanner's USB port and re-detect it."""

        def work() -> None:
            ok = reset_usb_device()
            time.sleep(RESET_SETTLE)
            GLib.idle_add(self._after_reset, ok, quiet)

        self._device_button.set_label("Resetting the scanner…")
        self._device_button.set_sensitive(False)
        threading.Thread(target=work, name="reset-scanner", daemon=True).start()

    def _after_reset(self, ok: bool, quiet: bool) -> bool:
        if not ok and not quiet:
            self.toast("The scanner could not be reset — unplug and replug it")
        self.refresh_device()
        return False

    # -- settings ---------------------------------------------------------
    def _current_settings(self) -> ScanSettings:
        return self._sidebar.get_settings()

    def _on_settings_changed(self) -> None:
        settings = self._current_settings()
        self._config.settings = settings
        preset = self._sidebar.current_preset()
        self._config.last_preset = preset.name if preset else ""
        self._command_label.set_text(self._preview_command(settings))
        self._update_actions()

    def _preview_command(self, settings: ScanSettings) -> str:
        from .argv import BATCH_PATTERN, build_argv, display_command

        device = self._device or "canon_dr:libusb:BUS:DEV"
        try:
            argv = build_argv(device, settings, f"/tmp/scanner-XXXX/{BATCH_PATTERN}")
        except GeometryError as exc:
            return str(exc)
        return display_command(argv)

    def _copy_command(self) -> None:
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(self._command_label.get_text())
        self.toast("Command copied")

    # -- presets ----------------------------------------------------------
    def _ask_save_preset(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Save preset",
            body="Store the current settings under a name you can pick again.",
        )
        entry = Gtk.Entry(placeholder_text="Preset name", activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        def responded(_dialog, response: str) -> None:
            if response != "save":
                return
            name = entry.get_text().strip()
            if not name:
                self.toast("Give the preset a name")
                return
            self._save_preset(name)

        dialog.connect("response", responded)
        dialog.present(self)

    def _save_preset(self, name: str) -> None:
        settings = self._current_settings()
        self._config.presets = [p for p in self._config.presets if p.name != name]
        self._config.presets.append(Preset(name=name, settings=settings))
        self._sidebar.set_presets(self._config.presets, selected=name)
        self._config.last_preset = name
        self.toast(f"Saved preset “{name}”")

    def _delete_preset(self, name: str) -> None:
        self._config.presets = [p for p in self._config.presets if p.name != name]
        self._sidebar.set_presets(self._config.presets)
        self.toast(f"Deleted preset “{name}”")

    # -- scanning ---------------------------------------------------------
    def _on_scan_clicked(self) -> None:
        if self._job is not None:
            self._cancel_scan()
        else:
            self._retried_after_rediscovery = False
            self._start_scan()

    def _start_scan(self) -> None:
        if self._device is None:
            self.toast("The scanner is not connected")
            return
        try:
            settings = self._current_settings().snapped()
        except GeometryError as exc:
            self._error_dialog("These settings cannot be scanned", str(exc))
            return

        # A new run's pages should end up selected on their own, not merged
        # into whatever was left selected (or not) from an earlier one.
        self._current_run_pages = []

        job = ScanJob(
            self._device,
            settings,
            on_page=lambda path: GLib.idle_add(self._on_page_scanned, path),
            on_progress=lambda fraction: GLib.idle_add(self._on_progress, fraction),
            on_finished=lambda result: GLib.idle_add(self._on_scan_finished, result),
        )
        self._job = job
        self._tmpdirs.append(job.tmpdir)
        self._command_label.set_text(job.command)

        self._scan_button.set_label("Cancel")
        self._scan_button.remove_css_class("suggested-action")
        self._scan_button.add_css_class("destructive-action")
        self._progress.set_visible(True)
        self._progress.set_fraction(0.0)
        self._progress.set_text("Starting the scanner…")
        self._update_actions()
        job.start()

    def _cancel_scan(self) -> None:
        job = self._job
        if job is None:
            return
        self._scan_button.set_sensitive(False)
        self._progress.set_text("Cancelling…")
        threading.Thread(target=job.cancel, name="cancel-scan", daemon=True).start()

    def _on_page_scanned(self, path: Path) -> None:
        job = self._job
        settings = job.settings if job is not None else self._current_settings()
        page = self._pages.add(
            path, duplex=settings.is_duplex, dpi=settings.resolution
        )
        self._add_card(page)
        self._current_run_pages.append(page)
        self._select_run_pages()
        if settings.autocrop:
            self._request_crop(page, settings.autocrop_margin_mm)
        else:
            self._request_thumbnail(page)
        self._stack.set_visible_child_name("pages")
        self._progress.set_text(f"Scanned {len(self._pages)} page(s)…")
        self._update_actions()

    def _select_run_pages(self) -> None:
        """Re-select every page scanned so far in the current run.

        ``_add_card`` unselects everything as a defensive measure against
        GtkFlowBox auto-selecting its first child on focus (see its comment),
        which would otherwise wipe out the earlier pages of a multi-page run
        each time a new one arrives. Selecting the whole run again after each
        arrival is what makes a duplex sheet, or any multi-page document,
        land fully selected rather than just its last page.
        """
        for run_page in self._current_run_pages:
            card = self._cards.get(id(run_page))
            if card is not None:
                self._flow.select_child(card.child)

    def _on_progress(self, fraction: float | None) -> None:
        if fraction is None:
            self._progress.pulse()
            return
        self._progress.set_fraction(min(max(fraction, 0.0), 1.0))
        pages = len(self._pages)
        self._progress.set_text(f"Scanning page {pages + 1}… {fraction * 100:.0f}%")

    def _on_scan_finished(self, result: ScanResult) -> None:
        job = self._job
        self._job = None
        self._scan_button.set_label("Scan")
        self._scan_button.remove_css_class("destructive-action")
        self._scan_button.add_css_class("suggested-action")
        self._scan_button.set_sensitive(True)
        self._progress.set_visible(False)
        self._update_actions()

        if (
            job is not None
            and job.device_open_failed
            and not self._retried_after_rediscovery
            and result.outcome is Outcome.ERROR
        ):
            # The USB address changed under us. Look the scanner up again and
            # run the scan once more rather than making the user do it.
            self._retried_after_rediscovery = True
            self.toast("Could not open the scanner — resetting it and retrying")
            self._recover_and_retry()
            return

        if result.outcome is Outcome.SUCCESS:
            self.toast(result.title)
        elif result.outcome is Outcome.CANCELLED:
            if job is not None and job.killed:
                # SIGKILL leaves the scanner unopenable until its port is reset.
                self.toast("Scan stopped — resetting the scanner")
                self.reset_scanner(quiet=True)
            else:
                self.toast("Scan cancelled")
        else:
            detail = result.detail
            if job is not None and result.exit_code == 9:
                # A mid-scan unplug looks like an I/O error; re-check the device
                # so the header stops claiming the scanner is ready.
                self.refresh_device()
            tail = job.stderr_tail if job else []
            if tail and result.exit_code not in (6, 7, 8):
                detail = f"{detail}\n\n{tail[-1]}"
            self._error_dialog(result.title, detail)

    # -- page cards -------------------------------------------------------
    def _add_card(self, page: Page) -> None:
        """Build the card for a page and start it loading.

        Every card is exactly the same size, whatever shape the page is. A
        page's aspect ratio here runs from A4 to a 1:5 till roll, and a card
        that follows the image makes the grid jump around every time a receipt
        comes out of the feeder.

        That takes two things. The thumbnail is decoded to fit inside a fixed
        box, so it is never larger than it. And the picture is centred inside a
        box carrying the size request, rather than being size-requested itself:
        GtkPicture measures height-for-width, so given the card's full width it
        demands the height its aspect ratio implies — 830 px for a receipt in a
        280 px box, and a size request only sets the minimum. Wrapped and
        centred, it is measured at its own natural width and the box holds.
        """
        box_width, box_height = thumbnails.THUMBNAIL_BOX
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("card")
        card.set_size_request(box_width + 24, -1)

        picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.CONTAIN,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        frame = Gtk.Box(margin_top=6, margin_start=6, margin_end=6)
        frame.append(picture)
        frame.set_size_request(box_width, box_height)

        spinner = Adw.Spinner(width_request=box_width, height_request=box_height)
        holder = Gtk.Stack()
        holder.add_named(spinner, "loading")
        holder.add_named(frame, "image")
        holder.set_visible_child_name("loading")
        card.append(holder)

        # Click the thumbnail to toggle this card in or out of the selection.
        # Not left to GtkFlowBox's own click-to-select gesture: Ctrl+click to
        # toggle a single card without touching the rest of the selection
        # does not register reliably in this app's environment (confirmed:
        # the selection count does not change), so this implements the
        # toggle directly rather than depending on it. Attached to `holder`
        # specifically — it has no buttons of its own — so it can never
        # compete with the rotate/move/delete/preview buttons below.
        select_gesture = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        select_gesture.connect(
            "pressed", lambda gesture, *_a, p=page: self._on_card_clicked(gesture, p)
        )
        holder.add_controller(select_gesture)

        label = Gtk.Label(label=page.label)
        label.add_css_class("caption")
        card.append(label)

        detail = Gtk.Label()
        detail.add_css_class("caption")
        detail.add_css_class("dim-label")
        card.append(detail)

        status = Gtk.Label(visible=False)
        status.add_css_class("caption")
        card.append(status)

        buttons = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER, margin_bottom=6)
        for icon, tooltip, callback in (
            ("go-previous-symbolic", "Move earlier", lambda: self._move_page(page, -1)),
            ("object-rotate-left-symbolic", "Rotate left", lambda: self._rotate_page(page, -90)),
            ("view-fullscreen-symbolic", "Preview", lambda: self._preview_page(page)),
            ("object-rotate-right-symbolic", "Rotate right", lambda: self._rotate_page(page, 90)),
            ("user-trash-symbolic", "Delete", lambda: self._delete_page(page)),
            ("go-next-symbolic", "Move later", lambda: self._move_page(page, 1)),
        ):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, has_frame=False)
            button.connect("clicked", lambda _b, cb=callback: cb())
            buttons.append(button)
        card.append(buttons)

        child = Gtk.FlowBoxChild(child=card)
        self._cards[id(page)] = _Card(
            child=child, holder=holder, picture=picture, detail=detail, status=status
        )
        self._flow.append(child)
        # GtkFlowBox selects the first child when it takes focus, which would
        # silently turn "nothing selected means all pages" into "page 1 only".
        self._flow.unselect_all()

    def _request_crop(self, page: Page, margin_mm: float) -> None:
        """Find the page's content box before showing it.

        Done before the first thumbnail rather than after, so the card never
        shows the uncropped page and then visibly re-crops itself.
        """
        thumbnails.detect_crop_async(
            page.path,
            lambda box: self._on_crop_detected(page, box),
            dpi=page.dpi,
            margin_px=imaging.margin_px(margin_mm, page.dpi),
        )

    def _on_crop_detected(self, page: Page, box) -> bool:
        page.crop = box
        # A blank or unreadable page yields no box; nothing to switch on then.
        page.crop_enabled = box is not None
        self._request_thumbnail(page)
        return False

    def _request_thumbnail(self, page: Page) -> None:
        """Decode the page thumbnail at its current crop and rotation.

        Both are carried through to the callback: a decode started before the
        user rotated or switched the crop off would otherwise land afterwards
        and silently undo it.
        """
        if id(page) not in self._cards:
            return
        rotation = page.rotation
        crop = page.effective_crop
        thumbnails.load_async(
            page.path,
            thumbnails.THUMBNAIL_BOX,
            lambda decoded: self._on_thumbnail(page, decoded, rotation, crop),
            rotation=rotation,
            crop=crop,
        )

    def _on_thumbnail(self, page: Page, decoded, rotation: int, crop) -> bool:
        card = self._cards.get(id(page))
        if card is None:
            return False
        if rotation != page.rotation or crop != page.effective_crop:
            return False  # superseded while this decode ran
        card.holder.set_visible_child_name("image")
        if decoded is None:
            card.picture.set_paintable(None)
            card.detail.set_label("could not be read")
            return False
        card.picture.set_paintable(decoded.texture)
        width, height = imaging.size_mm(decoded.size, page.dpi)
        card.detail.set_label(f"{width:.0f} × {height:.0f} mm")
        return False

    def _rotate_page(self, page: Page, degrees: int) -> None:
        page.rotate(degrees)
        self._request_thumbnail(page)

    def _delete_page(self, page: Page) -> None:
        card = self._cards.pop(id(page), None)
        self._pages.remove(page)
        if card is not None:
            self._flow.remove(card.child)
        if not len(self._pages):
            self._stack.set_visible_child_name("empty")
        self._update_actions()

    def _move_page(self, page: Page, offset: int) -> None:
        if not self._pages.move(page, offset):
            return
        card = self._cards.get(id(page))
        if card is None:
            return
        # GtkFlowBox has no reorder call, so the card is taken out and put back
        # at its new index. self._cards holds the reference that keeps it alive.
        self._flow.remove(card.child)
        self._flow.insert(card.child, self._pages.pages.index(page))
        self._update_actions()

    def _preview_page(self, page: Page) -> None:
        PagePreview(page, parent=self, on_crop_changed=self._request_thumbnail).present()

    def _on_card_clicked(self, gesture: Gtk.GestureClick, page: Page) -> None:
        # Claim the sequence so nothing else (GtkFlowBoxChild's own built-in
        # click handling included) also tries to act on the same click.
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        card = self._cards.get(id(page))
        if card is None:
            return
        if card.child.is_selected():
            self._flow.unselect_child(card.child)
        else:
            self._flow.select_child(card.child)
        self._update_actions()

    # -- selection and actions -------------------------------------------
    def selected_pages(self) -> list[Page]:
        """Selecting nothing means "every page not already queued or
        uploaded" — the right default now that a sent document's pages stay
        in the grid rather than disappearing (see PageStore.unsent)."""
        chosen: list[Page] = []
        for child in self._flow.get_selected_children():
            for page in self._pages:
                card = self._cards.get(id(page))
                if card is not None and card.child is child:
                    chosen.append(page)
        return self._pages.selection(chosen, default=self._pages.unsent())

    def _update_actions(self) -> None:
        scanning = self._job is not None
        has_pages = len(self._pages) > 0
        self._scan_button.set_sensitive(scanning or self._device is not None)
        for button in (self._pdf_button, self._images_button, self._paperless_button):
            button.set_sensitive(has_pages and not scanning)

        total = len(self._pages)
        selected = len(self._flow.get_selected_children())
        uploaded = sum(1 for page in self._pages if page.uploaded)
        if not total:
            self._page_count.set_text("")
        elif selected:
            self._page_count.set_text(f"{selected} of {total} pages selected")
        elif uploaded:
            self._page_count.set_text(
                f"{total} page(s) — {total - uploaded} will be sent "
                f"({uploaded} already uploaded)"
            )
        else:
            self._page_count.set_text(f"{total} page(s) — all will be saved")

    # -- saving -----------------------------------------------------------
    def _save_pdf(self) -> None:
        pages = self.selected_pages()
        if not pages:
            return
        dialog = Gtk.FileDialog(
            title="Save as PDF", initial_name=f"{_default_stem()}.pdf"
        )
        folder = self._initial_folder()
        if folder is not None:
            dialog.set_initial_folder(folder)

        def done(_dialog, result) -> None:
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return
            path = Path(file.get_path())
            self._remember_folder(path.parent)
            try:
                export_pdf(pages, path, document_dpi(pages))
            except ExportError as exc:
                self._error_dialog("Could not save the PDF", str(exc))
                return
            self.toast(f"Saved {path.name}")

        dialog.save(self, None, done)

    def _save_images(self) -> None:
        pages = self.selected_pages()
        if not pages:
            return
        dialog = Gtk.FileDialog(title="Save images into a folder")
        folder = self._initial_folder()
        if folder is not None:
            dialog.set_initial_folder(folder)

        def done(_dialog, result) -> None:
            try:
                file = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            directory = Path(file.get_path())
            self._remember_folder(directory)
            try:
                written = export_images(pages, directory, stem=_default_stem())
            except ExportError as exc:
                self._error_dialog("Could not save the images", str(exc))
                return
            self.toast(f"Saved {len(written)} image(s)")

        dialog.select_folder(self, None, done)

    def _initial_folder(self):
        if self._config.last_save_dir and Path(self._config.last_save_dir).is_dir():
            return Gio.File.new_for_path(self._config.last_save_dir)
        return None

    def _remember_folder(self, directory: Path) -> None:
        self._config.last_save_dir = str(directory)

    # -- paperless --------------------------------------------------------
    def _install_window_actions(self) -> None:
        for name, callback in (
            ("send-with-details", lambda *_: self._send_with_details()),
            ("show-upload-queue", lambda *_: self._show_upload_queue()),
            ("clear-uploaded-pages", lambda *_: self._clear_uploaded_pages()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def _send_to_paperless(self) -> None:
        """The primary action: queue the selection as one document and return
        immediately. This is the whole point of the queue — no dialog, no
        wait, so the next receipt can go straight into the feeder."""
        pages = self.selected_pages()
        if not pages:
            return
        item = self._app.upload_queue.submit(name=_queue_item_label(pages), pages=pages)
        for page in item.origins:
            page.queued = True
        self.clear_page_selection()
        active = self._app.upload_queue.counts().active
        self.toast(f"Queued — {active} upload(s) in progress")

    def clear_page_selection(self) -> None:
        """Deselect every card and refresh the buttons/count that depend on
        selection. Called after any send, so a still-selected, now-queued
        page cannot be resent by a follow-up click with nothing re-picked."""
        self._flow.unselect_all()
        self._update_actions()

    def _send_with_details(self) -> None:
        """The escape hatch: the old modal form, for the occasional document
        that needs a title, a date, or tags set by hand. Its upload now goes
        through the queue too (see upload/dialog.py's _send), so it no longer
        blocks the window either."""
        from .upload.dialog import SendToPaperlessDialog

        pages = self.selected_pages()
        if not pages:
            return
        SendToPaperlessDialog(
            self, self._config, pages, self._app.uploader(), self._app.upload_queue
        ).present(self)

    def _show_upload_queue(self) -> None:
        self._queue_button.set_active(True)

    def _clear_uploaded_pages(self) -> None:
        uploaded = [page for page in self._pages if page.uploaded]
        if not uploaded:
            self.toast("Nothing to clear — no pages have been uploaded yet")
            return
        for page in uploaded:
            self._delete_page(page)
        self.toast(f"Cleared {len(uploaded)} uploaded page(s)")

    def open_paperless_settings(self) -> None:
        from .upload.dialog import PaperlessSettingsDialog

        PaperlessSettingsDialog(
            self._config, on_saved=self._app.invalidate_uploader
        ).present(self)

    # -- upload queue -------------------------------------------------------
    def on_queue_item_changed(self, item) -> None:
        """Called by the app (already marshalled onto the main loop) on every
        upload queue state change."""
        self._apply_item_flags(item)
        self._apply_card_status(item)
        self._sync_queue_ui()

        if self._toast_when_queue_empties and self._app.upload_queue.counts().active == 0:
            self._toast_when_queue_empties = False
            self.toast("All uploads finished — the window can be closed now")

    def _apply_item_flags(self, item) -> None:
        queued = item.state not in (UploadState.DONE, UploadState.FAILED)
        uploaded = item.state is UploadState.DONE
        for page in item.origins:
            page.queued = queued
            page.uploaded = uploaded
        self._update_actions()

    def _apply_card_status(self, item) -> None:
        """Reflect one upload item's state on every card it came from.

        Keyed through ``item.origins`` — the live Page objects, not the
        detached snapshot the worker actually renders — so this still finds
        the right cards no matter what the worker is doing with its copies.
        """
        for page in item.origins:
            card = self._cards.get(id(page))
            if card is None:
                continue
            card.status.remove_css_class("success")
            card.status.remove_css_class("error")
            card.status.remove_css_class("dim-label")
            card.status.set_visible(True)
            if item.state is UploadState.DONE:
                text = (
                    f"Uploaded #{item.document_id}"
                    if item.document_id is not None
                    else "Uploaded"
                )
                card.status.set_label(text)
                card.status.add_css_class("success")
                card.child.set_opacity(_UPLOADED_CARD_OPACITY)
            elif item.state is UploadState.FAILED:
                card.status.set_label("Upload failed")
                card.status.add_css_class("error")
                card.child.set_opacity(1.0)
            else:
                card.status.set_label(_CARD_STATUS_TEXT.get(item.state, ""))
                card.status.add_css_class("dim-label")
                card.child.set_opacity(1.0)

    def _sync_queue_ui(self) -> None:
        items = self._app.upload_queue.items()
        for row in list(self._queue_rows.values()):
            self._queue_listbox.remove(row)
        self._queue_rows.clear()

        if not items:
            if self._queue_empty_row.get_parent() is None:
                self._queue_listbox.append(self._queue_empty_row)
        else:
            if self._queue_empty_row.get_parent() is not None:
                self._queue_listbox.remove(self._queue_empty_row)
            for item in items:
                row = self._build_queue_row(item)
                self._queue_rows[item.id] = row
                self._queue_listbox.append(row)

        self._refresh_queue_header(items)

    def _build_queue_row(self, item) -> Adw.ActionRow:
        row = Adw.ActionRow(title=item.name)
        if item.state is UploadState.QUEUED:
            row.set_subtitle("Waiting")
        elif item.state is UploadState.EXPORTING:
            row.set_subtitle("Building the PDF…")
            row.add_prefix(Adw.Spinner(width_request=16, height_request=16))
        elif item.state is UploadState.UPLOADING:
            row.set_subtitle("Uploading…")
            row.add_prefix(Adw.Spinner(width_request=16, height_request=16))
        elif item.state is UploadState.CONSUMING:
            row.set_subtitle("Paperless is consuming…")
            row.add_prefix(Adw.Spinner(width_request=16, height_request=16))
        elif item.state is UploadState.DONE:
            if item.document_id is not None:
                row.set_subtitle(f"Consumed as document #{item.document_id}")
            else:
                row.set_subtitle("Consumed by Paperless")
            row.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic"))
        elif item.state is UploadState.FAILED:
            row.set_subtitle(item.error or "Upload failed")
            row.set_subtitle_lines(3)
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
            retry = Gtk.Button(
                icon_name="view-refresh-symbolic",
                tooltip_text="Retry",
                valign=Gtk.Align.CENTER,
                has_frame=False,
            )
            retry.connect("clicked", lambda _b, item_id=item.id: self._retry_queue_item(item_id))
            discard = Gtk.Button(
                icon_name="window-close-symbolic",
                tooltip_text="Discard",
                valign=Gtk.Align.CENTER,
                has_frame=False,
            )
            discard.connect(
                "clicked", lambda _b, item_id=item.id: self._discard_queue_item(item_id)
            )
            row.add_suffix(retry)
            row.add_suffix(discard)
        return row

    def _refresh_queue_header(self, items) -> None:
        if not items:
            self._queue_button.set_visible(False)
            return
        self._queue_button.set_visible(True)

        active = sum(1 for item in items if item.active)
        failed = sum(1 for item in items if item.state is UploadState.FAILED)
        self._queue_status_stack.set_visible_child_name("spinner" if active else "icon")
        self._queue_retry_all_button.set_visible(failed > 0)

        if failed:
            self._queue_count_label.set_label(str(failed))
            self._queue_count_label.add_css_class("error")
            tooltip = f"{failed} failed" if not active else f"{active} uploading, {failed} failed"
        else:
            self._queue_count_label.remove_css_class("error")
            if active:
                self._queue_count_label.set_label(str(active))
                tooltip = f"{active} uploading"
            else:
                self._queue_count_label.set_label("")
                tooltip = "Upload queue"
        self._queue_button.set_tooltip_text(tooltip)

    def _retry_queue_item(self, item_id: int) -> None:
        self._app.upload_queue.retry(item_id)

    def _discard_queue_item(self, item_id: int) -> None:
        for item in self._app.upload_queue.items():
            if item.id == item_id:
                for page in item.origins:
                    page.queued = False
                break
        self._app.upload_queue.discard(item_id)
        self._update_actions()
        self._sync_queue_ui()

    def _retry_all_failed(self) -> None:
        for item in self._app.upload_queue.items():
            if item.state is UploadState.FAILED:
                self._app.upload_queue.retry(item.id)

    def _clear_finished_queue(self) -> None:
        self._app.upload_queue.clear_finished()
        self._sync_queue_ui()

    # -- misc -------------------------------------------------------------
    def toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=message, timeout=TOAST_TIMEOUT))

    def _error_dialog(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body or "")
        dialog.add_response("ok", "Close")
        dialog.set_default_response("ok")
        dialog.present(self)

    def _on_close(self, *_args) -> bool:
        if self._force_close:
            self._teardown()
            return False

        if self._job is not None:
            self._job.cancel()

        counts = self._app.upload_queue.counts()
        if counts.active:
            self._confirm_close_with_uploads_pending(counts)
            return True  # veto for now — the dialog's response decides what happens next

        self._teardown()
        return False

    def _confirm_close_with_uploads_pending(self, counts) -> None:
        """Deleting the scan tempdirs out from under an in-flight export would
        corrupt it, so a close with anything active in the upload queue needs
        the user's say-so rather than silently discarding work."""
        queue = self._app.upload_queue
        unsent = sum(1 for item in queue.items() if item.needs_sources)
        sent = counts.active - unsent

        parts = []
        if sent:
            parts.append(
                f"{sent} document(s) have already reached Paperless and will be "
                "filed whether or not you wait."
            )
        if unsent:
            parts.append(f"{unsent} document(s) have not been sent yet and would be lost.")

        dialog = Adw.AlertDialog(
            heading=f"{counts.active} upload(s) are still in progress",
            body="\n\n".join(parts),
        )
        dialog.add_response("wait", "Wait")
        dialog.add_response("close", "Close anyway")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("wait")
        dialog.set_close_response("wait")
        dialog.connect("response", self._on_close_confirmation)
        dialog.present(self)

    def _on_close_confirmation(self, _dialog, response: str) -> None:
        if response == "close":
            self._force_close = True
            self.close()
            return
        # Wait: everything still active finishes (or fails) fast enough that
        # it may already be done by the time this response arrives — check
        # now rather than only arming the flag, or a queue that settles
        # between the dialog opening and the click would never toast.
        if self._app.upload_queue.counts().active == 0:
            self.toast("All uploads finished — the window can be closed now")
            return
        self._toast_when_queue_empties = True

    def _teardown(self) -> None:
        # Give anything still mid-export a bounded moment to finish before
        # its source tempdir is deleted underneath it. A no-op, immediately,
        # when the queue has nothing active — the common case.
        self._app.upload_queue.stop()
        self._app.upload_queue.drain(2.0)

        width, height = self.get_default_size()
        self._config.window.width = width
        self._config.window.height = height
        self._config.window.maximized = self.is_maximized()
        self._config.settings = self._current_settings()
        try:
            self._config.save()
        except OSError:
            pass
        for tmpdir in self._tmpdirs:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _default_stem() -> str:
    return f"scan-{datetime.date.today().isoformat()}"


def _queue_item_label(pages: list[Page]) -> str:
    """A short label for the upload queue popover — not sent to Paperless."""
    count = len(pages)
    noun = "page" if count == 1 else "pages"
    when = datetime.datetime.now().strftime("%H:%M:%S")
    return f"{count} {noun} — {when}"


def _build_menu():
    menu = Gio.Menu()
    menu.append("Reset scanner", "app.reset-scanner")
    menu.append("Paperless settings…", "app.paperless-settings")
    menu.append("About Scanner", "app.about")
    return menu
