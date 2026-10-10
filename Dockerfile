# Web UI: built here, served by the API at /ui. Nothing Node runs in the final image.
FROM node:24-slim AS ui
WORKDIR /ui
# Packages first, so a code change does not reinstall them.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
    gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies come from uv.lock. The virtualenv lives outside /app so the
# entrypoint's chown of /app stays fast.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini .
COPY migrations/ migrations/
COPY src/ src/
COPY entrypoint.sh /entrypoint.sh
COPY --from=ui /ui/dist /app/ui

RUN mkdir -p data/cache data/geonames logs

ENV PUID=1000
ENV PGID=1000
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV UI_DIR=/app/ui

EXPOSE 9500

# 503 only when the database is unreachable; healthy while datasets build.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9500/health', timeout=4)"]

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "geonames_server.api.app:app", "--host", "0.0.0.0", "--port", "9500"]
