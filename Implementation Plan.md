# Mid-Columbia Fisheries Data Analysis — Implementation Plan

Status: draft v1 — agreed direction for Phases 0–3, later phases sketched and open to revision as we build.

This plan is the working reference for implementation. Update it as decisions change; don't let it drift out of sync with the code.

## 1. Decisions already made (with the user)

| Question | Decision |
|---|---|
| Folder structure authority | The **Project Description's nested tree** (`Project/Reach/Site/IS-N or OS-N/`, `Reach/ATM/`) is authoritative, not the current flat `data/Project/Reach/Carlson/Carlson 1..5` sample layout. Existing sample data will need to be reorganized to match (see §8, Migration). |
| Well identity (IS/OS/ATM, name, coordinates) | Assigned by the **user through the Site Management UI**, not inferred from filenames or folder names. The UI is what creates the well folder in the correct place under the tree; ingestion just reads whatever `.csv` files land in a well's folder. |
| Device/file formats for v1 | **CSV only** (HOBOware desktop export format, confirmed in the sample data). XLSX (HOBOconnect app export) and `.hobo` (binary HOBOware project file, not data) are explicitly out of scope for v1 — see §9 for what a future handler would need to account for. |
| Storage layer | **Local SQLite** cache/index, incrementally updated by rescanning `data/` for new or changed files. Not a reparse-everything-every-run approach. |

## 2. What the real sample data taught us

The `data/Project/Reach/Carlson/` sample set (5 sites + 1 ATM logger, 3 sequential downloads each) was used to validate assumptions before writing this plan:

- **Sequential downloads are contiguous, not overlapping.** A logger's second download picks up at the "Coupler Attached" event that ends the first download's file. Dedup-on-ingest should still be defensive (upsert keyed by `(well_id, timestamp, parameter)`), but we should not expect large overlaps in practice.
- **Columns vary between downloads of the same logger.** Some exports include `Coupler Detached`, `Coupler Attached`, `Stopped`, `End Of File` marker columns; others (e.g. the 3rd Carlson 1 download) only have `#, Date Time, Abs Pres, Temp`. The parser must key off header names, not column position, and must tolerate a variable column set.
- **Marker rows carry no sensor reading.** Rows like `Coupler Detached`/`Coupler Attached`/`Stopped`/`End Of File` have blank Abs Pres/Temp — they mark deployment/download boundaries, not readings. Store them separately as deployment events (useful later for "logger serviced on X"), and exclude them from the readings time series.
- **The stated UTC offset is fixed per file, not DST-aware.** Headers read like `"Date Time, GMT-08:00"`. A single file's timestamps can span a DST transition (verified: Carlson 1's 2026-03-11 file crosses the March 8 spring-forward with no gap or jump), but the offset label doesn't change mid-file. The parser must apply the file's declared fixed offset literally to every row in that file — never recompute an offset from calendar DST rules.
- **Encoding**: files start with a UTF-8 BOM (`﻿`) — the parser must handle it (Python's `utf-8-sig` codec).
- **Header duplication**: the CSV column headers embed the logger's serial number (e.g. `"Abs Pres, kPa (LGR S/N: 22332695, SEN S/N: 22332695)"`). Match columns by prefix (`"Abs Pres"`, `"Temp"`, `"Date Time"`) rather than exact string, since the serial number varies per file.

## 3. Tech stack

- **Python 3.13+**, managed with `uv` (`uv init`, `uv add`, `uv run`).
- **Backend / API**: FastAPI + Uvicorn. Async-friendly, minimal boilerplate, plays well with `uv`, and gives us OpenAPI docs for free during development.
- **Storage**: SQLite (via Python's stdlib `sqlite3`, or `sqlmodel`/`sqlalchemy` if the schema grows enough to want an ORM — decide at Phase 1 based on how the schema looks once written).
- **Frontend**: plain TypeScript + Vite (no heavy SPA framework required for v1's scope: a tree view, a map, hover popups, and a management form set). **Leaflet** for the map (no API key needed, works fine for local-first use, easy to swap tile providers later). **Chart.js** for the detail-view time series once that's defined (Phase 6).
  - This is a recommendation, not a locked decision — revisit if the UI grows complex enough to want React/Svelte for state management.
- **Testing**: `pytest`, run via `uv run pytest`. Real Carlson CSVs in `data/` double as parser test fixtures.

## 4. Codebase layout

```
mid-columbia/
  pyproject.toml
  settings.json                # app-level config (see §7)
  src/
    midcolumbia/
      models.py                # master dataclasses: Reading, DeploymentEvent, Well, Site, Reach, Project
      ingestion/
        base.py                 # LoggerHandler abstract base class + registry
        hoboware_csv.py          # v1 CSV handler
        scanner.py               # walks data/ tree, finds new/changed files
      calculations/
        base.py                  # Calculation ABC + registry
        water_depth.py           # ATM + water pressure -> depth
      storage/
        db.py                     # SQLite schema, connection, upsert helpers
      api/
        app.py                     # FastAPI app, routers
        routes_projects.py
        routes_wells.py
        routes_readings.py
        routes_ingest.py
      config.py                  # settings.json loading
  web/
    (Vite project: index.html, src/, package.json)
  data/
    <Project>/
      project.json5
      <Reach>/
        ATM/
          <atm logger>.csv files
        <Site>/
          site.json5
          IS 1/
            <logger>.csv files
          OS 1/
            <logger>.csv files
  tests/
    fixtures/                  # symlink or copy of representative sample CSVs
    test_ingestion_hoboware_csv.py
    test_calculations_water_depth.py
    test_storage.py
    test_api.py
```

## 5. Data model (master dataclasses)

```python
class ParameterType(Enum):
    AIR_TEMPERATURE = "air_temperature"
    AIR_PRESSURE = "air_pressure"
    WATER_TEMPERATURE = "water_temperature"
    WATER_PRESSURE = "water_pressure"
    # WATER_DEPTH is NOT here — it's a derived/calculated value, not raw ingestion output

class WellType(Enum):
    IN_STREAM = "in_stream"
    OUT_OF_STREAM = "out_of_stream"
    ATMOSPHERIC = "atmospheric"

@dataclass(frozen=True)
class Reading:
    well_id: str
    parameter: ParameterType
    timestamp_utc: datetime         # always normalized to UTC on ingest
    value: float
    unit: str                       # "kPa", "degC", etc. — kept explicit, no silent unit assumptions
    source_file: str                # relative path, for traceability/debugging
    source_row: int

@dataclass(frozen=True)
class DeploymentEvent:
    well_id: str
    timestamp_utc: datetime
    kind: str                       # "coupler_detached" | "coupler_attached" | "stopped" | "end_of_file"
    source_file: str

@dataclass
class Well:
    id: str
    site_id: str
    name: str                       # user-assigned, e.g. "IS 1"
    well_type: WellType
    folder_path: str                # relative to data/, e.g. "Project 1/Reach 1/Site 1/IS 1"
    device_serial: str | None       # optional, informational
    paired_atm_well_id: str | None  # which ATM logger to use for depth calc

@dataclass
class Site:
    id: str
    reach_id: str
    name: str
    latitude: float
    longitude: float
    wells: list[Well]

@dataclass
class Reach:
    id: str
    project_id: str
    name: str
    atm_well_id: str                # every Reach must have exactly one ATM well (per Project Description)
    sites: list[Site]

@dataclass
class Project:
    id: str
    name: str
    reaches: list[Reach]
```

Notes:
- Every dataclass that can fail to resolve something (e.g., a well with no paired ATM logger) must have that `None` case explicitly handled by the caller — never silently skip a calculation. Per CLAUDE.md: "If None is returned, make sure it is handled by the calling function."
- IDs: use a stable slug derived from the folder path (or a UUID stored in the relevant `.json5` file) — folder path alone is fragile if a user renames something later. Decide the exact scheme in Phase 0 once we design `site.json5`/`project.json5`.

## 6. Ingestion module

**Handler abstraction** (`ingestion/base.py`):

```python
class LoggerHandler(ABC):
    @abstractmethod
    def can_handle(self, path: Path) -> bool: ...

    @abstractmethod
    def parse(self, path: Path) -> tuple[list[Reading], list[DeploymentEvent]]: ...
```

A registry (list of handlers, tried in order) lets us add new device types — including the future XLSX/HOBOconnect handler — without touching the scanner or storage layer.

**v1 handler** (`ingestion/hoboware_csv.py`) implements the HOBOware CSV export format:
- Skip the `"Plot Title: ..."` line.
- Parse the header row; match `Date Time` (extract the `GMT±HH:MM` offset from the column name), `Abs Pres`, `Temp`, and any of the four marker columns, by prefix match.
- For each data row: if `Abs Pres`/`Temp` are present, emit `Reading`s (pressure as `AIR_PRESSURE` or `WATER_PRESSURE` depending on the well's `WellType`, temp as the matching `AIR_TEMPERATURE`/`WATER_TEMPERATURE`); if a marker column has `"Logged"`, emit a `DeploymentEvent` instead.
- Apply the file's fixed UTC offset to every row (see §2).
- Read with `encoding="utf-8-sig"`.

**Scanner** (`ingestion/scanner.py`):
- Walks `data/<Project>/<Reach>/{ATM, <Site>/<Well>}/*.csv`, using the well's `folder_path` from its `site.json5`/registered wells (not by guessing structure).
- For each file, compares mtime + size (or hash, if we want to be robust to touch-without-change) against what's recorded in SQLite; only parses new/changed files.
- Feeds parsed `Reading`/`DeploymentEvent` lists to the storage layer's upsert.

## 7. Configuration

Three tiers, matching both the Project Description and CLAUDE.md:

1. **`settings.json`** (app root, not inside `data/`) — application-level config: SQLite DB path, data root path (so `data/` could point elsewhere, e.g. a synced Google Drive folder), enabled device handlers, default units/timezone-for-display.
2. **`data/<Project>/project.json5`** — project-level metadata: display name, description, default map center/zoom.
3. **`data/<Project>/<Reach>/<Site>/site.json5`** — site-level metadata: display name, lat/long, and the list of wells (name, type, folder, device serial, paired ATM well). JSON5 so comments are allowed, per the Project Description.

The Site Management UI (Phase 5) is what writes `project.json5`/`site.json5` and creates the corresponding folders — users should not need to hand-edit these files, though they can.

## 8. Migration of existing sample data

The current `data/Project/Reach/Carlson/...` layout is flat and predates this plan's folder convention. Once Phase 0/1 land, we'll need either:
- a one-time script that reorganizes `Carlson 1..5` + `Carlson ATM` into `Project 1/Reach 1/Site 1/IS 1/…` etc. (requires deciding, per site, which files are IS vs OS — e.g. Carlson 3's `Site3a`/`Site3b` pair is a good candidate for IS+OS), or
- manual reorganization by the user via the future Site Management UI (create wells, then drop the existing CSVs into the generated folders).

Recommendation: build the migration script, since we already have the file inventory and it's a good early integration test for the folder-structure code. Revisit once Phase 0 folder-naming/ID scheme is settled.

## 9. Explicitly out of scope for v1 (but designed for)

- **XLSX / HOBOconnect handler.** Found in the sample data (`Site1, #8, 22449416 *.xlsx` etc.) but deferred per decision in §1. When we build it, note: these are MX20L Bluetooth loggers, multi-sheet workbooks (data + device/deployment metadata sheets), and the data sheet already includes an `ATM, kPa` column and pre-computed `depth_m`/`depth_ft` — i.e., this device format does its own barometric compensation. We'll need to decide then whether to trust the vendor's depth or recompute ourselves for consistency with CSV-sourced wells.
- **`.hobo` files** — binary HOBOware desktop project files, not raw data. Scanner should ignore them (not even attempt `can_handle`).
- **Detail data view** (Project Description: "we will define later") — Phase 6 is a placeholder until we design this together.
- **Cloud deployment** (AWS etc.) — explicitly out of scope per Project Description.
- **Auth / multi-user** — v1 is local-first, single user, no auth.

## 10. Calculations module

- Each calculation is a self-contained, named unit (not buried inline) exposing: required input parameter types, output type/unit, and a `compute()` function. Registered similarly to the ingestion handlers.
- First calculation: **water depth** = f(water well's `WATER_PRESSURE` series, its paired ATM well's `AIR_PRESSURE` series, water temperature for density correction if we choose to model it). Interpolation/alignment strategy for matching timestamps between a water well and its ATM well (both nominally hourly, but not guaranteed to be in phase) needs a decision at implementation time — flag as an open question for Phase 2, likely nearest-neighbor-within-tolerance or linear interpolation.
- Results are stored (not recomputed on every request) but must be invalidated/recomputed when their input readings change (e.g., new data ingested for that well or its paired ATM).

## 11. API surface (Phase 3, sketch)

- `GET /api/projects` — hierarchical list (Project → Reach → Site → Well) for the left-hand tree.
- `GET /api/sites/{id}/summary` — for map hover popups: reach name, site name, well name, point count, last reading timestamp.
- `GET /api/wells/{id}/readings?parameter=&from=&to=` — time series for detail view.
- `POST /api/ingest/run` — trigger a rescan; `GET /api/ingest/status` — last run result/errors.
- CRUD endpoints for Project/Reach/Site/Well under Phase 5 (management UI).

## 12. Frontend (Phase 4–5, sketch)

- Left pane: collapsible tree (Project > Reach > Site), driven by `/api/projects`.
- Right pane: Leaflet map. Selecting a Reach in the tree re-centers/zooms the map and plots its sites as dots (iconography beyond dots is a later decision, per Project Description).
- Hover popup on a site: Reach name, Site name, well name(s), point count, last data point — per Project Description's explicit list.
- Click a site: opens the detail view (Phase 6, TBD).
- Site Management UI: forms for create/edit/delete of Project/Reach/Site/Well, including lat/long entry (manual — no geocoding source specified) and ATM-pairing for water wells.

## 13. Testing strategy

- Unit tests per parser, per calculation, per dataclass validation rule.
- Use the real Carlson CSVs (already in `data/`) as fixtures for the HOBOware CSV handler — they already exercise: variable columns, marker rows, DST-crossing timestamps, BOM encoding, and multi-download-per-well sequences.
- Integration test: scan a small fixture tree end-to-end into a throwaway SQLite DB and assert reading counts / no duplicates on a re-run (idempotency check).
- `uv run pytest` must pass before any phase is considered done, per CLAUDE.md.

## 14. Phased milestones

- **Phase 0** — Project scaffolding: `uv init`, `pyproject.toml`, package layout, `settings.json` schema, base dataclasses, empty test scaffolding.
- **Phase 1** — CSV ingestion pipeline + SQLite storage, tested against Carlson sample data. Includes the migration script from §8.
- **Phase 2** — Calculations module (water depth), tested.
- **Phase 3** — FastAPI backend: read endpoints for tree/map/detail data, ingest trigger.
- **Phase 4** — Frontend shell: tree + Leaflet map + hover popups, wired to the Phase 3 API.
- **Phase 5** — Site Management UI: add/edit/delete Project/Reach/Site/Well, backed by new CRUD endpoints.
- **Phase 6** — Detail data view: design (with user) + implement.
- **Phase 7** — Polish pass: error-handling audit against CLAUDE.md's "errors must be handled, None must be handled by caller," cleanup, docs.

Each phase ends with passing tests before moving to the next.

## 15. Open items to revisit

- Exact ID/slug scheme for Project/Reach/Site/Well (Phase 0).
- Timestamp alignment strategy between a water well and its paired ATM well for depth calc (Phase 2).
- Water depth formula specifics: density assumption (fresh water, temperature-corrected or fixed?), gravity constant, kPa→m conversion (Phase 2).
- Iconography for map markers beyond "dots" (Phase 4, per Project Description — deferred by them too).
- Detail view design (Phase 6, deferred by Project Description).
- Display units/timezone preference (store UTC + source units internally regardless; decide user-facing default in Phase 4).
