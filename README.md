# Mid-Columbia Fisheries Data Analysis

Data ingestion and analysis tool for Mid-Columbia Fisheries stream restoration
monitoring — see [Project Description.md](Project%20Description.md) for what
this is and why, and [Implementation Plan.md](Implementation%20Plan.md) for
how it's built.

## Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** — manages the Python virtual environment and dependencies
- **Node.js 18+ and npm** — for the frontend

macOS: `brew install uv node`
Windows: `winget install astral-sh.uv OpenJS.NodeJS.LTS` (or install each from
its own site: [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/),
[nodejs.org](https://nodejs.org/))
Linux: use your distro's package manager, or uv's install script at the link above.

## Running locally (development)

Two processes, in two terminals:

```
uv run midcolumbia-serve        # backend API, http://127.0.0.1:8000
```

```
cd web
npm install                     # first time only
npm run dev                     # frontend dev server, http://localhost:5173
```

Open `http://localhost:5173` — the dev server proxies `/api/*` to the backend
(`web/vite.config.ts`). The commands are identical on macOS, Linux, and
Windows (PowerShell or cmd) — none of this project's tooling relies on a
Unix-only shell.

The backend automatically re-scans `data/` and recomputes calculations every
time it starts, so new logger files just need to be dropped into the right
site's folder before the next restart — nothing to run by hand.

## Running as a single process (matches the Render deployment)

```
cd web && npm run build
uv run midcolumbia-serve
```

This builds the frontend to `web/dist/`, which the backend then serves
directly alongside the API on one port (`$PORT`, defaulting to 8000) — see
Implementation Plan.md §16 for the full deployment writeup.

## Running the tests

```
uv run pytest
```

## Cross-platform notes

The codebase uses `pathlib` throughout (no hardcoded `/`-style paths), matches
file extensions case-insensitively, and has no dependency on a Unix-only
Python module or shell. `.gitattributes` marks the real sample logger files
(`.xlsx`, `.hobo`) and image assets as binary, so a Windows clone with git's
default `core.autocrlf=true` can't have them silently corrupted by
line-ending conversion — the CSV parser is separately robust to either line
ending on its own (`newline=""` + `csv.reader`, not manual line-splitting).

This hasn't been smoke-tested on an actual Windows machine as part of this
repo's own verification process (everything else in Implementation Plan.md
was verified by literally running it, but no Windows environment was
available to do that here) — if something doesn't work as described above,
that's the first thing to double-check.
