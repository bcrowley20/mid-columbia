from datetime import datetime, timezone
from pathlib import Path

from midcolumbia.ingestion.hoboconnect_xlsx import HoboConnectXlsxHandler
from midcolumbia.models import ParameterType, WellType

WELL_ID = "carlson-creek-restoration/lower-stream/site-1/is-1"
TZ = "America/Los_Angeles"


def _is1_file(data_root: Path, name: str) -> Path:
    return data_root / "Carlson Creek Restoration/Lower Stream/Site 1/IS 1" / name


def test_data_and_events_sheets_parse_correctly(data_root: Path):
    # Verifies the section 2/6 correction: events live on a separate "Events"
    # sheet, not inline marker columns on "Data" as originally assumed.
    path = _is1_file(data_root, "Site1, #8, 22449416 2026-02-27 16_58_14 PST (Data PST).xlsx")
    readings, events = HoboConnectXlsxHandler().parse(path, WELL_ID, WellType.IN_STREAM, TZ)

    assert len(readings) == 60  # 30 data rows * 2 parameters
    assert len(events) == 7

    first_pressure = next(r for r in readings if r.parameter is ParameterType.WATER_PRESSURE and r.source_row == 1)
    assert first_pressure.value == 95.87652321570722
    assert first_pressure.unit == "kPa"
    # Local deployment start 2026-02-26 11:00 PST -> UTC
    assert first_pressure.timestamp_utc == datetime(2026, 2, 26, 19, 0, tzinfo=timezone.utc)

    event_kinds = [e.kind for e in events]
    assert event_kinds.count("button_up") == 2
    assert event_kinds.count("button_down") == 2
    assert event_kinds.count("logger_launched") == 1
    assert event_kinds.count("logger_retrieved") == 1
    assert event_kinds.count("end_of_file") == 1
    assert all(e.well_id == WELL_ID for e in events)


def test_vendor_atm_and_depth_columns_are_ignored(data_root: Path):
    path = _is1_file(data_root, "Site1, #8, 22449416 2026-02-27 16_58_14 PST (Data PST).xlsx")
    readings, _ = HoboConnectXlsxHandler().parse(path, WELL_ID, WellType.IN_STREAM, TZ)
    assert {r.parameter for r in readings} == {ParameterType.WATER_PRESSURE, ParameterType.WATER_TEMPERATURE}


def test_downloads_are_cumulative_not_incremental(data_root: Path):
    # Verifies the section 2 finding: unlike CSV, each XLSX download re-dumps
    # the full deployment history from the start, so later downloads' reading
    # sets are supersets (not disjoint, unlike the CSV handler's downloads).
    handler = HoboConnectXlsxHandler()
    file1, _ = handler.parse(
        _is1_file(data_root, "Site1, #8, 22449416 2026-02-27 16_58_14 PST (Data PST).xlsx"), WELL_ID, WellType.IN_STREAM, TZ
    )
    file2, _ = handler.parse(
        _is1_file(data_root, "Site1, #8, 22449416 2026-03-11 13_14_54 PDT (Data PDT).xlsx"), WELL_ID, WellType.IN_STREAM, TZ
    )
    timestamps_1 = {r.timestamp_utc for r in file1}
    timestamps_2 = {r.timestamp_utc for r in file2}
    assert timestamps_1.issubset(timestamps_2)
    assert timestamps_1 != timestamps_2


def test_dst_spring_forward_gap_converts_without_a_missing_or_duplicated_hour(data_root: Path):
    # The explicit test case called for in Implementation Plan.md section 13.
    handler = HoboConnectXlsxHandler()
    readings, _ = handler.parse(
        _is1_file(data_root, "Site1, #8, 22449416 2026-03-11 13_14_54 PDT (Data PDT).xlsx"), WELL_ID, WellType.IN_STREAM, TZ
    )
    utc_timestamps = sorted({r.timestamp_utc for r in readings if r.timestamp_utc.month == 3 and r.timestamp_utc.day in (8, 9)})
    # Continuous hourly UTC timestamps across the local DST transition, no gap.
    for earlier, later in zip(utc_timestamps, utc_timestamps[1:]):
        assert (later - earlier).total_seconds() == 3600
    assert len(utc_timestamps) == 48  # 2 full days, hourly


def test_can_handle_only_xlsx(data_root: Path):
    handler = HoboConnectXlsxHandler()
    assert handler.can_handle(Path("foo.xlsx")) is True
    assert handler.can_handle(Path("foo.csv")) is False
    assert handler.can_handle(Path("foo.hobo")) is False
