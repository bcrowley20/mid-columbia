"""Validates the project.json5/site.json5 files against the real Carlson Creek
sample data on disk. This is deliberately not going through a catalog/loader
module yet (that's Phase 1, alongside the ingestion scanner) - here we're just
confirming the hand-written config files are valid JSON5 and agree with the
actual folder layout, since those files are the fixtures later phases build on.
"""

from pathlib import Path

import json5

PROJECT_DIR = "Carlson Creek Restoration"
REACH_DIR = "Lower Stream"


def test_project_json5_is_valid_and_matches_folder(data_root: Path):
    project_path = data_root / PROJECT_DIR / "project.json5"
    project = json5.loads(project_path.read_text(encoding="utf-8"))

    assert project["name"] == "Carlson Creek Restoration"
    assert len(project["reaches"]) == 1

    reach = project["reaches"][0]
    reach_dir = data_root / PROJECT_DIR / reach["folder"]
    assert reach_dir.is_dir()

    atm_dir = reach_dir / reach["atm_well"]["folder"]
    assert atm_dir.is_dir()
    assert any(atm_dir.glob("*.csv")), "ATM well should have HOBOware CSV files"


def _load_site(data_root: Path, site_folder: str) -> dict:
    site_path = data_root / PROJECT_DIR / REACH_DIR / site_folder / "site.json5"
    return json5.loads(site_path.read_text(encoding="utf-8"))


def test_site_1_through_5_wells_match_folders_and_file_types(data_root: Path):
    expected_well_count = {
        "Site 1": 2,
        "Site 2": 2,
        "Site 3": 3,  # two groundwater wells (3a, 3b) + one in-stream well
        "Site 4": 2,
        "Site 5": 2,
    }

    for site_folder, well_count in expected_well_count.items():
        site = _load_site(data_root, site_folder)
        assert site["name"] == site_folder
        assert len(site["wells"]) == well_count

        for well in site["wells"]:
            well_dir = data_root / PROJECT_DIR / REACH_DIR / site_folder / well["folder"]
            assert well_dir.is_dir(), f"missing well folder: {well_dir}"

            if well["type"] == "groundwater":
                assert any(well_dir.glob("*.csv")), f"{well_dir} should contain HOBOware CSV files"
            elif well["type"] == "in_stream":
                assert any(well_dir.glob("*.xlsx")), f"{well_dir} should contain HOBOconnect XLSX files"
            else:
                raise AssertionError(f"unexpected well type {well['type']!r} in {well_dir}")


def test_device_serials_are_unique_across_the_sample_project(data_root: Path):
    project = json5.loads((data_root / PROJECT_DIR / "project.json5").read_text(encoding="utf-8"))
    serials = [project["reaches"][0]["atm_well"]["device_serial"]]

    for site_folder in ("Site 1", "Site 2", "Site 3", "Site 4", "Site 5"):
        site = _load_site(data_root, site_folder)
        serials.extend(well["device_serial"] for well in site["wells"])

    assert len(serials) == len(set(serials)), "device serials should be unique"
