"""Water depth calculation. See Implementation Plan.md section 10.

depth = (well_pressure - atm_pressure) * KPA_TO_FEET, where well_pressure and
atm_pressure are the closest-in-time WATER_PRESSURE / AIR_PRESSURE readings for
a well and its paired ATM well. Vendor-provided pressure/depth values from the
source files are never used - only the raw pressure this app ingested itself
(see section 2/9). Applies uniformly to Groundwater and In-Stream wells.
"""

from __future__ import annotations

import bisect
import sqlite3
from datetime import timedelta

from ..catalog import Catalog
from ..config import CalculationSettings
from ..models import CalculatedReading, ParameterType, Reading, Well
from ..storage import db
from .base import Calculation

KPA_TO_FEET = 0.334553


class WaterDepthCalculation(Calculation):
    name = "water_depth"
    output_unit = "ft"

    def compute(
        self, well: Well, catalog: Catalog, conn: sqlite3.Connection, settings: CalculationSettings
    ) -> list[CalculatedReading]:
        water_readings = db.fetch_readings(conn, well.id, ParameterType.WATER_PRESSURE)
        if not water_readings:
            return []

        atm_readings: list[Reading] = []
        if well.paired_atm_well_id is not None:
            atm_readings = db.fetch_readings(conn, well.paired_atm_well_id, ParameterType.AIR_PRESSURE)
        atm_timestamps = [r.timestamp_utc for r in atm_readings]
        max_gap = timedelta(hours=settings.max_atm_gap_hours)

        results = []
        for reading in water_readings:
            nearest = _nearest_reading(atm_readings, atm_timestamps, reading.timestamp_utc)
            if nearest is None:
                results.append(
                    CalculatedReading(
                        well_id=well.id,
                        timestamp_utc=reading.timestamp_utc,
                        calculation=self.name,
                        value=None,
                        unit=self.output_unit,
                        status="unknown_no_atm_data",
                    )
                )
                continue

            gap = abs(nearest.timestamp_utc - reading.timestamp_utc)
            if gap > max_gap:
                results.append(
                    CalculatedReading(
                        well_id=well.id,
                        timestamp_utc=reading.timestamp_utc,
                        calculation=self.name,
                        value=None,
                        unit=self.output_unit,
                        status="unknown_atm_gap_too_large",
                    )
                )
                continue

            depth = (reading.value - nearest.value) * KPA_TO_FEET
            results.append(
                CalculatedReading(
                    well_id=well.id,
                    timestamp_utc=reading.timestamp_utc,
                    calculation=self.name,
                    value=depth,
                    unit=self.output_unit,
                    status="ok",
                )
            )

        return results


def _nearest_reading(readings: list[Reading], timestamps: list, target) -> Reading | None:
    """Nearest-neighbor lookup by absolute time difference (readings/timestamps
    must be sorted ascending, as db.fetch_readings returns them)."""
    if not readings:
        return None
    index = bisect.bisect_left(timestamps, target)
    candidates = []
    if index < len(readings):
        candidates.append(readings[index])
    if index > 0:
        candidates.append(readings[index - 1])
    return min(candidates, key=lambda r: abs(r.timestamp_utc - target))
