"""Unit tests for management.py (Phase 5 - Site Management UI backend).
Uses an isolated tmp_path data root throughout, never the real data/ tree,
since these tests write to disk.
"""

from pathlib import Path

import pytest

from midcolumbia import management
from midcolumbia.catalog import discover_project_folders, load_catalog
from midcolumbia.management import ManagementError
from midcolumbia.models import WellType


@pytest.fixture
def tmp_data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    return root


# ---- Project ----------------------------------------------------------


def test_create_project_writes_a_loadable_project(tmp_data_root: Path):
    project = management.create_project(tmp_data_root, "Test Project", "A test", "America/Los_Angeles", 47.0, -120.0, 12)
    assert project.name == "Test Project"
    assert (tmp_data_root / "Test Project" / "project.json5").exists()

    reloaded = load_catalog(tmp_data_root, "Test Project")
    assert reloaded.project.name == "Test Project"
    assert reloaded.timezone == "America/Los_Angeles"


def test_create_project_rejects_invalid_timezone(tmp_data_root: Path):
    with pytest.raises(ManagementError, match="unknown IANA timezone"):
        management.create_project(tmp_data_root, "Bad TZ", "", "Not/AZone", None, None, 12)


def test_create_project_rejects_duplicate_folder(tmp_data_root: Path):
    management.create_project(tmp_data_root, "Dup", "", "UTC", None, None, 12)
    with pytest.raises(ManagementError, match="already exists"):
        management.create_project(tmp_data_root, "Dup", "", "UTC", None, None, 12)


def test_update_project_preserves_reaches(tmp_data_root: Path):
    project = management.create_project(tmp_data_root, "P", "", "UTC", None, None, 12)
    management.create_reach(tmp_data_root, project, "R1", "ATM1", None, None, None)

    updated = management.update_project(tmp_data_root, project, "P Renamed", "new desc", "America/Denver", 1.0, 2.0, 5)
    assert updated.name == "P Renamed"

    reloaded = load_catalog(tmp_data_root, project.folder_path)
    assert reloaded.timezone == "America/Denver"
    assert len(reloaded.project.reaches) == 1
    assert reloaded.project.reaches[0].name == "R1"


def test_delete_project_soft_deletes(tmp_data_root: Path):
    project = management.create_project(tmp_data_root, "ToDelete", "", "UTC", None, None, 12)
    management.delete_project(tmp_data_root, project)

    assert not (tmp_data_root / "ToDelete" / "project.json5").exists()
    assert (tmp_data_root / "ToDelete" / "project.json5.deleted").exists()
    assert discover_project_folders(tmp_data_root) == []


def test_delete_project_twice_raises(tmp_data_root: Path):
    project = management.create_project(tmp_data_root, "ToDelete", "", "UTC", None, None, 12)
    management.delete_project(tmp_data_root, project)
    with pytest.raises(ManagementError, match="already deleted"):
        management.delete_project(tmp_data_root, project)


# ---- Reach --------------------------------------------------------------


def test_create_reach_creates_folders_and_atm_well(tmp_data_root: Path):
    project = management.create_project(tmp_data_root, "P", "", "UTC", None, None, 12)
    reach = management.create_reach(tmp_data_root, project, "Reach 1", "Reach 1 ATM", "SN1", 10.0, 20.0)

    assert (tmp_data_root / "P" / "Reach 1").is_dir()
    assert (tmp_data_root / "P" / "Reach 1" / "Reach 1 ATM").is_dir()

    catalog = load_catalog(tmp_data_root, project.folder_path)
    atm_well = catalog.wells[reach.atm_well_id]
    assert atm_well.device_serial == "SN1"
    assert atm_well.latitude == 10.0


def test_update_reach_renames_display_name_not_folder(tmp_data_root: Path):
    project = management.create_project(tmp_data_root, "P", "", "UTC", None, None, 12)
    reach = management.create_reach(tmp_data_root, project, "Reach 1", "ATM", None, None, None)
    original_folder = reach.folder_path

    updated = management.update_reach(tmp_data_root, project, reach, "Reach 1 Renamed", "ATM Renamed", "SN2", 1.0, 2.0)

    assert updated.name == "Reach 1 Renamed"
    assert updated.folder_path == original_folder  # folder never renamed
    assert updated.id == reach.id  # id (derived from folder) unchanged

    catalog = load_catalog(tmp_data_root, project.folder_path)
    assert catalog.wells[updated.atm_well_id].name == "ATM Renamed"


def test_delete_reach_removes_entry_but_leaves_folder(tmp_data_root: Path):
    project = management.create_project(tmp_data_root, "P", "", "UTC", None, None, 12)
    reach = management.create_reach(tmp_data_root, project, "Reach 1", "ATM", None, None, None)
    reach_dir = tmp_data_root / reach.folder_path
    marker = reach_dir / "ATM" / "some_logger_download.csv"
    marker.write_text("fake logger data", encoding="utf-8")

    management.delete_reach(tmp_data_root, project, reach)

    catalog = load_catalog(tmp_data_root, project.folder_path)
    assert catalog.project.reaches == []
    assert reach_dir.is_dir()  # folder untouched
    assert marker.exists()  # "logger data" untouched
    assert marker.read_text(encoding="utf-8") == "fake logger data"


# ---- Site -----------------------------------------------------------------


def test_create_update_delete_site(tmp_data_root: Path):
    project = management.create_project(tmp_data_root, "P", "", "UTC", None, None, 12)
    reach = management.create_reach(tmp_data_root, project, "R1", "ATM", None, None, None)

    site = management.create_site(tmp_data_root, project, reach, "Site 1", 5.0, 6.0)
    assert (tmp_data_root / site.folder_path / "site.json5").exists()

    updated = management.update_site(tmp_data_root, project, site, "Site 1 Renamed", 7.0, 8.0)
    assert updated.name == "Site 1 Renamed"
    assert updated.latitude == 7.0
    assert updated.folder_path == site.folder_path

    site_dir = tmp_data_root / updated.folder_path
    marker = site_dir / "leftover_download.xlsx"
    marker.write_text("fake", encoding="utf-8")

    management.delete_site(tmp_data_root, updated)
    assert (site_dir / "site.json5.deleted").exists()
    assert not (site_dir / "site.json5").exists()
    assert marker.exists()  # data untouched

    catalog = load_catalog(tmp_data_root, project.folder_path)
    assert catalog.project.reaches[0].sites == []


def test_create_site_rejects_duplicate_folder(tmp_data_root: Path):
    project = management.create_project(tmp_data_root, "P", "", "UTC", None, None, 12)
    reach = management.create_reach(tmp_data_root, project, "R1", "ATM", None, None, None)
    management.create_site(tmp_data_root, project, reach, "Site 1", None, None)
    with pytest.raises(ManagementError, match="already exists"):
        management.create_site(tmp_data_root, project, reach, "Site 1", None, None)


# ---- Well -------------------------------------------------------------------


def test_create_update_delete_well(tmp_data_root: Path):
    project = management.create_project(tmp_data_root, "P", "", "UTC", None, None, 12)
    reach = management.create_reach(tmp_data_root, project, "R1", "ATM", None, None, None)
    site = management.create_site(tmp_data_root, project, reach, "Site 1", None, None)

    well = management.create_well(tmp_data_root, project, site, "GW 1", WellType.GROUNDWATER, "SN9")
    assert well.well_type is WellType.GROUNDWATER
    assert well.paired_atm_well_id == reach.atm_well_id  # defaults to the reach's ATM well

    updated = management.update_well(tmp_data_root, project, well, "GW 1 Renamed", WellType.IN_STREAM, "SN10")
    assert updated.name == "GW 1 Renamed"
    assert updated.well_type is WellType.IN_STREAM
    assert updated.id == well.id  # folder/id unchanged by a name/type edit

    well_dir = tmp_data_root / updated.folder_path
    marker = well_dir / "download.csv"
    marker.write_text("fake reading data", encoding="utf-8")

    management.delete_well(tmp_data_root, project, updated)
    assert well_dir.is_dir()
    assert marker.exists()  # data untouched

    catalog = load_catalog(tmp_data_root, project.folder_path)
    site_after = catalog.project.reaches[0].sites[0]
    assert site_after.wells == []


def test_cannot_update_or_delete_atm_well_via_well_functions(tmp_data_root: Path):
    project = management.create_project(tmp_data_root, "P", "", "UTC", None, None, 12)
    reach = management.create_reach(tmp_data_root, project, "R1", "ATM", None, None, None)
    catalog = load_catalog(tmp_data_root, project.folder_path)
    atm_well = catalog.wells[reach.atm_well_id]

    with pytest.raises(ManagementError, match="ATM well"):
        management.update_well(tmp_data_root, project, atm_well, "New Name", WellType.GROUNDWATER, None)

    with pytest.raises(ManagementError, match="ATM well"):
        management.delete_well(tmp_data_root, project, atm_well)


def test_folder_name_sanitizes_unsafe_characters(tmp_data_root: Path):
    # "/" and ":" would otherwise break the folder structure or be
    # Windows-unsafe - both get replaced, while the display name (stored
    # in project.json5, not the folder) keeps the original punctuation.
    project = management.create_project(tmp_data_root, "Weird/Name:Here", "", "UTC", None, None, 12)
    assert project.folder_path == "Weird-Name-Here"
    assert (tmp_data_root / "Weird-Name-Here").is_dir()
    assert project.name == "Weird/Name:Here"


def test_empty_name_rejected(tmp_data_root: Path):
    with pytest.raises(ManagementError, match="empty"):
        management.create_project(tmp_data_root, "   ", "", "UTC", None, None, 12)
