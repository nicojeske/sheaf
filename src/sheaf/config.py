"""Persistence: last-used settings, window state, presets and Paperless config.

Read with the stdlib ``tomllib``, written with ``tomli-w``. Two things are
deliberately *not* stored here: the API token (that lives in the keyring, see
secrets.py) and the scanner device string (it encodes a USB bus address that
changes on replug, so it is rediscovered every launch).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .presets import BUILTIN_PRESETS, DEFAULT_PRESET_NAME, Preset
from .settings import ScanSettings

#: Deliberately still "scanner": this is where the config and the window state
#: already live, and renaming it when the app was renamed to Sheaf would orphan
#: them — along with the Paperless URL — for no gain the user can see.
APP_DIR_NAME = "scanner"
CONFIG_FILE_NAME = "config.toml"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR_NAME


def config_path() -> Path:
    return config_dir() / CONFIG_FILE_NAME


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / APP_DIR_NAME


@dataclass(slots=True)
class WindowState:
    width: int = 1200
    height: int = 800
    maximized: bool = False


@dataclass(slots=True)
class PaperlessConfig:
    base_url: str = ""
    default_tags: list[int] = field(default_factory=list)
    default_correspondent: int | None = None
    default_document_type: int | None = None
    default_storage_path: int | None = None
    title_template: str = "Receipt {date}"

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip())


@dataclass(slots=True)
class Config:
    window: WindowState = field(default_factory=WindowState)
    settings: ScanSettings = field(default_factory=ScanSettings)
    last_preset: str = ""
    presets: list[Preset] = field(default_factory=list)
    paperless: PaperlessConfig = field(default_factory=PaperlessConfig)
    last_save_dir: str = ""

    # -- load / save ------------------------------------------------------
    @classmethod
    def first_run(cls) -> Config:
        """Defaults for a machine that has never run the app.

        The flagship Receipt preset is selected, because auto-sized receipt
        scanning is the reason this app exists.
        """
        return cls(
            last_preset=DEFAULT_PRESET_NAME,
            settings=BUILTIN_PRESETS[0].settings,
        )

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Load config, falling back to defaults for anything malformed.

        A broken config file must never stop the app from starting.
        """
        path = path or config_path()
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return cls.first_run()

        config = cls()
        window = raw.get("window")
        if isinstance(window, dict):
            config.window = WindowState(
                width=_int(window.get("width"), 1200),
                height=_int(window.get("height"), 800),
                maximized=bool(window.get("maximized", False)),
            )

        settings = raw.get("settings")
        if isinstance(settings, dict):
            config.settings = ScanSettings.from_dict(settings)

        general = raw.get("general")
        if isinstance(general, dict):
            config.last_preset = str(general.get("last_preset", "") or "")
            config.last_save_dir = str(general.get("last_save_dir", "") or "")

        presets = raw.get("presets")
        if isinstance(presets, list):
            for entry in presets:
                if isinstance(entry, dict):
                    preset = Preset.from_dict(entry)
                    if preset is not None:
                        config.presets.append(preset)

        paperless = raw.get("paperless")
        if isinstance(paperless, dict):
            config.paperless = PaperlessConfig(
                base_url=str(paperless.get("base_url", "") or ""),
                default_tags=[
                    int(t) for t in paperless.get("default_tags", []) if isinstance(t, int)
                ],
                default_correspondent=_opt_int(paperless.get("default_correspondent")),
                default_document_type=_opt_int(paperless.get("default_document_type")),
                default_storage_path=_opt_int(paperless.get("default_storage_path")),
                title_template=str(paperless.get("title_template") or "Receipt {date}"),
            )
        return config

    def save(self, path: Path | None = None) -> None:
        import tomli_w

        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        document: dict[str, Any] = {
            "window": asdict(self.window),
            "general": {
                "last_preset": self.last_preset,
                "last_save_dir": self.last_save_dir,
            },
            "settings": self.settings.to_dict(),
            "paperless": _prune(asdict(self.paperless)),
            "presets": [p.to_dict() for p in self.presets if not p.builtin],
        }
        tmp = path.with_suffix(".toml.tmp")
        tmp.write_text(tomli_w.dumps(document), encoding="utf-8")
        tmp.replace(path)


def _prune(data: dict[str, Any]) -> dict[str, Any]:
    """tomli-w cannot serialise None; drop unset optional values."""
    return {k: v for k, v in data.items() if v is not None}


def _int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
