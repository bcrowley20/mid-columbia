"""Project/Reach/Site tree and site-summary endpoints. See Implementation
Plan.md section 11."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..catalog import Catalog, find_site
from ..storage import db
from .deps import get_catalogs, get_db
from .schemas import ProjectOut, SiteSummaryOut, WellSummaryOut

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
