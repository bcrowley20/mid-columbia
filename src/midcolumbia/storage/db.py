"""SQLite schema, connection, and upsert helpers. See Implementation Plan.md
section 1 ("Storage layer") and section 6 (scanner's incremental rescan needs).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..models import CalculatedReading, DeploymentEvent, ParameterType, Reading

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    well_id TEXT NOT NULL,
    parameter TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    PRIMARY KEY (well_id, parameter, timestamp_utc)
);

CREATE TABLE IF NOT EXISTS deployment_events (
    well_id TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_file TEXT NOT NULL,
    PRIMARY KEY (well_id, timestamp_utc, kind)
);

-- Tracks which source files have already been ingested, keyed by their path
-- relative to data_root, so the scanner can skip unchanged files on a rescan.
CREATE TABLE IF NOT EXISTS ingested_files (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL
);

-- Derived values (e.g. water depth) computed by the calculations module.
-- value is NULL when status is not "ok" - unknown is a first-class result,
-- not an absent row (Implementation Plan.md section 10).
CREATE TABLE IF NOT EXISTS calculated_readings (
    well_id TEXT NOT NULL,
    calculation TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    value REAL,
    unit TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (well_id, calculation, timestamp_utc)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def is_file_unchanged(conn: sqlite3.Connection, relative_path: str, mtime: float, size: int) -> bool:
    row = conn.execute(
        "SELECT mtime, size FROM ingested_files WHERE path = ?", (relative_path,)
    ).fetchone()
    return row is not None and row[0] == mtime and row[1] == size


def record_file_state(conn: sqlite3.Connection, relative_path: str, mtime: float, size: int) -> None:
    conn.execute(
        "INSERT INTO ingested_files (path, mtime, size) VALUES (?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size",
        (relative_path, mtime, size),
    )


def upsert_readings(conn: sqlite3.Connection, readings: list[Reading]) -> None:
    conn.executemany(
        "INSERT INTO readings (well_id, parameter, timestamp_utc, value, unit, source_file, source_row) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(well_id, parameter, timestamp_utc) DO UPDATE SET "
        "value = excluded.value, unit = excluded.unit, "
        "source_file = excluded.source_file, source_row = excluded.source_row",
        [
            (r.well_id, r.parameter.value, r.timestamp_utc.isoformat(), r.value, r.unit, r.source_file, r.source_row)
            for r in readings
        ],
    )


def upsert_deployment_events(conn: sqlite3.Connection, events: list[DeploymentEvent]) -> None:
    conn.executemany(
        "INSERT INTO deployment_events (well_id, timestamp_utc, kind, source_file) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(well_id, timestamp_utc, kind) DO UPDATE SET source_file = excluded.source_file",
        [(e.well_id, e.timestamp_utc.isoformat(), e.kind, e.source_file) for e in events],
    )


def count_readings(conn: sqlite3.Connection, well_id: str | None = None) -> int:
    if well_id is None:
        return conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM readings WHERE well_id = ?", (well_id,)).fetchone()[0]


def count_deployment_events(conn: sqlite3.Connection, well_id: str | None = None) -> int:
    if well_id is None:
        return conn.execute("SELECT COUNT(*) FROM deployment_events").fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM deployment_events WHERE well_id = ?", (well_id,)).fetchone()[0]


def upsert_calculated_readings(conn: sqlite3.Connection, results: list[CalculatedReading]) -> None:
    conn.executemany(
        "INSERT INTO calculated_readings (well_id, calculation, timestamp_utc, value, unit, status) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(well_id, calculation, timestamp_utc) DO UPDATE SET "
        "value = excluded.value, unit = excluded.unit, status = excluded.status",
        [(r.well_id, r.calculation, r.timestamp_utc.isoformat(), r.value, r.unit, r.status) for r in results],
    )


def count_calculated_readings(conn: sqlite3.Connection, well_id: str | None = None) -> int:
    if well_id is None:
        return conn.execute("SELECT COUNT(*) FROM calculated_readings").fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM calculated_readings WHERE well_id = ?", (well_id,)).fetchone()[0]


def fetch_calculated_readings(conn: sqlite3.Connection, well_id: str, calculation: str) -> list[CalculatedReading]:
    rows = conn.execute(
        "SELECT well_id, timestamp_utc, calculation, value, unit, status FROM calculated_readings "
        "WHERE well_id = ? AND calculation = ? ORDER BY timestamp_utc",
        (well_id, calculation),
    ).fetchall()
    return [
        CalculatedReading(
            well_id=row[0],
            timestamp_utc=datetime.fromisoformat(row[1]).replace(tzinfo=timezone.utc),
            calculation=row[2],
            value=row[3],
            unit=row[4],
            status=row[5],
        )
        for row in rows
    ]


def count_distinct_timestamps(conn: sqlite3.Connection, well_id: str) -> int:
    """"Number of data points" for a well, per the Project Description's hover
    popup spec (section 11/12) - distinct sample times, not raw reading rows
    (a single hourly sample yields two rows: pressure and temperature)."""
    return conn.execute(
        "SELECT COUNT(DISTINCT timestamp_utc) FROM readings WHERE well_id = ?", (well_id,)
    ).fetchone()[0]


def latest_reading_timestamp(conn: sqlite3.Connection, well_id: str) -> datetime | None:
    row = conn.execute("SELECT MAX(timestamp_utc) FROM readings WHERE well_id = ?", (well_id,)).fetchone()
    if row is None or row[0] is None:
        return None
    return datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)


def fetch_readings(conn: sqlite3.Connection, well_id: str, parameter: ParameterType) -> list[Reading]:
    rows = conn.execute(
        "SELECT well_id, parameter, timestamp_utc, value, unit, source_file, source_row FROM readings "
        "WHERE well_id = ? AND parameter = ? ORDER BY timestamp_utc",
        (well_id, parameter.value),
    ).fetchall()
    return [
        Reading(
            well_id=row[0],
            parameter=ParameterType(row[1]),
            timestamp_utc=datetime.fromisoformat(row[2]).replace(tzinfo=timezone.utc),
            value=row[3],
            unit=row[4],
            source_file=row[5],
            source_row=row[6],
        )
        for row in rows
    ]
