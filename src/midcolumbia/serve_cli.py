"""Runs the FastAPI server. Usage: uv run midcolumbia-serve

Binds 0.0.0.0 and reads $PORT (defaulting to 8000) - matching how the Docker
image's CMD invokes uvicorn for Render, so this is also the closest local
equivalent to "start it the way Render will" (see Implementation Plan.md's
Deployment section). Keeps --reload, unlike the Docker CMD, since that's a
local-dev-only convenience with no equivalent concern in a built container.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("midcolumbia.api.app:app", host="0.0.0.0", port=port, reload=True)


if __name__ == "__main__":
    main()
