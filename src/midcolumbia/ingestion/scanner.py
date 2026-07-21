"""Walks data/ for new/changed logger files and ingests them into SQLite.
See Implementation Plan.md section 6.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..catalog import discover_project_folders, load_catalog
from ..storage import db
from .base import LoggerHandler, ParseError
from .hoboconnect_xlsx import HoboConnectXlsxHandler
from .hoboware_csv import HoboWareCsvHandler

DEFAULT_HANDLERS: tuple[LoggerHandler, ...] = (HoboWareCsvHandler(), HoboConnectXlsxHandler())


@dataclass
class ScanResult:
    files_scanned: int = 0
    files_ingested: int = 0
    readings_ingested: int = 0
    events_ingested: int = 0
    errors: list[str] = field(default_factory=list)


def scan_all(
    data_root: Path,
    conn: sqlite3.Connection,
    enabled_handlers: tuple[str, ...],
    handlers: tuple[LoggerHandler, ...] = DEFAULT_HANDLERS,
) -> ScanResult:
    active_handlers = [h for h in handlers if h.name in enabled_handlers]
    result = ScanResult()

    for project_folder in discover_project_folders(data_root):
        catalog = load_catalog(data_root, project_folder)
        for well in catalog.wells.values():
            well_dir = data_root / well.folder_path
            for file_path in sorted(well_dir.iterdir()):
                if not file_path.is_file():
                    continue

                handler = _find_handler(active_handlers, file_path)
                if handler is None:
                    continue  # e.g. .hobo files, or a disabled handler's format

                result.files_scanned += 1
                relative_path = str(file_path.relative_to(data_root))
                stat = file_path.stat()
                if db.is_file_unchanged(conn, relative_path, stat.st_mtime, stat.st_size):
                    continue

                try:
                    readings, events = handler.parse(file_path, well.id, well.well_type, catalog.timezone)
                except ParseError as exc:
                    # One bad file shouldn't abort the whole scan, but the error
                    # must surface, not be swallowed - and the file is left
                    # unrecorded so it's retried on the next scan.
                    result.errors.append(f"{relative_path}: {exc}")
                    continue

                db.upsert_readings(conn, readings)
                db.upsert_deployment_events(conn, events)
                db.record_file_state(conn, relative_path, stat.st_mtime, stat.st_size)
                conn.commit()

                result.files_ingested += 1
                result.readings_ingested += len(readings)
                result.events_ingested += len(events)

    return result


def _find_handler(handlers: list[LoggerHandler], path: Path) -> LoggerHandler | None:
    for handler in handlers:
        if handler.can_handle(path):
            return handler
    return None
