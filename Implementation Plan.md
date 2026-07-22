# Mid-Columbia Fisheries Data Analysis — Implementation Plan

Status: draft v12 — **Phases 0–6 complete**, plus a Render deployment (§16, done outside the phase sequence); later phases sketched and open to revision as we build.

This plan is the working reference for implementation. Update it as decisions change; don't let it drift out of sync with the code.

## 1. Decisions already made (with the user)

| Question | Decision |
|---|---|
| Folder structure authority | The nested tree (`Project/Reach/Site/IS N or GW N/`, `Reach/<name> ATM/`) is authoritative. The user has manually reorganized the sample data into this shape (see §8) — `data/Carlson Creek Restoration/Lower Stream/...` is now the live example. |
| Well identity (type, name, coordinates) | Assigned by the **user through the Site Management UI**, not inferred from filenames or folder names. The UI is what creates a well's folder in the correct place under the tree; ingestion just reads whatever logger files land in it. |
| Well-type terminology | Three well types: **In Stream (IS)**, **Groundwater (GW)** — the user's chosen name for what the Project Description calls "out of stream" wells — and **Atmospheric (ATM)**, one per Reach. |
| Device/file formats for v1 | **Both CSV (HOBOware) and XLSX (HOBOconnect) in v1.** Originally CSV-only was going to be v1 scope, but the reorganized sample data revealed that all 5 sites' IS wells are exclusively XLSX and all GW wells are exclusively CSV — CSV-only would ingest zero in-stream data, which defeats the point of a stream-restoration monitoring tool. Both handlers are built in Phase 1. `.hobo` (binary HOBOware project file) stays out of scope — it's not a data export. |
| Storage layer | **Local SQLite** cache/index, incrementally updated by rescanning `data/` for new or changed files. Not a reparse-everything-every-run approach. |

## 2. What the real sample data taught us

The sample set at `data/Carlson Creek Restoration/Lower Stream/` (5 sites, each with a GW well and an IS well — Site 3 has two GW wells — plus one reach-level ATM well) was used to validate assumptions before/while writing this plan. The two logger export formats behave differently enough that they need separate handling logic, not just separate parsers for the same semantics:

### CSV (HOBOware desktop export) — used by all GW wells and the ATM well
- **Sequential downloads are contiguous, not overlapping.** A logger's second download picks up right at the "Coupler Attached" event that ends the first download's file.
- **Columns vary between downloads of the same logger.** Some exports include `Coupler Detached`, `Coupler Attached`, `Stopped`, `End Of File` marker columns; others only have `#, Date Time, Abs Pres, Temp`. Match by header name, not column position.
- **Marker rows *usually* carry no sensor reading, but not always** — `Coupler Attached`/`Stopped`/`End Of File` rows (retrieval-side) are reliably blank, but the `Coupler Detached` (launch) row can carry a real reading on the same row as the marker (verified: Site 1 GW well's very first row is both `Coupler Detached=Logged` *and* a valid Abs Pres/Temp reading). **Caught as a bug while writing Phase 1 tests**: an earlier version of the handler skipped reading emission for any row with a marker, silently dropping every well's first data point. Fixed — event and reading emission are independent, driven by whether each field is actually blank, not by whether a marker fired on that row.
- **The stated UTC offset is fixed per file, not DST-aware.** Headers read `"Date Time, GMT-08:00"`. Verified: a file spanning the March 8 spring-forward has continuous hourly timestamps with no gap — the logger/export never adjusts for DST, it just stamps everything with whatever fixed offset was configured at deployment. The parser must apply that literal offset to every row in the file.
- UTF-8 BOM at the start of the file (`utf-8-sig` codec). Column headers embed the logger's serial number (e.g. `"Abs Pres, kPa (LGR S/N: 22332695, ...)"`) — match by prefix (`"Abs Pres"`, `"Temp"`, `"Date Time"`), not exact string.

### XLSX (HOBOconnect app export, MX20L loggers) — used by all IS wells
- **Each download is a full cumulative history dump from deployment start, not an incremental delta.** Verified directly: the second download for Site 1's IS well starts at row 2 with `2026-02-26 11:00:00` — the original deployment start — not where the first download left off. Every later download re-includes every earlier reading. This makes upsert-by-`(well_id, timestamp, parameter)` a **required** part of ingestion, not just a defensive nicety — the XLSX handler will "reparse and overwrite" every time a well gets a new download, while the CSV handler mostly just appends.
- **Timestamps are true local wall-clock time with real DST transitions**, not a fixed offset. Verified directly by decoding the Excel serial dates: the same file has a row at `2026-03-08 01:00:00` followed immediately by a row at `2026-03-08 03:00:00` — a genuine spring-forward gap (2 AM skipped), which only happens with DST-aware local time. Converting to UTC requires the actual IANA timezone (e.g. `America/Los_Angeles`) via `zoneinfo`, not a per-file fixed offset like the CSV format. The header/filename's `PST`/`PDT` label is just a hint of which zone, not the offset to use for the whole file. **Decided in Phase 1**: for the ambiguous repeated local hour at fall-back, use `fold=0` (the earlier of the two moments) — implemented and unit-tested in `ingestion/hoboconnect_xlsx.py`.
- Dates are stored as Excel serial numbers (days since 1899-12-30) — but in practice **openpyxl auto-converts date-formatted cells to native `datetime` objects** on load (the cell's style carries a date number format), so the handler doesn't need to do the serial-number math itself in the common case; it only falls back to manual decoding if a cell ever comes back as a plain float.
- **Corrected in Phase 1** (the original assumption below was wrong): it's a **3-sheet** workbook, always named **"Data"**, **"Events"**, **"Details"** (verified identical across every sample file) — not "a data sheet plus metadata sheets" with the data sheet position undetermined. This actually resolves the "reliably first sheet" open item from Phase 0: the handler looks sheets up **by name** (`workbook["Data"]`, `workbook["Events"]`), not by position, so sheet order doesn't matter.
  - **"Data"** sheet: `#`, `Date-Time`, `Absolute Pressure`, `Temperature`, plus vendor `ATM, kPa` / `depth_m` / `depth_ft` columns (see below) — one row per hourly reading, no event/marker columns at all.
  - **"Events"** sheet: a **separate table**, own row numbering, with columns `#`, `Date-Time`, `Host Connected`, `End of File`, `Started`, `Button Up`, `Button Down` — the marker-column convention (a `"Logged"` value in the relevant column) is the same idea as the CSV format's marker columns, just on its own sheet instead of inline with the data rows as originally assumed in the draft plan.
  - **"Details"** sheet: device/deployment key-value metadata (product model, firmware, deployment settings). Out of scope for ingestion, as originally planned.
- **The Data sheet's `ATM, kPa` column is often simply empty** (no cell at all, not even a zero) — verified directly: in the first Site 1 IS-well download, column E (`ATM`) has no value on any row, yet the `depth_m` formula (`=(C-E)/9.81`) still references it, meaning Excel silently treats the missing ATM value as 0 and the resulting "depth" is not actually barometrically compensated. This further confirms (beyond the reasoning already in the original plan) that the vendor depth/ATM columns aren't reliable and should not be used — reinforces, rather than changes, the existing decision below.
- **Decision: v1 ignores the vendor `ATM`/`depth_m`/`depth_ft` columns.** We extract only `Absolute Pressure` → `WATER_PRESSURE` and `Temperature` → `WATER_TEMPERATURE` from the Data sheet (matching what the CSV handler extracts from GW/ATM wells), and always compute depth ourselves in the Calculations module using the reach's actual ATM well. Rationale: consistency across well types (GW wells have no vendor depth to fall back on), and not wanting to depend on an unverified — and, per the finding above, sometimes literally empty — vendor computation.
- Event/marker vocabulary on the Events sheet: `Host Connected`, `Started`, `Button Up`, `Button Down`, `End of File`, with `"Logged"` as the marker value (vs. CSV's `Coupler Detached`/`Attached`, `Stopped`, `End Of File`). Both map into the same `DeploymentEvent.kind` field but need per-handler normalization (see §6). `Button Up`/`Button Down` are real, frequent events (a field technician's button presses during retrieval) — captured as their own kinds, not dropped.

## 3. Tech stack

- **Python 3.13+**, managed with `uv` (`uv init`, `uv add`, `uv run`).
- **Node.js/npm** — needed starting Phase 4 for the frontend. Not present on this machine by default; installed via `brew install node` (v26.5.0/npm 11.17.0) when Phase 4 started. Worth having ready before a fresh-machine setup.
- **Backend / API**: FastAPI + Uvicorn. Async-friendly, minimal boilerplate, plays well with `uv`, and gives us OpenAPI docs for free during development.
- **Storage**: SQLite (via Python's stdlib `sqlite3`, or `sqlmodel`/`sqlalchemy` if the schema grows enough to want an ORM — decide at Phase 1 based on how the schema looks once written).
- **XLSX parsing**: `openpyxl` (read-only mode for performance on large sheets).
- **Frontend**: plain TypeScript + Vite (no heavy SPA framework required for v1's scope: a tree view, a map, hover popups, and a management form set). **Leaflet** for the map (no API key needed, works fine for local-first use, easy to swap tile providers later). **uPlot** for the Phase 6 detail-view time series (chosen over Chart.js/D3 once that phase was actually scoped — see §12's writeup for why).
  - This is a recommendation, not a locked decision — revisit if the UI grows complex enough to want React/Svelte for state management.
- **Testing**: `pytest`, run via `uv run pytest`. Real Carlson CSV/XLSX files double as parser test fixtures.

## 4. Codebase layout

```
mid-columbia/
  pyproject.toml
  settings.json                # app-level config (see §7)
  src/
    midcolumbia/
      models.py                # master dataclasses: Reading, DeploymentEvent, Well, Site, Reach, Project
      catalog.py                # project.json5/site.json5 -> dataclasses (id scheme, folder resolution)
      management.py              # Phase 5: create/update/(soft-)delete Project/Reach/Site/Well
      config.py                  # settings.json loading
      ingest_cli.py               # `uv run midcolumbia-ingest` - runs a full scan, prints a summary
      ingestion/
        base.py                 # LoggerHandler ABC + ParseError
        _util.py                 # shared header/unit-parsing helpers
        hoboware_csv.py          # CSV handler (GW + ATM wells)
        hoboconnect_xlsx.py      # XLSX handler (IS wells) - Data + Events sheets
        scanner.py               # walks data/ tree, finds new/changed files, upserts
      calculations/
        base.py                  # Calculation ABC
        water_depth.py           # ATM + water pressure -> depth (used for both GW and IS wells)
        runner.py                 # compute_all() - runs every calculation for every non-ATM well
      storage/
        db.py                     # SQLite schema, connection, upsert helpers
      api/
        app.py                     # FastAPI app, CORS, exception handlers, health check
        deps.py                     # get_settings/get_db/get_catalogs dependencies
        schemas.py                   # Pydantic response models
        routes_projects.py            # GET/POST/PATCH/DELETE /projects, /reaches, /sites; GET /sites/summary
        routes_wells.py                # GET/POST/PATCH/DELETE /wells; GET /wells/summary
        routes_readings.py              # GET /wells/readings
        routes_ingest.py                # POST /ingest/run, GET /ingest/status
      serve_cli.py               # `uv run midcolumbia-serve` - runs the dev server
  web/                        # Vite + TypeScript, no framework (section 12)
    index.html                 # two-pane app shell: #tree-pane, #map-pane
    vite.config.ts              # dev proxy: /api/* -> http://127.0.0.1:8000
    src/
      main.ts                    # bootstrap + refresh(): fetch projects, wire tree -> map, re-render after any mutation
      api.ts                      # typed fetch wrappers for the Phase 3 API + Phase 5 CRUD
      types.ts                    # hand-kept mirror of api/schemas.py
      tree.ts                     # Project > Reach > Site > Well tree, reach selection, add/edit/delete buttons
      map.ts                       # Leaflet map, site dots, hover tooltips
      management.ts                # Phase 5: shared <dialog> form logic for create/edit/delete
      style.css
  data/
    Carlson Creek Restoration/            # Project
      project.json5
      Lower Stream/                       # Reach
        Carlson ATM/                      # Atmospheric well (reach-level, one required per Reach)
          <atm logger>.csv files
        Site 1/
          site.json5
          GW 1/                           # Groundwater well
            <logger>.csv files
          IS 1/                           # In-stream well
            <logger>.xlsx files
        Site 3/
          GW 3a/
          GW 3b/                          # a site can have more than one well of a given type
          IS 3/
        ...
  tests/
    conftest.py                # repo_root/data_root fixtures - tests read the real data/ tree directly, no fixtures/ copy needed
    test_models.py
    test_config.py
    test_sample_data.py         # validates project.json5/site.json5 against the real folder layout
    test_catalog.py
    test_ingestion_hoboware_csv.py
    test_ingestion_hoboconnect_xlsx.py
    test_storage.py
    test_scanner.py             # integration: full scan against real Carlson data, idempotency, handler filtering
    test_calculations_water_depth.py   # formula/nearest-neighbor/gap-threshold unit tests
    test_calculations_runner.py         # integration: compute_all against real Carlson data
    test_api.py                         # FastAPI TestClient, full endpoint coverage against real Carlson data
    test_management.py                  # unit tests for management.py, isolated tmp_path data root
    test_api_management.py               # CRUD endpoints via TestClient, isolated tmp_path data root
```

## 5. Data model (master dataclasses)

```python
class ParameterType(Enum):
    AIR_TEMPERATURE = "air_temperature"
    AIR_PRESSURE = "air_pressure"
    WATER_TEMPERATURE = "water_temperature"
    WATER_PRESSURE = "water_pressure"
    # WATER_DEPTH is NOT here — it's a derived/calculated value, not raw ingestion output.
    # Vendor-precomputed depth/ATM columns in XLSX exports are parsed but discarded (see §2).

class WellType(Enum):
    IN_STREAM = "in_stream"          # "IS"
    GROUNDWATER = "groundwater"      # "GW" — the Project Description's "out of stream" wells
    ATMOSPHERIC = "atmospheric"      # "ATM" — one per Reach

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
    kind: str                       # normalized: "logger_launched" | "logger_retrieved" | "stopped" | "end_of_file" | ...
    source_file: str

@dataclass(frozen=True)
class CalculatedReading:            # added in Phase 2 - moved here from the §10 sketch since
    well_id: str                     # it's a master dataclass on par with Reading/DeploymentEvent
    timestamp_utc: datetime
    calculation: str                # e.g. "water_depth"
    value: float | None              # None when status is not "ok"
    unit: str                        # "ft" for water_depth
    status: str                      # "ok" | "unknown_no_atm_data" | "unknown_atm_gap_too_large"

@dataclass
class Well:
    id: str
    site_id: str | None             # None for a Reach-level ATM well
    reach_id: str | None            # set for a Reach-level ATM well, None otherwise
    name: str                       # user-assigned, e.g. "IS 1", "GW 3a"
    well_type: WellType
    folder_path: str                # relative to data/, e.g. "Carlson Creek Restoration/Lower Stream/Site 1/IS 1"
    device_serial: str | None       # optional, informational
    paired_atm_well_id: str | None  # which ATM well to use for this well's depth calc (defaults to the Reach's ATM well)
    # Added post-Phase-4 so the map could plot the ATM well too. Only ever set
    # for a reach-level ATM well - a Site-affiliated well's location is its
    # parent Site's latitude/longitude instead. Defaults to None so every
    # pre-existing Well(...) call site kept working unchanged.
    latitude: float | None = None
    longitude: float | None = None

@dataclass
class Site:
    id: str
    reach_id: str
    name: str
    latitude: float | None          # None until set via the Site Management UI (Phase 5)
    longitude: float | None
    wells: list[Well]
    folder_path: str                # added in Phase 5 - same convention as Well.folder_path,
                                     # lets management.py locate site.json5 without reverse-engineering a path from the id slug

@dataclass
class Reach:
    id: str
    project_id: str
    name: str
    atm_well_id: str                # every Reach must have exactly one ATM well (per Project Description)
    sites: list[Site]
    folder_path: str                # added in Phase 5 - e.g. "Carlson Creek Restoration/Lower Stream"

@dataclass
class Project:
    id: str
    name: str
    reaches: list[Reach]
    folder_path: str                 # added in Phase 5 - the project's own folder name
    # Added in Phase 5 - project.json5's own fields (§7), needed so the
    # management UI's edit form has something to pre-fill. `timezone` also
    # lives on Catalog (used by the ingestion scanner) - duplicated rather
    # than refactored, since both are always sourced from the same raw field.
    description: str = ""
    timezone: str = ""
    map_center_lat: float | None = None
    map_center_lon: float | None = None
    map_zoom: int = 12
```

Notes:
- Every dataclass that can fail to resolve something (e.g., a well with no paired ATM well) must have that `None` case explicitly handled by the caller — never silently skip a calculation. Per CLAUDE.md: "If None is returned, make sure it is handled by the calling function."
- **IDs — decided in Phase 0**: `id` is a slug derived from the entity's path relative to `data/` (e.g. a Site 1 groundwater well's id is derived from `Carlson Creek Restoration/Lower Stream/Site 1/GW 1`), computed at load time by the Phase 1 catalog loader — **not** stored as a field in `project.json5`/`site.json5`. This keeps the config files from having a value that can drift out of sync with the actual folder name. Known tradeoff: renaming a folder changes its id, which would orphan any stored references (e.g. `paired_atm_well_id` resolved into the DB) until a rescan. **Resolved for Phase 5, deliberately, by scope-limiting rather than solving it**: the management UI's "edit" forms only ever change a `name` field, never the `folder` a name was created with — `management.py` never renames a folder once created, so ids stay stable across every edit (verified in tests: `update_reach`/`update_well` assert `updated.id == original.id`). A future "rename the folder too" feature would need to actually solve the orphaning problem; not attempted here.

## 6. Ingestion module

**Handler abstraction** (`ingestion/base.py`) — implemented with one addition over the original sketch: `parse()` also takes `well_id`, since it's the handler's job to stamp the correct id onto every `Reading`/`DeploymentEvent` it produces (both are frozen dataclasses, so this can't be patched on after the fact):

```python
class LoggerHandler(ABC):
    name: str  # matches an entry in settings.json's enabled_device_handlers

    @abstractmethod
    def can_handle(self, path: Path) -> bool: ...

    @abstractmethod
    def parse(
        self, path: Path, well_id: str, well_type: WellType, timezone: str
    ) -> tuple[list[Reading], list[DeploymentEvent]]: ...
```

`timezone` is always passed (an IANA zone name from the project's `Catalog`) even though the CSV handler ignores it — keeps the interface uniform across handlers rather than special-casing one of them. A `ParseError` exception (also in `base.py`) is raised on malformed input; the scanner catches it per-file so one bad file doesn't abort a whole scan (see below).

**CSV handler** (`ingestion/hoboware_csv.py`) — HOBOware desktop export, used by GW and ATM wells:
- Skip the `"Plot Title: ..."` line; read with `encoding="utf-8-sig"`, via the stdlib `csv` module (handles the quoted, comma-containing header fields correctly).
- Parse the header row; match `Date Time` (extract the `GMT±HH:MM` offset from the column name), `Abs Pres`, `Temp`, and marker columns, by prefix match. Units are parsed out of the header text itself (e.g. `"Abs Pres, kPa (...)"` → `"kPa"`), not hard-coded — `°C` is normalized to `"degC"`.
- Emit `Reading`s for rows with `Abs Pres`/`Temp` present: pressure as `AIR_PRESSURE`/`WATER_PRESSURE` and temp as `AIR_TEMPERATURE`/`WATER_TEMPERATURE`, chosen by the well's `WellType` (`ATMOSPHERIC` → air, `GROUNDWATER`/`IN_STREAM` → water).
- Independently, for rows where a marker column reads `"Logged"`, emit a `DeploymentEvent`, with `kind` normalized from the column name (`Coupler Detached` → `logger_launched`, `Coupler Attached` → `logger_retrieved`, `Stopped` → `stopped`, `End Of File` → `end_of_file`). Event and reading emission are independent per row (see §2's launch-row finding) — a row can produce both.
- Apply the file's fixed UTC offset (parsed from the header) to every row — never recompute via calendar DST rules.

**XLSX handler** (`ingestion/hoboconnect_xlsx.py`) — HOBOconnect app export, used by IS wells. Revised from the original sketch after inspecting the real workbook structure (see §2):
- Open with `openpyxl` (`read_only=True, data_only=True`). Look up the **`"Data"`** and **`"Events"`** sheets **by name** (verified stable across every sample file — resolves the "reliably first sheet" open item from Phase 0).
- **Data sheet**: match `Absolute Pressure` → `WATER_PRESSURE`, `Temperature` → `WATER_TEMPERATURE`, by header prefix; unit parsed from the header the same way as the CSV handler (shared helper in `ingestion/_util.py`). Explicitly skip the vendor `ATM, kPa`, `depth_m`, `depth_ft` columns (see §2 rationale — the ATM column is sometimes entirely empty).
- **Events sheet**: separate table, own `#`/`Date-Time` columns, with marker columns `Host Connected` → `logger_retrieved`, `Started` → `logger_launched`, `End of File` → `end_of_file`, `Button Up` → `button_up`, `Button Down` → `button_down` (a `"Logged"` cell value marks occurrence, same convention as CSV's marker columns).
- Timestamp handling: openpyxl auto-converts date-formatted cells to naive `datetime` objects (falls back to manual Excel-serial decoding — days since 1899-12-30 — if a cell ever comes back as a plain float instead). The naive local datetime is localized with the project's IANA timezone (§7) via `zoneinfo`, then converted to UTC. Do not trust the header's `PST`/`PDT` label as a fixed offset — it's descriptive, not authoritative (see §2). **Decided**: ambiguous fall-back-DST local times use `fold=0` (the earlier of the two moments).
- Because every download is a full cumulative re-dump (§2), this handler will typically produce readings that mostly already exist — rely on the storage layer's upsert-by-`(well_id, timestamp, parameter)` to make this a no-op for unchanged rows rather than trying to diff/skip in the handler itself.

**Scanner** (`ingestion/scanner.py`):
- For each project `discover_project_folders()` finds under `data_root`, loads its `Catalog` (via `catalog.py`) and iterates every well in `catalog.wells` (including the reach-level ATM well) — folder structure is walked once, by the catalog loader, not re-derived here.
- For each file directly inside a well's folder, dispatches to the first handler (from `DEFAULT_HANDLERS`, filtered down to `settings.enabled_device_handlers`) whose `can_handle()` matches; `.hobo` and anything else unrecognized is silently skipped, not an error.
- Compares mtime + size against what's recorded in the `ingested_files` SQLite table; skips files that haven't changed.
- On a `ParseError`, records the error in the returned `ScanResult.errors` and moves on to the next file — the bad file is **not** marked as ingested, so it's retried on the next scan rather than silently accepted or silently dropped forever.
- On success, upserts the parsed `Reading`/`DeploymentEvent` lists, records the file's new mtime/size, and commits — per file, so an interrupted scan leaves already-processed files durably recorded rather than losing all progress.

**Storage** (`storage/db.py`) — three SQLite tables, created with `CREATE TABLE IF NOT EXISTS` on connect (no migration framework yet — fine for a single-developer v1, revisit if the schema needs to evolve under real user data):
- `readings (well_id, parameter, timestamp_utc, value, unit, source_file, source_row)`, `PRIMARY KEY (well_id, parameter, timestamp_utc)` — this is the upsert key discussed throughout §2/§6.
- `deployment_events (well_id, timestamp_utc, kind, source_file)`, `PRIMARY KEY (well_id, timestamp_utc, kind)`.
- `ingested_files (path, mtime, size)` — what the scanner's unchanged-file check reads/writes.

A small CLI (`ingest_cli.py`, registered as the `midcolumbia-ingest` script) runs `scan_all()` against `settings.json`'s configuration and prints a summary — useful for manually verifying ingestion against real data outside of pytest, ahead of Phase 3's API-triggered ingest. Run against the real Carlson data during Phase 1 development: **36 files, 30,824 parsed readings, 208 parsed deployment events, 0 errors** (stored counts are lower after upsert dedup, since XLSX downloads are cumulative re-dumps — see §2).

## 7. Configuration

Three tiers, matching both the Project Description and CLAUDE.md. Schemas below are the real ones written and validated in Phase 0 (see `data/Carlson Creek Restoration/project.json5` and its `Site N/site.json5` files for live examples).

1. **`settings.json`** (app root, not inside `data/`, plain JSON — no comments needed) — application-level config, loaded by `midcolumbia.config.load_settings()`:
   ```json5
   {
     "data_root": "data",
     "database_path": "midcolumbia.sqlite3",
     "enabled_device_handlers": ["hoboware_csv", "hoboconnect_xlsx"],
     "display": {
       "pressure_unit": "kPa",
       "temperature_unit": "degC",
       "depth_unit": "ft",
       "timezone": "America/Los_Angeles"
     },
     "calculations": {
       "max_atm_gap_hours": 12
     }
   }
   ```
   `load_settings()` raises `SettingsError` (not a silent default) if the file is missing, isn't valid JSON, or is missing a required field. `calculations.max_atm_gap_hours` is user-configurable per §10/§15 — the water depth calculation won't pair a water reading with an ATM reading further away than this many hours.

2. **`data/<Project>/project.json5`** — project-level metadata, JSON5 with comments allowed. Contains display name, description, default map center/zoom, the **IANA timezone** used to interpret XLSX local timestamps (§6), and one entry per **Reach**, each declaring its own `folder` (relative to the project) and its required **ATM well** (`name`, `folder` relative to the Reach, `device_serial`, and — added post-Phase-4 — `latitude`/`longitude`, nullable, same convention as a Site's). Sites are *not* listed here — they're discovered by walking the Reach folder for subdirectories that contain their own `site.json5` (see §6 scanner, Phase 1).

3. **`data/<Project>/<Reach>/<Site>/site.json5`** — site-level metadata: display name, `latitude`/`longitude` (nullable — `null` until set; see below), and a `wells` list. Each well entry has `name`, `folder` (relative to the site), `type` (`"in_stream"` | `"groundwater"`), `device_serial` (informational), and `paired_atm_well` (`null` = use the Reach's default ATM well).

   **Updated in Phase 4**: Sites 1–5 now have real, user-provided coordinates rather than `null` (needed so the Phase 4 map has something to plot). The user gave one reach-wide reference point (47.2547, -120.9048 — real, lands near an actual "Carlson Creek"/"Carlson Creek Road" on the map); the 5 sites were spaced ~150–200m apart in a line around it as an approximation, since we don't have precise per-site GPS. `project.json5`'s `map.center_lat`/`center_lon` was set to the same reference point. Both are marked in-file as approximate and refinable later via hand-edit or the Phase 5 UI. The `null` path (no location set) is still real production behavior — a new project/site will start with `null` until someone provides coordinates, and the map (section 12) handles that case explicitly rather than assuming it can't happen.

Every folder-backed entity carries an explicit `folder` field distinct from its display `name`, so a rename in the UI doesn't have to mean a filesystem rename (or vice versa).

The Site Management UI (Phase 5) is what writes `project.json5`/`site.json5` and creates the corresponding folders — users should not need to hand-edit these files, though they can (JSON5 comments are there specifically so hand-editing stays reasonable).

Turning these files into the §5 dataclasses is **done, in Phase 1** — `catalog.py`'s `load_catalog(data_root, project_folder)` reads `project.json5` and every `Site N/site.json5` beneath it, resolves `folder` references into real paths, derives ids as `/`-joined slugs of each entity's path relative to `data_root` (e.g. `carlson-creek-restoration/lower-stream/site-1/gw-1`, matching the scheme decided in Phase 0), and resolves each well's `paired_atm_well` (or the `null` default) into a concrete ATM well id. It returns a small `Catalog` aggregate — `Catalog(project, wells, timezone)` — not just a bare `Project`: `wells` is a flat `{id: Well}` map covering *every* well including reach-level ATM wells (which `Project`/`Site` alone can't reach, since `Reach` only stores `atm_well_id` as a string), and `timezone` carries the project's IANA zone since it's config, not part of the `Project` identity dataclass in `models.py`. The scanner and, later, the calculations module both need this flat lookup. `CatalogError` is raised (not a bare `KeyError`/`ValueError`) for a missing file, invalid JSON5, a `folder` that doesn't exist on disk, or a `paired_atm_well` that doesn't resolve.

## 8. Data reorganization — done

The sample data has already been manually reorganized by the user into the agreed structure: `data/Carlson Creek Restoration/Lower Stream/{Carlson ATM, Site 1..5/{GW N, IS N}}`. This is now the canonical example/fixture set for Phase 1 development and tests. No migration script is needed — the earlier plan draft's proposed migration step is no longer necessary.

Still to do in Phase 0/1: write `project.json5` and `site.json5` for this real example project (by hand initially, or via a small one-off script), since those files don't exist yet and the scanner/UI will expect them.

## 9. Explicitly out of scope for v1

- **`.hobo` files** — binary HOBOware desktop project files, not raw data. Scanner should ignore them (not even attempt `can_handle`).
- **Trusting vendor-computed ATM/depth from XLSX** — parsed columns are discarded in favor of our own calculation (see §2, §10). Could be revisited later as a cross-check.
- **Detail data view** (Project Description: "we will define later") — designed with the user and built as Phase 6 (§12); year-over-year comparisons and moving averages were explicitly deferred by the sponsor within that design, not part of this scope note.
- **Cloud deployment** (AWS etc.) — explicitly out of scope per Project Description for v1's *architecture* (still true: no auth, no multi-tenancy, SQLite not a hosted DB). **Superseded for the narrower purpose of sharing a preview with others for feedback**: a Render deployment was added afterward, alongside these phases rather than as one of them — see §16. Not a reversal of "local-first" as the app's design center, just an additional way to demo it.
- **Auth / multi-user** — v1 is local-first, single user, no auth.

## 10. Calculations module — done (Phase 2)

**Abstraction** (`calculations/base.py`), mirroring the ingestion handler pattern:

```python
class Calculation(ABC):
    name: str          # stored in calculated_readings.calculation, e.g. "water_depth"
    output_unit: str    # e.g. "ft"

    @abstractmethod
    def compute(
        self, well: Well, catalog: Catalog, conn: sqlite3.Connection, settings: CalculationSettings
    ) -> list[CalculatedReading]: ...
```

`catalog` is part of the interface for forward-compatibility (a future calculation might need to look at other wells or project settings) even though `WaterDepthCalculation` doesn't currently use it — `well.paired_atm_well_id` is already resolved to a concrete id by the catalog loader, so the water depth calculation only needs `well` and `conn`. Same "keep the interface uniform, even if one implementation ignores a parameter" choice as the ingestion handlers' `timezone` argument (§6).

**Water depth** (`calculations/water_depth.py`) — formula and algorithm as agreed:
- `depth = (well_pressure - atm_pressure) * KPA_TO_FEET` (`KPA_TO_FEET = 0.334553`), where `well_pressure` is a `WATER_PRESSURE` reading (kPa) from an IS or GW well, `atm_pressure` is an `AIR_PRESSURE` reading (kPa) from that well's paired ATM well, result in **feet**. Applies uniformly to GW and IS wells — vendor-provided pressure/depth values in the source files are never used (§2, §9), only the raw pressure ingested ourselves.
- For each `WATER_PRESSURE` reading, finds the **closest-in-time** `AIR_PRESSURE` reading from the paired ATM well via a `bisect`-based nearest-neighbor lookup (O(log n) per reading, not a linear scan — matters once a well has years of hourly data) — either before or after, not interpolation between two bracketing points.
- If the gap to that nearest reading is within `settings.calculations.max_atm_gap_hours` (12h default, §7): status `"ok"`, `value` set. If the gap exceeds it: status `"unknown_atm_gap_too_large"`, `value=None`. If the paired ATM well (or `well.paired_atm_well_id` itself) has no readings at all: status `"unknown_no_atm_data"`, `value=None`. A well with zero `WATER_PRESSURE` readings produces no rows at all (nothing to compute from) rather than a list of unknowns.
- `CalculatedReading` now lives in `models.py` (§5) rather than being sketched inline here, since Phase 2 actually uses it as a shared type across `calculations/`, `storage/`, and (later) `api/`.

**Storage**: a fourth SQLite table, `calculated_readings (well_id, calculation, timestamp_utc, value, unit, status)`, `PRIMARY KEY (well_id, calculation, timestamp_utc)`, `value` nullable (an "unknown" row is still a stored row, not an absent one — round-trips a real `NULL`, verified in tests). Same upsert pattern as `readings`/`deployment_events`.

**Runner** (`calculations/runner.py`) — `compute_all(data_root, conn, settings)`: for every project/well `catalog.py` finds (skipping ATM wells, which have no `WATER_PRESSURE` to compute from), runs every registered `Calculation` (currently just `WaterDepthCalculation`) and upserts the results. **v1 simplification, decided in Phase 2**: recomputes *every* non-ATM well on every run rather than tracking which wells' input readings actually changed since the last computation. At this data scale (thousands of rows per well) a full recompute is fast and the upsert makes it idempotent/safe; a targeted "only recompute wells whose readings changed this scan" optimization is deferred (§15) rather than built speculatively.

`ingest_cli.py` now chains ingestion and calculation: `uv run midcolumbia-ingest` runs `scan_all()` then `compute_all()` and prints both summaries. Run against the real Carlson data: **11 wells processed, 13,990 `"ok"` results, 0 `"unknown"`** (the sample ATM well fully covers the same hourly date range as every water well, so nothing falls outside the 12-hour gap tolerance in this dataset — the `"unknown"` paths are covered by unit tests with synthetic data instead, see §13).

## 11. API surface — done (Phase 3)

**A real bug caught by smoke-testing before writing the plan update or the pytest suite**: the originally sketched routes above used `{id}` **path** parameters (`/sites/{id}/summary`, `/wells/{id}/readings`). Since well/site/reach/project ids are `/`-joined slugs by design (§5's decided id scheme — e.g. `carlson-creek-restoration/lower-stream/site-1/gw-1`), they contain literal `/` characters, and Starlette's router treats `/` as a path-segment boundary no matter what's inside a `{placeholder}`. Every id-based route 404'd at the routing layer itself (never even reaching the handler) the first time they were actually hit with a real id. `:path` converters don't fix it either, since they're greedy and would swallow trailing segments like `/readings`. **Fix**: id-based lookups moved to **query parameters** instead of path segments — query strings don't have this ambiguity (a `/` inside a query value is just a value, unambiguous). The endpoints actually built:

- `GET /api/projects` — hierarchical list (Project → Reach → Site → Well) for the left-hand tree. `response_model=list[ProjectOut]` (`api/schemas.py`).
- `GET /api/sites/summary?site_id=` — for map hover popups: reach name, site name, and one entry per well with `well_name`, `point_count`, `last_reading_at` — matching the Project Description's exact hover-popup field list. `point_count` is `COUNT(DISTINCT timestamp_utc)`, not a raw row count (a single hourly sample is 2 rows — pressure + temperature — so a raw count would double what a biologist would call "number of data points"). 404 if the site id doesn't resolve.
- `GET /api/wells?well_id=` — well metadata (name, type, device serial, resolved paired ATM well id). 404 if unresolved.
- `GET /api/wells/summary?well_id=` — added post-Phase-4: the same point-count/last-reading stats `/sites/summary` gives per well, but for any single well id, including the reach-level ATM well (which isn't part of any Site, so `/sites/summary` can't reach it). Backed by the same `db.count_distinct_timestamps`/`latest_reading_timestamp` helpers. The shared schema is named `WellSummaryOut` (renamed from `WellSiteSummary`, since it's no longer only used within a site's summary).
- `GET /api/wells/readings?well_id=&parameter=&from=&to=` — time series, one consistent shape (`SeriesPointOut`: `timestamp_utc`, `value`, `unit`, `status`) whether `parameter` is a raw `ParameterType` value or the calculated `"water_depth"` — `status` is always `None` for raw readings (there's no "unknown" concept there) and `"ok"`/`"unknown_..."` for the calculation. An unrecognized `parameter` is a 400, an unresolved `well_id` is a 404. `from`/`to` are optional bounds; a value with no UTC offset is treated as UTC rather than raising (stored timestamps are always UTC — see §5), filtered in Python after fetching (fine at this data scale, would move to a SQL `WHERE` clause first if datasets got large enough to matter).
- `POST /api/ingest/run` — runs `scan_all()` then `compute_all()` **synchronously within the request** (fast enough at v1 data scale — no background job queue built) and returns a summary (`IngestRunOut`); also stores it on `app.state.last_ingest_result`.
- `GET /api/ingest/status` — returns the last run's summary, or `{"has_run": false, "result": null}` if the server hasn't run one yet. **In-memory only** (`app.state`) — resets on server restart. Acceptable for v1 (a fresh run is one request away); would need real persistence if "what happened on the last ingest" needs to survive a restart.
- `GET /api/health` — trivial liveness check, added during Phase 3 (not in the original sketch) since it's useful for the frontend/tests to confirm the server is up.
- **Management (Phase 5) — done.** Full create/update/delete for Project/Reach/Site/Well, all in `routes_projects.py` (Project/Reach/Site) and `routes_wells.py` (Well), backed by the new `management.py` module:
  - `POST /api/projects` (body `ProjectWrite`) → 201 `ProjectOut`; `PATCH /api/projects?project_id=` → 200; `DELETE /api/projects?project_id=` → 204.
  - `POST /api/reaches?project_id=` (body `ReachWrite` — includes the required ATM well's `atm_name`/`atm_device_serial`/`atm_latitude`/`atm_longitude`, since a Reach can't exist without one) → 201 `ReachOut`; `PATCH /api/reaches?reach_id=`; `DELETE /api/reaches?reach_id=`.
  - `POST /api/sites?reach_id=` (body `SiteWrite`) → 201 `SiteOut`; `PATCH /api/sites?site_id=`; `DELETE /api/sites?site_id=`.
  - `POST /api/wells?site_id=` (body `WellWrite`) → 201 `WellOut`; `PATCH /api/wells?well_id=`; `DELETE /api/wells?well_id=` — **Site-affiliated wells only**. A Reach's ATM well is created/edited/deleted through the Reach endpoints instead (`management.update_well`/`delete_well` explicitly reject an ATM well id with a 400, rather than silently doing something wrong or leaving a Reach without its required ATM well).
  - Every create/update rewrites the affected `project.json5`/`site.json5` and re-`load_catalog`s to build the response, so what's returned always reflects what's actually on disk, not an in-memory guess.
  - `ProjectOut` gained `description`/`timezone`/`map_center_lat`/`map_center_lon`/`map_zoom` (previously not in Project at all — never parsed off `project.json5`, since only `Catalog.timezone` needed it before now) — the edit form needs current values to pre-fill.
  - `ReachOut.atm_well_id: str` was replaced with a nested `atm_well: WellOut` back in the Phase 4 follow-up; `WellOut` already carrying the ATM well's `latitude`/`longitude` turned out to also be exactly what the Reach edit form needed to pre-fill, at no extra cost.
  - Bad input is a `400` with a real message (`ManagementError` → `HTTPException`): unknown IANA timezone, empty name, a folder-name collision, editing/deleting an ATM well through the well endpoints, or a `well_type` that isn't `"in_stream"`/`"groundwater"`. An unresolved parent/target id is a `404`.

**Cross-cutting**:
- `api/deps.py`: `get_settings()` loads `settings.json` fresh per call; `get_db()` yields a per-request `sqlite3.Connection` (opened/closed per request); `get_catalogs()` calls the new `catalog.load_all(data_root)` (loads every project found under `data_root`, for searching across all of them). Tests override just `get_settings` via `app.dependency_overrides` — `get_db`/`get_catalogs` both depend on it, so one override redirects everything to an isolated `tmp_path` database while still reading the real `data/` tree.
- **Bug found in Phase 4, fixed in `storage/db.py`**: `db.connect()` now passes `check_same_thread=False`. FastAPI runs sync dependencies/endpoints via a threadpool, and a single request's dependency setup/query/teardown can land on *different* pooled worker threads for the same connection object — sqlite3's default same-thread check rejects that even though there's never real concurrent access to one connection (each request still gets its own dedicated connection). This never surfaced in `TestClient`-based tests (§13), which never produced enough real concurrency to hit it — it only showed up once the actual frontend fired 5 parallel `/api/sites/summary` requests (`Promise.all` in `map.ts`) against a real running `uvicorn` process. A concrete example of why CLAUDE.md's "start the dev server and use the feature in a browser" matters beyond the test suite.
- New `catalog.py` helpers used by the API layer: `load_all()`, `find_well(catalogs, well_id)`, `find_site(catalogs, site_id)`.
- New `storage/db.py` helpers: `count_distinct_timestamps()`, `latest_reading_timestamp()`.
- `CatalogError`/`SettingsError` get a dedicated exception handler returning a `500` with the real message, instead of FastAPI's generic unhandled-exception response — these mean the app's own configuration is broken, which is worth a clear message (CLAUDE.md: "errors must be handled, not just ignored").
- Permissive CORS for `localhost:5173`/`127.0.0.1:5173` (Vite's default dev port) added now, ahead of Phase 4, so the frontend won't hit a CORS wall on day one. Fine for a local-first, no-auth, single-user app (§9); would need reconsidering if this ever ran anywhere non-local.
- `serve_cli.py` (`uv run midcolumbia-serve`) runs `uvicorn.run("midcolumbia.api.app:app", ...)` with `reload=True` for local dev, mirroring the `ingest_cli.py` pattern. Verified against real data with an actual running server (not just `TestClient`): `GET /api/health` and `GET /api/projects` both responded correctly over real HTTP on `127.0.0.1`.
- Test dependency note: `httpx` (needed for FastAPI's `TestClient`) was replaced with **`httpx2`** — the installed Starlette version (1.3.1) deprecated `TestClient`'s use of `httpx` in favor of it; switching removed the deprecation warning entirely.

**`management.py` (Phase 5) — design decisions**:
- **Deletes are soft, by explicit user decision, not a default**: a delete renames the relevant `.json5` (`project.json5` → `project.json5.deleted`, `site.json5` → `site.json5.deleted`) or removes just that entity's own entry from its *parent's* `.json5` array (a Reach's entry in `project.json5`'s `reaches`, a Well's entry in `site.json5`'s `wells`) — chosen per entity based on which file actually owns that entity's definition (Sites and Projects each have their own file; Reaches and Wells are array entries inside their parent's file). Either way, **the folder and every logger file underneath are never touched**, and the operation is reversible (rename the file back / re-add the entry) where an actual `rm -rf` would not be. Verified directly in tests: a marker file written into a well/reach folder before deletion is still present, byte-for-byte, afterward.
- **Folders are never renamed after creation** — only the display `name` field changes on edit (§5's note on this). A folder-name collision on create is a clear `ManagementError`, not a silent overwrite or auto-suffix.
- **JSON5 writes regenerate the whole file** via `json5.dumps(data, indent=4)` (which — happily, checked before committing to this approach — already produces unquoted keys and trailing commas matching our hand-written style, no bespoke pretty-printer needed) prefixed with a short static header comment. This means **hand-written per-file comments get replaced** the first time the UI saves a file it wasn't the one to create — an explicit, disclosed tradeoff (JSON5 was chosen for the *option* to hand-edit and comment, not a promise that the UI preserves whatever a human wrote there).
- Every write ends by calling `catalog.load_catalog()` again and returning the freshly-reloaded object, rather than constructing the response from in-memory state — guarantees the API never reports something that doesn't match what's actually on disk.
- Timezone validation uses the stdlib: `timezone_name in zoneinfo.available_timezones()`.

## 12. Frontend — done (Phases 4–6)

**Left pane** (`tree.ts`): renders Project > Reach > Site from `GET /api/projects`, sites listed under each Reach for context. Only Reach labels are interactive (per this phase's scope — sites aren't clickable yet, that's Phase 6's detail view). Clicking a Reach highlights it and calls into the map. Keyboard-accessible (`tabIndex` + Enter/Space, not just click).

**Right pane** (`map.ts`): a `SiteMap` class wrapping Leaflet. `showReach(reach)`:
- Filters the reach's sites to those with non-null `latitude`/`longitude`.
- If none are located: shows a small "No sites in *Reach* have a location set yet" banner over the map and leaves the view alone, rather than erroring or silently showing nothing. This is real, expected behavior — a brand-new project/reach starts with every site unlocated (§7) — not just an edge case guard.
- Otherwise: fetches `GET /api/sites/summary` for every located site up front (a handful of parallel requests per reach) so hover is instant rather than round-tripping per-marker; plots each as an `L.circleMarker` ("dots," per the Project Description — iconography beyond that is still an open item, section 15); binds a Leaflet **tooltip** (hover-triggered, matching "as the user hovers... they see a popup" — Leaflet's `bindPopup` is click-triggered, `bindTooltip` is the hover one) containing Reach name, Site name, and one row per well with name/point-count/last-reading, exactly the Project Description's field list; `fitBounds`s the map to the located sites (or a single `setView` if there's only one).
- Basemap: OpenStreetMap standard tiles, no API key, with attribution — fine for local single-user dev use per the existing "no API key" plan (§3).
- No click-to-detail-view yet — explicitly Phase 6, not built here.

**App shell** (`main.ts`, `index.html`, `style.css`): a header bar, an error banner (shown if `/api/projects` or a summary fetch fails — the frontend's baseline "errors must be handled" per CLAUDE.md), and the two-pane layout. The first project's first reach auto-selects on load so the map isn't blank on first paint.

**Dev wiring**: `web/vite.config.ts` proxies `/api/*` to `http://127.0.0.1:8000` (the FastAPI dev server, `uv run midcolumbia-serve`) so the frontend calls relative paths — no hardcoded backend URL, no reliance on CORS during local dev (the CORS middleware from Phase 3 stays in place regardless, for the case of hitting the API directly from a browser without going through Vite).

**Verified in an actual browser**, not just by type-checking (`npx tsc --noEmit` and `npm run build` both clean): no headless browser tool was preinstalled, so Playwright + its bundled Chromium were installed into the scratch dir and driven with a small script (`nav` → wait for tree text → wait for `.leaflet-interactive` markers → screenshot → hover a marker → screenshot → check console errors). This is what caught the SQLite thread-safety bug (§11) — it only reproduced under the real concurrent requests a live browser session generates, never under the sequential `TestClient` tests. After the fix: 5 markers render, tree matches the real hierarchy, the hover tooltip shows correct real data (`GW 1 — 1,271 pts — 4/20/2026, 10:00:00 AM`, etc., matching numbers already verified in Phases 1–3), and the console is clean.

**Site Management UI — done (Phase 5)**: `tree.ts` now renders every level down to Well (leaf), and every node gets inline action buttons — `+ Reach`/`+ Site`/`+ Well` to add a child (not on Wells, which are leaves), plus `Edit`/`Delete` on everything including Projects. One shared `<dialog id="entity-dialog">` (`index.html`) is reused for every entity type and both create/edit modes; `management.ts` renders its fields from a small declarative `FieldSpec[]` per entity (label/type/required/step), pre-fills them for edit, and posts to the matching `api.ts` function on submit. A native `confirm()` gates every delete, with a message that's explicit about what does and doesn't happen (see §11's soft-delete note). After any successful mutation, `main.ts`'s `refresh()` re-fetches `/api/projects` and re-renders the tree **and** the map from scratch — no optimistic/partial state patching, simplest thing that's correct at this data scale — re-selecting the previously-selected reach if it still exists (found by id, not array position) or falling back to the first reach otherwise.
- **Real accessibility bug caught by Playwright, fixed before it shipped**: the action buttons were originally `visibility: hidden` until `:hover` on the row (a common "declutter the tree" pattern). Playwright's `click()` refused to click them — its actionability check requires an element to *already* be visible before it will act, and it won't hover a different element first to reveal the target. That's not a Playwright quirk to work around; it's the same wall a keyboard-only or touch user would hit, since neither can hover at all. Fixed by making the buttons always visible (small, muted styling instead of hidden-until-hover) — a case where a test-tooling failure pointed at a genuine UX gap rather than needing a workaround.
- The tree pane grew from 280px → 380px partway through, once the always-visible buttons started crowding out project names (`text-overflow: ellipsis` was truncating "Carlson Creek Restoration" to "Carlson Creek Res...").
- `ReachWrite`'s fields double as the ATM well's own fields (`atm_name`, `atm_device_serial`, `atm_latitude`, `atm_longitude`) — creating/editing a Reach is the only way to create/edit its ATM well, matching the backend's "a Reach can't exist without exactly one ATM well" rule directly in the form rather than needing a separate always-required "add ATM well" step.
- Verified with the same real-browser Playwright approach as Phase 4, against an **isolated copy** of the Carlson data (never the real `data/` tree, since these operations write to disk) — full lifecycle exercised end-to-end: add Reach → add Site → add Well → edit Well (confirmed correct pre-fill) → delete Well → delete Site → delete Reach, zero console errors throughout.

**Follow-up after Phase 4 (requested by the user)**: the reach-level ATM well wasn't on the map at all — it isn't a Site, so it was unreachable from `reach.sites`. Added `latitude`/`longitude` to `models.Well` (§5, defaulted to `None` so every existing call site kept working) and to `project.json5`'s `atm_well` block (§7); the API's `ReachOut` now nests the full `atm_well: WellOut` instead of just `atm_well_id: str` (`api/schemas.py`, `routes_projects.py` — `ProjectOut.from_project`/`ReachOut.from_reach` take the catalog's `wells` dict to resolve it); the frontend plots it as a **red** `circleMarker` (vs. blue for sites). Carlson ATM's coordinates were a placeholder near the other sites at first; the user has since moved all six locations (5 sites + ATM) by hand to their real positions (visibly right along the actual creek on the basemap now). Verified the same way as the rest of Phase 4 — real browser, Playwright — 6 markers total, red one found and hoverable, clean console.

**Second follow-up (requested by the user)**: the ATM tooltip initially had no data (just "Atmospheric reference"), unlike site tooltips. Added `GET /api/wells/summary?well_id=` (§11) so any well — not just ones belonging to a Site — can get point-count/last-reading stats; the frontend fetches it alongside the site summaries (one more parallel request per reach, only when the ATM well is located) and the tooltip now reads e.g. "Lower Stream › Carlson ATM / Atmospheric reference / 1,272 pts / 4/20/2026, 11:00:00 AM" — full parity with site well rows.

**Branding**: replaced Vite's default favicon and added the Mid-Columbia Fisheries logo to the header. The source file (`Black+no+background-02.webp`, 1500×2146, alpha-transparent) was provided by the user at the project root — resized via macOS `sips` (no new dependency) into `web/public/favicon.png` (45×64) and `web/public/logo.png` (280×400), both PNG to sidestep any older-Safari webp-favicon quirk. The header (`#app-header`) is now a flex row with the title on the left and a 40px-tall logo on the right (`index.html`/`style.css`).

**Detail chart view — done (Phase 6)**: the sponsor's brief asked for annual depth (and temperature) comparisons for groundwater vs. instream, plus a correlation angle with air temperature, but explicitly deferred year-over-year overlays and moving averages — this phase builds only the "click a Site, see this year's data, pan/zoom to hourly" piece that's actually in scope now.

- **Trigger**: clicking a Site — either its row in the tree (`tree.ts`, now the interactive element `renderSiteNode` was missing since Phase 4/5) or its marker on the map (`map.ts`, a new `click` handler alongside the existing hover `bindTooltip`) — opens the same chart. Both call a shared `onSelectSite(reach, site)` callback wired in `main.ts`.
- **Placement**: a bottom slide-up panel inside `#map-pane` (`#chart-panel`, `index.html`/`style.css`), not a separate browser window/tab — avoids popup-blocker friction and separate-window state management for no real benefit in what's still a single-page app. Flagged as an assumption before building; user agreed.
- **No new backend work**: `GET /api/wells/readings?well_id&parameter&from&to` (Phase 3) already returns raw (`water_temperature`, `air_temperature`) and calculated (`water_depth`) series with date-range filtering — exactly what the chart needs. The reach's ATM well (for the air-temperature overlay) was already exposed via `ReachOut.atm_well` (Phase 4's follow-up), so "air temp from the ATM site" needed zero plumbing, just a client-side fetch against a well id the frontend already has.
- **Library**: [uPlot](https://github.com/leeoniya/uPlot) (`web/src/chart.ts`), not Chart.js or D3 — canvas-based, renders tens of thousands of points live, no framework dependency (matches the existing vanilla-TS/Leaflet style), and small (~50KB). Chart.js would need a separate zoom plugin and is markedly slower at this point count; D3 would mean hand-writing the zoom/pan machinery uPlot already gets close to for free.
- **Default view**: water depth for every well at the site, from Jan 1 of the current year (UTC) to now — "year to date," per the sponsor. `Water temperature` and `Air temperature` checkboxes (unchecked by default) toggle the corresponding series via `uPlot.setSeries(idx, {show})` rather than refetching; all series are fetched once up front (small enough data volume — a handful of wells × a few thousand hourly points each — that eager-fetch-everything is simpler than fetching per checkbox toggle).
- **Two Y-axes**: depth (all wells share units) on the left, temperature (water + air, once toggled on) on the right — standard dual-axis time series, native to uPlot (`scales`/`axes` keyed by `'depth'`/`'temp'`).
- **Colors**: groundwater wells get a blue-family shade, instream wells a green-family shade (cycling through a small palette if a site has more than one well of a type), matching the blue-site/red-ATM convention already established on the map; the ATM air-temperature line is the same red as its map marker. Temperature series are dashed (lighter dash for the ATM line) so they read as secondary to the solid depth lines even before a checkbox is touched.
- **Alignment/"interpolation for display"**: uPlot requires every series to share one x-axis array positionally, but the GW/IS/ATM loggers don't necessarily sample at the exact same second. Timestamps are snapped to the nearest hour (`toPointMap` in `chart.ts`) to build one shared hourly grid across all series being plotted — averaging together the rare case of two raw points snapping to the same hour. This is the "we will have to interpolate the data for display" step the sponsor's brief anticipated, deliberately kept to this minimal form (snap-to-grid, not curve-fitting or synthetic resampling) since anything fancier is only useful once the deferred moving-average/year-over-year work is actually in scope. A missing hour on the grid renders as a real gap in that series' line (`spanGaps` stays at its default `false`) rather than being bridged — correct, since it means that well genuinely has no reading there, not that some *other* series happened to have a timestamp there.
- **Pan/zoom**: uPlot has no built-in zoom, so `chart.ts` implements the standard recipe from uPlot's own demos, extended for pan: **drag** selects an x-range and zooms to it (`cursor.drag` + a `setSelect` hook that calls `u.setScale('x', ...)`); **mouse wheel** zooms in/out centered on the cursor position; **shift+wheel** pans without changing the zoom level (a plain mouse wheel only ever reports vertical delta, hence the modifier); a trackpad's native two-finger left/right swipe pans directly with no modifier needed, since that gesture reports a horizontal `deltaX` on its own (`isTrackpadSwipe = |deltaX| > |deltaY|` picks it out from a vertical scroll); **double-click** or the **"Reset zoom"** button restores the full loaded range. This covers "pan and zoom and keep going down until looking at hourly data" without needing to re-query the API mid-interaction — the full year's hourly data is already loaded client-side, so zooming is instant. **Discoverability**: none of this is standard browser-chart behavior a user would already know, so a small always-visible hint strip (`#chart-panel-hint`, between the header and the chart) spells out all four gestures in one line — added after the user had to ask how to pan, rather than leaving it to be rediscovered by trial or another question.
- **Verified in a real browser** (Playwright, same pattern as Phases 4–5): clicked a Site in the tree, confirmed the panel opens with the right title and both wells' depth series plotted (visually matching a real runoff-driven creek — depth rises through mid-March, peaks, recedes); toggled both temperature checkboxes and confirmed the second axis and diurnal air-temperature cycle render correctly; drag-zoomed into a sub-range and confirmed the x-axis narrowed correctly; wheel-zoomed and reset-zoomed and confirmed both worked; closed the panel and clicked a different site's map marker and confirmed it reopened for the correct site. Zero console errors throughout.
- **Real bug found by the user in actual use, fixed after initial ship**: uPlot's default legend renders *below* the chart, inside the same container the canvas was sized to fill exactly — so the legend's own height pushed the total content taller than the panel, and since the page's ancestors don't clip overflow, the whole page grew a scrollbar instead of the chart panel staying self-contained (the user had to hunt for it, worse on a shorter browser window with several series toggled on). Two real, separate bugs, not one:
  1. **Legend placement**: uPlot supports moving its legend elsewhere via `legend.mount(self, el)` — used to relocate it into the header strip, between the site name and the temperature checkboxes, with `legend.live: false` to drop the per-hover value column uPlot's compact "inline" legend mode doesn't need in a static strip.
  2. **Sizing math, found while fixing the first bug**: even after relocating the legend, the chart canvas still overflowed the panel by a consistent ~8px. Root cause: `chart.ts` sized the plot to `bodyEl.clientHeight`, but `clientHeight` already includes that element's own CSS padding, and the plot is a *child* placed inside the padded content box — sizing it to the full padded height, not the content-box height, overflowed by exactly the padding amount. Fixed by computing content-box dimensions explicitly (`chartSize()`: `clientWidth`/`clientHeight` minus computed padding) everywhere the plot is sized or resized.
  - A third, smaller issue surfaced while testing the fix: the legend's CSS initially had `white-space: nowrap` on the whole legend block (meant to keep one series' label from breaking mid-word), which also blocked the chips from wrapping onto a second line — with temperature toggled on for a multi-well site, 7 chips need more width than the header has, and `nowrap` forced them into one unreadable, clipped row instead. Scoped `nowrap` to each individual chip (`.u-series`) instead, so chips wrap onto multiple lines first; the legend keeps a capped `max-height` with its own `overflow-y: auto` only as a fallback for sites with enough wells that even wrapped chips don't fit — if a hunt for a scrollbar is ever still needed, it's confined to that small strip, never the page.
  - Re-verified the same way: real browser, both a normal (900px) and a short (700px) viewport height, with all series and both temperature checkboxes on (worst case for both bugs) — `document.body.scrollHeight` no longer exceeds the viewport at either height, the canvas bottom stays inside the panel's bounds, and the legend reads as two clean rows of colored chips instead of a clipped strip. Zero console errors.
- **Pan discoverability**: the user asked how to pan a zoomed-in chart, since shift+scroll isn't something a browser teaches anywhere. Added trackpad two-finger-swipe panning (no modifier needed — a native swipe already reports a horizontal `deltaX` uPlot's wheel handler wasn't previously reading at all, so it silently did nothing before this) and the `#chart-panel-hint` strip described in the Pan/zoom bullet above, so the four gestures are visible without having to ask.
- **Second real bug found by the user in actual use**: clicking the panel's "×" close button hid its *content* but left a blank panel-shaped box on screen — worse, the panel was actually visible on **every** page load too, before any site was ever clicked, just as an empty flex box (the user hadn't noticed only because Site 1 tends to get clicked quickly). Root cause: `#chart-panel`'s base CSS rule set `display: flex` unconditionally; the browser's own default `[hidden] { display: none }` rule is *always* beaten by any author-stylesheet rule regardless of selector specificity, since user-agent styles are the lowest-priority cascade origin — so the `hidden` attribute (set in `index.html` on load, and by `ChartPanel.close()`) was never actually doing anything visually. Fixed with a second, higher-specificity rule, `#chart-panel[hidden] { display: none; }`, which — being an author rule itself, not relying on the UA default — correctly wins over the base rule when the attribute is present. Verified in a real browser: not visible on initial page load (confirmed with Playwright's `isVisible()`, not just eyeballing a screenshot), visible with the right content after clicking a Site, fully gone (not just empty) after clicking close, and reopens correctly for a different Site afterward. Zero console errors. Also fixed the map now correctly filling the full pane height before any site is selected, previously silently shortened by the same phantom empty panel.

## 13. Testing strategy

- Unit tests per parser, per calculation, per dataclass validation rule.
- Use the real Carlson files (already in `data/`) as fixtures for both handlers — they already exercise: variable CSV columns, marker rows in both vocabularies, DST-crossing timestamps in both the fixed-offset (CSV) and DST-aware (XLSX) forms, BOM encoding, incremental (CSV) vs. cumulative-redump (XLSX) download patterns, and multiple wells per site.
- Explicit test case: the XLSX spring-forward gap (`2026-03-08 01:00` → `2026-03-08 03:00` local) must convert to UTC correctly and not silently produce a bad/missing hour.
- Integration test: scan a small fixture tree end-to-end into a throwaway SQLite DB and assert reading counts / no duplicates on a re-run (idempotency check) — this matters especially for the XLSX cumulative-redump behavior.
- Water depth calculation: unit tests against synthetic readings (not the real dataset, which never actually exercises the "unknown" paths) for the formula constant, nearest-neighbor selection among multiple ATM readings, the `max_atm_gap_hours` boundary exactly and one minute past it, no-ATM-data, and no-paired-ATM-well. Plus an integration test running `compute_all()` against the real Carlson data end-to-end and checking recompute idempotency.
- `uv run pytest` must pass before any phase is considered done, per CLAUDE.md.

## 14. Phased milestones

- **Phase 0 — done.** `uv init --package` scaffolding (`midcolumbia` package under `src/`, Python ≥3.13, `json5` + `pytest` deps); `models.py` with the §5 dataclasses; `settings.json` + `config.py` loader (raises `SettingsError` on missing/invalid config rather than silently defaulting); `project.json5`/`site.json5` written and validated for the real Carlson Creek Restoration example (§7); `.gitignore`; 13 passing tests (`uv run pytest`) covering the dataclasses, the settings loader (including error paths), and that the JSON5 files agree with the actual folder layout and file types on disk. Deliberately **not** built yet: the JSON5-to-dataclass catalog loader and the ingestion handlers themselves — those belong to Phase 1, next.
- **Phase 1 — done.** `catalog.py` (JSON5 → dataclasses, id scheme, flat well lookup); `ingestion/base.py` (`LoggerHandler` ABC, `ParseError`) and both handlers (`hoboware_csv.py`, `hoboconnect_xlsx.py` — the latter revised after inspecting the real workbook structure, see §2/§6); `ingestion/scanner.py` (incremental rescan, per-file error isolation, handler filtering by `settings.enabled_device_handlers`); `storage/db.py` (SQLite schema + upserts); `ingest_cli.py` (`uv run midcolumbia-ingest`). 43 passing tests, including an integration test that runs a full scan against the real Carlson data and checks idempotency on rescan. One real bug was caught and fixed while writing tests: the CSV handler was dropping the first reading of every well because it skipped the whole row whenever a deployment marker fired, even though a launch-row can carry a marker *and* a valid reading (§2).
- **Phase 2 — done.** `models.CalculatedReading`; `calculations/base.py` (`Calculation` ABC); `calculations/water_depth.py` (formula, `bisect`-based nearest-neighbor ATM pairing, gap-threshold and no-data unknown states); `calculations/runner.py` (`compute_all()`, full-recompute-every-run simplification); a fourth SQLite table (`calculated_readings`, nullable `value`); `ingest_cli.py` now runs calculations right after ingestion. 58 passing tests (up from 43) — 15 new, covering the formula, nearest-neighbor/gap-threshold edge cases with synthetic data, NULL round-tripping, and an integration run against the real Carlson data (11 wells, 13,990 `"ok"` results, 0 `"unknown"`).
- **Phase 3 — done.** FastAPI app (`api/app.py`, `deps.py`, `schemas.py`) and four routers covering every endpoint from §11: project tree, site summary, well metadata, well readings (raw + calculated, unified shape), ingest trigger/status, plus a health check. `catalog.load_all/find_well/find_site` and `db.count_distinct_timestamps/latest_reading_timestamp` added to support them. `serve_cli.py` (`uv run midcolumbia-serve`). 73 passing tests (up from 58) — 15 new, all against the real Carlson data via `TestClient`, plus a real running-server smoke test. One real bug caught before it reached the test suite: `/`-containing ids don't work as REST path parameters (Starlette routing, not application logic) — fixed by moving id-based lookups to query parameters, documented in §11.
- **Phase 4 — done.** `web/` (Vite + TypeScript + Leaflet, no framework): tree pane, map pane, hover tooltips, wired to the Phase 3 API via a dev proxy. Node/npm installed as a new prerequisite. Sites 1–5 given real (user-provided, approximated/spaced) coordinates so the map has something to plot. One real bug found and fixed by actually driving the app in a browser rather than relying on `TestClient`: a SQLite thread-safety issue in `storage/db.py` that only reproduced under genuine concurrent requests (§11). Verified end-to-end with Playwright (installed ad hoc for this, no project skill existed yet) — 5 markers, correct tree, correct hover-tooltip data, clean console. **Follow-ups**: the reach-level ATM well is now on the map too, as a distinct red marker with full stats parity via a new `GET /api/wells/summary` endpoint (§11/§12); branding (favicon + header logo) added (§12). 76 passing tests (up from 73).
- **Phase 5 — done.** `management.py` (soft-delete via `.json5.deleted` rename or parent-array-entry removal, folders never renamed after creation, `json5.dumps`-based file writes); full CRUD API (`routes_projects.py`, `routes_wells.py`); `Project` dataclass gained `description`/`timezone`/`map_center_lat`/`map_center_lon`/`map_zoom`, `Project`/`Reach`/`Site` all gained `folder_path` (§5); frontend `management.ts` + `tree.ts` add/edit/delete UI via one shared `<dialog>`. Confirmed the "soft delete, don't touch data files" decision was the user's explicit call, not assumed (§11 note). 101 passing tests (up from 76) — 25 new, split between pure `management.py` unit tests (isolated `tmp_path`, never the real `data/` tree) and API-level CRUD tests. One accessibility bug caught by Playwright and fixed before shipping: hover-revealed action buttons are unclickable by both automation and keyboard/touch users alike (§12).
- **Phase 6 — done.** Detail chart view (§12's new subsection has the full design/implementation writeup). `web/src/chart.ts` (`ChartPanel` class, uPlot), `web/src/tree.ts`/`map.ts`/`main.ts` wired so clicking a Site (tree row or map marker) opens it. No backend changes were needed — `GET /api/wells/readings` (Phase 3) already supported everything (raw + calculated parameters, `from`/`to` filtering).
- **Phase 7** — Polish pass: error-handling audit against CLAUDE.md's "errors must be handled, None must be handled by caller," cleanup, docs.

Each phase ends with passing tests before moving to the next.

## 15. Open items to revisit

- Where the XLSX-conversion IANA timezone lives if it ever needs to vary per-well rather than per-project (starting assumption, still in place: one timezone per project, in `project.json5`, implemented as `Catalog.timezone` in Phase 1).
- Whether `Button Up`/`Button Down` events (XLSX) are worth surfacing in the UI as site-visit markers — captured and stored since Phase 1, not surfaced by the Phase 6 chart (which plots readings/calculations only); still open if wanted later, e.g. as vertical markers on the chart.
- No migration framework for the SQLite schema yet (`CREATE TABLE IF NOT EXISTS` only) — fine for now, revisit if the schema needs to change under real ingested data (Phase 2+).
- `compute_all()` recomputes every non-ATM well's calculations on every run rather than tracking which wells' inputs actually changed (§10) — fine at v1 data scale, revisit if recompute time becomes noticeable.
- The real Carlson dataset never exercises the `"unknown_no_atm_data"`/`"unknown_atm_gap_too_large"` paths (full ATM coverage). The Phase 6 chart doesn't visually distinguish "unknown" (calculated but no usable value, `SeriesPointOut.status`) from "no reading logged at all" — both render as the same line gap. Fine while the real data never hits it; worth a distinct visual treatment (e.g. a shaded band) if a future project has real ATM gaps.
- Iconography for map markers beyond "dots" — built as plain `circleMarker` dots in Phase 4, now with one bit of color coding (red = ATM, blue = site); still deferred, per Project Description, whether anything richer (per-well-type colors, status indicators) is wanted later.
- Year-over-year comparisons and weekly/monthly moving averages — explicitly deferred by the sponsor when Phase 6 was scoped (see §12); the sponsor was also still undecided between weekly/monthly and wanted groundwater and instream to use the same choice whenever this is picked back up.
- Display units preference (`settings.json`'s `display.pressure_unit`/`temperature_unit`/`depth_unit` — store UTC + source units internally regardless) still has no editing UI anywhere (it's app-wide config, not per-project, so it wouldn't belong on the Phase 5 Project form anyway). A project's **timezone** *is* now editable, via the Phase 5 Project edit form.
- `POST /api/ingest/run` runs synchronously in-request; fine at v1 data scale (a couple seconds for the whole real dataset) but would need to become a background job with polling/websocket status if a much larger dataset ever made a single scan take long enough to risk a request timeout.
- `GET /api/ingest/status`'s last-run result lives in `app.state` only, not persisted — lost on server restart (§11). Revisit if "what happened on the last ingest" ever needs to survive a restart.
- CORS origins (`localhost:5173`/`127.0.0.1:5173`) — still correct for local Vite-dev-server use. The Render deployment (§16) doesn't need CORS touched at all, since it serves the frontend from the same origin as the API.
- `web/src/types.ts` is a hand-kept mirror of `api/schemas.py` — no shared codegen between Python and TypeScript. Fine at this size (a handful of small interfaces); worth automating (e.g. generating TS types from the FastAPI OpenAPI schema) if the API surface grows much further.
- `map.ts`'s `FALLBACK_CENTER`/`FALLBACK_ZOOM` are hardcoded to the one real reference point we have, used only before any reach has been selected — `project.json5`'s `map.center_lat/center_lon/zoom` field (§7) still isn't wired through the API/frontend; dynamic `fitBounds` on real site coordinates does the actual work. Revisit if a project-level custom default view is ever wanted.
- Site (and now ATM) coordinates for Carlson Creek Restoration started as an approximate, evenly-spaced placement (§7) but the user has since hand-edited all six `site.json5`/`project.json5` locations to their real positions — no longer just a placeholder.
- No frontend automated test suite (no Vitest/Playwright test files committed) — Phases 4–6 were verified with type-checking (`tsc`, `vite build`) plus ad hoc, not-committed Playwright scripts driven manually against a running dev server each time. Per CLAUDE.md's UI-verification guidance this is real "used it in a browser" verification, but it isn't repeatable via `uv run pytest`. Revisit if a `/run-skill-generator`-style committed browser check would pull its weight — it would have caught the Phase 5 hover-button accessibility bug (§12) automatically on every future change, not just because this session happened to drive it manually.
- Folder renaming isn't supported anywhere in the Phase 5 management UI (§5's ID note) — only a display-`name` edit. A real "rename the folder too" feature still needs an actual fix for the id-orphaning problem, not just the current scope-limit.
- No "undo delete" UI — restoring a soft-deleted entity (§11) means manually renaming the `.json5.deleted` file back or re-adding the removed array entry by hand. Fine for now since nothing is destroyed, but not a one-click recovery.
- Editing a Well's `type` between "in_stream" and "groundwater" is allowed (unlike changing an ATM well, which is blocked outright) — deliberately, since both map to the same `WATER_PRESSURE`/`WATER_TEMPERATURE` parameters during ingestion (§6), so already-ingested rows are never misinterpreted by the change. Worth remembering if a future calculation or display ever *does* distinguish IS from GW meaningfully — this assumption would need revisiting then.
- `Project.timezone` now exists in two places (`Project.timezone` and `Catalog.timezone`, §5) — both always sourced from the same raw field, so never actually inconsistent, but a small duplication that could be cleaned up (e.g. making `Catalog.timezone` a passthrough property) if it ever becomes confusing.
- JSON5 writes regenerate the whole file (§11) — any comment a user hand-writes into `project.json5`/`site.json5` is replaced with the standard header the next time the management UI saves that file. Disclosed, not hidden, but worth remembering before hand-editing a file the UI also manages.

## 16. Deployment (Render) — done

Requested directly by the user, to share a running preview with others for feedback — not part of the Phase 0–7 sequence, done alongside it. Render needs one start command per web service; this app is Python (FastAPI) *and* Node (Vite build) at build time, so:

**Docker, not a native Render runtime environment.** Render's native runtimes are one language per service — Python *or* Node, not both — so building the frontend would need a workaround (e.g. downloading a standalone Node tarball inside a Python build command, no root/apt needed, but hacky and harder to verify locally). A Dockerfile gives exact control over both toolchains **and** the ability to reproduce the literal Render environment locally via `docker build`/`docker run` — genuinely easier here, not just a default reach-for-Docker.

**`Dockerfile`** — multi-stage:
1. `node:22-slim` stage: `npm ci` + `npm run build` → `web/dist/`.
2. `python:3.13-slim` stage: `pip install uv`, `uv sync --frozen --no-dev` (production deps only — `pytest`/`httpx2` excluded), copies in `settings.json`, `data/` (the committed Carlson sample — see the persistence note below), and the built `web/dist/` from stage 1.
3. `CMD ["sh", "-c", "uv run --no-sync uvicorn midcolumbia.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]` — Render sets `$PORT` at runtime; `${PORT:-8000}` also makes `docker run` work standalone without it.

**Two real bugs, both only found by actually building and running the image** (not by reading the Dockerfile and reasoning about it):
- `uv run uvicorn ...` (without `--no-sync`) re-syncs the venv on every container start and **silently re-installs the dev dependency group** (`pytest`, `httpx2`, and their transitive deps like `pygments`) — undoing the build stage's `--no-dev` and adding real startup latency pulling packages the running service never needs. Fixed by adding `--no-sync` to the runtime `uv run` invocation, so the container uses exactly the venv baked in at build time.
- The new startup-ingest log line (below) **never appeared in `docker logs`** — Python's `logging` module doesn't attach a handler to an arbitrary `logging.getLogger("midcolumbia")` by default, uvicorn only configures its own loggers, so `logger.info(...)` calls were silent no-ops. Fixed with a small scoped handler (`api/app.py`) attached only to the `"midcolumbia"` logger (not `logging.basicConfig()`, which would also touch uvicorn's own already-configured loggers and risk duplicate lines).

**Automatic ingest on startup (`api/app.py`'s `lifespan`)** — this is the part of the Project Description that had been sitting unimplemented since Phase 1: *"next time the app is run new data is automatically picked up and added to existing data for each site."* Until now, ingestion only ran when something explicitly called `POST /api/ingest/run` or `midcolumbia-ingest`. A FastAPI `lifespan` context manager now runs `scan_all()` + `compute_all()` once at process startup (factored into a shared `run_ingest_and_compute()` in `routes_ingest.py`, so "what happened at boot" and "what happened when someone hit `/ingest/run`" are always represented identically) and records the result on `app.state.last_ingest_result`, so `GET /api/ingest/status` reflects the automatic run without anyone having to trigger it manually. Errors are logged, not fatal — a broken scan shouldn't prevent the whole service from coming up to serve whatever was already ingested.
- This turned out to matter for **testing**, not just Render: bare `TestClient(app)` (used everywhere in the existing suite) does **not** trigger FastAPI's lifespan at all in the installed Starlette version — verified empirically before relying on it, rather than assumed — so the 102 existing tests were completely unaffected. A new dedicated test (`test_startup_lifespan_auto_ingests`) uses `with TestClient(app) as client:` (which *does* trigger lifespan) specifically to exercise this path, since nothing else would have.
- The lifespan handler manually checks `app.dependency_overrides.get(get_settings, load_settings)` — outside FastAPI's per-request `Depends()` resolution, so this is a deliberate manual lookup of the same override dict tests already populate, ensuring the startup-ingest test (and any future one) can point it at isolated data rather than the real `settings.json`/`data/`.

**Serving the frontend from FastAPI** (`api/app.py`): `StaticFiles(directory="web/dist", html=True)` mounted at `/`, *after* the `/api/*` routers so there's no path collision, and only if `web/dist` exists (so a fresh clone that hasn't run `npm run build` yet still serves the API fine on its own — no hard dependency on the frontend being built). This is what lets one process/one port serve both, which is what Render's single-start-command model needs.

**Local start, changed to match Render (requested)**: `serve_cli.py` now binds `0.0.0.0` and reads `$PORT` (default 8000) instead of hardcoding `127.0.0.1:8000` — the same address/port resolution the Docker `CMD` uses, so `uv run midcolumbia-serve` and the container behave the same way modulo `--reload` (kept locally only; no equivalent need in an immutable built image). Binding `0.0.0.0` does mean the local dev server is reachable from other devices on the same network, not just `localhost` — a deliberate, disclosed tradeoff for local/prod parity, acceptable given there's no auth either way (§9). The Vite dev-server workflow (`npm run dev` + `midcolumbia-serve`, two processes, HMR, §12) is untouched and still the fastest loop for active frontend work; the new single-process path is for testing the Render-like deploy locally (via the built frontend) or for Docker/Render itself.

**`render.yaml`** (Blueprint) — `env: docker`, points at the root `Dockerfile`, `plan: free` (Render's free tier cold-starts after inactivity; fine for an on-demand feedback preview, trivially changed later), `healthCheckPath: /api/health`. Not required — Render also auto-detects a root `Dockerfile` when a plain Web Service is created from the repo via the dashboard — but committing it makes the one-click "New +" → "Blueprint" flow fully reproducible from the repo alone, which is what "give Render access to the repository so it can deploy right out of it" asked for.

**Data persistence — a real tradeoff, not hidden**: Render's native web service filesystem is ephemeral across deploys/restarts (no attached Disk in this setup). This is fine, *specifically because* every boot now re-ingests from `data/` (above) — the SQLite cache is designed to be fully disposable and rebuildable, so losing it on redeploy costs nothing. But it also means: **new logger data can only reach the deployed preview by being committed to `data/` and redeployed** — there's no way for someone using the Render URL to drop a new CSV in and have it picked up, unlike the local-first design's actual intended workflow (§7/Project Description). That's an acceptable limitation for "share a preview for feedback," not for anything resembling real field use — worth being explicit about if this is ever a point of confusion later.

**Verified locally**, not just written and reasoned about: Docker wasn't installed in this environment at all — installed `colima` + the `docker` CLI (lighter-weight than Docker Desktop, no GUI app to interactively approve) via Homebrew, started it, then `docker build` + `docker run -p 8123:8000`. Confirmed: clean build; `docker logs` shows the startup-ingest line with the same real numbers verified in every earlier phase (36 files, 30,824 readings, 208 events, 0 errors); `/api/health`, `/api/ingest/status`, `/api/projects` all correct over real HTTP; `/` serves the built frontend (`text/html`, correct favicon); a real-browser Playwright pass against the running container shows all 6 markers (5 sites + ATM) and a clean console, from the single containerized process — not through the Vite dev proxy, the first time that unified path has been exercised at all. 102 backend tests still pass (1 new).

**What's still on the user, outside my tool access**: actually connecting Render to the GitHub repo (an external SaaS dashboard action) and choosing plan/region — I can prepare and verify everything up to that point, not click through Render's UI. Also worth deciding, before sharing the URL widely: the deployed preview will show the real reference coordinates near an actual "Carlson Creek" (§7/§12) to anyone who opens the link, since Render web services are publicly reachable by default without a paid plan's access controls.
