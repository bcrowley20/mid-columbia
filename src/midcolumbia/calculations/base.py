"""Calculation abstraction. See Implementation Plan.md section 10."""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod

from ..catalog import Catalog
from ..config import CalculationSettings
from ..models import CalculatedReading, Well


class Calculation(ABC):
    #: Stored in calculated_readings.calculation, e.g. "water_depth".
    name: str
    #: Unit of the value this calculation produces, e.g. "ft".
    output_unit: str

    @abstractmethod
    def compute(
        self, well: Well, catalog: Catalog, conn: sqlite3.Connection, settings: CalculationSettings
    ) -> list[CalculatedReading]: ...
