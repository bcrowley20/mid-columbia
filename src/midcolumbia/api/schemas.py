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
    reaches: list[ReachOut]

    @classmethod
    def from_project(cls, project: Project, wells: dict[str, Well]) -> ProjectOut:
        return cls(
            id=project.id,
            name=project.name,
            reaches=[ReachOut.from_reach(r, wells) for r in project.reaches],
        )


class WellSiteSummary(BaseModel):
    """One well's row within a site's hover-popup summary - matches the
    Project Description's explicit field list (well name, point count, last
    data point), plus reach/site name are on the parent SiteSummaryOut.
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
    wells: list[WellSiteSummary]


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
