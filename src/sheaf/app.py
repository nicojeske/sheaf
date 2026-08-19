"""The application object."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk  # noqa: E402

from .config import Config  # noqa: E402
from .window import SheafWindow  # noqa: E402

APP_ID = "dev.njeske.Sheaf"
VERSION = "0.1.0"


class SheafApplication(Adw.Application):
    __gtype_name__ = "SheafApplication"

    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._config = Config.load()
        self._window: SheafWindow | None = None

        self._add_action("reset-scanner", self._on_reset_scanner)
        self._add_action("paperless-settings", self._on_paperless_settings)
        self._add_action("about", self._on_about)
        self._add_action("quit", lambda *_: self.quit())
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def _add_action(self, name: str, callback) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def do_activate(self) -> None:
        if self._window is None:
            self._window = SheafWindow(self, self._config)
        self._window.present()

    def _on_reset_scanner(self, *_args) -> None:
        if self._window is not None:
            self._window.reset_scanner()

    def _on_paperless_settings(self, *_args) -> None:
        if self._window is not None:
            self._window.open_paperless_settings()

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
