from datetime import datetime, timedelta, timezone
from pathlib import Path

from midcolumbia.calculations.water_depth import KPA_TO_FEET, WaterDepthCalculation
from midcolumbia.config import CalculationSettings
from midcolumbia.models import ParameterType, Reading, Well, WellType
from midcolumbia.storage import db

WELL_ID = "w1"
ATM_WELL_ID = "atm1"
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
SETTINGS = CalculationSettings(max_atm_gap_hours=12)


def _well(paired_atm_well_id: str | None = ATM_WELL_ID) -> Well:
    return Well(
        id=WELL_ID,
        site_id="site-1",
        reach_id=None,
        name="GW 1",
        well_type=WellType.GROUNDWATER,
        folder_path="irrelevant",
        device_serial=None,
        paired_atm_well_id=paired_atm_well_id,
    )


def _pressure_reading(well_id: str, timestamp: datetime, value: float) -> Reading:
    return Reading(
        well_id=well_id,
        parameter=ParameterType.WATER_PRESSURE if well_id == WELL_ID else ParameterType.AIR_PRESSURE,
        timestamp_utc=timestamp,
        value=value,
        unit="kPa",
        source_file="f",
        source_row=1,
    )


def test_formula_matches_the_agreed_constant(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    db.upsert_readings(conn, [_pressure_reading(WELL_ID, T0, 105.0)])
    db.upsert_readings(conn, [_pressure_reading(ATM_WELL_ID, T0, 100.0)])

    results = WaterDepthCalculation().compute(_well(), catalog=None, conn=conn, settings=SETTINGS)

    assert len(results) == 1
    assert results[0].status == "ok"
    assert results[0].unit == "ft"
    assert results[0].value == (105.0 - 100.0) * KPA_TO_FEET
    assert results[0].well_id == WELL_ID
    assert results[0].calculation == "water_depth"


def test_no_atm_data_marks_unknown(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    db.upsert_readings(conn, [_pressure_reading(WELL_ID, T0, 105.0)])
    # no ATM readings ingested at all

    results = WaterDepthCalculation().compute(_well(), catalog=None, conn=conn, settings=SETTINGS)

    assert len(results) == 1
    assert results[0].status == "unknown_no_atm_data"
    assert results[0].value is None


def test_well_with_no_paired_atm_well_marks_unknown(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    db.upsert_readings(conn, [_pressure_reading(WELL_ID, T0, 105.0)])
    db.upsert_readings(conn, [_pressure_reading(ATM_WELL_ID, T0, 100.0)])

    results = WaterDepthCalculation().compute(_well(paired_atm_well_id=None), catalog=None, conn=conn, settings=SETTINGS)

    assert results[0].status == "unknown_no_atm_data"


def test_no_water_readings_returns_empty(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    db.upsert_readings(conn, [_pressure_reading(ATM_WELL_ID, T0, 100.0)])

    results = WaterDepthCalculation().compute(_well(), catalog=None, conn=conn, settings=SETTINGS)
    assert results == []


def test_atm_reading_within_max_gap_is_used(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    db.upsert_readings(conn, [_pressure_reading(WELL_ID, T0, 105.0)])
    atm_time = T0 - timedelta(hours=12)  # exactly at the boundary
    db.upsert_readings(conn, [_pressure_reading(ATM_WELL_ID, atm_time, 100.0)])

    results = WaterDepthCalculation().compute(_well(), catalog=None, conn=conn, settings=SETTINGS)
    assert results[0].status == "ok"
    assert results[0].value == (105.0 - 100.0) * KPA_TO_FEET


def test_atm_reading_beyond_max_gap_marks_unknown(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    db.upsert_readings(conn, [_pressure_reading(WELL_ID, T0, 105.0)])
    atm_time = T0 - timedelta(hours=12, minutes=1)  # one minute past the boundary
    db.upsert_readings(conn, [_pressure_reading(ATM_WELL_ID, atm_time, 100.0)])

    results = WaterDepthCalculation().compute(_well(), catalog=None, conn=conn, settings=SETTINGS)
    assert results[0].status == "unknown_atm_gap_too_large"
    assert results[0].value is None


def test_picks_closest_of_multiple_atm_readings(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    db.upsert_readings(conn, [_pressure_reading(WELL_ID, T0, 105.0)])
    db.upsert_readings(
        conn,
        [
            _pressure_reading(ATM_WELL_ID, T0 - timedelta(hours=5), 90.0),
            _pressure_reading(ATM_WELL_ID, T0 - timedelta(hours=1), 100.0),  # closest
            _pressure_reading(ATM_WELL_ID, T0 + timedelta(hours=3), 110.0),
        ],
    )

    results = WaterDepthCalculation().compute(_well(), catalog=None, conn=conn, settings=SETTINGS)
    assert results[0].value == (105.0 - 100.0) * KPA_TO_FEET


def test_calculated_readings_are_stored_and_fetchable(tmp_path: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    db.upsert_readings(conn, [_pressure_reading(WELL_ID, T0, 105.0)])
    db.upsert_readings(conn, [_pressure_reading(ATM_WELL_ID, T0, 100.0)])

    results = WaterDepthCalculation().compute(_well(), catalog=None, conn=conn, settings=SETTINGS)
    db.upsert_calculated_readings(conn, results)

    fetched = db.fetch_calculated_readings(conn, WELL_ID, "water_depth")
    assert fetched == results
