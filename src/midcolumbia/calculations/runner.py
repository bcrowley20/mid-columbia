"""Runs every registered calculation for every applicable well across all
projects under data_root. See Implementation Plan.md section 10.

v1 simplification: recomputes every non-ATM well's results on every run rather
than tracking fine-grained invalidation of which wells' inputs actually
changed. Cheap and correct at this data scale (upsert makes it idempotent);
revisit if recomputing everything ever becomes a real cost.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..catalog import discover_project_folders, load_catalog
from ..config import CalculationSettings
from ..models import WellType
from ..storage import db
from .base import Calculation
from .water_depth import WaterDepthCalculation

DEFAULT_CALCULATIONS: tuple[Calculation, ...] = (WaterDepthCalculation(),)


@dataclass
class CalculationRunResult:
    wells_processed: int = 0
    results_ok: int = 0
    results_unknown: int = 0


def compute_all(
    data_root: Path,
    conn: sqlite3.Connection,
    settings: CalculationSettings,
    calculations: tuple[Calculation, ...] = DEFAULT_CALCULATIONS,
) -> CalculationRunResult:
    result = CalculationRunResult()

    for project_folder in discover_project_folders(data_root):
        catalog = load_catalog(data_root, project_folder)
        for well in catalog.wells.values():
            if well.well_type is WellType.ATMOSPHERIC:
                continue  # depth is computed for water wells, not the ATM reference itself

            for calculation in calculations:
                calculated = calculation.compute(well, catalog, conn, settings)
                if not calculated:
                    continue
                db.upsert_calculated_readings(conn, calculated)
                for item in calculated:
                    if item.status == "ok":
                        result.results_ok += 1
                    else:
                        result.results_unknown += 1

            result.wells_processed += 1
        conn.commit()

    return result
