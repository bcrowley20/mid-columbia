from datetime import datetime, timezone
from pathlib import Path

from midcolumbia.ingestion.hoboware_csv import HoboWareCsvHandler
from midcolumbia.models import ParameterType, WellType

WELL_ID = "carlson-creek-restoration/lower-stream/site-1/gw-1"
TZ = "America/Los_Angeles"  # unused by this handler, but part of the interface


def _gw1_file(data_root: Path, name: str) -> Path:
    return data_root / "Carlson Creek Restoration/Lower Stream/Site 1/GW 1" / name


def test_first_download_has_launch_event_and_reading_on_same_row(data_root: Path):
    # Row 1 has both a "Coupler Detached" launch marker AND a valid reading -
    # the handler must emit both, not skip the reading (Implementation Plan.md
    # section 2/6 - a real bug caught while writing this test).
    path = _gw1_file(data_root, "2026-02-27, GW_Site_1,_ID_2,_22332695.csv")
    readings, events = HoboWareCsvHandler().parse(path, WELL_ID, WellType.GROUNDWATER, TZ)

    assert len(readings) == 60  # 30 rows with data * 2 parameters
    assert len(events) == 4  # launch, retrieved, stopped, end_of_file

    first_pressure = next(r for r in readings if r.parameter is ParameterType.WATER_PRESSURE and r.source_row == 1)
    assert first_pressure.value == 100.126
    assert first_pressure.unit == "kPa"
    assert first_pressure.timestamp_utc == datetime(2026, 2, 26, 19, 0, tzinfo=timezone.utc)

    first_temp = next(r for r in readings if r.parameter is ParameterType.WATER_TEMPERATURE and r.source_row == 1)
    assert first_temp.value == 4.623
    assert first_temp.unit == "degC"

    event_kinds = {e.kind for e in events}
    assert event_kinds == {"logger_launched", "logger_retrieved", "stopped", "end_of_file"}

    launch = next(e for e in events if e.kind == "logger_launched")
    assert launch.timestamp_utc == datetime(2026, 2, 26, 19, 0, tzinfo=timezone.utc)
    assert launch.well_id == WELL_ID


def test_download_without_marker_columns_still_parses(data_root: Path):
    # The third download for GW 1 only has #, Date Time, Abs Pres, Temp columns
    # - no marker columns at all (Implementation Plan.md section 2).
    path = _gw1_file(data_root, "2026-04-20, Site_1,_ID_2,_22332695.csv")
    readings, events = HoboWareCsvHandler().parse(path, WELL_ID, WellType.GROUNDWATER, TZ)

    assert events == []
    assert len(readings) > 0
    assert all(r.parameter in (ParameterType.WATER_PRESSURE, ParameterType.WATER_TEMPERATURE) for r in readings)


def test_atmospheric_well_type_maps_to_air_parameters(data_root: Path):
    atm_id = "carlson-creek-restoration/lower-stream/carlson-atm"
    path = data_root / "Carlson Creek Restoration/Lower Stream/Carlson ATM/2026-02-27, GW_Site_ATM,_ID_1,_22332694.csv"
    readings, events = HoboWareCsvHandler().parse(path, atm_id, WellType.ATMOSPHERIC, TZ)

    assert readings
    assert {r.parameter for r in readings} == {ParameterType.AIR_PRESSURE, ParameterType.AIR_TEMPERATURE}
    assert all(r.well_id == atm_id for r in readings)


def test_readings_are_contiguous_and_non_overlapping_across_downloads(data_root: Path):
    # Verifies the section 2 finding that CSV downloads pick up where the
    # previous one left off, rather than re-including earlier readings.
    handler = HoboWareCsvHandler()
    file1_readings, _ = handler.parse(
        _gw1_file(data_root, "2026-02-27, GW_Site_1,_ID_2,_22332695.csv"), WELL_ID, WellType.GROUNDWATER, TZ
    )
    file2_readings, _ = handler.parse(
        _gw1_file(data_root, "2026-03-11, Site_1,_ID_2,_22332695_0.csv"), WELL_ID, WellType.GROUNDWATER, TZ
    )
    timestamps_1 = {r.timestamp_utc for r in file1_readings}
    timestamps_2 = {r.timestamp_utc for r in file2_readings}
    assert timestamps_1.isdisjoint(timestamps_2)


def test_can_handle_only_csv(data_root: Path):
    handler = HoboWareCsvHandler()
    assert handler.can_handle(Path("foo.csv")) is True
    assert handler.can_handle(Path("foo.CSV")) is True
    assert handler.can_handle(Path("foo.xlsx")) is False
    assert handler.can_handle(Path("foo.hobo")) is False
