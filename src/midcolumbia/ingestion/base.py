"""Logger handler abstraction. See Implementation Plan.md section 6."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import DeploymentEvent, Reading, WellType


class ParseError(Exception):
    """Raised when a logger file can't be parsed - malformed header, unrecognized
    format, etc. Callers (the scanner) should catch this per-file rather than let
    one bad file abort an entire scan."""


class LoggerHandler(ABC):
    #: Short name used in settings.json's enabled_device_handlers list.
    name: str

    @abstractmethod
    def can_handle(self, path: Path) -> bool: ...

    @abstractmethod
    def parse(
        self, path: Path, well_id: str, well_type: WellType, timezone: str
    ) -> tuple[list[Reading], list[DeploymentEvent]]: ...
