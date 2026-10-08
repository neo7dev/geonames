# GeoNames Server

GeoNames Server is a FastAPI + PostgreSQL service that mirrors core GeoNames dumps into local database tables and exposes them through queryable REST endpoints.

At startup, the app:
1. Verifies database connectivity.
2. Runs migrations.
3. Seeds dataset metadata rows.
4. Checks remote GeoNames metadata (etag/last-modified).
5. Rebuilds datasets that need a full refresh.
6. Starts a scheduler for daily sync checks.

## What Data This Service Hosts

The API serves these datasets:
- `allcountries`
- `cities500`
- `cities1000`
- `cities5000`
- `cities15000`
- `modifications` (daily changed rows)
- `deletions` (daily deleted rows)
- dataset metadata + sync status endpoints

## How Endpoints Work

Base URL (Docker default):
- `http://localhost:9500`

Interactive docs:
- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- OpenAPI JSON: `GET /openapi.json`

### 1) Dataset Endpoints

These route groups exist and all return GeoNames-shaped records:
- `/allcountries`
- `/cities500`
- `/cities1000`
- `/cities5000`
- `/cities15000`

Each dataset exposes:

1. List endpoint
	- `GET /<dataset>/`
	- Query params:
	  - `limit` (default `100`, min `1`, max controlled by `MAX_RESULTS`)
	  - `offset` (default `0`)
	- Behavior:
	  - Returns a paginated list ordered by repository defaults.

2. Filter endpoint
	- `GET /<dataset>/filter`
	- Common filters include:
	  - identity/text: `geonameid`, `name`, `asciiname`, `alternatenames`
	  - geo/admin: `feature_class`, `feature_code`, `country_code`, `cc2`, `admin1_code`, `admin2_code`, `admin3_code`, `admin4_code`, `timezone`
	  - numeric: `population`, `delta`, `min_population`, `max_population`, `elevation`, `dem`
	  - dates: `modification_date`, `modified_after`, `modified_before`, `synced_after`, `synced_before`
	  - pagination/sort: `limit`, `offset`, `order_by`
	- Behavior details:
	  - If `limit` is omitted, the API uses `100`.
	  - `limit` is capped by `MAX_RESULTS`.
	  - If `order_by` is omitted, default is descending geoname id (`-geonameid`).
	  - Validation rule: you cannot combine `population + delta` with `min_population/max_population`.

3. Record-by-id endpoint
	- `GET /<dataset>/{geonameid}`
	- Behavior:
	  - Returns a single row when present.
	  - Returns `404` if missing.

Example:

```bash
curl "http://localhost:9500/cities15000/filter?country_code=US&min_population=1000000&order_by=-population&limit=20"
```

### 2) Modifications Endpoints

Route group: `/modifications`

1. `GET /modifications/`
	- Basic pagination (`limit`, `offset`).

2. `GET /modifications/filter`
	- Same filter model as dataset filter endpoint.

3. `GET /modifications/{geonameid}`
	- Returns all modification rows for one geoname id.
	- Supports `limit` and `offset`.

4. `GET /modifications/date/{modification_date}`
	- Returns modification rows for a specific date (`YYYY-MM-DD`).
	- Supports `limit` and `offset`.

### 3) Deletions Endpoints

Route group: `/deletions`

1. `GET /deletions/`
	- Pagination with `limit` and `offset`.

2. `GET /deletions/filter`
	- Filters include `deletion_date`, `geonameid`, `name`, `reason`, plus `limit`, `offset`, `order_by`.

3. `GET /deletions/{geonameid}`
	- Returns a single deletion record if present.
	- Returns `404` if missing.

### 4) Dataset Metadata Endpoints

Route group: `/metadata`

1. `GET /metadata/`
	- List metadata records with pagination.

2. `GET /metadata/out-of-sync`
	- Returns datasets where local state differs from remote metadata.

3. `GET /metadata/needs-rebuild`
	- Returns datasets flagged for full rebuild.

4. `GET /metadata/filter`
	- Filter by `dataset`, `out_of_sync`, `needs_rebuild`, etag fields, and modified-time windows.

5. `GET /metadata/{dataset}`
	- Returns one dataset metadata record.
	- Returns `404` if dataset key does not exist.

### 5) Sync Endpoints

Route group: `/sync`

1. Start sync
	- `POST /sync/`
	- Behavior:
	  - Starts background sync and returns `202 Accepted` with `sync_id`.
	  - Returns `409` if a sync is already running.

2. Track sync status
	- `GET /sync/{sync_id}`
	- Behavior:
	  - Returns lifecycle fields such as `status`, `current_step`, timestamps, per-dataset statuses, and optional error.
	  - Returns `404` for unknown sync ids.

Example:

```bash
# Start a sync
curl -X POST http://localhost:9500/sync/

# Then poll status with returned sync_id
curl "http://localhost:9500/sync/<sync_id>"
```

## Daily Sync and Update Logic

The daily update pipeline:
1. Re-checks remote metadata for configured datasets.
2. Downloads `modifications-YYYY-MM-DD.txt` and `deletes-YYYY-MM-DD.txt` when available.
3. Loads them into staging tables.
4. Applies changes to out-of-sync datasets:
	- upsert from `modifications`
	- delete from `deletions`
5. Uses population thresholds per dataset:
	- `allcountries`: no population filter
	- `cities500`: `population >= 500`
	- `cities1000`: `population >= 1000`
	- `cities5000`: `population >= 5000`
	- `cities15000`: `population >= 15000`
6. Syncs metadata fields so `out_of_sync` clears once local and remote values match.

## Docker Quickstart

### Prerequisites

- Docker Engine (or Docker Desktop)
- Docker Compose v2 (`docker compose`)

### 1) Configure Environment

Create `.env` from example:

```bash
cp .env.example .env
```

Minimum variables to verify in `.env`:

```dotenv
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=geonames
GEONAMES_URL=https://download.geonames.org/export/dump/
ENABLED_DATASETS=cities15000,cities5000,cities1000,cities500,allcountries
MAX_RESULTS=1000
SYNC_SCHEDULE_HOURS=3,15
FORCE_REBUILD=false
LOG_LEVEL=INFO
```

### 2) Build and Start

```bash
docker compose up --build
```

This starts:
- `app` (FastAPI on port `9500`)
- `postgres` (internal network)

### 3) Verify Service Health

Open:
- `http://localhost:9500/docs`

Quick API check:

```bash
curl "http://localhost:9500/allcountries/?limit=5"
```

### 4) Run in Background

```bash
docker compose up -d
docker compose logs -f app
```

### 5) Stop and Clean Up

```bash
docker compose down
```

Remove volumes too (deletes local DB/data/log volumes):

```bash
docker compose down -v
```

## Configuration Reference

Main runtime settings (environment variables):
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_DB`
- `GEONAMES_URL`
- `ENABLED_DATASETS`
- `MAX_RESULTS`
- `SYNC_SCHEDULE_HOURS`
- `FORCE_REBUILD`
- `LOG_LEVEL`

Notes:
- `MAX_RESULTS` caps list/filter endpoint page size.
- `FORCE_REBUILD=true` forces a full rebuild on next startup.
- `SYNC_SCHEDULE_HOURS` is interpreted in UTC.

## Data Source Attribution

This project uses data from GeoNames:
- Source: https://www.geonames.org
- Download endpoint used by default: https://download.geonames.org/export/dump/

GeoNames extract files state that the dataset is licensed under:
- Creative Commons Attribution 4.0 (CC BY 4.0)
- https://creativecommons.org/licenses/by/4.0/

Please keep proper attribution to GeoNames in downstream uses of this data and review their terms and documentation:
- https://www.geonames.org
- https://www.geonames.org/datasources/

## Development Notes

- API implementation lives under `src/geonames_server/api`.
- Ingestion and sync logic lives under `src/geonames_server/ingestion`.
- Alembic migrations are under `migrations/`.
- A generated OpenAPI snapshot is in `openapi.yaml`.
