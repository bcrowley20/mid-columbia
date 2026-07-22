"""Ingest trigger endpoint. See Implementation Plan.md section 11.

The last run's result is kept in-memory on app.state only - it resets on
server restart. Acceptable for v1: a fresh run is always one request away,
and ingestion is fast enough at this data scale to run synchronously within
the request rather than as a background job (see section 15).
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile

from ..calculations.runner import compute_all
from ..catalog import Catalog, find_well_by_device_serial
from ..config import Settings
from ..ingestion.base import ParseError
from ..ingestion.scanner import DEFAULT_HANDLERS, find_handler, scan_all
from .deps import get_catalogs, get_db, get_settings
from .schemas import IngestRunOut, IngestStatusOut, IngestUploadOut, UploadFileResultOut

router = APIRouter(tags=["ingest"])


def run_ingest_and_compute(settings: Settings, conn: sqlite3.Connection) -> IngestRunOut:
    """Runs scan_all() + compute_all() and builds the summary both the HTTP
    endpoint and the app startup hook (app.py) report - factored out so
    "what happened automatically at boot" and "what happened when someone
    hit /ingest/run" are always represented the same way.
    """
    scan_result = scan_all(settings.data_root, conn, settings.enabled_device_handlers)
    calc_result = compute_all(settings.data_root, conn, settings.calculations)

    return IngestRunOut(
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


@router.post("/ingest/run", response_model=IngestRunOut)
def run_ingest(
    request: Request,
    settings: Settings = Depends(get_settings),
    conn: sqlite3.Connection = Depends(get_db),
) -> IngestRunOut:
    result = run_ingest_and_compute(settings, conn)
    request.app.state.last_ingest_result = result
    return result


@router.get("/ingest/status", response_model=IngestStatusOut)
def get_ingest_status(request: Request) -> IngestStatusOut:
    result: IngestRunOut | None = request.app.state.last_ingest_result
    return IngestStatusOut(has_run=result is not None, result=result)


@router.post("/ingest/upload", response_model=IngestUploadOut)
def upload_ingest_files(
    request: Request,
    files: list[UploadFile] = File(...),
    settings: Settings = Depends(get_settings),
    catalogs: list[Catalog] = Depends(get_catalogs),
    conn: sqlite3.Connection = Depends(get_db),
) -> IngestUploadOut:
    """Add Data importer: routes each uploaded raw logger file to the well
    whose configured device_serial matches the serial embedded in the file
    itself (see LoggerHandler.extract_device_serial), copies it into that
    well's folder, then re-runs the normal scan_all()+compute_all() pipeline
    so the new data is ingested in the same request - deliberately not a
    separate code path from a manually-dropped-in file, just a new way to
    find where a file belongs.
    """
    active_handlers = [h for h in DEFAULT_HANDLERS if h.name in settings.enabled_device_handlers]
    results: list[UploadFileResultOut] = []
    any_placed = False

    for upload in files:
        # Never trust a client-supplied path component - basename only, so a
        # crafted filename can't write outside the target well's folder.
        filename = Path(upload.filename or "unnamed file").name

        handler = find_handler(active_handlers, Path(filename))
        if handler is None:
            results.append(UploadFileResultOut(filename=filename, status="error", message="unsupported file type"))
            continue

        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix) as tmp:
            shutil.copyfileobj(upload.file, tmp)
            tmp.flush()
            tmp_path = Path(tmp.name)

            try:
                serial = handler.extract_device_serial(tmp_path)
            except ParseError as exc:
                results.append(
                    UploadFileResultOut(filename=filename, status="error", message=f"not a readable HOBO file: {exc}")
                )
                continue

            well = find_well_by_device_serial(catalogs, serial)
            if well is None:
                results.append(
                    UploadFileResultOut(
                        filename=filename, status="error", message=f"no well found with device serial {serial!r}"
                    )
                )
                continue

            # Overwriting a same-named file is safe: the scanner's mtime/size
            # change-detection (storage/db.py) treats a changed file as
            # needing re-parse, so this is idempotent on re-upload.
            shutil.copy(tmp_path, settings.data_root / well.folder_path / filename)

        results.append(UploadFileResultOut(filename=filename, status="ingested", well_id=well.id, well_name=well.name))
        any_placed = True

    ingest_result = None
    if any_placed:
        ingest_result = run_ingest_and_compute(settings, conn)
        request.app.state.last_ingest_result = ingest_result

    return IngestUploadOut(files=results, ingest=ingest_result)
