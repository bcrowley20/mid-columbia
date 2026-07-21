from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from midcolumbia.api.app import app
from midcolumbia.api.deps import get_settings
from midcolumbia.calculations.runner import compute_all
from midcolumbia.config import CalculationSettings, DisplaySettings, Settings
from midcolumbia.ingestion.scanner import scan_all
from midcolumbia.storage import db

ALL_HANDLERS = ("hoboware_csv", "hoboconnect_xlsx")

SITE1_ID = "carlson-creek-restoration/lower-stream/site-1"
GW1_ID = "carlson-creek-restoration/lower-stream/site-1/gw-1"
ATM_ID = "carlson-creek-restoration/lower-stream/carlson-atm"


def _test_settings(data_root: Path, database_path: Path) -> Settings:
    return Settings(
        data_root=data_root,
        database_path=database_path,
        enabled_device_handlers=ALL_HANDLERS,
        display=DisplaySettings(pressure_unit="kPa", temperature_unit="degC", depth_unit="ft", timezone="America/Los_Angeles"),
        calculations=CalculationSettings(max_atm_gap_hours=12),
    )


@pytest.fixture(autouse=True)
def _reset_app_state():
    # app/app.state are module-level singletons shared across the whole test
    # session - without this, one test's dependency override or ingest run
    # would leak into the next.
    yield
    app.dependency_overrides.clear()
    app.state.last_ingest_result = None


@pytest.fixture
def populated_client(tmp_path: Path, data_root: Path) -> TestClient:
    db_path = tmp_path / "test.sqlite3"
    conn = db.connect(db_path)
    scan_all(data_root, conn, ALL_HANDLERS)
    compute_all(data_root, conn, CalculationSettings(max_atm_gap_hours=12))
    conn.close()

    app.dependency_overrides[get_settings] = lambda: _test_settings(data_root, db_path)
    return TestClient(app)


@pytest.fixture
def empty_client(tmp_path: Path, data_root: Path) -> TestClient:
    db_path = tmp_path / "test.sqlite3"
    db.connect(db_path).close()  # schema only, nothing ingested yet

    app.dependency_overrides[get_settings] = lambda: _test_settings(data_root, db_path)
    return TestClient(app)


def test_health(populated_client: TestClient):
    r = populated_client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_list_projects(populated_client: TestClient):
    r = populated_client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1

    project = data[0]
    assert project["name"] == "Carlson Creek Restoration"
    reach = project["reaches"][0]
    assert reach["name"] == "Lower Stream"
    assert len(reach["sites"]) == 5

    site3 = next(s for s in reach["sites"] if s["name"] == "Site 3")
    assert {w["name"] for w in site3["wells"]} == {"GW 3a", "GW 3b", "IS 3"}


def test_site_summary_matches_known_well_counts(populated_client: TestClient):
    # point_count/last_reading_at values here are the same real numbers
    # verified directly against the data in earlier phases.
    r = populated_client.get("/api/sites/summary", params={"site_id": SITE1_ID})
    assert r.status_code == 200
    data = r.json()
    assert data["reach_name"] == "Lower Stream"
    assert data["site_name"] == "Site 1"

    wells_by_name = {w["well_name"]: w for w in data["wells"]}
    assert wells_by_name["GW 1"]["point_count"] == 1271
    assert wells_by_name["GW 1"]["last_reading_at"] == "2026-04-20T17:00:00Z"
    assert wells_by_name["IS 1"]["point_count"] == 1269


def test_site_summary_404_for_unknown_site(populated_client: TestClient):
    r = populated_client.get("/api/sites/summary", params={"site_id": "nonexistent"})
    assert r.status_code == 404


def test_get_well_metadata(populated_client: TestClient):
    r = populated_client.get("/api/wells", params={"well_id": GW1_ID})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "GW 1"
    assert data["well_type"] == "groundwater"
    assert data["device_serial"] == "22332695"
    assert data["paired_atm_well_id"] == ATM_ID


def test_get_well_metadata_404_for_unknown_well(populated_client: TestClient):
    r = populated_client.get("/api/wells", params={"well_id": "nonexistent"})
    assert r.status_code == 404


def test_well_readings_raw_parameter(populated_client: TestClient):
    r = populated_client.get("/api/wells/readings", params={"well_id": GW1_ID, "parameter": "water_pressure"})
    assert r.status_code == 200
    data = r.json()
    assert len(data["points"]) == 1271

    first = data["points"][0]
    assert first["value"] == 100.126
    assert first["unit"] == "kPa"
    assert first["status"] is None

    timestamps = [p["timestamp_utc"] for p in data["points"]]
    assert timestamps == sorted(timestamps)


def test_well_readings_calculated_parameter(populated_client: TestClient):
    r = populated_client.get("/api/wells/readings", params={"well_id": GW1_ID, "parameter": "water_depth"})
    assert r.status_code == 200
    data = r.json()
    assert len(data["points"]) == 1271
    assert all(p["status"] == "ok" for p in data["points"])
    assert all(p["value"] is not None for p in data["points"])


def test_well_readings_unknown_parameter_is_400(populated_client: TestClient):
    r = populated_client.get("/api/wells/readings", params={"well_id": GW1_ID, "parameter": "bogus"})
    assert r.status_code == 400


def test_well_readings_unknown_well_is_404(populated_client: TestClient):
    r = populated_client.get("/api/wells/readings", params={"well_id": "nonexistent", "parameter": "water_pressure"})
    assert r.status_code == 404


def test_well_readings_date_range_filtering(populated_client: TestClient):
    r = populated_client.get(
        "/api/wells/readings",
        params={
            "well_id": GW1_ID,
            "parameter": "water_pressure",
            "from": "2026-03-01T00:00:00Z",
            "to": "2026-03-02T00:00:00Z",
        },
    )
    assert r.status_code == 200
    points = r.json()["points"]
    assert 0 < len(points) < 1271
    assert all("2026-03-01" <= p["timestamp_utc"][:10] <= "2026-03-02" for p in points)


def test_well_readings_naive_from_is_treated_as_utc(populated_client: TestClient):
    r = populated_client.get(
        "/api/wells/readings",
        params={"well_id": GW1_ID, "parameter": "water_pressure", "from": "2026-04-20T00:00:00"},
    )
    assert r.status_code == 200
    assert len(r.json()["points"]) > 0


def test_ingest_status_before_any_run(empty_client: TestClient):
    r = empty_client.get("/api/ingest/status")
    assert r.status_code == 200
    assert r.json() == {"has_run": False, "result": None}


def test_ingest_run_then_status(empty_client: TestClient):
    r = empty_client.post("/api/ingest/run")
    assert r.status_code == 200
    result = r.json()
    assert result["files_ingested"] == 36
    assert result["readings_ingested"] > 0
    assert result["wells_processed"] == 11
    assert result["errors"] == []

    r = empty_client.get("/api/ingest/status")
    assert r.status_code == 200
    status = r.json()
    assert status["has_run"] is True
    assert status["result"]["files_ingested"] == 36


def test_ingest_run_is_idempotent_on_rerun(empty_client: TestClient):
    empty_client.post("/api/ingest/run")
    r = empty_client.post("/api/ingest/run")
    assert r.json()["files_ingested"] == 0
