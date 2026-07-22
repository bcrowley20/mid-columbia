"""HOBOware desktop CSV export handler - used by Groundwater and Atmospheric
wells. See Implementation Plan.md section 2 and 6 for the format notes this
implements: fixed (non-DST-aware) per-file UTC offset, variable column sets
across downloads of the same logger, marker rows that carry no reading.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from ..models import DeploymentEvent, ParameterType, Reading, WellType
from ._util import extract_unit
from .base import LoggerHandler, ParseError

_TIMESTAMP_FORMAT = "%m/%d/%y %I:%M:%S %p"

# Marker column header prefix -> normalized DeploymentEvent.kind
_MARKER_KINDS = {
    "Coupler Detached": "logger_launched",
    "Coupler Attached": "logger_retrieved",
    "Stopped": "stopped",
    "End Of File": "end_of_file",
}


class _ColumnMap:
    def __init__(self, header: list[str]):
        self.date_time_index: int | None = None
        self.utc_offset: timedelta | None = None
        self.pressure_index: int | None = None
        self.pressure_unit: str = ""
        self.temp_index: int | None = None
        self.temp_unit: str = ""
        self.marker_indices: dict[int, str] = {}

        for i, cell in enumerate(header):
            if cell.startswith("Date Time"):
                self.date_time_index = i
                self.utc_offset = self._parse_offset(cell)
            elif cell.startswith("Abs Pres"):
                self.pressure_index = i
                self.pressure_unit = extract_unit(cell)
            elif cell.startswith("Temp"):
                self.temp_index = i
                self.temp_unit = extract_unit(cell)
            else:
                for prefix, kind in _MARKER_KINDS.items():
                    if cell.startswith(prefix):
                        self.marker_indices[i] = kind
                        break

        if self.date_time_index is None or self.utc_offset is None:
            raise ParseError(f"CSV header is missing a 'Date Time' column: {header}")

    @staticmethod
    def _parse_offset(cell: str) -> timedelta:
        match = re.search(r"GMT([+-])(\d{2}):(\d{2})", cell)
        if not match:
            raise ParseError(f"could not find a GMT offset in Date Time column header {cell!r}")
        sign = 1 if match.group(1) == "+" else -1
        hours, minutes = int(match.group(2)), int(match.group(3))
        return sign * timedelta(hours=hours, minutes=minutes)


_DEVICE_SERIAL_RE = re.compile(r"LGR S/N:\s*(\w+)")


class HoboWareCsvHandler(LoggerHandler):
    name = "hoboware_csv"

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".csv"

    def extract_device_serial(self, path: Path) -> str:
        header = self._header_row(self._read_rows(path), path)
        match = _DEVICE_SERIAL_RE.search(",".join(header))
        if not match:
            raise ParseError(f"could not find a device serial number (LGR S/N) in header: {header}")
        return match.group(1)

    def parse(
        self, path: Path, well_id: str, well_type: WellType, timezone: str
    ) -> tuple[list[Reading], list[DeploymentEvent]]:
        rows = self._read_rows(path)
        if not rows:
            return [], []

        start = 1 if rows[0] and rows[0][0].startswith("Plot Title") else 0
        header = rows[start]
        columns = _ColumnMap(header)

        pressure_param = ParameterType.AIR_PRESSURE if well_type is WellType.ATMOSPHERIC else ParameterType.WATER_PRESSURE
        temp_param = ParameterType.AIR_TEMPERATURE if well_type is WellType.ATMOSPHERIC else ParameterType.WATER_TEMPERATURE

        readings: list[Reading] = []
        events: list[DeploymentEvent] = []

        for row_num, row in enumerate(rows[start + 1 :], start=1):
            if len(row) <= columns.date_time_index or not row[columns.date_time_index]:
                continue

            local_naive = datetime.strptime(row[columns.date_time_index], _TIMESTAMP_FORMAT)
            timestamp_utc = (local_naive - columns.utc_offset).replace(tzinfo=dt_timezone.utc)
            source_row = int(row[0]) if row and row[0].strip().isdigit() else row_num

            for marker_index, kind in columns.marker_indices.items():
                if marker_index < len(row) and row[marker_index].strip() == "Logged":
                    events.append(
                        DeploymentEvent(well_id=well_id, timestamp_utc=timestamp_utc, kind=kind, source_file=path.name)
                    )

            # Marker rows usually carry no reading (blank Abs Pres/Temp), but a
            # launch event can coincide with the deployment's first reading on
            # the same row - so readings are emitted independently of markers,
            # relying on the blank-field checks below rather than skipping the
            # whole row (see Implementation Plan.md section 2).
            if columns.pressure_index is not None and len(row) > columns.pressure_index and row[columns.pressure_index]:
                readings.append(
                    Reading(
                        well_id=well_id,
                        parameter=pressure_param,
                        timestamp_utc=timestamp_utc,
                        value=float(row[columns.pressure_index]),
                        unit=columns.pressure_unit,
                        source_file=path.name,
                        source_row=source_row,
                    )
                )
            if columns.temp_index is not None and len(row) > columns.temp_index and row[columns.temp_index]:
                readings.append(
                    Reading(
                        well_id=well_id,
                        parameter=temp_param,
                        timestamp_utc=timestamp_utc,
                        value=float(row[columns.temp_index]),
                        unit=columns.temp_unit,
                        source_file=path.name,
                        source_row=source_row,
                    )
                )

        return readings, events

    @staticmethod
    def _read_rows(path: Path) -> list[list[str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.reader(f))

    @staticmethod
    def _header_row(rows: list[list[str]], path: Path) -> list[str]:
        if not rows:
            raise ParseError(f"{path.name} is empty")
        start = 1 if rows[0] and rows[0][0].startswith("Plot Title") else 0
        if start >= len(rows):
            raise ParseError(f"{path.name} has no header row")
        return rows[start]
