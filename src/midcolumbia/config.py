"""Loader for the app-level settings.json (see Implementation Plan.md section 7)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SETTINGS_PATH = Path("settings.json")


class SettingsError(Exception):
    """Raised when settings.json is missing or malformed."""


@dataclass(frozen=True)
class DisplaySettings:
    pressure_unit: str
    temperature_unit: str
    depth_unit: str
    timezone: str


@dataclass(frozen=True)
class Settings:
    data_root: Path
    database_path: Path
    enabled_device_handlers: tuple[str, ...]
    display: DisplaySettings


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> Settings:
    """Load and validate settings.json. Raises SettingsError with a clear message
    on missing file, invalid JSON, or missing/malformed required fields — callers
    should not have to guess why configuration failed to load.
    """
    if not path.exists():
        raise SettingsError(f"settings.json not found at {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SettingsError(f"settings.json at {path} is not valid JSON: {exc}") from exc

    try:
        display_raw = raw["display"]
        return Settings(
            data_root=Path(raw["data_root"]),
            database_path=Path(raw["database_path"]),
            enabled_device_handlers=tuple(raw["enabled_device_handlers"]),
            display=DisplaySettings(
                pressure_unit=display_raw["pressure_unit"],
                temperature_unit=display_raw["temperature_unit"],
                depth_unit=display_raw["depth_unit"],
                timezone=display_raw["timezone"],
            ),
        )
    except KeyError as exc:
        raise SettingsError(f"settings.json at {path} is missing required field: {exc}") from exc
