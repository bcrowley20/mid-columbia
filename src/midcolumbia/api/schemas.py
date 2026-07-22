"""Pydantic response models. See Implementation Plan.md section 11."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from ..models import Project, Reach, Site, Well


class WellOut(BaseModel):
    id: str
    name: str
    well_type: str
    device_serial: str | None
    paired_atm_well_id: str | None
    # Only meaningful for a reach-level ATM well - a Site-affiliated well's
    # location is its parent SiteOut's latitude/longitude instead (models.Well).
    latitude: float | None
    longitude: float | None

    @classmethod
    def from_well(cls, well: Well) -> WellOut:
        return cls(
            id=well.id,
            name=well.name,
            well_type=well.well_type.value,
            device_serial=well.device_serial,
            paired_atm_well_id=well.paired_atm_well_id,
            latitude=well.latitude,
            longitude=well.longitude,
        )


class SiteOut(BaseModel):
    id: str
    name: str
    latitude: float | None
    longitude: float | None
    wells: list[WellOut]

    @classmethod
    def from_site(cls, site: Site) -> SiteOut:
        return cls(
            id=site.id,
            name=site.name,
            latitude=site.latitude,
            longitude=site.longitude,
            wells=[WellOut.from_well(w) for w in site.wells],
        )


class ReachOut(BaseModel):
    id: str
    name: str
    # Full well (not just its id) so the frontend can plot it on the map
    # alongside sites, distinctly colored (Phase 4 follow-up) - a reach-level
    # well isn't reachable through `sites`, so it has to be included here.
    atm_well: WellOut
    sites: list[SiteOut]

    @classmethod
    def from_reach(cls, reach: Reach, wells: dict[str, Well]) -> ReachOut:
        return cls(
            id=reach.id,
            name=reach.name,
            atm_well=WellOut.from_well(wells[reach.atm_well_id]),
            sites=[SiteOut.from_site(s) for s in reach.sites],
        )


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    timezone: str
    map_center_lat: float | None
    map_center_lon: float | None
    map_zoom: int
    reaches: list[ReachOut]

    @classmethod
    def from_project(cls, project: Project, wells: dict[str, Well]) -> ProjectOut:
        return cls(
            id=project.id,
            name=project.name,
            description=project.description,
            timezone=project.timezone,
            map_center_lat=project.map_center_lat,
            map_center_lon=project.map_center_lon,
            map_zoom=project.map_zoom,
            reaches=[ReachOut.from_reach(r, wells) for r in project.reaches],
        )


class WellSummaryOut(BaseModel):
    """A well's data stats - matches the Project Description's explicit
    field list (well name, point count, last data point). Used both as a row
    within a site's hover-popup summary (SiteSummaryOut.wells) and standalone
    for a well with no site (the reach-level ATM well - GET /wells/summary).
    """

    well_id: str
    well_name: str
    well_type: str
    point_count: int
    last_reading_at: datetime | None


class SiteSummaryOut(BaseModel):
    site_id: str
    site_name: str
    reach_id: str
    reach_name: str
    latitude: float | None
    longitude: float | None
    wells: list[WellSummaryOut]


class SeriesPointOut(BaseModel):
    timestamp_utc: datetime
    value: float | None
    unit: str
    # Only set for a calculated series (e.g. water_depth) - None for raw
    # readings, which don't have an "unknown" concept.
    status: str | None = None


class WellReadingsOut(BaseModel):
    well_id: str
    parameter: str
    points: list[SeriesPointOut]


class IngestRunOut(BaseModel):
    ran_at: datetime
    files_scanned: int
    files_ingested: int
    readings_ingested: int
    events_ingested: int
    errors: list[str]
    wells_processed: int
    calculations_ok: int
    calculations_unknown: int


class IngestStatusOut(BaseModel):
    has_run: bool
    result: IngestRunOut | None = None


class UploadFileResultOut(BaseModel):
    filename: str
    status: str  # "ingested" | "error"
    well_id: str | None = None
    well_name: str | None = None
    message: str | None = None  # error detail, or None on success


class IngestUploadOut(BaseModel):
    files: list[UploadFileResultOut]
    # None only when every file errored out - nothing was placed, so there
    # was nothing worth re-scanning.
    ingest: IngestRunOut | None = None


# ---- Management (Phase 5) request bodies ----------------------------------
# Create/Update share the same shape for every entity here (full-object
# replace, not a partial PATCH merge) - simpler to reason about than merge
# semantics, and the edit forms always start from the current values anyway.


class ProjectWrite(BaseModel):
    name: str
    description: str = ""
    timezone: str
    map_center_lat: float | None = None
    map_center_lon: float | None = None
    map_zoom: int = 12


class ReachWrite(BaseModel):
    name: str
    atm_name: str
    atm_device_serial: str | None = None
    atm_latitude: float | None = None
    atm_longitude: float | None = None


class SiteWrite(BaseModel):
    name: str
    latitude: float | None = None
    longitude: float | None = None


class WellWrite(BaseModel):
    name: str
    well_type: str  # "in_stream" | "groundwater" - validated by the route
    device_serial: str | None = None
