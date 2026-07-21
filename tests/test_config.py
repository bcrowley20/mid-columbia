from pathlib import Path

import pytest

from midcolumbia.config import SettingsError, load_settings


def test_load_settings_from_repo_root(repo_root: Path):
    settings = load_settings(repo_root / "settings.json")
    assert settings.data_root == Path("data")
    assert settings.database_path == Path("midcolumbia.sqlite3")
    assert "hoboware_csv" in settings.enabled_device_handlers
    assert "hoboconnect_xlsx" in settings.enabled_device_handlers
    assert settings.display.pressure_unit == "kPa"
    assert settings.display.timezone == "America/Los_Angeles"
    assert settings.calculations.max_atm_gap_hours == 12


def test_load_settings_missing_file_raises(tmp_path: Path):
    with pytest.raises(SettingsError, match="not found"):
        load_settings(tmp_path / "does_not_exist.json")


def test_load_settings_invalid_json_raises(tmp_path: Path):
    bad_file = tmp_path / "settings.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(SettingsError, match="not valid JSON"):
        load_settings(bad_file)


def test_load_settings_missing_field_raises(tmp_path: Path):
    bad_file = tmp_path / "settings.json"
    bad_file.write_text('{"data_root": "data"}', encoding="utf-8")
    with pytest.raises(SettingsError, match="missing required field"):
        load_settings(bad_file)


def test_load_settings_missing_calculations_section_raises(tmp_path: Path):
    bad_file = tmp_path / "settings.json"
    bad_file.write_text(
        """{
            "data_root": "data",
            "database_path": "midcolumbia.sqlite3",
            "enabled_device_handlers": [],
            "display": {
                "pressure_unit": "kPa",
                "temperature_unit": "degC",
                "depth_unit": "ft",
                "timezone": "America/Los_Angeles"
            }
        }""",
        encoding="utf-8",
    )
    with pytest.raises(SettingsError, match="missing required field"):
        load_settings(bad_file)
