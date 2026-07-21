"""Shared FastAPI dependencies. See Implementation Plan.md section 11.

Tests override get_settings via app.dependency_overrides to point at an
isolated tmp_path database while still reading the real data/ tree - get_db
and get_catalogs both depend on it, so overriding it alone is enough.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Depends

from ..catalog import Catalog, load_all
from ..config import Settings, load_settings
from ..storage import db


def get_settings() -> Settings:
    return load_settings()


def get_db(settings: Settings = Depends(get_settings)) -> Iterator[sqlite3.Connection]:
    conn = db.connect(settings.database_path)
    try:
        yield conn
    finally:
        conn.close()


def get_catalogs(settings: Settings = Depends(get_settings)) -> list[Catalog]:
    return load_all(settings.data_root)
