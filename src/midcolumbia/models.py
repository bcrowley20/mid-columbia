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


@dataclass
class Reach:
    id: str
    project_id: str
    name: str
    atm_well_id: str
    sites: list[Site]


@dataclass
class Project:
    id: str
    name: str
    reaches: list[Reach]
