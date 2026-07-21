# Mid-Columbia Fisheries Data Analysis — Implementation Plan

Status: draft v7 — **Phases 0–2 complete**; agreed direction for Phase 3, later phases sketched and open to revision as we build.

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
- **Backend / API**: FastAPI + Uvicorn. Async-friendly, minimal boilerplate, plays well with `uv`, and gives us OpenAPI docs for free during development.
- **Storage**: SQLite (via Python's stdlib `sqlite3`, or `sqlmodel`/`sqlalchemy` if the schema grows enough to want an ORM — decide at Phase 1 based on how the schema looks once written).
- **XLSX parsing**: `openpyxl` (read-only mode for performance on large sheets).
- **Frontend**: plain TypeScript + Vite (no heavy SPA framework required for v1's scope: a tree view, a map, hover popups, and a management form set). **Leaflet** for the map (no API key needed, works fine for local-first use, easy to swap tile providers later). **Chart.js** for the detail-view time series once that's defined (Phase 6).
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
        app.py                     # FastAPI app, routers
        routes_projects.py
        routes_wells.py
        routes_readings.py
        routes_ingest.py
  web/
    (Vite project: index.html, src/, package.json)
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
    test_api.py                         # Phase 3
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

@dataclass
class Site:
    id: str
    reach_id: str
    name: str
    latitude: float | None          # None until set via the Site Management UI (Phase 5)
    longitude: float | None
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
- Every dataclass that can fail to resolve something (e.g., a well with no paired ATM well) must have that `None` case explicitly handled by the caller — never silently skip a calculation. Per CLAUDE.md: "If None is returned, make sure it is handled by the calling function."
- **IDs — decided in Phase 0**: `id` is a slug derived from the entity's path relative to `data/` (e.g. a Site 1 groundwater well's id is derived from `Carlson Creek Restoration/Lower Stream/Site 1/GW 1`), computed at load time by the Phase 1 catalog loader — **not** stored as a field in `project.json5`/`site.json5`. This keeps the config files from having a value that can drift out of sync with the actual folder name. Known tradeoff: renaming a folder changes its id, which would orphan any stored references (e.g. `paired_atm_well_id` resolved into the DB) until a rescan. Acceptable for now since Phase 5 (rename support) is well out — revisit if it becomes a real pain point.

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

2. **`data/<Project>/project.json5`** — project-level metadata, JSON5 with comments allowed. Contains display name, description, default map center/zoom, the **IANA timezone** used to interpret XLSX local timestamps (§6), and one entry per **Reach**, each declaring its own `folder` (relative to the project) and its required **ATM well** (`name`, `folder` relative to the Reach, `device_serial`). Sites are *not* listed here — they're discovered by walking the Reach folder for subdirectories that contain their own `site.json5` (see §6 scanner, Phase 1).

3. **`data/<Project>/<Reach>/<Site>/site.json5`** — site-level metadata: display name, `latitude`/`longitude` (nullable — `null` until set via the Site Management UI, Phase 5), and a `wells` list. Each well entry has `name`, `folder` (relative to the site), `type` (`"in_stream"` | `"groundwater"`), `device_serial` (informational), and `paired_atm_well` (`null` = use the Reach's default ATM well).

Every folder-backed entity carries an explicit `folder` field distinct from its display `name`, so a rename in the UI doesn't have to mean a filesystem rename (or vice versa).

The Site Management UI (Phase 5) is what writes `project.json5`/`site.json5` and creates the corresponding folders — users should not need to hand-edit these files, though they can (JSON5 comments are there specifically so hand-editing stays reasonable).

Turning these files into the §5 dataclasses is **done, in Phase 1** — `catalog.py`'s `load_catalog(data_root, project_folder)` reads `project.json5` and every `Site N/site.json5` beneath it, resolves `folder` references into real paths, derives ids as `/`-joined slugs of each entity's path relative to `data_root` (e.g. `carlson-creek-restoration/lower-stream/site-1/gw-1`, matching the scheme decided in Phase 0), and resolves each well's `paired_atm_well` (or the `null` default) into a concrete ATM well id. It returns a small `Catalog` aggregate — `Catalog(project, wells, timezone)` — not just a bare `Project`: `wells` is a flat `{id: Well}` map covering *every* well including reach-level ATM wells (which `Project`/`Site` alone can't reach, since `Reach` only stores `atm_well_id` as a string), and `timezone` carries the project's IANA zone since it's config, not part of the `Project` identity dataclass in `models.py`. The scanner and, later, the calculations module both need this flat lookup. `CatalogError` is raised (not a bare `KeyError`/`ValueError`) for a missing file, invalid JSON5, a `folder` that doesn't exist on disk, or a `paired_atm_well` that doesn't resolve.

## 8. Data reorganization — done

The sample data has already been manually reorganized by the user into the agreed structure: `data/Carlson Creek Restoration/Lower Stream/{Carlson ATM, Site 1..5/{GW N, IS N}}`. This is now the canonical example/fixture set for Phase 1 development and tests. No migration script is needed — the earlier plan draft's proposed migration step is no longer necessary.

Still to do in Phase 0/1: write `project.json5` and `site.json5` for this real example project (by hand initially, or via a small one-off script), since those files don't exist yet and the scanner/UI will expect them.

## 9. Explicitly out of scope for v1

- **`.hobo` files** — binary HOBOware desktop project files, not raw data. Scanner should ignore them (not even attempt `can_handle`).
- **Trusting vendor-computed ATM/depth from XLSX** — parsed columns are discarded in favor of our own calculation (see §2, §10). Could be revisited later as a cross-check.
- **Detail data view** (Project Description: "we will define later") — Phase 6 is a placeholder until we design this together.
- **Cloud deployment** (AWS etc.) — explicitly out of scope per Project Description.
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
- Site Management UI: forms for create/edit/delete of Project/Reach/Site/Well (IS/GW/ATM), including lat/long entry (manual — no geocoding source specified) and ATM-pairing for water wells.

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
- **Phase 3** — FastAPI backend: read endpoints for tree/map/detail data, ingest trigger.
- **Phase 4** — Frontend shell: tree + Leaflet map + hover popups, wired to the Phase 3 API.
- **Phase 5** — Site Management UI: add/edit/delete Project/Reach/Site/Well, backed by new CRUD endpoints.
- **Phase 6** — Detail data view: design (with user) + implement.
- **Phase 7** — Polish pass: error-handling audit against CLAUDE.md's "errors must be handled, None must be handled by caller," cleanup, docs.

Each phase ends with passing tests before moving to the next.

## 15. Open items to revisit

- Where the XLSX-conversion IANA timezone lives if it ever needs to vary per-well rather than per-project (starting assumption, still in place: one timezone per project, in `project.json5`, implemented as `Catalog.timezone` in Phase 1).
- Whether `Button Up`/`Button Down` events (XLSX) are worth surfacing in the UI as site-visit markers — captured and stored since Phase 1, decision on UI treatment deferred to Phase 6.
- No migration framework for the SQLite schema yet (`CREATE TABLE IF NOT EXISTS` only) — fine for now, revisit if the schema needs to change under real ingested data (Phase 2+).
- `compute_all()` recomputes every non-ATM well's calculations on every run rather than tracking which wells' inputs actually changed (§10) — fine at v1 data scale, revisit if recompute time becomes noticeable.
- The real Carlson dataset never exercises the `"unknown_no_atm_data"`/`"unknown_atm_gap_too_large"` paths (full ATM coverage) — worth keeping in mind if a future real project has a gap in ATM coverage, since that's the first time the "unknown" UI treatment (Phase 6) will be seen against real data rather than synthetic tests.
- Iconography for map markers beyond "dots" (Phase 4, per Project Description — deferred by them too).
- Detail view design (Phase 6, deferred by Project Description).
- Display units/timezone preference (store UTC + source units internally regardless; decide user-facing default in Phase 4).
