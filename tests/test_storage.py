from datetime import datetime, timezone
from pathlib import Path

from midcolumbia.models import DeploymentEvent, ParameterType, Reading
from midcolumbia.storage import db


def _reading(value=1.0, timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc), well_id="w1", source_row=1) -> Reading:
    return Reading(
        well_id=well_id,
        parameter=ParameterType.WATER_PRESSURE,
        timestamp_utc=timestamp,
        value=value,
        unit="kPa",
        source_file="f.csv",
        source_row=source_row,
    )


def test_upsert_readings_then_fetch_roundtrips(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    reading = _reading()
    db.upsert_readings(conn, [reading])

    fetched = db.fetch_readings(conn, "w1", ParameterType.WATER_PRESSURE)
    assert fetched == [reading]


def test_upsert_readings_is_idempotent(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    reading = _reading()
    db.upsert_readings(conn, [reading])
    db.upsert_readings(conn, [reading])

    assert db.count_readings(conn) == 1


def test_upsert_readings_overwrites_value_on_conflict(tmp_path: Path):
    # Simulates a re-downloaded file correcting/replacing a previously ingested
    # value for the same (well, parameter, timestamp).
    conn = db.connect(tmp_path / "test.sqlite3")
    db.upsert_readings(conn, [_reading(value=1.0)])
    db.upsert_readings(conn, [_reading(value=2.0)])

    fetched = db.fetch_readings(conn, "w1", ParameterType.WATER_PRESSURE)
    assert len(fetched) == 1
    assert fetched[0].value == 2.0


def test_different_wells_and_parameters_dont_collide(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    same_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.upsert_readings(
        conn,
        [
            _reading(value=1.0, timestamp=same_timestamp, well_id="w1"),
            _reading(value=2.0, timestamp=same_timestamp, well_id="w2"),
        ],
    )
    assert db.count_readings(conn) == 2


def test_deployment_events_upsert_and_dedup(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    event = DeploymentEvent(
        well_id="w1", timestamp_utc=datetime(2026, 1, 1, tzinfo=timezone.utc), kind="logger_launched", source_file="f.csv"
    )
    db.upsert_deployment_events(conn, [event])
    db.upsert_deployment_events(conn, [event])
    assert db.count_deployment_events(conn) == 1


def test_file_state_tracking_detects_changes(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    assert db.is_file_unchanged(conn, "some/file.csv", mtime=100.0, size=10) is False

    db.record_file_state(conn, "some/file.csv", mtime=100.0, size=10)
    assert db.is_file_unchanged(conn, "some/file.csv", mtime=100.0, size=10) is True
    assert db.is_file_unchanged(conn, "some/file.csv", mtime=200.0, size=10) is False
    assert db.is_file_unchanged(conn, "some/file.csv", mtime=100.0, size=999) is False
