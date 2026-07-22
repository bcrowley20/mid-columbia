"""API-level tests for the Add Data importer (POST /api/ingest/upload). Uses
its own isolated tmp_path data root - never the real Carlson data/ tree,
since these tests write to disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from midcolumbia.api.app import app
from midcolumbia.api.deps import get_settings
from midcolumbia.config import CalculationSettings, DisplaySettings, Settings
from midcolumbia.models import ParameterType
from midcolumbia.storage import db

ALL_HANDLERS = ("hoboware_csv", "hoboconnect_xlsx")

VALID_CSV = (
    '"Plot Title: test"\r\n'
    '"#","Date Time, GMT-08:00","Abs Pres, kPa (LGR S/N: 42)","Temp, °C (LGR S/N: 42)"\r\n'
    "1,01/01/26 12:00:00 PM,100.0,5.0\r\n"
)


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
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def upload_root(tmp_path: Path) -> Path:
    # A minimal on-disk project/reach/site/well tree - same shape as
    # test_scanner.py's test_incremental_scan_only_reparses_new_or_changed_files
    # fixture - with GW 1 configured for device_serial "42", matching VALID_CSV.
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
        'folder: "GW 1", type: "groundwater", device_serial: "42", paired_atm_well: null}]}',
        encoding="utf-8",
    )
    return data_root


@pytest.fixture
def client(tmp_path: Path, upload_root: Path) -> TestClient:
    db_path = tmp_path / "test.sqlite3"
    db.connect(db_path).close()
    app.dependency_overrides[get_settings] = lambda: _test_settings(upload_root, db_path)
    return TestClient(app)


def test_upload_matching_serial_places_file_and_ingests(client: TestClient, upload_root: Path, tmp_path: Path):
    r = client.post(
        "/api/ingest/upload",
        files={"files": ("download1.csv", VALID_CSV.encode("utf-8-sig"), "text/csv")},
    )
    assert r.status_code == 200
    body = r.json()

    assert len(body["files"]) == 1
    result = body["files"][0]
    assert result["status"] == "ingested"
    assert result["well_name"] == "GW 1"
    assert result["message"] is None

    assert (upload_root / "Proj/Reach/Site 1/GW 1/download1.csv").is_file()

    assert body["ingest"] is not None
    assert body["ingest"]["files_ingested"] == 1

    conn = db.connect(tmp_path / "test.sqlite3")
    readings = db.fetch_readings(conn, "proj/reach/site-1/gw-1", ParameterType.WATER_PRESSURE)
    assert len(readings) == 1
    assert readings[0].value == 100.0


def test_upload_unmatched_serial_reports_error_and_touches_nothing(client: TestClient, upload_root: Path):
    unmatched_csv = VALID_CSV.replace("LGR S/N: 42", "LGR S/N: 999")
    r = client.post(
        "/api/ingest/upload",
        files={"files": ("download2.csv", unmatched_csv.encode("utf-8-sig"), "text/csv")},
    )
    assert r.status_code == 200
    body = r.json()
    result = body["files"][0]
    assert result["status"] == "error"
    assert "999" in result["message"]
    assert body["ingest"] is None

    assert not (upload_root / "Proj/Reach/Site 1/GW 1/download2.csv").exists()


def test_upload_garbage_file_reports_error_without_crashing(client: TestClient):
    r = client.post(
        "/api/ingest/upload",
        files={"files": ("garbage.csv", b"not,a,hobo,file\n1,2,3,4\n", "text/csv")},
    )
    assert r.status_code == 200
    result = r.json()["files"][0]
    assert result["status"] == "error"
    assert "not a readable HOBO file" in result["message"]


def test_upload_unsupported_extension_reports_error(client: TestClient):
    r = client.post(
        "/api/ingest/upload",
        files={"files": ("notes.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 200
    result = r.json()["files"][0]
    assert result["status"] == "error"
    assert result["message"] == "unsupported file type"
