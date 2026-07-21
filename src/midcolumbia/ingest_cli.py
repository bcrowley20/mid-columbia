"""Command-line entry point that runs a full ingestion scan, then recomputes
calculations (water depth), against the data_root configured in settings.json.
Usage: uv run midcolumbia-ingest
"""

from __future__ import annotations

from .calculations.runner import compute_all
from .config import load_settings
from .ingestion.scanner import scan_all
from .storage import db


def main() -> None:
    settings = load_settings()
    conn = db.connect(settings.database_path)
    try:
        scan_result = scan_all(settings.data_root, conn, settings.enabled_device_handlers)
        calc_result = compute_all(settings.data_root, conn, settings.calculations)
    finally:
        conn.close()

    print(f"Scanned {scan_result.files_scanned} files, ingested {scan_result.files_ingested} new/changed files")
    print(f"  {scan_result.readings_ingested} readings, {scan_result.events_ingested} deployment events")
    if scan_result.errors:
        print(f"  {len(scan_result.errors)} error(s):")
        for error in scan_result.errors:
            print(f"    {error}")

    print(f"Computed calculations for {calc_result.wells_processed} wells")
    print(f"  {calc_result.results_ok} ok, {calc_result.results_unknown} unknown")


if __name__ == "__main__":
    main()
