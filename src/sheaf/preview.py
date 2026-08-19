"""The full-page preview window.

Two things matter here and neither was obvious from the brief.

The page is decoded at the scan's own resolution rather than at a
display-sized thumbnail. The first implementation fitted *both* dimensions into
a 1600 px box, which for a 927 x 3648 receipt means a 406 px wide image blown
back up to fill the window — a preview that looked like a fax of the scan
rather than the scan.

And zoom is a requirement, not a nicety. Fit-to-window on a 355 mm strip puts
about 250 px of width on screen whatever the resolution, so the only way to
actually read a receipt is to zoom in and scroll. Zoom is expressed as a
fraction of the scanned pixels, so 100% means one scan pixel per widget pixel
even when the texture had to be capped.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import imaging, thumbnails  # noqa: E402
from .pages import Page  # noqa: E402

#: Zoom stops, as a fraction of the scanned pixels. "Fit" is a separate mode
#: rather than a stop, because it has to survive the window being resized.
ZOOM_STOPS: tuple[float, ...] = (0.1, 0.15, 0.25, 0.33, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)
_DEFAULT_STOP = ZOOM_STOPS.index(1.0)


class PagePreview(Adw.Window):
    """Shows one page large, with zoom and a switch for the app-side crop."""

    __gtype_name__ = "PagePreview"

    def __init__(
        self,
        page: Page,
        *,
        parent: Gtk.Window,
        on_crop_changed: Callable[[Page], None],
    ) -> None:
        super().__init__(
            transient_for=parent, modal=True, default_width=900, default_height=1000
        )
        self._page = page
        self._on_crop_changed = on_crop_changed
        self._decoded: thumbnails.Decoded | None = None
        #: None means fit-to-window; otherwise an index into ZOOM_STOPS.
        self._stop: int | None = None

        self._title = Adw.WindowTitle(title=page.label)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())

        self._picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN)
        self._scroller = Gtk.ScrolledWindow(
            child=self._picture,
            hexpand=True,
            vexpand=True,
            # Without this the scrolled window asks for the whole texture as its
            # natural size and the window opens the size of the scan.
            propagate_natural_width=False,
            propagate_natural_height=False,
        )
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self._scroller.add_controller(scroll)

        self._spinner = Adw.Spinner(width_request=48, height_request=48)
        self._stack = Gtk.Stack(vexpand=True)
        self._stack.add_named(self._spinner, "loading")
        self._stack.add_named(self._scroller, "image")
        self._stack.set_visible_child_name("loading")
        toolbar.set_content(self._stack)
        self.set_content(toolbar)

        self._reload()

    # -- construction -----------------------------------------------------
    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        header.set_title_widget(self._title)

        self._crop_button = Gtk.ToggleButton(
            icon_name="edit-cut-symbolic",
            tooltip_text="Crop to content",
            active=self._page.crop_enabled,
            visible=self._page.crop is not None,
        )
        self._crop_button.connect("toggled", self._on_crop_toggled)
        header.pack_start(self._crop_button)

        zoom = Gtk.Box(spacing=0)
        zoom.add_css_class("linked")
        for icon, tooltip, delta in (
            ("zoom-out-symbolic", "Zoom out", -1),
            ("zoom-in-symbolic", "Zoom in", 1),
        ):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
            button.connect("clicked", lambda _b, d=delta: self._step_zoom(d))
            zoom.append(button)
        fit = Gtk.Button(icon_name="zoom-fit-best-symbolic", tooltip_text="Fit to window")
        fit.connect("clicked", lambda *_: self._set_stop(None))
        zoom.append(fit)
        header.pack_end(zoom)

        self._zoom_label = Gtk.Label(label="Fit", width_chars=5)
        self._zoom_label.add_css_class("numeric")
        self._zoom_label.add_css_class("dim-label")
        header.pack_end(self._zoom_label)
        return header

    # -- loading ----------------------------------------------------------
    def _reload(self) -> None:
        self._stack.set_visible_child_name("loading")
        rotation = self._page.rotation
        crop = self._page.effective_crop
        thumbnails.load_async(
            self._page.path,
            None,
            lambda decoded: self._on_loaded(decoded, crop, rotation),
            rotation=rotation,
            crop=crop,
        )

    def _on_loaded(self, decoded, crop, rotation: int) -> bool:
        if crop != self._page.effective_crop or rotation != self._page.rotation:
            return False  # superseded while this decode ran
        if decoded is None:
            self._title.set_subtitle("This page could not be read")
            return False
        self._decoded = decoded
        self._picture.set_paintable(decoded.texture)
        self._title.set_subtitle(_describe(decoded.size, self._page.dpi))
        self._apply_zoom()
        self._stack.set_visible_child_name("image")
        return False

    # -- zoom -------------------------------------------------------------
    def _set_stop(self, stop: int | None) -> None:
        self._stop = stop
        self._apply_zoom()

    def _step_zoom(self, delta: int) -> None:
        current = self._stop if self._stop is not None else self._fit_stop()
        self._set_stop(min(max(current + delta, 0), len(ZOOM_STOPS) - 1))

    def _fit_stop(self) -> int:
        """The stop nearest to what fit-to-window is currently showing.

        Zooming out of fit on a long receipt should carry on getting smaller,
        not jump to 100%, so the first step has to start from the scale that is
        actually on screen.
        """
        if self._decoded is None:
            return _DEFAULT_STOP
        width, height = self._decoded.size
        available_w = max(1, self._scroller.get_width())
        available_h = max(1, self._scroller.get_height())
        if not width or not height:
            return _DEFAULT_STOP
        scale = min(available_w / width, available_h / height)
        return min(
            range(len(ZOOM_STOPS)), key=lambda i: abs(ZOOM_STOPS[i] - scale)
        )

    def _apply_zoom(self) -> None:
        if self._decoded is None:
            return
        if self._stop is None:
            # can_shrink lets the picture go below the texture size, so
            # ContentFit.CONTAIN fits the window in both directions.
            self._picture.set_size_request(-1, -1)
            self._zoom_label.set_label("Fit")
            return
        zoom = ZOOM_STOPS[self._stop]
        width, height = self._decoded.size
        self._picture.set_size_request(max(1, round(width * zoom)), max(1, round(height * zoom)))
        self._zoom_label.set_label(f"{zoom * 100:.0f}%")

    def _on_scroll(self, controller: Gtk.EventControllerScroll, _dx: float, dy: float) -> bool:
        if not controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return False
        if dy:
            self._step_zoom(-1 if dy > 0 else 1)
        return True

    # -- crop -------------------------------------------------------------
    def _on_crop_toggled(self, button: Gtk.ToggleButton) -> None:
        self._page.crop_enabled = button.get_active()
        self._reload()
        self._on_crop_changed(self._page)


def _describe(size: tuple[int, int], dpi: int) -> str:
    width_mm, height_mm = imaging.size_mm(size, dpi)
    return (
        f"{size[0]} × {size[1]} px · "
        f"{width_mm:.0f} × {height_mm:.0f} mm at {dpi} dpi"
    )
