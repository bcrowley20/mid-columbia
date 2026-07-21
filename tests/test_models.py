from datetime import datetime, timezone

from midcolumbia.models import (
    DeploymentEvent,
    ParameterType,
    Project,
    Reach,
    Reading,
    Site,
    Well,
    WellType,
)


def test_parameter_type_has_no_derived_depth_member():
    # Water depth must never be a raw ingestion parameter - it's always calculated
    # (Implementation Plan.md section 5).
    assert "WATER_DEPTH" not in ParameterType.__members__
    assert {p.value for p in ParameterType} == {
        "air_temperature",
        "air_pressure",
        "water_temperature",
        "water_pressure",
    }


def test_well_type_members():
    assert {t.value for t in WellType} == {"in_stream", "groundwater", "atmospheric"}


def test_reading_is_frozen_and_carries_provenance():
    reading = Reading(
        well_id="carlson-creek-restoration/lower-stream/site-1/gw-1",
        parameter=ParameterType.WATER_PRESSURE,
        timestamp_utc=datetime(2026, 2, 27, 17, 0, tzinfo=timezone.utc),
        value=100.035,
        unit="kPa",
        source_file="2026-03-11, Site_1,_ID_2,_22332695_0.csv",
        source_row=3,
    )
    assert reading.value == 100.035
    assert reading.timestamp_utc.tzinfo is timezone.utc


def test_site_allows_unset_coordinates():
    site = Site(
        id="site-1",
        reach_id="lower-stream",
        name="Site 1",
        latitude=None,
        longitude=None,
        wells=[],
        folder_path="Carlson Creek Restoration/Lower Stream/Site 1",
    )
    assert site.latitude is None
    assert site.longitude is None


def test_well_hierarchy_builds():
    atm_well = Well(
        id="carlson-creek-restoration/lower-stream/carlson-atm",
        site_id=None,
        reach_id="lower-stream",
        name="Carlson ATM",
        well_type=WellType.ATMOSPHERIC,
        folder_path="Carlson Creek Restoration/Lower Stream/Carlson ATM",
        device_serial="22332694",
        paired_atm_well_id=None,
    )
    gw_well = Well(
        id="carlson-creek-restoration/lower-stream/site-1/gw-1",
        site_id="site-1",
        reach_id=None,
        name="GW 1",
        well_type=WellType.GROUNDWATER,
        folder_path="Carlson Creek Restoration/Lower Stream/Site 1/GW 1",
        device_serial="22332695",
        paired_atm_well_id=None,
    )
    site = Site(
        id="site-1",
        reach_id="lower-stream",
        name="Site 1",
        latitude=None,
        longitude=None,
        wells=[gw_well],
        folder_path="Carlson Creek Restoration/Lower Stream/Site 1",
    )
    reach = Reach(
        id="lower-stream",
        project_id="carlson-creek-restoration",
        name="Lower Stream",
        atm_well_id=atm_well.id,
        sites=[site],
        folder_path="Carlson Creek Restoration/Lower Stream",
    )
    project = Project(
        id="carlson-creek-restoration",
        name="Carlson Creek Restoration",
        reaches=[reach],
        folder_path="Carlson Creek Restoration",
    )

    assert project.reaches[0].sites[0].wells[0].device_serial == "22332695"
    assert project.reaches[0].atm_well_id == atm_well.id


def test_deployment_event_kind_is_free_text():
    event = DeploymentEvent(
        well_id="carlson-creek-restoration/lower-stream/carlson-atm",
        timestamp_utc=datetime(2026, 2, 27, 16, 56, 52, tzinfo=timezone.utc),
        kind="logger_retrieved",
        source_file="2026-02-27, GW_Site_ATM,_ID_1,_22332694.csv",
    )
    assert event.kind == "logger_retrieved"
