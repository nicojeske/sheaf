"""Config persistence. tomli-w cannot serialise None, hence the pruning."""

from __future__ import annotations

from pathlib import Path

from sheaf.config import Config, PaperlessConfig
from sheaf.presets import BUILTIN_PRESETS, DEFAULT_PRESET_NAME, Preset
from sheaf.settings import ScanSettings


def test_first_run_selects_the_receipt_preset():
    config = Config.first_run()
    assert config.last_preset == DEFAULT_PRESET_NAME
    assert config.settings.swcrop
    assert config.settings.page_width == BUILTIN_PRESETS[0].settings.page_width


def test_missing_file_falls_back_to_first_run(tmp_path: Path):
    config = Config.load(tmp_path / "nope.toml")
    assert config.last_preset == DEFAULT_PRESET_NAME


def test_broken_file_does_not_stop_the_app(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("this is [not valid toml")
    assert Config.load(path).last_preset == DEFAULT_PRESET_NAME


def test_round_trip_preserves_settings_presets_and_paperless(tmp_path: Path):
    path = tmp_path / "config.toml"
    original = Config(
        settings=ScanSettings(
            source="ADF Duplex", mode="Lineart", resolution=600, threshold=99,
            page_width=80.0, page_height=355.5, width=80.0, height=355.5,
            swcrop=True, swdespeck=3, swskip=2.5, dropout_front="Enhance Red",
        ),
        last_preset="My receipts",
        presets=[Preset(name="My receipts", settings=ScanSettings(resolution=400))],
        paperless=PaperlessConfig(
            base_url="https://paperless.example.com",
            default_tags=[3, 9],
            default_document_type=2,
            title_template="Bon {date}",
        ),
    )
    original.window.width = 1400
    original.window.maximized = True
    original.save(path)

    loaded = Config.load(path)
    assert loaded.settings == original.settings
    assert loaded.last_preset == "My receipts"
    assert [p.name for p in loaded.presets] == ["My receipts"]
    assert loaded.presets[0].settings.resolution == 400
    assert loaded.paperless.base_url == "https://paperless.example.com"
    assert loaded.paperless.default_tags == [3, 9]
    assert loaded.paperless.default_document_type == 2
    assert loaded.paperless.default_correspondent is None
    assert loaded.paperless.title_template == "Bon {date}"
    assert loaded.window.width == 1400
    assert loaded.window.maximized is True


def test_unset_optional_values_are_dropped_rather_than_written(tmp_path: Path):
    # tomli-w raises on a None value, so optional ids must be pruned before
    # serialising. ("None" as a *value* is legitimate — it is what the backend
    # calls a disabled colour dropout.)
    path = tmp_path / "config.toml"
    Config().save(path)
    text = path.read_text()
    assert "default_correspondent" not in text
    assert "default_document_type" not in text
    assert "= None" not in text
    assert 'dropout_front = "None"' in text


def test_builtin_presets_are_not_persisted(tmp_path: Path):
    path = tmp_path / "config.toml"
    config = Config(presets=list(BUILTIN_PRESETS))
    config.save(path)
    assert Config.load(path).presets == []


def test_the_token_and_device_are_never_written(tmp_path: Path):
    path = tmp_path / "config.toml"
    config = Config(paperless=PaperlessConfig(base_url="https://p.example.com"))
    config.save(path)
    text = path.read_text()
    assert "token" not in text
    assert "libusb" not in text
