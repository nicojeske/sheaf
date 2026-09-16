"""The application object."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .config import Config  # noqa: E402
from .secrets import TokenStore  # noqa: E402
from .upload.dialog import make_uploader  # noqa: E402
from .upload.paperless import PaperlessUploader  # noqa: E402
from .upload.queue import UploadItem, UploadQueue  # noqa: E402
from .window import SheafWindow  # noqa: E402

APP_ID = "dev.njeske.Sheaf"
VERSION = "0.1.0"


class SheafApplication(Adw.Application):
    __gtype_name__ = "SheafApplication"

    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._config = Config.load()
        self._window: SheafWindow | None = None

        self._token_store = TokenStore()
        self._uploader = make_uploader(self._config, self._token_store)
        # Owned here rather than by the window: the uploader's metadata cache
        # (tags/correspondents/document types, paperless.py's
        # PaperlessUploader._cache) is worth keeping across dialog opens, and
        # the queue must outlive any one dialog so uploads keep draining
        # while the user scans the next receipt.
        self._queue = UploadQueue(self._uploader, on_changed=self._on_queue_item_changed)
        self._queue.start()

        self._add_action("reset-scanner", self._on_reset_scanner)
        self._add_action("paperless-settings", self._on_paperless_settings)
        self._add_action("about", self._on_about)
        self._add_action("quit", lambda *_: self._on_quit())
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    @property
    def upload_queue(self) -> UploadQueue:
        return self._queue

    def uploader(self) -> PaperlessUploader:
        return self._uploader

    def invalidate_uploader(self) -> None:
        """Rebuild the shared uploader — call after Paperless settings change.

        A fresh PaperlessUploader means a fresh metadata cache rather than
        one still keyed to the old server or token. The queue keeps running;
        it is only told to send future work through the new instance.
        """
        self._uploader = make_uploader(self._config, self._token_store)
        self._queue.set_uploader(self._uploader)

    def _add_action(self, name: str, callback) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def do_activate(self) -> None:
        if self._window is None:
            self._window = SheafWindow(self, self._config)
        self._window.present()

    def _on_quit(self) -> None:
        # Gio.Application.quit() tears the process down without emitting
        # close-request, so it skips SheafWindow._on_close entirely — no
        # config save, no tmpdir cleanup, and (once there is one) no chance
        # for the upload queue to warn about work still in flight. Closing
        # the window runs that logic first; only fall back to a hard quit
        # when there is no window left to close.
        if self._window is not None:
            self._window.close()
        else:
            self.quit()

    def _on_reset_scanner(self, *_args) -> None:
        if self._window is not None:
            self._window.reset_scanner()

    def _on_paperless_settings(self, *_args) -> None:
        if self._window is not None:
            self._window.open_paperless_settings()

    def _on_queue_item_changed(self, item: UploadItem) -> None:
        # Called from the queue's worker thread — marshal onto the main loop
        # before touching the window, exactly as ScanJob's callbacks do.
        GLib.idle_add(self._dispatch_queue_change, item)

    def _dispatch_queue_change(self, item: UploadItem) -> bool:
        if self._window is not None:
            self._window.on_queue_item_changed(item)
        return False

    def _on_about(self, *_args) -> None:
        about = Adw.AboutDialog(
            application_name="Sheaf",
            application_icon="scanner-symbolic",
            version=VERSION,
            developer_name="njeske",
            comments=(
                "Scans documents and receipts on a Canon imageFORMULA P-208II "
                "and uploads them to Paperless-ngx."
            ),
            license_type=Gtk.License.MIT_X11,
        )
        about.present(self._window)
