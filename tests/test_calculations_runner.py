from pathlib import Path

from midcolumbia.calculations.runner import compute_all
from midcolumbia.config import CalculationSettings
from midcolumbia.ingestion.scanner import scan_all
from midcolumbia.storage import db

ALL_HANDLERS = ("hoboware_csv", "hoboconnect_xlsx")
SETTINGS = CalculationSettings(max_atm_gap_hours=12)


def test_compute_all_against_real_carlson_data(tmp_path: Path, data_root: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    scan_all(data_root, conn, ALL_HANDLERS)

    result = compute_all(data_root, conn, SETTINGS)

    # 5 sites * 2 wells + Site 3's extra GW well = 11 non-ATM wells
    assert result.wells_processed == 11
    assert result.results_ok > 0
    # The real ATM well fully covers the same date range as every water well,
    # hourly, so nothing should be unpaired at a 12-hour tolerance.
    assert result.results_unknown == 0
    assert db.count_calculated_readings(conn) == result.results_ok + result.results_unknown


def test_atm_well_itself_has_no_calculated_readings(tmp_path: Path, data_root: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    scan_all(data_root, conn, ALL_HANDLERS)
    compute_all(data_root, conn, SETTINGS)

    atm_well_id = "carlson-creek-restoration/lower-stream/carlson-atm"
    assert db.count_calculated_readings(conn, atm_well_id) == 0


def test_recompute_is_idempotent(tmp_path: Path, data_root: Path):
    conn = db.connect(tmp_path / "test.sqlite3")
    scan_all(data_root, conn, ALL_HANDLERS)

    first = compute_all(data_root, conn, SETTINGS)
    count_after_first = db.count_calculated_readings(conn)

    second = compute_all(data_root, conn, SETTINGS)

    assert first.results_ok == second.results_ok
    assert db.count_calculated_readings(conn) == count_after_first


def test_gw1_depth_is_a_small_plausible_value(tmp_path: Path, data_root: Path):
    # Sanity check against the real data rather than a hardcoded expectation -
    # a shallow groundwater well should read a modest number of feet, not
    # something wildly large that would indicate a unit/formula mistake.
    conn = db.connect(tmp_path / "test.sqlite3")
    scan_all(data_root, conn, ALL_HANDLERS)
    compute_all(data_root, conn, SETTINGS)

    depths = db.fetch_calculated_readings(conn, "carlson-creek-restoration/lower-stream/site-1/gw-1", "water_depth")
    assert depths
    assert all(d.status == "ok" for d in depths)
    assert all(-5.0 < d.value < 20.0 for d in depths)
