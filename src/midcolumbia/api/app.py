"""FastAPI application. See Implementation Plan.md section 11.

Run with `uv run midcolumbia-serve` (see serve_cli.py) or directly via
`uv run uvicorn midcolumbia.api.app:app --reload`.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..catalog import CatalogError
from ..config import SettingsError
from .routes_ingest import router as ingest_router
from .routes_projects import router as projects_router
from .routes_readings import router as readings_router
from .routes_wells import router as wells_router

app = FastAPI(title="Mid-Columbia Fisheries Data Analysis")
app.state.last_ingest_result = None

# Local-first, single-user, no auth (section 9) - CORS is only relevant for
# the Phase 4 Vite dev server calling this API from the browser during local
# development. Revisit if/when a non-local deployment is ever considered.
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
