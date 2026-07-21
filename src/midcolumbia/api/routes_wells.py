"""Well metadata, stats, and management (Phase 5) endpoints. See
Implementation Plan.md section 11."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from .. import management
from ..catalog import Catalog, find_site, find_well
from ..config import Settings
from ..management import ManagementError
from ..models import Project, WellType
from ..storage import db
from .deps import get_catalogs, get_db, get_settings
from .schemas import WellOut, WellSummaryOut, WellWrite

router = APIRouter(tags=["wells"])


@router.get("/wells", response_model=WellOut)
def get_well(well_id: str, catalogs: list[Catalog] = Depends(get_catalogs)) -> WellOut:
    well = find_well(catalogs, well_id)
    if well is None:
        raise HTTPException(status_code=404, detail=f"well {well_id!r} not found")
    return WellOut.from_well(well)


@router.get("/wells/summary", response_model=WellSummaryOut)
def get_well_summary(
    well_id: str,
    catalogs: list[Catalog] = Depends(get_catalogs),
    conn: sqlite3.Connection = Depends(get_db),
) -> WellSummaryOut:
    # Stats for a single well regardless of whether it belongs to a Site -
    # added so the map's ATM marker (not part of any Site) can show the same
    # point-count/last-reading data a site's wells get via /sites/summary.
    well = find_well(catalogs, well_id)
    if well is None:
        raise HTTPException(status_code=404, detail=f"well {well_id!r} not found")
    return WellSummaryOut(
        well_id=well.id,
        well_name=well.name,
        well_type=well.well_type.value,
        point_count=db.count_distinct_timestamps(conn, well.id),
        last_reading_at=db.latest_reading_timestamp(conn, well.id),
    )


def _project_owning_well(catalogs: list[Catalog], well_id: str) -> Project | None:
    for catalog in catalogs:
        if well_id in catalog.wells:
            return catalog.project
    return None


def _parse_well_type(value: str) -> WellType:
    try:
        return WellType(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"unknown well_type {value!r}") from exc


# ---- Well management (Site-affiliated wells only - a Reach's ATM well is
#      managed via /reaches, see management.py's guard) ----------------------


@router.post("/wells", response_model=WellOut, status_code=201)
def create_well(
    site_id: str,
    body: WellWrite,
    catalogs: list[Catalog] = Depends(get_catalogs),
    settings: Settings = Depends(get_settings),
) -> WellOut:
    found = find_site(catalogs, site_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"site {site_id!r} not found")
    reach, site = found
    project = next((c.project for c in catalogs if c.project.id == reach.project_id), None)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project for site {site_id!r} not found")

    well_type = _parse_well_type(body.well_type)
    try:
        well = management.create_well(settings.data_root, project, site, body.name, well_type, body.device_serial)
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WellOut.from_well(well)


@router.patch("/wells", response_model=WellOut)
def update_well(
    well_id: str,
    body: WellWrite,
    catalogs: list[Catalog] = Depends(get_catalogs),
    settings: Settings = Depends(get_settings),
) -> WellOut:
    well = find_well(catalogs, well_id)
    if well is None:
        raise HTTPException(status_code=404, detail=f"well {well_id!r} not found")
    project = _project_owning_well(catalogs, well_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project for well {well_id!r} not found")

    well_type = _parse_well_type(body.well_type)
    try:
        updated = management.update_well(settings.data_root, project, well, body.name, well_type, body.device_serial)
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WellOut.from_well(updated)


@router.delete("/wells", status_code=204)
def delete_well(
    well_id: str,
    catalogs: list[Catalog] = Depends(get_catalogs),
    settings: Settings = Depends(get_settings),
) -> None:
    well = find_well(catalogs, well_id)
    if well is None:
        raise HTTPException(status_code=404, detail=f"well {well_id!r} not found")
    project = _project_owning_well(catalogs, well_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project for well {well_id!r} not found")
    try:
        management.delete_well(settings.data_root, project, well)
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
