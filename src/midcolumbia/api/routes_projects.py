"""Project/Reach/Site tree, site-summary, and management (Phase 5) endpoints.
See Implementation Plan.md section 11."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from .. import management
from ..catalog import Catalog, find_project, find_reach, find_site, load_catalog
from ..config import Settings
from ..management import ManagementError
from ..storage import db
from .deps import get_catalogs, get_db, get_settings
from .schemas import ProjectOut, ProjectWrite, ReachOut, ReachWrite, SiteOut, SiteSummaryOut, SiteWrite, WellSummaryOut

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(catalogs: list[Catalog] = Depends(get_catalogs)) -> list[ProjectOut]:
    return [ProjectOut.from_project(catalog.project, catalog.wells) for catalog in catalogs]


@router.get("/sites/summary", response_model=SiteSummaryOut)
def get_site_summary(
    site_id: str,
    catalogs: list[Catalog] = Depends(get_catalogs),
    conn: sqlite3.Connection = Depends(get_db),
) -> SiteSummaryOut:
    found = find_site(catalogs, site_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"site {site_id!r} not found")
    reach, site = found

    wells = [
        WellSummaryOut(
            well_id=well.id,
            well_name=well.name,
            well_type=well.well_type.value,
            point_count=db.count_distinct_timestamps(conn, well.id),
            last_reading_at=db.latest_reading_timestamp(conn, well.id),
        )
        for well in site.wells
    ]
    return SiteSummaryOut(
        site_id=site.id,
        site_name=site.name,
        reach_id=reach.id,
        reach_name=reach.name,
        latitude=site.latitude,
        longitude=site.longitude,
        wells=wells,
    )


# ---- Project management ----------------------------------------------------


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectWrite, settings: Settings = Depends(get_settings)) -> ProjectOut:
    try:
        project = management.create_project(
            settings.data_root,
            body.name,
            body.description,
            body.timezone,
            body.map_center_lat,
            body.map_center_lon,
            body.map_zoom,
        )
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    catalog = load_catalog(settings.data_root, project.folder_path)
    return ProjectOut.from_project(catalog.project, catalog.wells)


@router.patch("/projects", response_model=ProjectOut)
def update_project(
    project_id: str,
    body: ProjectWrite,
    catalogs: list[Catalog] = Depends(get_catalogs),
    settings: Settings = Depends(get_settings),
) -> ProjectOut:
    project = find_project(catalogs, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    try:
        updated = management.update_project(
            settings.data_root,
            project,
            body.name,
            body.description,
            body.timezone,
            body.map_center_lat,
            body.map_center_lon,
            body.map_zoom,
        )
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    catalog = load_catalog(settings.data_root, updated.folder_path)
    return ProjectOut.from_project(catalog.project, catalog.wells)


@router.delete("/projects", status_code=204)
def delete_project(
    project_id: str,
    catalogs: list[Catalog] = Depends(get_catalogs),
    settings: Settings = Depends(get_settings),
) -> None:
    project = find_project(catalogs, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    try:
        management.delete_project(settings.data_root, project)
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---- Reach management -------------------------------------------------------


@router.post("/reaches", response_model=ReachOut, status_code=201)
def create_reach(
    project_id: str,
    body: ReachWrite,
    catalogs: list[Catalog] = Depends(get_catalogs),
    settings: Settings = Depends(get_settings),
) -> ReachOut:
    project = find_project(catalogs, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    try:
        reach = management.create_reach(
            settings.data_root,
            project,
            body.name,
            body.atm_name,
            body.atm_device_serial,
            body.atm_latitude,
            body.atm_longitude,
        )
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    catalog = load_catalog(settings.data_root, project.folder_path)
    return ReachOut.from_reach(reach, catalog.wells)


@router.patch("/reaches", response_model=ReachOut)
def update_reach(
    reach_id: str,
    body: ReachWrite,
    catalogs: list[Catalog] = Depends(get_catalogs),
    settings: Settings = Depends(get_settings),
) -> ReachOut:
    found = find_reach(catalogs, reach_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"reach {reach_id!r} not found")
    project, reach = found
    try:
        updated = management.update_reach(
            settings.data_root,
            project,
            reach,
            body.name,
            body.atm_name,
            body.atm_device_serial,
            body.atm_latitude,
            body.atm_longitude,
        )
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    catalog = load_catalog(settings.data_root, project.folder_path)
    return ReachOut.from_reach(updated, catalog.wells)


@router.delete("/reaches", status_code=204)
def delete_reach(
    reach_id: str,
    catalogs: list[Catalog] = Depends(get_catalogs),
    settings: Settings = Depends(get_settings),
) -> None:
    found = find_reach(catalogs, reach_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"reach {reach_id!r} not found")
    project, reach = found
    try:
        management.delete_reach(settings.data_root, project, reach)
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---- Site management --------------------------------------------------------


@router.post("/sites", response_model=SiteOut, status_code=201)
def create_site(
    reach_id: str,
    body: SiteWrite,
    catalogs: list[Catalog] = Depends(get_catalogs),
    settings: Settings = Depends(get_settings),
) -> SiteOut:
    found = find_reach(catalogs, reach_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"reach {reach_id!r} not found")
    project, reach = found
    try:
        site = management.create_site(settings.data_root, project, reach, body.name, body.latitude, body.longitude)
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SiteOut.from_site(site)


@router.patch("/sites", response_model=SiteOut)
def update_site(
    site_id: str,
    body: SiteWrite,
    catalogs: list[Catalog] = Depends(get_catalogs),
    settings: Settings = Depends(get_settings),
) -> SiteOut:
    found = find_site(catalogs, site_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"site {site_id!r} not found")
    reach, site = found
    # Bare next() with no default would raise an unhandled StopIteration
    # instead of the app's usual 404 if this ever came up empty (Phase 7
    # error-handling audit, Implementation Plan.md section 14) - matches the
    # explicit-default + None-check pattern routes_wells.py's
    # _project_owning_well callers already use for the same "project that
    # owns this entity" lookup.
    project = next((c.project for c in catalogs if c.project.id == reach.project_id), None)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project for site {site_id!r} not found")
    try:
        updated = management.update_site(settings.data_root, project, site, body.name, body.latitude, body.longitude)
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SiteOut.from_site(updated)


@router.delete("/sites", status_code=204)
def delete_site(
    site_id: str,
    catalogs: list[Catalog] = Depends(get_catalogs),
    settings: Settings = Depends(get_settings),
) -> None:
    found = find_site(catalogs, site_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"site {site_id!r} not found")
    _reach, site = found
    try:
        management.delete_site(settings.data_root, site)
    except ManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
