"""Well time-series endpoint - raw readings or a calculation (e.g. water_depth)
behind one consistent shape. See Implementation Plan.md section 11.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from ..catalog import Catalog, find_well
from ..models import ParameterType
from ..storage import db
from .deps import get_catalogs, get_db
from .schemas import SeriesPointOut, WellReadingsOut

router = APIRouter(tags=["readings"])

# Calculation names that can be requested through the same `parameter` query
# param as raw ParameterType values (currently just water_depth, Phase 2).
_CALCULATED_PARAMETERS = {"water_depth"}


def _as_utc(value: datetime | None) -> datetime | None:
    # A "from"/"to" query value with no UTC offset is assumed to already mean
    # UTC, rather than raising on the (very likely) common case of a plain
    # date/time with no offset - stored timestamps are always UTC (section 5).
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@router.get("/wells/readings", response_model=WellReadingsOut)
def get_well_readings(
    well_id: str,
    parameter: str,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    catalogs: list[Catalog] = Depends(get_catalogs),
    conn: sqlite3.Connection = Depends(get_db),
) -> WellReadingsOut:
    if find_well(catalogs, well_id) is None:
        raise HTTPException(status_code=404, detail=f"well {well_id!r} not found")

    if parameter in _CALCULATED_PARAMETERS:
        calculated = db.fetch_calculated_readings(conn, well_id, parameter)
        points = [
            SeriesPointOut(timestamp_utc=r.timestamp_utc, value=r.value, unit=r.unit, status=r.status)
            for r in calculated
        ]
    else:
        try:
            param_enum = ParameterType(parameter)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown parameter {parameter!r}") from None
        readings = db.fetch_readings(conn, well_id, param_enum)
        points = [SeriesPointOut(timestamp_utc=r.timestamp_utc, value=r.value, unit=r.unit) for r in readings]

    from_utc, to_utc = _as_utc(from_), _as_utc(to)
    if from_utc is not None:
        points = [p for p in points if p.timestamp_utc >= from_utc]
    if to_utc is not None:
        points = [p for p in points if p.timestamp_utc <= to_utc]

    return WellReadingsOut(well_id=well_id, parameter=parameter, points=points)
