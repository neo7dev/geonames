# Web UI: built here, served by the API at /ui. Nothing Node runs in the final image.
FROM node:24-slim AS ui
WORKDIR /ui
# Packages first, so a code change does not reinstall them.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Runtime dependencies only, pinned in requirements.txt (exported from uv.lock by
# make requirements; make requirements-check keeps the two in step). No uv here.
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

# The app reads its version from pyproject.toml.
COPY pyproject.toml .
COPY alembic.ini .
COPY migrations/ migrations/
COPY src/ src/
COPY entrypoint.sh /entrypoint.sh
COPY --from=ui /ui/dist /app/ui

RUN mkdir -p data/cache data/geonames logs

ENV PUID=1000
ENV PGID=1000
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV UI_DIR=/app/ui

EXPOSE 9500

# 503 only when the database is unreachable; healthy while datasets build.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9500/health', timeout=4)"]

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "geonames_server.api.app:app", "--host", "0.0.0.0", "--port", "9500"]
