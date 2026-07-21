"""Small helpers shared by the logger handlers."""

from __future__ import annotations

from .base import ParseError

_UNIT_ALIASES = {"°C": "degC"}


def normalize_unit(raw_unit: str) -> str:
    return _UNIT_ALIASES.get(raw_unit, raw_unit)


def extract_unit(header_cell: str) -> str:
    # Both source formats use a "<Label>, <unit> (...)" header convention, e.g.
    # "Abs Pres, kPa (LGR S/N: ...)" or "Absolute Pressure , kPa".
    try:
        after_comma = header_cell.split(",", 1)[1]
    except IndexError as exc:
        raise ParseError(f"could not find a unit in column header {header_cell!r}") from exc
    return normalize_unit(after_comma.split("(")[0].strip())
