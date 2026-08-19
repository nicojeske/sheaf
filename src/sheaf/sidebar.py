"""The settings sidebar.

Groups are laid out exactly as the backend groups its options — Standard,
Geometry, Enhancement, Advanced — so that what the user sees maps one-to-one
onto ``scanimage --help``. Every range and default comes from caps.py.

"After scanning" is the one group that is not a backend group: it holds the
app's own post-processing, which is separated out so that the mapping above
stays honest and so it is obvious which controls do not appear in the echoed
scanimage command.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk  # noqa: E402

from . import caps  # noqa: E402
from .presets import BUILTIN_PRESETS, Preset  # noqa: E402
from .settings import AUTOCROP_MARGIN_RANGE, ScanSettings  # noqa: E402

CUSTOM_LABEL = "Custom"
_GEOMETRY_STEP = 0.5


class SettingsSidebar(Gtk.Box):
    """Emits ``changed`` whenever the user alters anything.

    AdwToolbarView is a final type, so it is composed rather than subclassed.
    """

    __gtype_name__ = "SettingsSidebar"

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "preset-save-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "preset-delete-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._suppress = 0
        self._presets: list[Preset] = list(BUILTIN_PRESETS)

        toolbar = Adw.ToolbarView(vexpand=True)
        header = Adw.HeaderBar(show_end_title_buttons=False)
        header.set_title_widget(Adw.WindowTitle(title="Scan settings"))
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        self.append(toolbar)

        page.add(self._build_presets_group())
        page.add(self._build_standard_group())
        page.add(self._build_geometry_group())
        page.add(self._build_enhancement_group())
        page.add(self._build_advanced_group())
        page.add(self._build_postprocess_group())

        self._sync_sensitivity()

    # -- construction -----------------------------------------------------
    def _build_presets_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Preset")

        self.preset_row = Adw.ComboRow(title="Preset")
        self.preset_row.set_model(Gtk.StringList.new([CUSTOM_LABEL]))
        self.preset_row.connect("notify::selected", self._on_preset_selected)
        group.add(self.preset_row)

        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_top=6)
        save = Gtk.Button(label="Save as preset…")
        save.connect("clicked", lambda *_: self.emit("preset-save-requested"))
        self._delete_button = Gtk.Button(label="Delete preset")
        self._delete_button.add_css_class("destructive-action")
        self._delete_button.connect("clicked", self._on_delete_clicked)
        buttons.append(save)
        buttons.append(self._delete_button)
        group.add(buttons)
        return group

    def _build_standard_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Standard")

        self.source_row = self._combo("Source", caps.SOURCES)
        self.source_row.set_subtitle("Duplex produces two images per sheet")
        group.add(self.source_row)

        self.mode_row = self._combo("Mode", caps.MODES)
        self.mode_row.connect("notify::selected", lambda *_: self._sync_sensitivity())
        group.add(self.mode_row)

        self.resolution_row = self._combo(
            "Resolution", tuple(f"{dpi} dpi" for dpi in caps.RESOLUTIONS)
        )
        group.add(self.resolution_row)
        return group

    def _build_geometry_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Geometry",
            description=(
                f"The scanner cannot exceed {caps.MAX_PAGE_HEIGHT_MM:.1f} mm in one pass."
            ),
        )

        self.link_row = Adw.SwitchRow(
            title="Scan the whole page",
            subtitle="Keep the scan area matched to the page size",
            active=True,
        )
        self.link_row.connect("notify::active", self._on_link_toggled)
        group.add(self.link_row)

        self.page_width_row = self._spin("Page width", caps.MAX_PAGE_WIDTH_MM)
        self.page_height_row = self._spin("Page height", caps.MAX_PAGE_HEIGHT_MM)
        self.page_width_row.connect("notify::value", self._on_page_size_changed)
        self.page_height_row.connect("notify::value", self._on_page_size_changed)
        group.add(self.page_width_row)
        group.add(self.page_height_row)

        self.left_row = self._spin("Offset from left", caps.MAX_PAGE_WIDTH_MM)
        self.top_row = self._spin("Offset from top", caps.MAX_PAGE_HEIGHT_MM)
        self.width_row = self._spin("Scan width", caps.MAX_PAGE_WIDTH_MM)
        self.height_row = self._spin("Scan height", caps.MAX_PAGE_HEIGHT_MM)
        for row in (self.left_row, self.top_row, self.width_row, self.height_row):
            group.add(row)
        return group

    def _build_enhancement_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Enhancement")
        self.brightness_row = self._int_spin("Brightness", caps.BRIGHTNESS_RANGE)
        self.contrast_row = self._int_spin("Contrast", caps.CONTRAST_RANGE)
        self.threshold_row = self._int_spin("Threshold", caps.THRESHOLD_RANGE)
        self.threshold_row.set_subtitle("Only available in Lineart mode")
        for row in (self.brightness_row, self.contrast_row, self.threshold_row):
            group.add(row)
        return group

    def _build_advanced_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Advanced")
        expander = Adw.ExpanderRow(
            title="Advanced options",
            subtitle="Driver-side cropping, deskew and double-feed detection",
        )
        group.add(expander)

        self.swcrop_row = self._switch(
            "Crop to content (swcrop)", "Trim empty borders — auto-sizes receipts"
        )
        self.swdeskew_row = self._switch("Deskew digitally (swdeskew)", "Straighten in the driver")
        self.rollerdeskew_row = self._switch(
            "Deskew mechanically (rollerdeskew)", "Let the scanner straighten the sheet"
        )
        self.despeck_row = self._int_spin("Remove lone dots up to", caps.SWDESPECK_RANGE)
        self.despeck_row.set_subtitle("Diameter in pixels (swdespeck)")
        self.swskip_row = self._float_spin("Skip blank pages below", caps.SWSKIP_RANGE, 0.1, 1)
        self.swskip_row.set_subtitle("Percent of dark pixels (swskip)")
        self.df_thickness_row = self._switch("Double feed: thickness sensor", None)
        self.df_length_row = self._switch("Double feed: length compare", None)
        self.stapledetect_row = self._switch("Stop on stapled pages", None)
        self.dropout_front_row = self._combo("Colour dropout, front", caps.DROPOUT_VALUES)
        self.dropout_back_row = self._combo("Colour dropout, back", caps.DROPOUT_VALUES)
        self.buffermode_row = self._switch(
            "Buffer in scanner memory", "Read pages asynchronously (buffermode)"
        )

        for row in (
            self.swcrop_row,
            self.swdeskew_row,
            self.rollerdeskew_row,
            self.despeck_row,
            self.swskip_row,
            self.df_thickness_row,
            self.df_length_row,
            self.stapledetect_row,
            self.dropout_front_row,
            self.dropout_back_row,
            self.buffermode_row,
        ):
            expander.add_row(row)

        self.slow_banner = Adw.ActionRow(
            title="Cropping and deskew buffer each page in memory",
            subtitle="Scans are noticeably slower with these on",
        )
        self.slow_banner.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        expander.add_row(self.slow_banner)
        return group

    def _build_postprocess_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="After scanning",
            description="Done by this app on the scanned image, not by the scanner",
        )

        self.autocrop_row = self._switch(
            "Crop to content with a margin",
            "swcrop trims to the ink and leaves no margin; this re-finds the "
            "edges and keeps one",
        )
        group.add(self.autocrop_row)

        self.autocrop_margin_row = self._float_spin(
            "Margin", AUTOCROP_MARGIN_RANGE, 0.5, 1
        )
        self.autocrop_margin_row.set_subtitle(
            "Millimetres kept around the content, padded with the paper colour "
            "where the scan has none left"
        )
        group.add(self.autocrop_margin_row)
        return group

    # -- widget factories -------------------------------------------------
    def _combo(self, title: str, values: tuple[str, ...]) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title)
        row.set_model(Gtk.StringList.new(list(values)))
        row.connect("notify::selected", self._on_changed)
        return row

    def _switch(self, title: str, subtitle: str | None) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.connect("notify::active", self._on_changed)
        return row

    def _spin(self, title: str, maximum: float) -> Adw.SpinRow:
        return self._float_spin(title, (0.0, maximum), _GEOMETRY_STEP, 2)

    def _float_spin(
        self, title: str, bounds: tuple[float, float], step: float, digits: int
    ) -> Adw.SpinRow:
        lo, hi = bounds
        row = Adw.SpinRow.new_with_range(lo, hi, step)
        row.set_title(title)
        row.set_digits(digits)
        row.connect("notify::value", self._on_changed)
        return row

    def _int_spin(self, title: str, bounds: tuple[int, int]) -> Adw.SpinRow:
        lo, hi = bounds
        row = Adw.SpinRow.new_with_range(float(lo), float(hi), 1.0)
        row.set_title(title)
        row.set_digits(0)
        row.connect("notify::value", self._on_changed)
        return row

    # -- signal plumbing --------------------------------------------------
    def _on_changed(self, *_args) -> None:
        if self._suppress:
            return
        self._mark_custom()
        self._sync_sensitivity()
        self.emit("changed")

    def _on_page_size_changed(self, *_args) -> None:
        if self._suppress:
            return
        if self.link_row.get_active():
            self._apply_link()
        self._on_changed()

    def _on_link_toggled(self, *_args) -> None:
        if self.link_row.get_active():
            self._apply_link()
        self._sync_sensitivity()
        if not self._suppress:
            self._on_changed()

    def _apply_link(self) -> None:
        with self._quiet():
            self.left_row.set_value(0.0)
            self.top_row.set_value(0.0)
            self.width_row.set_value(self.page_width_row.get_value())
            self.height_row.set_value(self.page_height_row.get_value())

    def _sync_sensitivity(self) -> None:
        lineart = self.mode_row.get_selected() == caps.MODES.index(caps.THRESHOLD_MODE)
        # --threshold is reported [inactive] outside Lineart, so it is both
        # disabled here and omitted from argv.
        self.threshold_row.set_sensitive(lineart)

        linked = self.link_row.get_active()
        for row in (self.left_row, self.top_row, self.width_row, self.height_row):
            row.set_sensitive(not linked)

        self.autocrop_margin_row.set_sensitive(self.autocrop_row.get_active())

        self.slow_banner.set_visible(
            self.swcrop_row.get_active()
            or self.swdeskew_row.get_active()
            or self.despeck_row.get_value() > 0
        )

    def _mark_custom(self) -> None:
        if self.preset_row.get_selected() != 0:
            with self._quiet():
                self.preset_row.set_selected(0)
        self._update_delete_sensitivity()

    def _update_delete_sensitivity(self) -> None:
        preset = self.current_preset()
        self._delete_button.set_sensitive(preset is not None and not preset.builtin)

    def _quiet(self) -> _Quiet:
        """Update widgets without re-emitting ``changed``."""
        return _Quiet(self)

    # -- presets ----------------------------------------------------------
    def set_presets(self, user_presets: list[Preset], *, selected: str = "") -> None:
        self._presets = list(BUILTIN_PRESETS) + list(user_presets)
        names = [CUSTOM_LABEL] + [p.name for p in self._presets]
        with self._quiet():
            self.preset_row.set_model(Gtk.StringList.new(names))
            index = names.index(selected) if selected in names else 0
            self.preset_row.set_selected(index)
        self._update_delete_sensitivity()

    def current_preset(self) -> Preset | None:
        index = self.preset_row.get_selected()
        if index <= 0 or index - 1 >= len(self._presets):
            return None
        return self._presets[index - 1]

    def _on_preset_selected(self, *_args) -> None:
        if self._suppress:
            return
        preset = self.current_preset()
        self._update_delete_sensitivity()
        if preset is None:
            return
        self.set_settings(preset.settings, keep_preset=True)
        self.emit("changed")

    def _on_delete_clicked(self, *_args) -> None:
        preset = self.current_preset()
        if preset is not None and not preset.builtin:
            self.emit("preset-delete-requested", preset.name)

    # -- settings <-> widgets --------------------------------------------
    def set_settings(self, settings: ScanSettings, *, keep_preset: bool = False) -> None:
        with self._quiet():
            self.source_row.set_selected(_index(caps.SOURCES, settings.source))
            self.mode_row.set_selected(_index(caps.MODES, settings.mode))
            self.resolution_row.set_selected(
                caps.RESOLUTIONS.index(settings.resolution)
                if settings.resolution in caps.RESOLUTIONS
                else caps.RESOLUTIONS.index(300)
            )
            self.page_width_row.set_value(settings.page_width)
            self.page_height_row.set_value(settings.page_height)
            self.left_row.set_value(settings.left)
            self.top_row.set_value(settings.top)
            self.width_row.set_value(settings.width)
            self.height_row.set_value(settings.height)
            self.brightness_row.set_value(settings.brightness)
            self.contrast_row.set_value(settings.contrast)
            self.threshold_row.set_value(settings.threshold)
            self.swcrop_row.set_active(settings.swcrop)
            self.swdeskew_row.set_active(settings.swdeskew)
            self.rollerdeskew_row.set_active(settings.rollerdeskew)
            self.despeck_row.set_value(settings.swdespeck)
            self.swskip_row.set_value(settings.swskip)
            self.df_thickness_row.set_active(settings.df_thickness)
            self.df_length_row.set_active(settings.df_length)
            self.stapledetect_row.set_active(settings.stapledetect)
            self.dropout_front_row.set_selected(
                _index(caps.DROPOUT_VALUES, settings.dropout_front)
            )
            self.dropout_back_row.set_selected(
                _index(caps.DROPOUT_VALUES, settings.dropout_back)
            )
            self.buffermode_row.set_active(settings.buffermode)
            self.autocrop_row.set_active(settings.autocrop)
            self.autocrop_margin_row.set_value(settings.autocrop_margin_mm)
            self.link_row.set_active(_is_whole_page(settings))
            if not keep_preset:
                self.preset_row.set_selected(0)
        self._sync_sensitivity()
        self._update_delete_sensitivity()

    def get_settings(self) -> ScanSettings:
        return ScanSettings(
            source=caps.SOURCES[self.source_row.get_selected()],
            mode=caps.MODES[self.mode_row.get_selected()],
            resolution=caps.RESOLUTIONS[self.resolution_row.get_selected()],
            page_width=self.page_width_row.get_value(),
            page_height=self.page_height_row.get_value(),
            left=self.left_row.get_value(),
            top=self.top_row.get_value(),
            width=self.width_row.get_value(),
            height=self.height_row.get_value(),
            brightness=int(self.brightness_row.get_value()),
            contrast=int(self.contrast_row.get_value()),
            threshold=int(self.threshold_row.get_value()),
            df_thickness=self.df_thickness_row.get_active(),
            df_length=self.df_length_row.get_active(),
            rollerdeskew=self.rollerdeskew_row.get_active(),
            swdeskew=self.swdeskew_row.get_active(),
            swdespeck=int(self.despeck_row.get_value()),
            swcrop=self.swcrop_row.get_active(),
            swskip=self.swskip_row.get_value(),
            stapledetect=self.stapledetect_row.get_active(),
            dropout_front=caps.DROPOUT_VALUES[self.dropout_front_row.get_selected()],
            dropout_back=caps.DROPOUT_VALUES[self.dropout_back_row.get_selected()],
            buffermode=self.buffermode_row.get_active(),
            autocrop=self.autocrop_row.get_active(),
            autocrop_margin_mm=self.autocrop_margin_row.get_value(),
        )


class _Quiet:
    """Context manager that suppresses change notifications while updating."""

    def __init__(self, owner: SettingsSidebar) -> None:
        self._owner = owner

    def __enter__(self) -> SettingsSidebar:
        self._owner._suppress += 1
        return self._owner

    def __exit__(self, *exc) -> bool:
        self._owner._suppress -= 1
        return False


def _index(values: tuple[str, ...], value: str) -> int:
    try:
        return values.index(value)
    except ValueError:
        return 0


def _is_whole_page(settings: ScanSettings) -> bool:
    return (
        abs(settings.left) < 0.05
        and abs(settings.top) < 0.05
        and abs(settings.width - settings.page_width) < 0.05
        and abs(settings.height - settings.page_height) < 0.05
    )
