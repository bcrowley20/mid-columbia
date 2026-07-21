"""Well metadata endpoint. See Implementation Plan.md section 11."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..catalog import Catalog, find_well
from .deps import get_catalogs
from .schemas import WellOut

router = APIRouter(tags=["wells"])


@router.get("/wells", response_model=WellOut)
def get_well(well_id: str, catalogs: list[Catalog] = Depends(get_catalogs)) -> WellOut:
    well = find_well(catalogs, well_id)
    if well is None:
        raise HTTPException(status_code=404, detail=f"well {well_id!r} not found")
    return WellOut.from_well(well)
