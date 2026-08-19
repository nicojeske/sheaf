"""Paperless dialogs: settings, and sending a document."""

from __future__ import annotations

import datetime
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..config import Config, cache_dir  # noqa: E402
from ..export import ExportError, export_pdf  # noqa: E402
from ..pages import Page  # noqa: E402
from ..secrets import TokenStore  # noqa: E402
from .base import ConnectionInfo, DocumentMeta, MetadataItem, TaskOutcome, UploadError  # noqa: E402
from .paperless import PaperlessUploader  # noqa: E402

NONE_LABEL = "— none —"
#: Consumption is queued server-side, so polling has to be patient but bounded.
POLL_DELAYS = (1.0, 1.0, 2.0, 2.0, 3.0, 5.0)
POLL_TIMEOUT = 180.0


def make_uploader(config: Config, store: TokenStore | None = None) -> PaperlessUploader:
    store = store or TokenStore()
    base_url = config.paperless.base_url
    token = store.get(base_url or "default") or ""
    return PaperlessUploader(base_url, token)


class PaperlessSettingsDialog(Adw.Dialog):
    """Base URL, API token and a connection test."""

    __gtype_name__ = "PaperlessSettingsDialog"

    def __init__(self, config: Config) -> None:
        super().__init__(title="Paperless settings", content_width=560, content_height=520)
        self._config = config
        self._store = TokenStore()

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        self.set_child(toolbar)

        server = Adw.PreferencesGroup(title="Server")
        self._url_row = Adw.EntryRow(title="Base URL")
        self._url_row.set_text(config.paperless.base_url)
        server.add(self._url_row)

        self._token_row = Adw.PasswordEntryRow(title="API token")
        existing = self._store.get(config.paperless.base_url or "default")
        if existing:
            self._token_row.set_text(existing)
        server.add(self._token_row)

        note = Adw.ActionRow(title="Token storage", subtitle=self._store.storage_note())
        note.set_subtitle_selectable(True)
        server.add(note)

        test_row = Adw.ActionRow(title="Connection")
        self._test_button = Gtk.Button(label="Test connection", valign=Gtk.Align.CENTER)
        self._test_button.connect("clicked", lambda *_: self._test())
        test_row.add_suffix(self._test_button)
        test_row.set_subtitle("Not tested yet")
        self._test_row = test_row
        server.add(test_row)
        page.add(server)

        defaults = Adw.PreferencesGroup(
            title="Defaults", description="Used to prefill the send dialog."
        )
        self._title_row = Adw.EntryRow(title="Title template")
        self._title_row.set_text(config.paperless.title_template)
        self._title_row.set_tooltip_text("{date} is replaced with today's date")
        defaults.add(self._title_row)
        page.add(defaults)

        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self._save())
        header.pack_end(save)

    def _current(self) -> tuple[str, str]:
        return self._url_row.get_text().strip(), self._token_row.get_text().strip()

    def _test(self) -> None:
        base_url, token = self._current()
        self._test_button.set_sensitive(False)
        self._test_row.set_subtitle("Testing…")
        uploader = PaperlessUploader(base_url, token)

        def work() -> None:
            info = uploader.test_connection()
            GLib.idle_add(self._show_result, info)

        threading.Thread(target=work, name="paperless-test", daemon=True).start()

    def _show_result(self, info: ConnectionInfo) -> bool:
        self._test_button.set_sensitive(True)
        self._test_row.set_subtitle(info.detail)
        return False

    def _save(self) -> None:
        base_url, token = self._current()
        self._config.paperless.base_url = base_url
        self._config.paperless.title_template = (
            self._title_row.get_text().strip() or "Receipt {date}"
        )
        if token:
            self._store.set(base_url or "default", token)
        try:
            self._config.save()
        except OSError as exc:
            self._test_row.set_subtitle(f"Could not save the configuration: {exc}")
            return
        self.close()


class SendToPaperlessDialog(Adw.Dialog):
    """Collects metadata, then assembles, uploads and polls."""

    __gtype_name__ = "SendToPaperlessDialog"

    def __init__(self, window, config: Config, pages: list[Page]) -> None:
        super().__init__(title="Send to Paperless", content_width=580, content_height=680)
        self._window = window
        self._config = config
        self._pages = pages
        self._store = TokenStore()
        self._uploader = make_uploader(config, self._store)
        self._tags: list[MetadataItem] = []
        self._correspondents: list[MetadataItem] = []
        self._document_types: list[MetadataItem] = []
        self._tag_switches: dict[int, Adw.SwitchRow] = {}
        self._pdf_path: Path | None = None

        toolbar = Adw.ToolbarView()
        self._header = Adw.HeaderBar()
        toolbar.add_top_bar(self._header)
        self._stack = Gtk.Stack()
        toolbar.set_content(self._stack)
        self.set_child(toolbar)

        self._stack.add_named(self._build_form(), "form")
        self._stack.add_named(self._build_status(), "status")
        self._stack.set_visible_child_name("form")

        self._send_button = Gtk.Button(label="Send")
        self._send_button.add_css_class("suggested-action")
        self._send_button.connect("clicked", lambda *_: self._send())
        self._header.pack_end(self._send_button)

        if not config.paperless.configured:
            self._show_status(
                "Paperless is not configured",
                "Add the server URL and API token in Paperless settings first.",
            )
            self._send_button.set_sensitive(False)
        else:
            self._load_metadata()

    # -- form -------------------------------------------------------------
    def _build_form(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        document = Adw.PreferencesGroup(title="Document")
        today = datetime.date.today()
        self._title_row = Adw.EntryRow(title="Title")
        self._title_row.set_text(
            self._config.paperless.title_template.replace("{date}", today.isoformat())
        )
        document.add(self._title_row)

        self._date_row = Adw.EntryRow(title="Created")
        self._date_row.set_text(today.isoformat())
        self._date_row.set_tooltip_text("YYYY-MM-DD")
        document.add(self._date_row)

        self._correspondent_row = Adw.ComboRow(title="Correspondent")
        self._correspondent_row.set_model(Gtk.StringList.new([NONE_LABEL]))
        document.add(self._correspondent_row)

        self._type_row = Adw.ComboRow(title="Document type")
        self._type_row.set_model(Gtk.StringList.new([NONE_LABEL]))
        document.add(self._type_row)
        page.add(document)

        tags = Adw.PreferencesGroup(title="Tags")
        self._tags_expander = Adw.ExpanderRow(title="Tags", subtitle="Loading…")
        tags.add(self._tags_expander)
        refresh = Gtk.Button(
            icon_name="view-refresh-symbolic",
            tooltip_text="Reload tags, correspondents and types",
            valign=Gtk.Align.CENTER,
        )
        refresh.connect("clicked", lambda *_: self._load_metadata(refresh=True))
        self._tags_expander.add_suffix(refresh)
        page.add(tags)

        pages_group = Adw.PreferencesGroup(title="Pages")
        pages_group.add(
            Adw.ActionRow(
                title=f"{len(self._pages)} page(s) will be sent",
                subtitle="Select pages in the main window to send only those",
            )
        )
        page.add(pages_group)
        return page

    def _build_status(self) -> Gtk.Widget:
        self._status_page = Adw.StatusPage(title="", description="")
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER
        )
        self._spinner = Adw.Spinner(width_request=32, height_request=32)
        box.append(self._spinner)
        self._retry_button = Gtk.Button(label="Try again", visible=False)
        self._retry_button.add_css_class("suggested-action")
        self._retry_button.connect("clicked", lambda *_: self._back_to_form())
        box.append(self._retry_button)
        self._status_page.set_child(box)
        return self._status_page

    def _show_status(
        self, title: str, description: str, *, busy: bool = False, retry: bool = False
    ) -> None:
        self._status_page.set_title(title)
        self._status_page.set_description(description)
        self._spinner.set_visible(busy)
        self._retry_button.set_visible(retry)
        self._stack.set_visible_child_name("status")

    def _back_to_form(self) -> None:
        self._stack.set_visible_child_name("form")
        self._send_button.set_sensitive(True)

    # -- metadata ---------------------------------------------------------
    def _load_metadata(self, *, refresh: bool = False) -> None:
        self._tags_expander.set_subtitle("Loading…")

        def work() -> None:
            try:
                tags = self._uploader.list_tags(refresh=refresh)
                correspondents = self._uploader.list_correspondents(refresh=refresh)
                types = self._uploader.list_document_types(refresh=refresh)
            except UploadError as exc:
                GLib.idle_add(self._metadata_failed, str(exc))
                return
            GLib.idle_add(self._metadata_loaded, tags, correspondents, types)

        threading.Thread(target=work, name="paperless-metadata", daemon=True).start()

    def _metadata_failed(self, detail: str) -> bool:
        self._tags_expander.set_subtitle(detail)
        return False

    def _metadata_loaded(
        self,
        tags: list[MetadataItem],
        correspondents: list[MetadataItem],
        types: list[MetadataItem],
    ) -> bool:
        self._tags = tags
        self._correspondents = correspondents
        self._document_types = types

        _fill_combo(
            self._correspondent_row,
            correspondents,
            self._config.paperless.default_correspondent,
        )
        _fill_combo(self._type_row, types, self._config.paperless.default_document_type)

        for row in list(self._tag_switches.values()):
            self._tags_expander.remove(row)
        self._tag_switches.clear()
        defaults = set(self._config.paperless.default_tags)
        for tag in tags:
            row = Adw.SwitchRow(title=tag.name, active=tag.id in defaults)
            self._tags_expander.add_row(row)
            self._tag_switches[tag.id] = row
        self._tags_expander.set_subtitle(
            f"{len(tags)} available" if tags else "No tags on the server"
        )
        return False

    # -- sending ----------------------------------------------------------
    def _meta(self) -> DocumentMeta:
        created = _parse_date(self._date_row.get_text())
        return DocumentMeta(
            title=self._title_row.get_text().strip() or None,
            created=created,
            correspondent=_combo_id(self._correspondent_row, self._correspondents),
            document_type=_combo_id(self._type_row, self._document_types),
            tags=tuple(
                tag_id for tag_id, row in self._tag_switches.items() if row.get_active()
            ),
        )

    def _send(self) -> None:
        if _parse_date(self._date_row.get_text()) is None:
            self._date_row.add_css_class("error")
            return
        self._date_row.remove_css_class("error")
        meta = self._meta()
        # Remember the choices so the next receipt is one click lighter.
        self._config.paperless.default_tags = list(meta.tags)
        self._config.paperless.default_correspondent = meta.correspondent
        self._config.paperless.default_document_type = meta.document_type

        self._send_button.set_sensitive(False)
        self._show_status("Preparing the PDF…", "", busy=True)

        threading.Thread(
            target=self._upload_worker, args=(meta,), name="paperless-upload", daemon=True
        ).start()

    def _upload_worker(self, meta: DocumentMeta) -> None:
        dpi = self._config.settings.resolution
        target = cache_dir() / f"{_safe_stem(meta.title)}-{int(time.time())}.pdf"
        try:
            export_pdf(self._pages, target, dpi)
        except ExportError as exc:
            GLib.idle_add(self._failed, "Could not build the PDF", str(exc))
            return
        self._pdf_path = target

        GLib.idle_add(self._progress, "Uploading…", target.name)
        try:
            task_id = self._uploader.upload(target, meta)
        except UploadError as exc:
            GLib.idle_add(self._failed, "Upload failed", f"{exc}\n\nThe PDF is kept at {target}")
            return

        GLib.idle_add(self._progress, "Waiting for Paperless to consume it…", "")
        deadline = time.monotonic() + POLL_TIMEOUT
        for index in range(10_000):
            time.sleep(POLL_DELAYS[min(index, len(POLL_DELAYS) - 1)])
            try:
                state = self._uploader.poll(task_id)
            except UploadError as exc:
                GLib.idle_add(
                    self._failed,
                    "Could not check the upload",
                    f"{exc}\n\nThe PDF is kept at {target}",
                )
                return
            if state.outcome is TaskOutcome.SUCCESS:
                GLib.idle_add(self._succeeded, state.document_id, target)
                return
            if state.outcome is TaskOutcome.FAILURE:
                GLib.idle_add(
                    self._failed,
                    "Paperless rejected the document",
                    f"{state.message}\n\nThe PDF is kept at {target}",
                )
                return
            if time.monotonic() > deadline:
                GLib.idle_add(
                    self._failed,
                    "Paperless is still processing",
                    "The upload was accepted but consumption has not finished. "
                    f"Check the server.\n\nThe PDF is kept at {target}",
                )
                return

    def _progress(self, title: str, detail: str) -> bool:
        self._show_status(title, detail, busy=True)
        return False

    def _failed(self, title: str, detail: str) -> bool:
        self._show_status(title, detail, retry=True)
        return False

    def _succeeded(self, document_id: int | None, pdf: Path) -> bool:
        if document_id is not None:
            message = f"Consumed as document #{document_id}"
        else:
            message = "Consumed by Paperless"
        pdf.unlink(missing_ok=True)
        try:
            self._config.save()
        except OSError:
            pass
        self._window.toast(message)
        self.close()
        return False


def _fill_combo(row: Adw.ComboRow, items: list[MetadataItem], selected: int | None) -> None:
    names = [NONE_LABEL] + [item.name for item in items]
    row.set_model(Gtk.StringList.new(names))
    if selected is not None:
        for index, item in enumerate(items, start=1):
            if item.id == selected:
                row.set_selected(index)
                return
    row.set_selected(0)


def _combo_id(row: Adw.ComboRow, items: list[MetadataItem]) -> int | None:
    index = row.get_selected()
    if index <= 0 or index - 1 >= len(items):
        return None
    return items[index - 1].id


def _parse_date(text: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(text.strip())
    except ValueError:
        return None


def _safe_stem(title: str | None) -> str:
    if not title:
        return "document"
    keep = [c if c.isalnum() or c in "-_" else "-" for c in title]
    return "".join(keep).strip("-") or "document"
