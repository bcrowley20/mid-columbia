"""Loads project.json5 / site.json5 into the models.py dataclass hierarchy.

See Implementation Plan.md section 7 for the file schemas this reads, and the
"IDs" note under section 5 for the id scheme implemented here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import json5

from .models import Project, Reach, Site, Well, WellType


class CatalogError(Exception):
    """Raised when project.json5/site.json5 is missing, malformed, or references
    a folder or well that doesn't resolve on disk."""


@dataclass(frozen=True)
class Catalog:
    project: Project
    # Every Well in the project, including reach-level ATM wells, keyed by id.
    # Site.wells only holds a site's own wells - this is the flat lookup the
    # scanner and calculations module need to reach ATM wells too.
    wells: dict[str, Well]
    # IANA timezone used to interpret local (XLSX) logger timestamps for this
    # project (Implementation Plan.md section 7) - not part of the core Project
    # dataclass in models.py since it's config, not identity.
    timezone: str


def discover_project_folders(data_root: Path) -> list[str]:
    """Names of immediate subdirectories of data_root that contain a project.json5."""
    if not data_root.is_dir():
        raise CatalogError(f"data root not found: {data_root}")
    return sorted(
        entry.name
        for entry in data_root.iterdir()
        if entry.is_dir() and (entry / "project.json5").exists()
    )


def load_all(data_root: Path) -> list[Catalog]:
    """Loads every project under data_root - what the API layer (section 11)
    needs to search across all projects for a given well/site id."""
    return [load_catalog(data_root, folder) for folder in discover_project_folders(data_root)]


def find_well(catalogs: list[Catalog], well_id: str) -> Well | None:
    for catalog in catalogs:
        well = catalog.wells.get(well_id)
        if well is not None:
            return well
    return None


def find_site(catalogs: list[Catalog], site_id: str) -> tuple[Reach, Site] | None:
    for catalog in catalogs:
        for reach in catalog.project.reaches:
            for site in reach.sites:
                if site.id == site_id:
                    return reach, site
    return None


def load_catalog(data_root: Path, project_folder: str) -> Catalog:
    project_dir = data_root / project_folder
    project_file = project_dir / "project.json5"
    raw = _read_json5(project_file)

    project_id = _slug_path([project_folder])
    wells: dict[str, Well] = {}
    try:
        reaches = [
            _load_reach(data_root, project_dir, project_folder, project_id, reach_raw, wells)
            for reach_raw in raw.get("reaches", [])
        ]
        project = Project(id=project_id, name=raw["name"], reaches=reaches)
        project_timezone = raw["timezone"]
    except KeyError as exc:
        raise CatalogError(
            f"a project.json5/site.json5 under {project_dir} is missing required field: {exc}"
        ) from exc
    return Catalog(project=project, wells=wells, timezone=project_timezone)


def _load_reach(
    data_root: Path,
    project_dir: Path,
    project_folder: str,
    project_id: str,
    reach_raw: dict,
    wells: dict[str, Well],
) -> Reach:
    reach_folder = reach_raw["folder"]
    reach_dir = project_dir / reach_folder
    if not reach_dir.is_dir():
        raise CatalogError(f"Reach folder not found: {reach_dir}")

    reach_id = _slug_path([project_folder, reach_folder])

    atm_raw = reach_raw["atm_well"]
    atm_folder = atm_raw["folder"]
    atm_dir = reach_dir / atm_folder
    if not atm_dir.is_dir():
        raise CatalogError(f"ATM well folder not found: {atm_dir}")

    atm_well_id = _slug_path([project_folder, reach_folder, atm_folder])
    wells[atm_well_id] = Well(
        id=atm_well_id,
        site_id=None,
        reach_id=reach_id,
        name=atm_raw["name"],
        well_type=WellType.ATMOSPHERIC,
        folder_path=str(atm_dir.relative_to(data_root)),
        device_serial=atm_raw.get("device_serial"),
        paired_atm_well_id=None,
        latitude=atm_raw.get("latitude"),
        longitude=atm_raw.get("longitude"),
    )
    atm_wells_by_name = {atm_raw["name"]: atm_well_id}

    sites = []
    for entry in sorted(reach_dir.iterdir()):
        if not entry.is_dir() or entry == atm_dir:
            continue
        if not (entry / "site.json5").exists():
            continue  # not a Site folder - skip rather than error
        sites.append(
            _load_site(data_root, project_folder, reach_folder, reach_id, entry, atm_well_id, atm_wells_by_name, wells)
        )

    return Reach(id=reach_id, project_id=project_id, name=reach_raw["name"], atm_well_id=atm_well_id, sites=sites)


def _load_site(
    data_root: Path,
    project_folder: str,
    reach_folder: str,
    reach_id: str,
    site_dir: Path,
    reach_atm_well_id: str,
    atm_wells_by_name: dict[str, str],
    wells: dict[str, Well],
) -> Site:
    raw = _read_json5(site_dir / "site.json5")
    site_folder = site_dir.name
    site_id = _slug_path([project_folder, reach_folder, site_folder])

    site_wells = []
    for well_raw in raw.get("wells", []):
        well_folder = well_raw["folder"]
        well_dir = site_dir / well_folder
        if not well_dir.is_dir():
            raise CatalogError(f"Well folder not found: {well_dir}")

        try:
            well_type = WellType(well_raw["type"])
        except ValueError as exc:
            raise CatalogError(f"Unknown well type {well_raw['type']!r} for {well_dir}") from exc

        paired_name = well_raw.get("paired_atm_well")
        if paired_name is None:
            paired_atm_well_id = reach_atm_well_id
        elif paired_name in atm_wells_by_name:
            paired_atm_well_id = atm_wells_by_name[paired_name]
        else:
            raise CatalogError(
                f"Well {well_dir} references paired_atm_well {paired_name!r}, "
                f"which doesn't match this Reach's ATM well ({', '.join(atm_wells_by_name)})"
            )

        well_id = _slug_path([project_folder, reach_folder, site_folder, well_folder])
        well = Well(
            id=well_id,
            site_id=site_id,
            reach_id=None,
            name=well_raw["name"],
            well_type=well_type,
            folder_path=str(well_dir.relative_to(data_root)),
            device_serial=well_raw.get("device_serial"),
            paired_atm_well_id=paired_atm_well_id,
        )
        site_wells.append(well)
        wells[well_id] = well

    return Site(
        id=site_id,
        reach_id=reach_id,
        name=raw["name"],
        latitude=raw.get("latitude"),
        longitude=raw.get("longitude"),
        wells=site_wells,
    )


def _read_json5(path: Path) -> dict:
    if not path.exists():
        raise CatalogError(f"{path.name} not found at {path}")
    try:
        return json5.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CatalogError(f"{path} is not valid JSON5: {exc}") from exc


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def _slug_path(parts: list[str]) -> str:
    return "/".join(_slug(p) for p in parts)
