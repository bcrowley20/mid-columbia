"""Ingest trigger endpoint. See Implementation Plan.md section 11.

The last run's result is kept in-memory on app.state only - it resets on
server restart. Acceptable for v1: a fresh run is always one request away,
and ingestion is fast enough at this data scale to run synchronously within
the request rather than as a background job (see section 15).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from ..calculations.runner import compute_all
from ..config import Settings
from ..ingestion.scanner import scan_all
from .deps import get_db, get_settings
from .schemas import IngestRunOut, IngestStatusOut

router = APIRouter(tags=["ingest"])


@router.post("/ingest/run", response_model=IngestRunOut)
def run_ingest(
    request: Request,
    settings: Settings = Depends(get_settings),
    conn: sqlite3.Connection = Depends(get_db),
) -> IngestRunOut:
    scan_result = scan_all(settings.data_root, conn, settings.enabled_device_handlers)
    calc_result = compute_all(settings.data_root, conn, settings.calculations)

    result = IngestRunOut(
        ran_at=datetime.now(timezone.utc),
        files_scanned=scan_result.files_scanned,
        files_ingested=scan_result.files_ingested,
        readings_ingested=scan_result.readings_ingested,
        events_ingested=scan_result.events_ingested,
        errors=scan_result.errors,
        wells_processed=calc_result.wells_processed,
        calculations_ok=calc_result.results_ok,
        calculations_unknown=calc_result.results_unknown,
    )
    request.app.state.last_ingest_result = result
    return result


@router.get("/ingest/status", response_model=IngestStatusOut)
def get_ingest_status(request: Request) -> IngestStatusOut:
    result: IngestRunOut | None = request.app.state.last_ingest_result
    return IngestStatusOut(has_run=result is not None, result=result)
