# Mid-Columbia Fisheries Data Analysis — Implementation Plan

Status: draft v5 — **Phase 0 complete**; agreed direction for Phases 1–3, later phases sketched and open to revision as we build.

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
- **Marker rows carry no sensor reading** (`Coupler Detached`/`Coupler Attached`/`Stopped`/`End Of File` with blank Abs Pres/Temp) — these are deployment/download boundary events, not readings.
- **The stated UTC offset is fixed per file, not DST-aware.** Headers read `"Date Time, GMT-08:00"`. Verified: a file spanning the March 8 spring-forward has continuous hourly timestamps with no gap — the logger/export never adjusts for DST, it just stamps everything with whatever fixed offset was configured at deployment. The parser must apply that literal offset to every row in the file.
- UTF-8 BOM at the start of the file (`utf-8-sig` codec). Column headers embed the logger's serial number (e.g. `"Abs Pres, kPa (LGR S/N: 22332695, ...)"`) — match by prefix (`"Abs Pres"`, `"Temp"`, `"Date Time"`), not exact string.

### XLSX (HOBOconnect app export, MX20L loggers) — used by all IS wells
- **Each download is a full cumulative history dump from deployment start, not an incremental delta.** Verified directly: the second download for Site 1's IS well starts at row 2 with `2026-02-26 11:00:00` — the original deployment start — not where the first download left off. Every later download re-includes every earlier reading. This makes upsert-by-`(well_id, timestamp, parameter)` a **required** part of ingestion, not just a defensive nicety — the XLSX handler will "reparse and overwrite" every time a well gets a new download, while the CSV handler mostly just appends.
- **Timestamps are true local wall-clock time with real DST transitions**, not a fixed offset. Verified directly by decoding the Excel serial dates: the same file has a row at `2026-03-08 01:00:00` followed immediately by a row at `2026-03-08 03:00:00` — a genuine spring-forward gap (2 AM skipped), which only happens with DST-aware local time. Converting to UTC requires the actual IANA timezone (e.g. `America/Los_Angeles`) via `zoneinfo`, not a per-file fixed offset like the CSV format. The header/filename's `PST`/`PDT` label is just a hint of which zone, not the offset to use for the whole file. Need an explicit decision for the ambiguous repeated hour at fall-back (see §15).
- Dates are stored as Excel serial numbers (days since 1899-12-30), not text — needs `openpyxl` (or manual XML parsing) rather than a CSV-style text parser.
- It's a multi-sheet workbook: a data sheet plus metadata sheets (device info, deployment settings, app info). Only the data sheet is relevant to ingestion.
- **The data sheet already includes an `ATM, kPa` column and pre-computed `depth_m`/`depth_ft` columns** — this device format does its own barometric compensation, using some ATM source we don't control or know the provenance of. **Decision: v1 ignores these columns.** We extract only `Absolute Pressure` → `WATER_PRESSURE` and `Temperature` → `WATER_TEMPERATURE` from XLSX (matching what the CSV handler extracts from GW/ATM wells), and always compute depth ourselves in the Calculations module using the reach's actual ATM well. Rationale: consistency across well types (GW wells have no vendor depth to fall back on), and not wanting to depend on an unverified vendor computation. This can be revisited later as a cross-validation check, not a blocker now.
- Event/marker columns use different vocabulary than CSV: `Host Connected`, `Started`, `Button Up`, `Button Down`, `End of File`, `Logged` (vs. CSV's `Coupler Detached`/`Attached`, `Stopped`, `End Of File`). Both map into the same `DeploymentEvent.kind` field but need per-handler normalization (see §6).

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
      ingestion/
        base.py                 # LoggerHandler abstract base class + registry
        hoboware_csv.py          # CSV handler (GW + ATM wells)
        hoboconnect_xlsx.py      # XLSX handler (IS wells)
        scanner.py               # walks data/ tree, finds new/changed files
      calculations/
        base.py                  # Calculation ABC + registry
        water_depth.py           # ATM + water pressure -> depth (used for both GW and IS wells)
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
    fixtures/                  # symlink or copy of representative sample CSV/XLSX files
    test_ingestion_hoboware_csv.py
    test_ingestion_hoboconnect_xlsx.py
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

**Handler abstraction** (`ingestion/base.py`):

```python
class LoggerHandler(ABC):
    @abstractmethod
    def can_handle(self, path: Path) -> bool: ...

    @abstractmethod
    def parse(self, path: Path, well_type: WellType) -> tuple[list[Reading], list[DeploymentEvent]]: ...
```

A registry (list of handlers, tried in order) lets us add new device types later without touching the scanner or storage layer.

**CSV handler** (`ingestion/hoboware_csv.py`) — HOBOware desktop export, used by GW and ATM wells:
- Skip the `"Plot Title: ..."` line; read with `encoding="utf-8-sig"`.
- Parse the header row; match `Date Time` (extract the `GMT±HH:MM` offset from the column name), `Abs Pres`, `Temp`, and marker columns, by prefix match.
- For data rows with `Abs Pres`/`Temp` present: emit `Reading`s — pressure as `AIR_PRESSURE`/`WATER_PRESSURE` and temp as `AIR_TEMPERATURE`/`WATER_TEMPERATURE`, chosen by the well's `WellType` (`ATMOSPHERIC` → air, `GROUNDWATER`/`IN_STREAM` → water).
- For rows where a marker column reads `"Logged"`, emit a `DeploymentEvent` instead, with `kind` normalized from the column name (`Coupler Detached` → `logger_launched`, `Coupler Attached` → `logger_retrieved`, `Stopped` → `stopped`, `End Of File` → `end_of_file`).
- Apply the file's fixed UTC offset (parsed from the header) to every row — never recompute via calendar DST rules.

**XLSX handler** (`ingestion/hoboconnect_xlsx.py`) — HOBOconnect app export, used by IS wells:
- Open with `openpyxl` (read-only mode), locate the data sheet (first sheet in the samples seen so far — confirm this holds generally once we have more real files, don't hard-code by name if avoidable).
- Match `Absolute Pressure` → `WATER_PRESSURE`, `Temperature` → `WATER_TEMPERATURE`, by header prefix. Explicitly skip `ATM, kPa`, `depth_m`, `depth_ft` columns (see §2 rationale).
- Convert each row's Excel serial date to a naive local datetime, then localize using the project/reach's configured IANA timezone (see §7) via `zoneinfo`, then convert to UTC. Do not trust the header's `PST`/`PDT` label as a fixed offset — it's descriptive, not authoritative (see §2).
- Marker columns (`Host Connected`, `Started`, `Button Up`, `Button Down`, `End of File`) map to `DeploymentEvent`s with normalized `kind` values consistent with the CSV handler's vocabulary where the semantics match (`Started` → `logger_launched`, `Host Connected` → `logger_retrieved`, `End of File` → `end_of_file`); `Button Up`/`Button Down` are recorded as their own `kind` (likely a field technician's manual site-visit marker) rather than dropped, pending a decision on whether they're useful to surface in the UI.
- Because every download is a full cumulative re-dump (§2), this handler will typically produce readings that mostly already exist — rely on the storage layer's upsert-by-`(well_id, timestamp, parameter)` to make this a no-op for unchanged rows rather than trying to diff/skip in the handler itself.

**Scanner** (`ingestion/scanner.py`):
- Walks `data/<Project>/<Reach>/{<ATM well folder>, <Site>/<Well folder>}/*` using the well list from `site.json5`/the project registry (not by guessing structure from folder names).
- Dispatches each file to the first handler whose `can_handle()` matches (by extension at minimum; `.hobo` matches no handler and is ignored).
- Compares mtime + size (or hash, for robustness) against what's recorded in SQLite; only parses new/changed files.
- Feeds parsed `Reading`/`DeploymentEvent` lists to the storage layer's upsert.

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

Turning these files into the §5 dataclasses (resolving `folder` references into full paths, deriving `id`s, resolving `paired_atm_well` names into ids) is a **Phase 1** job, done by the ingestion scanner's catalog-loading step — Phase 0 only needed the files to exist and be valid, which is what `tests/test_sample_data.py` checks.

## 8. Data reorganization — done

The sample data has already been manually reorganized by the user into the agreed structure: `data/Carlson Creek Restoration/Lower Stream/{Carlson ATM, Site 1..5/{GW N, IS N}}`. This is now the canonical example/fixture set for Phase 1 development and tests. No migration script is needed — the earlier plan draft's proposed migration step is no longer necessary.

Still to do in Phase 0/1: write `project.json5` and `site.json5` for this real example project (by hand initially, or via a small one-off script), since those files don't exist yet and the scanner/UI will expect them.

## 9. Explicitly out of scope for v1

- **`.hobo` files** — binary HOBOware desktop project files, not raw data. Scanner should ignore them (not even attempt `can_handle`).
- **Trusting vendor-computed ATM/depth from XLSX** — parsed columns are discarded in favor of our own calculation (see §2, §10). Could be revisited later as a cross-check.
- **Detail data view** (Project Description: "we will define later") — Phase 6 is a placeholder until we design this together.
- **Cloud deployment** (AWS etc.) — explicitly out of scope per Project Description.
- **Auth / multi-user** — v1 is local-first, single user, no auth.

## 10. Calculations module

- Each calculation is a self-contained, named unit (not buried inline) exposing: required input parameter types, output type/unit, and a `compute()` function. Registered similarly to the ingestion handlers.
- **Water depth — finalized formula and algorithm:**
  - `depth = (well_pressure - atm_pressure) * 0.334553`, where `well_pressure` is a `WATER_PRESSURE` reading (kPa) from an IS or GW well, `atm_pressure` is an `AIR_PRESSURE` reading (kPa) from that well's paired ATM well, and the result unit is **feet** (0.334553 is the kPa→ft-of-water conversion constant). This applies uniformly to GW and IS wells — vendor-provided pressure/depth values in the source files are never used (see §2, §9), only the raw pressure we ingest ourselves.
  - For each `WATER_PRESSURE` reading, find the **closest-in-time** `AIR_PRESSURE` reading from the paired ATM well (nearest neighbor by absolute time difference, either before or after — not interpolation between two bracketing points). Compute depth from that pair, **provided the gap is within `settings.calculations.max_atm_gap_hours`** (user-configurable, default **12 hours** — decided and implemented in Phase 0's `config.py`).
  - **If the paired ATM well has no readings at all to pair with, or the closest one is further away than `max_atm_gap_hours`**, the depth for that timestamp is explicitly marked **unknown** rather than omitted or computed from a too-distant/bad/default value. "Unknown" is a first-class result, not an absence of a result — the UI should be able to show "no depth available for this period" distinctly from "no pressure reading at all," and ideally distinguish "no ATM data at all" from "ATM data exists but too far away" for troubleshooting.
  - Output representation is a distinct type from raw `Reading`s, since it's derived and can carry an unknown state:
    ```python
    @dataclass(frozen=True)
    class CalculatedReading:
        well_id: str
        timestamp_utc: datetime
        calculation: str            # e.g. "water_depth"
        value: float | None         # None when unknown
        unit: str                   # "ft" for water_depth
        status: str                 # "ok" | "unknown_no_atm_data" | "unknown_atm_gap_too_large"
    ```
- Results are stored (not recomputed on every request) but must be invalidated/recomputed when their input readings change (e.g., new data ingested for that well or its paired ATM well).

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
- `uv run pytest` must pass before any phase is considered done, per CLAUDE.md.

## 14. Phased milestones

- **Phase 0 — done.** `uv init --package` scaffolding (`midcolumbia` package under `src/`, Python ≥3.13, `json5` + `pytest` deps); `models.py` with the §5 dataclasses; `settings.json` + `config.py` loader (raises `SettingsError` on missing/invalid config rather than silently defaulting); `project.json5`/`site.json5` written and validated for the real Carlson Creek Restoration example (§7); `.gitignore`; 13 passing tests (`uv run pytest`) covering the dataclasses, the settings loader (including error paths), and that the JSON5 files agree with the actual folder layout and file types on disk. Deliberately **not** built yet: the JSON5-to-dataclass catalog loader and the ingestion handlers themselves — those belong to Phase 1, next.
- **Phase 1** — Ingestion pipeline (CSV + XLSX handlers) + a catalog loader (`project.json5`/`site.json5` → §5 dataclasses, resolving `folder`/`paired_atm_well` references into ids) + SQLite storage, tested against the real Carlson sample data.
- **Phase 2** — Calculations module (water depth), tested.
- **Phase 3** — FastAPI backend: read endpoints for tree/map/detail data, ingest trigger.
- **Phase 4** — Frontend shell: tree + Leaflet map + hover popups, wired to the Phase 3 API.
- **Phase 5** — Site Management UI: add/edit/delete Project/Reach/Site/Well, backed by new CRUD endpoints.
- **Phase 6** — Detail data view: design (with user) + implement.
- **Phase 7** — Polish pass: error-handling audit against CLAUDE.md's "errors must be handled, None must be handled by caller," cleanup, docs.

Each phase ends with passing tests before moving to the next.

## 15. Open items to revisit

- Where the XLSX-conversion IANA timezone lives if it ever needs to vary per-well rather than per-project (Phase 0/1 — starting assumption is one timezone per project, in `project.json5`).
- How to handle the DST fall-back ambiguous hour (the repeated 1–2 AM local hour) for XLSX timestamp conversion — needs an explicit, documented rule (e.g. assume the first occurrence / standard time) rather than leaving it to `zoneinfo` defaults unexamined (Phase 1).
- Whether `Button Up`/`Button Down` events (XLSX) are worth surfacing in the UI as site-visit markers (Phase 1/6).
- Whether the XLSX data sheet is reliably always the first sheet, once we see files from more devices/exports (Phase 1).
- Iconography for map markers beyond "dots" (Phase 4, per Project Description — deferred by them too).
- Detail view design (Phase 6, deferred by Project Description).
- Display units/timezone preference (store UTC + source units internally regardless; decide user-facing default in Phase 4).
