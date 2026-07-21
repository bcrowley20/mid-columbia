"""Command-line entry point that runs a full ingestion scan against the
data_root configured in settings.json. Usage: uv run midcolumbia-ingest
"""

from __future__ import annotations

from .config import load_settings
from .ingestion.scanner import scan_all
from .storage import db


def main() -> None:
    settings = load_settings()
    conn = db.connect(settings.database_path)
    try:
        result = scan_all(settings.data_root, conn, settings.enabled_device_handlers)
    finally:
        conn.close()

    print(f"Scanned {result.files_scanned} files, ingested {result.files_ingested} new/changed files")
    print(f"  {result.readings_ingested} readings, {result.events_ingested} deployment events")
    if result.errors:
        print(f"  {len(result.errors)} error(s):")
        for error in result.errors:
            print(f"    {error}")


if __name__ == "__main__":
    main()
