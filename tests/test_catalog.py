from pathlib import Path

import pytest

from midcolumbia.catalog import CatalogError, discover_project_folders, find_project, find_reach, load_catalog
from midcolumbia.models import WellType


def test_discover_project_folders_finds_carlson(data_root: Path):
    assert discover_project_folders(data_root) == ["Carlson Creek Restoration"]


def test_load_catalog_builds_full_hierarchy(data_root: Path):
    catalog = load_catalog(data_root, "Carlson Creek Restoration")

    assert catalog.project.id == "carlson-creek-restoration"
    assert catalog.project.name == "Carlson Creek Restoration"
    assert catalog.timezone == "America/Los_Angeles"
    assert len(catalog.project.reaches) == 1

    reach = catalog.project.reaches[0]
    assert reach.id == "carlson-creek-restoration/lower-stream"
    assert reach.atm_well_id == "carlson-creek-restoration/lower-stream/carlson-atm"
    assert len(reach.sites) == 5

    site_3 = next(s for s in reach.sites if s.name == "Site 3")
    assert len(site_3.wells) == 3
    well_names = {w.name for w in site_3.wells}
    assert well_names == {"GW 3a", "GW 3b", "IS 3"}


def test_load_catalog_flat_wells_include_atm(data_root: Path):
    catalog = load_catalog(data_root, "Carlson Creek Restoration")
    # 5 sites * 2 wells + Site 3's extra GW well + 1 ATM well
    assert len(catalog.wells) == 12
    atm_well = catalog.wells["carlson-creek-restoration/lower-stream/carlson-atm"]
    assert atm_well.well_type is WellType.ATMOSPHERIC
    assert atm_well.site_id is None
    assert atm_well.device_serial == "22332694"
    # Real coordinates - the user moved these by hand after Phase 4.
    assert atm_well.latitude == pytest.approx(47.25533)
    assert atm_well.longitude == pytest.approx(-120.90511)


def test_site_wells_have_no_own_coordinates(data_root: Path):
    # Site-affiliated wells' location is their parent Site's lat/long instead
    # (models.Well) - only a reach-level ATM well carries its own.
    catalog = load_catalog(data_root, "Carlson Creek Restoration")
    gw1 = catalog.wells["carlson-creek-restoration/lower-stream/site-1/gw-1"]
    assert gw1.latitude is None
    assert gw1.longitude is None


def test_wells_default_to_reach_atm_well(data_root: Path):
    catalog = load_catalog(data_root, "Carlson Creek Restoration")
    gw1 = catalog.wells["carlson-creek-restoration/lower-stream/site-1/gw-1"]
    assert gw1.well_type is WellType.GROUNDWATER
    assert gw1.paired_atm_well_id == "carlson-creek-restoration/lower-stream/carlson-atm"


def test_well_folder_path_is_real_filesystem_path(data_root: Path):
    catalog = load_catalog(data_root, "Carlson Creek Restoration")
    gw1 = catalog.wells["carlson-creek-restoration/lower-stream/site-1/gw-1"]
    assert (data_root / gw1.folder_path).is_dir()
    assert gw1.folder_path == "Carlson Creek Restoration/Lower Stream/Site 1/GW 1"


def test_project_reach_site_have_folder_paths(data_root: Path):
    catalog = load_catalog(data_root, "Carlson Creek Restoration")
    assert catalog.project.folder_path == "Carlson Creek Restoration"
    reach = catalog.project.reaches[0]
    assert reach.folder_path == "Carlson Creek Restoration/Lower Stream"
    site_1 = next(s for s in reach.sites if s.name == "Site 1")
    assert site_1.folder_path == "Carlson Creek Restoration/Lower Stream/Site 1"
    assert (data_root / site_1.folder_path).is_dir()


def test_find_reach_and_find_project(data_root: Path):
    catalog = load_catalog(data_root, "Carlson Creek Restoration")

    found = find_reach([catalog], "carlson-creek-restoration/lower-stream")
    assert found is not None
    project, reach = found
    assert project.id == "carlson-creek-restoration"
    assert reach.name == "Lower Stream"

    assert find_reach([catalog], "nonexistent") is None

    project = find_project([catalog], "carlson-creek-restoration")
    assert project is not None
    assert project.name == "Carlson Creek Restoration"
    assert find_project([catalog], "nonexistent") is None


def test_missing_project_json5_raises(tmp_path: Path):
    (tmp_path / "Some Project").mkdir()
    with pytest.raises(CatalogError, match="not found"):
        load_catalog(tmp_path, "Some Project")


def test_missing_reach_folder_raises(tmp_path: Path):
    project_dir = tmp_path / "Some Project"
    project_dir.mkdir()
    (project_dir / "project.json5").write_text(
        '{name: "Some Project", timezone: "UTC", reaches: [{name: "R", folder: "R", '
        'atm_well: {name: "ATM", folder: "ATM", device_serial: "1"}}]}',
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="Reach folder not found"):
        load_catalog(tmp_path, "Some Project")


def test_unknown_paired_atm_well_raises(tmp_path: Path):
    project_dir = tmp_path / "Some Project"
    reach_dir = project_dir / "R"
    atm_dir = reach_dir / "ATM"
    site_dir = reach_dir / "Site 1"
    well_dir = site_dir / "GW 1"
    well_dir.mkdir(parents=True)
    atm_dir.mkdir(parents=True)

    (project_dir / "project.json5").write_text(
        '{name: "Some Project", timezone: "UTC", reaches: [{name: "R", folder: "R", '
        'atm_well: {name: "ATM", folder: "ATM", device_serial: "1"}}]}',
        encoding="utf-8",
    )
    (site_dir / "site.json5").write_text(
        '{name: "Site 1", latitude: null, longitude: null, wells: [{name: "GW 1", '
        'folder: "GW 1", type: "groundwater", device_serial: "2", paired_atm_well: "Nonexistent ATM"}]}',
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="Nonexistent ATM"):
        load_catalog(tmp_path, "Some Project")
