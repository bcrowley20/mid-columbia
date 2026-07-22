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
    def extract_device_serial(self, path: Path) -> str:
        """Reads just enough of the file to recover the logger's device serial
        number, without needing a well_id/well_type yet - used to route an
        uploaded file to the well whose configured device_serial matches
        (see the Add Data importer). Raises ParseError if the file doesn't
        look like this handler's format at all (bad header, missing serial),
        which also doubles as the "is this actually a readable HOBO file"
        check for that flow."""
        ...

    @abstractmethod
    def parse(
        self, path: Path, well_id: str, well_type: WellType, timezone: str
    ) -> tuple[list[Reading], list[DeploymentEvent]]: ...
