"""Command-line entry point that runs a full ingestion scan, then recomputes
calculations (water depth), against the data_root configured in settings.json.
Usage: uv run midcolumbia-ingest
"""

from __future__ import annotations

from .api.routes_ingest import run_ingest_and_compute
from .config import load_settings
from .storage import db


def main() -> None:
    settings = load_settings()
    conn = db.connect(settings.database_path)
    try:
        result = run_ingest_and_compute(settings, conn)
    finally:
        conn.close()

    print(f"Scanned {result.files_scanned} files, ingested {result.files_ingested} new/changed files")
    print(f"  {result.readings_ingested} readings, {result.events_ingested} deployment events")
    if result.errors:
        print(f"  {len(result.errors)} error(s):")
        for error in result.errors:
            print(f"    {error}")

    print(f"Computed calculations for {result.wells_processed} wells")
    print(f"  {result.calculations_ok} ok, {result.calculations_unknown} unknown")


if __name__ == "__main__":
    main()
