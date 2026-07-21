"""Create/update/delete operations for Project/Reach/Site/Well - the Site
Management UI's backend (Phase 5). See Implementation Plan.md section 11.

Deletes are soft: a delete renames the relevant .json5 file (or removes just
that entity's entry from its parent's .json5) so the app stops seeing it, but
never touches the logger data files or already-ingested SQLite rows
underneath. This was a deliberate, explicit decision (not a default) - field
data can be irreplaceable, and the operation is trivially reversible (rename
the file back / re-add the entry) where a real `rm -rf` would not be.
"""

from __future__ import annotations

import re
import zoneinfo
from pathlib import Path

import json5

from .catalog import Catalog, CatalogError, load_catalog
from .models import Project, Reach, Site, Well, WellType


class ManagementError(Exception):
    """Raised for user-facing management errors: invalid input, a name/folder
    collision, an already-deleted entity, or something that doesn't resolve."""


_PROJECT_HEADER = (
    '// Mid-Columbia Fisheries Data Analysis - Project settings\n'
    '// See "Implementation Plan.md" section 7 for the config schema this follows.\n'
    "// Managed by the Site Management UI - hand edits are fine, but will be\n"
    "// reformatted (comments regenerated) the next time this file is saved via the UI.\n"
)

_SITE_HEADER = (
    '// Site settings - see "Implementation Plan.md" section 7.\n'
    "// Managed by the Site Management UI - hand edits are fine, but will be\n"
    "// reformatted (comments regenerated) the next time this file is saved via the UI.\n"
)


# ---- Project -----------------------------------------------------------


def create_project(
    data_root: Path,
    name: str,
    description: str,
    timezone: str,
    map_center_lat: float | None,
    map_center_lon: float | None,
    map_zoom: int,
) -> Project:
    _validate_timezone(timezone)
    data_root.mkdir(parents=True, exist_ok=True)
    project_dir = data_root / _sanitize_folder_name(name)
    _check_available(project_dir)
    project_dir.mkdir(parents=True)

    _write_project_json5(
        project_dir,
        {
            "name": name,
            "description": description,
            "timezone": timezone,
            "map": {"center_lat": map_center_lat, "center_lon": map_center_lon, "zoom": map_zoom},
            "reaches": [],
        },
    )
    return _load_catalog(data_root, project_dir.name).project


def update_project(
    data_root: Path,
    project: Project,
    name: str,
    description: str,
    timezone: str,
    map_center_lat: float | None,
    map_center_lon: float | None,
    map_zoom: int,
) -> Project:
    _validate_timezone(timezone)
    project_dir = data_root / project.folder_path
    raw = _read_raw(project_dir / "project.json5")
    raw["name"] = name
    raw["description"] = description
    raw["timezone"] = timezone
    raw["map"] = {"center_lat": map_center_lat, "center_lon": map_center_lon, "zoom": map_zoom}
    _write_project_json5(project_dir, raw)
    return _load_catalog(data_root, project.folder_path).project


def delete_project(data_root: Path, project: Project) -> None:
    _soft_delete(data_root / project.folder_path / "project.json5")


# ---- Reach (+ its required ATM well) ------------------------------------


def create_reach(
    data_root: Path,
    project: Project,
    name: str,
    atm_name: str,
    atm_device_serial: str | None,
    atm_latitude: float | None,
    atm_longitude: float | None,
) -> Reach:
    project_dir = data_root / project.folder_path
    reach_folder = _sanitize_folder_name(name)
    reach_dir = project_dir / reach_folder
    _check_available(reach_dir)
    atm_folder = _sanitize_folder_name(atm_name)

    reach_dir.mkdir(parents=True)
    (reach_dir / atm_folder).mkdir()

    raw = _read_raw(project_dir / "project.json5")
    raw.setdefault("reaches", []).append(
        {
            "name": name,
            "folder": reach_folder,
            "atm_well": {
                "name": atm_name,
                "folder": atm_folder,
                "device_serial": atm_device_serial,
                "latitude": atm_latitude,
                "longitude": atm_longitude,
            },
        }
    )
    _write_project_json5(project_dir, raw)

    catalog = _load_catalog(data_root, project.folder_path)
    return _find_by_folder(catalog.project.reaches, str(reach_dir.relative_to(data_root)), "reach")


def update_reach(
    data_root: Path,
    project: Project,
    reach: Reach,
    name: str,
    atm_name: str,
    atm_device_serial: str | None,
    atm_latitude: float | None,
    atm_longitude: float | None,
) -> Reach:
    project_dir = data_root / project.folder_path
    raw = _read_raw(project_dir / "project.json5")
    reach_folder = Path(reach.folder_path).name
    entry = _find_dict_by_folder(raw.get("reaches", []), reach_folder, "reach")
    entry["name"] = name
    entry["atm_well"]["name"] = atm_name
    entry["atm_well"]["device_serial"] = atm_device_serial
    entry["atm_well"]["latitude"] = atm_latitude
    entry["atm_well"]["longitude"] = atm_longitude
    _write_project_json5(project_dir, raw)

    catalog = _load_catalog(data_root, project.folder_path)
    return _find_by_folder(catalog.project.reaches, reach.folder_path, "reach")


def delete_reach(data_root: Path, project: Project, reach: Reach) -> None:
    project_dir = data_root / project.folder_path
    raw = _read_raw(project_dir / "project.json5")
    reach_folder = Path(reach.folder_path).name
    remaining = [r for r in raw.get("reaches", []) if r.get("folder") != reach_folder]
    if len(remaining) == len(raw.get("reaches", [])):
        raise ManagementError(f"reach folder {reach_folder!r} not found in project.json5")
    raw["reaches"] = remaining
    _write_project_json5(project_dir, raw)


# ---- Site ----------------------------------------------------------------


def create_site(data_root: Path, project: Project, reach: Reach, name: str, latitude: float | None, longitude: float | None) -> Site:
    reach_dir = data_root / reach.folder_path
    site_dir = reach_dir / _sanitize_folder_name(name)
    _check_available(site_dir)
    site_dir.mkdir(parents=True)

    _write_site_json5(site_dir, {"name": name, "latitude": latitude, "longitude": longitude, "wells": []})

    catalog = _load_catalog(data_root, project.folder_path)
    updated_reach = _find_by_folder(catalog.project.reaches, reach.folder_path, "reach")
    return _find_by_folder(updated_reach.sites, str(site_dir.relative_to(data_root)), "site")


def update_site(data_root: Path, project: Project, site: Site, name: str, latitude: float | None, longitude: float | None) -> Site:
    site_dir = data_root / site.folder_path
    raw = _read_raw(site_dir / "site.json5")
    raw["name"] = name
    raw["latitude"] = latitude
    raw["longitude"] = longitude
    _write_site_json5(site_dir, raw)

    catalog = _load_catalog(data_root, project.folder_path)
    for reach in catalog.project.reaches:
        for updated_site in reach.sites:
            if updated_site.folder_path == site.folder_path:
                return updated_site
    raise ManagementError(f"site {site.folder_path!r} not found after update")


def delete_site(data_root: Path, site: Site) -> None:
    _soft_delete(data_root / site.folder_path / "site.json5")


# ---- Well (Site-affiliated only - a Reach's ATM well is edited/deleted via
#      update_reach/delete_reach, not here; see routes_wells.py's guard) -----


def create_well(data_root: Path, project: Project, site: Site, name: str, well_type: WellType, device_serial: str | None) -> Well:
    site_dir = data_root / site.folder_path
    well_dir = site_dir / _sanitize_folder_name(name)
    _check_available(well_dir)
    well_dir.mkdir(parents=True)

    raw = _read_raw(site_dir / "site.json5")
    raw.setdefault("wells", []).append(
        {
            "name": name,
            "folder": well_dir.name,
            "type": well_type.value,
            "device_serial": device_serial,
            "paired_atm_well": None,
        }
    )
    _write_site_json5(site_dir, raw)

    catalog = _load_catalog(data_root, project.folder_path)
    new_folder_path = str(well_dir.relative_to(data_root))
    for well in catalog.wells.values():
        if well.folder_path == new_folder_path:
            return well
    raise ManagementError(f"well {new_folder_path!r} not found after creation")


def update_well(data_root: Path, project: Project, well: Well, name: str, well_type: WellType, device_serial: str | None) -> Well:
    if well.site_id is None:
        raise ManagementError("cannot edit a Reach's ATM well here - edit the Reach instead")

    site_dir = data_root / Path(well.folder_path).parent
    raw = _read_raw(site_dir / "site.json5")
    well_folder = Path(well.folder_path).name
    entry = _find_dict_by_folder(raw.get("wells", []), well_folder, "well")
    entry["name"] = name
    entry["type"] = well_type.value
    entry["device_serial"] = device_serial
    _write_site_json5(site_dir, raw)

    catalog = _load_catalog(data_root, project.folder_path)
    updated = catalog.wells.get(well.id)
    if updated is None:
        raise ManagementError(f"well {well.id!r} not found after update")
    return updated


def delete_well(data_root: Path, project: Project, well: Well) -> None:
    if well.site_id is None:
        raise ManagementError("cannot delete a Reach's ATM well directly - delete the Reach instead")

    site_dir = data_root / Path(well.folder_path).parent
    raw = _read_raw(site_dir / "site.json5")
    well_folder = Path(well.folder_path).name
    remaining = [w for w in raw.get("wells", []) if w.get("folder") != well_folder]
    if len(remaining) == len(raw.get("wells", [])):
        raise ManagementError(f"well folder {well_folder!r} not found in site.json5")
    raw["wells"] = remaining
    _write_site_json5(site_dir, raw)


# ---- helpers ---------------------------------------------------------------


def _validate_timezone(timezone_name: str) -> None:
    if timezone_name not in zoneinfo.available_timezones():
        raise ManagementError(f"unknown IANA timezone: {timezone_name!r}")


def _sanitize_folder_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ManagementError("name must not be empty")
    # Only the characters that are actually unsafe in a folder name (Windows'
    # reserved set, a superset of macOS/Linux's) get replaced - punctuation
    # like commas or apostrophes is left alone (Project Description note 4:
    # names "can contain spaces and punctuation").
    return re.sub(r'[<>:"/\\|?*]', "-", name)


def _check_available(path: Path) -> None:
    if path.exists():
        raise ManagementError(f"a folder named {path.name!r} already exists in {path.parent}")


def _soft_delete(config_file: Path) -> None:
    deleted_file = config_file.with_suffix(config_file.suffix + ".deleted")
    if not config_file.exists():
        if deleted_file.exists():
            raise ManagementError(f"{config_file.name} is already deleted")
        raise ManagementError(f"{config_file} not found")
    config_file.rename(deleted_file)


def _read_raw(path: Path) -> dict:
    try:
        return json5.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManagementError(f"{path} not found") from exc
    except ValueError as exc:
        raise ManagementError(f"{path} is not valid JSON5: {exc}") from exc


def _write_project_json5(project_dir: Path, data: dict) -> None:
    (project_dir / "project.json5").write_text(_PROJECT_HEADER + json5.dumps(data, indent=4) + "\n", encoding="utf-8")


def _write_site_json5(site_dir: Path, data: dict) -> None:
    (site_dir / "site.json5").write_text(_SITE_HEADER + json5.dumps(data, indent=4) + "\n", encoding="utf-8")


def _load_catalog(data_root: Path, project_folder: str) -> Catalog:
    return load_catalog(data_root, project_folder)


def _find_dict_by_folder(entries: list[dict], folder: str, kind: str) -> dict:
    for entry in entries:
        if entry.get("folder") == folder:
            return entry
    raise ManagementError(f"{kind} folder {folder!r} not found")


def _find_by_folder(entries, folder_path: str, kind: str):
    for entry in entries:
        if entry.folder_path == folder_path:
            return entry
    raise ManagementError(f"{kind} {folder_path!r} not found after write")
