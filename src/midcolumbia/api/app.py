"""FastAPI application. See Implementation Plan.md section 11.

Run with `uv run midcolumbia-serve` (see serve_cli.py) or directly via
`uv run uvicorn midcolumbia.api.app:app --reload`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..catalog import CatalogError
from ..config import SettingsError, load_settings
from ..storage import db
from .deps import get_settings
from .routes_ingest import router as ingest_router
from .routes_ingest import run_ingest_and_compute
from .routes_projects import router as projects_router
from .routes_readings import router as readings_router
from .routes_wells import router as wells_router

logger = logging.getLogger("midcolumbia")
# Neither Python nor uvicorn attaches a handler to an arbitrary named logger
# by default - found by actually running the container and noticing the
# startup-ingest log line below never appeared. Scoped to our own logger
# only (not logging.basicConfig(), which would also affect uvicorn's own
# already-configured loggers and risk double-printed lines).
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Project Description: "next time the app is run new data is
    # automatically picked up and added to existing data for each site."
    # Runs once at process startup - covers both a fresh Render deploy (the
    # committed data/ has to be ingested from scratch into an ephemeral
    # SQLite file every boot, see the deployment notes in the plan) and a
    # local restart picking up files dropped in since the last run.
    #
    # Respects app.dependency_overrides for get_settings (if a test has set
    # one) rather than always loading the real settings.json - lifespan runs
    # outside FastAPI's per-request Depends() resolution, so this is a
    # manual lookup of the same override dict tests already populate.
    settings_provider = app.dependency_overrides.get(get_settings, load_settings)
    try:
        settings = settings_provider()
        conn = db.connect(settings.database_path)
        try:
            result = run_ingest_and_compute(settings, conn)
        finally:
            conn.close()
        app.state.last_ingest_result = result
        logger.info(
            "startup ingest: %d files ingested, %d readings, %d events, %d error(s)",
            result.files_ingested,
            result.readings_ingested,
            result.events_ingested,
            len(result.errors),
        )
        if result.errors:
            for error in result.errors:
                logger.warning("startup ingest error: %s", error)
    except Exception:
        # A broken scan at boot shouldn't take the whole service down - it
        # should still come up and serve whatever was already ingested
        # (e.g. from a previous run's persisted DB) rather than fail to
        # start entirely. CLAUDE.md: "errors must be handled, not ignored" -
        # logged loudly, not silently swallowed, just not fatal.
        logger.exception("startup ingest failed - continuing to serve with existing data")
    yield


app = FastAPI(title="Mid-Columbia Fisheries Data Analysis", lifespan=lifespan)
app.state.last_ingest_result = None

# Local-first, single-user, no auth (section 9) - CORS is only relevant for
# the Phase 4 Vite dev server calling this API from a different origin during
# local development. The Render deployment serves the frontend from the same
# origin as the API (see the static mount below), so it never needs this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects_router, prefix="/api")
app.include_router(wells_router, prefix="/api")
app.include_router(readings_router, prefix="/api")
app.include_router(ingest_router, prefix="/api")


def _config_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # CatalogError/SettingsError mean the app's own configuration is broken
    # (bad settings.json, malformed project.json5, ...) - a 500 with the real
    # message is more useful here than FastAPI's generic unhandled-exception
    # response, per CLAUDE.md's "errors must be handled, not just ignored."
    return JSONResponse(status_code=500, content={"detail": str(exc)})


app.add_exception_handler(CatalogError, _config_error_handler)
app.add_exception_handler(SettingsError, _config_error_handler)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# Serves the built frontend (web/dist, produced by `npm run build`) so a
# single process/port can serve both the API and the UI - what Render's
# single-start-command web service needs (see the plan's Deployment
# section), and also usable locally once the frontend has been built.
# Mounted last, and only if the build actually exists, so a fresh clone
# that hasn't run `npm run build` yet still serves the API fine on its own.
_WEB_DIST = Path("web/dist")
if _WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_DIST, html=True), name="frontend")
