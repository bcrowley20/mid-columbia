from pathlib import Path

from midcolumbia.ingestion.scanner import scan_all
from midcolumbia.models import ParameterType
from midcolumbia.storage import db

ALL_HANDLERS = ("hoboware_csv", "hoboconnect_xlsx")


def test_scan_all_against_real_carlson_data_has_no_errors(tmp_path: Path, data_root: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    result = scan_all(data_root, conn, ALL_HANDLERS)

    assert result.errors == []
    # 6 groundwater folders (incl. Site 3's two) * 3 downloads + 1 ATM * 3 + 5 in-stream * 3
    assert result.files_scanned == 36
    assert result.files_ingested == 36
    assert result.readings_ingested > 0
    assert result.events_ingested > 0
    # Stored count can be lower than the parsed count: XLSX downloads are
    # cumulative re-dumps (section 2), so upsert collapses repeated readings.
    assert 0 < db.count_readings(conn) <= result.readings_ingested


def test_rescan_is_idempotent(tmp_path: Path, data_root: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    first = scan_all(data_root, conn, ALL_HANDLERS)
    reading_count_after_first = db.count_readings(conn)
    event_count_after_first = db.count_deployment_events(conn)

    second = scan_all(data_root, conn, ALL_HANDLERS)

    assert second.files_ingested == 0  # every file already recorded as unchanged
    assert second.readings_ingested == 0
    assert db.count_readings(conn) == reading_count_after_first
    assert db.count_deployment_events(conn) == event_count_after_first
    assert first.errors == second.errors == []


def test_hobo_files_are_silently_skipped(tmp_path: Path, data_root: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    result = scan_all(data_root, conn, ALL_HANDLERS)
    # 36 recognized files is strictly fewer than every file on disk under the
    # wells (the .hobo project files are present too but never dispatched).
    all_files_under_wells = sum(
        1
        for p in (data_root / "Carlson Creek Restoration").rglob("*")
        if p.is_file() and p.suffix.lower() != ".json5"
    )
    assert all_files_under_wells > result.files_scanned
    assert result.errors == []


def test_disabling_a_handler_excludes_its_readings(tmp_path: Path, data_root: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    scan_all(data_root, conn, ("hoboware_csv",))  # XLSX handler disabled

    is1_readings = db.fetch_readings(
        conn, "carlson-creek-restoration/lower-stream/site-1/is-1", ParameterType.WATER_PRESSURE
    )
    gw1_readings = db.fetch_readings(
        conn, "carlson-creek-restoration/lower-stream/site-1/gw-1", ParameterType.WATER_PRESSURE
    )
    assert is1_readings == []  # in-stream wells are XLSX-only
    assert gw1_readings != []


def test_incremental_scan_only_reparses_new_or_changed_files(tmp_path: Path):
    data_root = tmp_path / "data"
    project_dir = data_root / "Proj"
    reach_dir = project_dir / "Reach"
    atm_dir = reach_dir / "ATM"
    site_dir = reach_dir / "Site 1"
    well_dir = site_dir / "GW 1"
    well_dir.mkdir(parents=True)
    atm_dir.mkdir(parents=True)

    (project_dir / "project.json5").write_text(
        '{name: "Proj", timezone: "UTC", reaches: [{name: "Reach", folder: "Reach", '
        'atm_well: {name: "ATM", folder: "ATM", device_serial: "1"}}]}',
        encoding="utf-8",
    )
    (site_dir / "site.json5").write_text(
        '{name: "Site 1", latitude: null, longitude: null, wells: [{name: "GW 1", '
        'folder: "GW 1", type: "groundwater", device_serial: "2", paired_atm_well: null}]}',
        encoding="utf-8",
    )
    csv_content = (
        '"Plot Title: test"\r\n'
        '"#","Date Time, GMT-08:00","Abs Pres, kPa (LGR S/N: 1)","Temp, °C (LGR S/N: 1)"\r\n'
        '1,01/01/26 12:00:00 PM,100.0,5.0\r\n'
    )
    data_file = well_dir / "download1.csv"
    data_file.write_text(csv_content, encoding="utf-8-sig")

    conn = db.connect(tmp_path / "test.sqlite3")
    first = scan_all(data_root, conn, ALL_HANDLERS)
    assert first.files_ingested == 1
    assert db.count_readings(conn) == 2  # pressure + temp

    second = scan_all(data_root, conn, ALL_HANDLERS)
    assert second.files_ingested == 0

    # Append a new reading and re-scan - only the changed file should reparse.
    data_file.write_text(csv_content + "2,01/01/26 01:00:00 PM,101.0,5.5\r\n", encoding="utf-8-sig")
    third = scan_all(data_root, conn, ALL_HANDLERS)
    assert third.files_ingested == 1
    assert db.count_readings(conn) == 4
