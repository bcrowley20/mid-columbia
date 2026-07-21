"""API-level tests for the Phase 5 management endpoints (create/update/delete
Project/Reach/Site/Well). Uses its own isolated tmp_path data root - never the
real Carlson data/ tree, since these tests write to disk.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from midcolumbia.api.app import app
from midcolumbia.api.deps import get_settings
from midcolumbia.config import CalculationSettings, DisplaySettings, Settings

ALL_HANDLERS = ("hoboware_csv", "hoboconnect_xlsx")


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
def client(tmp_path: Path) -> TestClient:
    data_root = tmp_path / "data"
    data_root.mkdir()
    db_path = tmp_path / "test.sqlite3"

    from midcolumbia.storage import db

    db.connect(db_path).close()

    app.dependency_overrides[get_settings] = lambda: _test_settings(data_root, db_path)
    return TestClient(app)


def test_full_crud_lifecycle(client: TestClient):
    r = client.post("/api/projects", json={"name": "New Project", "description": "d", "timezone": "America/Denver"})
    assert r.status_code == 201
    project = r.json()
    assert project["name"] == "New Project"
    assert project["description"] == "d"
    assert project["timezone"] == "America/Denver"
    assert project["map_zoom"] == 12
    project_id = project["id"]

    r = client.post(
        "/api/reaches",
        params={"project_id": project_id},
        json={"name": "Reach X", "atm_name": "ATM X", "atm_device_serial": "999", "atm_latitude": 40.0, "atm_longitude": -105.0},
    )
    assert r.status_code == 201
    reach = r.json()
    assert reach["atm_well"]["name"] == "ATM X"
    assert reach["atm_well"]["latitude"] == 40.0
    reach_id = reach["id"]

    r = client.post("/api/sites", params={"reach_id": reach_id}, json={"name": "Site X1", "latitude": 40.1, "longitude": -105.1})
    assert r.status_code == 201
    site = r.json()
    site_id = site["id"]

    r = client.post(
        "/api/wells", params={"site_id": site_id}, json={"name": "GW X1", "well_type": "groundwater", "device_serial": "111"}
    )
    assert r.status_code == 201
    well = r.json()
    assert well["paired_atm_well_id"] == reach["atm_well"]["id"]
    well_id = well["id"]

    r = client.patch(
        "/api/wells", params={"well_id": well_id}, json={"name": "GW X1 Updated", "well_type": "in_stream", "device_serial": "222"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "GW X1 Updated"
    assert r.json()["well_type"] == "in_stream"
    assert r.json()["id"] == well_id  # id/folder unaffected by rename

    r = client.patch("/api/sites", params={"site_id": site_id}, json={"name": "Site X1 Updated", "latitude": 41.0, "longitude": -106.0})
    assert r.status_code == 200
    assert r.json()["latitude"] == 41.0

    r = client.patch(
        "/api/reaches",
        params={"reach_id": reach_id},
        json={"name": "Reach X Updated", "atm_name": "ATM X Updated", "atm_device_serial": "888", "atm_latitude": 42.0, "atm_longitude": -107.0},
    )
    assert r.status_code == 200
    assert r.json()["atm_well"]["name"] == "ATM X Updated"

    r = client.patch("/api/projects", params={"project_id": project_id}, json={"name": "New Project Updated", "timezone": "UTC"})
    assert r.status_code == 200
    assert r.json()["name"] == "New Project Updated"
    assert r.json()["timezone"] == "UTC"

    r = client.get("/api/projects")
    assert len(r.json()) == 1

    assert client.delete("/api/wells", params={"well_id": well_id}).status_code == 204
    assert client.delete("/api/sites", params={"site_id": site_id}).status_code == 204
    assert client.delete("/api/reaches", params={"reach_id": reach_id}).status_code == 204
    assert client.delete("/api/projects", params={"project_id": project_id}).status_code == 204

    assert client.get("/api/projects").json() == []


def test_create_project_bad_timezone_is_400(client: TestClient):
    r = client.post("/api/projects", json={"name": "Bad TZ", "timezone": "Not/AZone"})
    assert r.status_code == 400
    assert "timezone" in r.json()["detail"]


def test_create_reach_under_unknown_project_is_404(client: TestClient):
    r = client.post("/api/reaches", params={"project_id": "nonexistent"}, json={"name": "R", "atm_name": "ATM"})
    assert r.status_code == 404


def test_create_site_under_unknown_reach_is_404(client: TestClient):
    r = client.post("/api/sites", params={"reach_id": "nonexistent"}, json={"name": "S"})
    assert r.status_code == 404


def test_create_well_bad_well_type_is_400(client: TestClient):
    project = client.post("/api/projects", json={"name": "P", "timezone": "UTC"}).json()
    reach = client.post("/api/reaches", params={"project_id": project["id"]}, json={"name": "R", "atm_name": "ATM"}).json()
    site = client.post("/api/sites", params={"reach_id": reach["id"]}, json={"name": "S"}).json()

    r = client.post("/api/wells", params={"site_id": site["id"]}, json={"name": "W", "well_type": "bogus"})
    assert r.status_code == 400


def test_duplicate_reach_folder_name_is_400(client: TestClient):
    project = client.post("/api/projects", json={"name": "P", "timezone": "UTC"}).json()
    r1 = client.post("/api/reaches", params={"project_id": project["id"]}, json={"name": "Reach A", "atm_name": "ATM"})
    assert r1.status_code == 201
    r2 = client.post("/api/reaches", params={"project_id": project["id"]}, json={"name": "Reach A", "atm_name": "ATM2"})
    assert r2.status_code == 400


def test_cannot_delete_atm_well_directly(client: TestClient):
    project = client.post("/api/projects", json={"name": "P", "timezone": "UTC"}).json()
    reach = client.post("/api/reaches", params={"project_id": project["id"]}, json={"name": "R", "atm_name": "ATM"}).json()

    r = client.delete("/api/wells", params={"well_id": reach["atm_well"]["id"]})
    assert r.status_code == 400
    assert "ATM well" in r.json()["detail"]


def test_delete_reach_leaves_data_files_on_disk(client: TestClient, tmp_path: Path):
    project = client.post("/api/projects", json={"name": "P", "timezone": "UTC"}).json()
    reach = client.post("/api/reaches", params={"project_id": project["id"]}, json={"name": "R", "atm_name": "ATM"}).json()

    reach_dir = tmp_path / "data" / "P" / "R"
    marker = reach_dir / "ATM" / "real_logger_data.csv"
    marker.write_text("irreplaceable field data", encoding="utf-8")

    assert client.delete("/api/reaches", params={"reach_id": reach["id"]}).status_code == 204

    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "irreplaceable field data"
    assert client.get("/api/projects").json()[0]["reaches"] == []
