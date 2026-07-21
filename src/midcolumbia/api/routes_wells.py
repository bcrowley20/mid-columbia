"""Well metadata and stats endpoints. See Implementation Plan.md section 11."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..catalog import Catalog, find_well
from ..storage import db
from .deps import get_catalogs, get_db
from .schemas import WellOut, WellSummaryOut

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
