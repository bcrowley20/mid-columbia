"""Master dataclasses for the project/reach/site/well hierarchy and logger readings.

See "Implementation Plan.md" section 5 for the design this implements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ParameterType(Enum):
    """Raw parameter kinds a logger can report. Water depth is deliberately absent:
    it is always a derived/calculated value (see the calculations module), never a
    raw ingestion output, even when a source file happens to include a vendor-computed
    depth column.
    """

    AIR_TEMPERATURE = "air_temperature"
    AIR_PRESSURE = "air_pressure"
    WATER_TEMPERATURE = "water_temperature"
    WATER_PRESSURE = "water_pressure"


class WellType(Enum):
    IN_STREAM = "in_stream"
    GROUNDWATER = "groundwater"
    ATMOSPHERIC = "atmospheric"


@dataclass(frozen=True)
class Reading:
    well_id: str
    parameter: ParameterType
    timestamp_utc: datetime
    value: float
    unit: str
    source_file: str
    source_row: int


@dataclass(frozen=True)
class DeploymentEvent:
    well_id: str
    timestamp_utc: datetime
    kind: str
    source_file: str


@dataclass(frozen=True)
class CalculatedReading:
    """A derived value (e.g. water depth), distinct from a raw ingested Reading
    because it can be explicitly unknown - see the calculations module and
    Implementation Plan.md section 10.
    """

    well_id: str
    timestamp_utc: datetime
    calculation: str
    value: float | None  # None when status is not "ok"
    unit: str
    status: str  # "ok" | "unknown_no_atm_data" | "unknown_atm_gap_too_large"


@dataclass
class Well:
    id: str
    site_id: str | None
    reach_id: str | None
    name: str
    well_type: WellType
    folder_path: str
    device_serial: str | None
    paired_atm_well_id: str | None
    # Only meaningful for a reach-level ATM well - a Site-affiliated well's
    # location is its parent Site's latitude/longitude instead. None until
    # set (same "unlocated until someone provides it" convention as Site).
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class Site:
    id: str
    reach_id: str
    name: str
    # None until a user sets a location via the Site Management UI (Phase 5) —
    # callers that need coordinates (e.g. the map view) must handle the unset case.
    latitude: float | None
    longitude: float | None
    wells: list[Well]
    # Added for Phase 5 (management writes) - relative to data/, e.g.
    # "Carlson Creek Restoration/Lower Stream/Site 1", same convention as
    # Well.folder_path. Lets the management module locate site.json5 to
    # rewrite it without having to reverse-engineer a path from the id slug.
    folder_path: str


@dataclass
class Reach:
    id: str
    project_id: str
    name: str
    atm_well_id: str
    sites: list[Site]
    # See Site.folder_path - e.g. "Carlson Creek Restoration/Lower Stream".
    folder_path: str


@dataclass
class Project:
    id: str
    name: str
    reaches: list[Reach]
    # See Site.folder_path - the project's own folder name, e.g.
    # "Carlson Creek Restoration" (a single path component, relative to data/).
    folder_path: str
    # Added for Phase 5 - project.json5's own fields (§7), needed so the
    # management UI's edit form has something to pre-fill. `timezone` also
    # lives on Catalog (used by the ingestion scanner) - duplicated rather
    # than refactored, since both are always sourced from the same raw field.
    description: str = ""
    timezone: str = ""
    map_center_lat: float | None = None
    map_center_lon: float | None = None
    map_zoom: int = 12
