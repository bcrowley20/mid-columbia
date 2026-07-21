# Multi-stage build: Node to compile the Vite frontend, then a Python image
# that serves it (as static files, see api/app.py) alongside the API. Used
# by Render (see render.yaml) - also runnable locally to test the exact
# deploy: `docker build -t midcolumbia . && docker run -p 8000:8000 midcolumbia`.
# See Implementation Plan.md's Deployment section for the reasoning.

FROM node:22-slim AS frontend-builder
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


FROM python:3.13-slim AS runtime
RUN pip install --no-cache-dir uv
WORKDIR /app

# Python deps first (better layer caching - these change less often than
# data/ or the frontend).
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

COPY settings.json ./
COPY data/ ./data/
COPY --from=frontend-builder /app/web/dist ./web/dist

# Render sets $PORT at runtime; default 8000 lets `docker run` work without
# it too. Shell form so ${PORT} actually expands. --no-sync: the venv built
# above is exactly what should run - without it, `uv run` re-checks/re-syncs
# on every container start and (found by actually running the image, not
# just reasoning about it) pulls in the dev dependency group again, undoing
# the --no-dev from the build step and adding needless startup latency.
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync uvicorn midcolumbia.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
