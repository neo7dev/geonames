# GeoNames Server

GeoNames Server is a FastAPI + PostgreSQL service that mirrors core GeoNames dumps into local database tables and exposes them through queryable REST endpoints.

At startup, the app:
1. Verifies database connectivity.
2. Runs migrations.
3. Seeds dataset metadata rows.
4. Starts serving requests. The remaining steps run in the background; `GET /health` reports their progress.
5. Checks remote GeoNames metadata (etag/last-modified).
6. Rebuilds datasets that need a full refresh.
7. Starts a scheduler for daily sync checks.

## What Data This Service Hosts

The API serves these datasets:

| Dataset key | Contents | Source file |
|---|---|---|
| `allcountries` | Every place in GeoNames (about 13M rows, ~420 MB zipped) | https://download.geonames.org/export/dump/allCountries.zip |
| `cities500` | Cities with population > 500, or seats of administrative divisions | https://download.geonames.org/export/dump/cities500.zip |
| `cities1000` | Cities with population > 1,000 | https://download.geonames.org/export/dump/cities1000.zip |
| `cities5000` | Cities with population > 5,000 | https://download.geonames.org/export/dump/cities5000.zip |
| `cities15000` | Cities with population > 15,000 (about 34k rows) | https://download.geonames.org/export/dump/cities15000.zip |

Choose which datasets to load with `ENABLED_DATASETS` (comma-separated, case-insensitive, e.g. `cities15000,cities5000,allCountries`). Dataset definitions live in `src/geonames_server/ingestion/registry.py`.

Daily update files, applied to every enabled dataset:
- `modifications` (daily changed rows): `https://download.geonames.org/export/dump/modifications-YYYY-MM-DD.txt`
- `deletions` (daily deleted rows): `https://download.geonames.org/export/dump/deletes-YYYY-MM-DD.txt`

Also exposed: dataset metadata and sync status endpoints.

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

Generate the Supabase secrets and replace every `replace-with-make-supabase-keys` value in `.env` with the output (run it once; the anon and service keys are signed with that run's `JWT_SECRET`):

```bash
make supabase-keys
```

Also set the public URLs (`SUPABASE_PUBLIC_URL`, `API_EXTERNAL_URL`, `SITE_URL`) and the SMTP settings for confirmation emails. Keep `POSTGRES_USER=postgres`, `POSTGRES_HOST=db` and `POSTGRES_DB=postgres`: Supabase expects that database name.

### 2) Build and Start

```bash
docker compose up --build
```

This starts:
- `app` (FastAPI on port `9500`)
- `db` (Supabase Postgres 17, internal network). Holds geonames data and Supabase auth
- `auth` (Supabase Auth: signup, login, OAuth), `meta` and `studio`
- `kong` on port `8000`: Supabase Studio admin dashboard at `/` (basic auth `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`) and the Auth API at `/auth/v1`
- `redis` (internal network) for rate limiting and quotas

Supabase config (Kong routes, db init scripts) lives in `supabase/`. It is a trimmed copy of the upstream self-hosted stack; PostgREST, Realtime, Storage, Edge Functions and Analytics are not deployed.

### 3) Verify Service Health

The API answers as soon as migrations finish; datasets download and load in the background. Check progress with:

```bash
curl "http://localhost:9500/health"
```

`status` is `ok` once every enabled dataset is built, `degraded` while datasets are still building (listed in `datasets.pending`), if the background startup failed, or if Redis is unreachable, and `error` (HTTP 503) only when the database is unreachable. The container's healthcheck uses this endpoint, so it stays healthy while a large dataset such as `allcountries` loads.

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

Remove volumes too (deletes the database, including Supabase auth users, plus data/log volumes):

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
- `POSTGRES_SCHEMA` (default `geonames`)
- `REDIS_URL` (e.g. `redis://redis:6379/0`; reported by `/health` when set)
- `GEONAMES_URL`
- `ENABLED_DATASETS`
- `MAX_RESULTS`
- `SYNC_SCHEDULE_HOURS`
- `FORCE_REBUILD`
- `LOG_LEVEL`

Notes:
- `MAX_RESULTS` caps list/filter endpoint page size.
- `FORCE_REBUILD=true` forces a full rebuild on next startup. It drops the `POSTGRES_SCHEMA` schema, so it never touches Supabase's own tables.
- All geonames tables, including `alembic_version`, live in the `POSTGRES_SCHEMA` schema, not Supabase's `public` schema. Supabase grants its API roles (`anon`, `authenticated`) access to every table in `public`, which is what Studio's security advisor flags. Databases from older versions are moved into the schema automatically on the next startup.
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

### Simulated downloads in development

In the devcontainer, `.env.dev` sets `GEONAMES_URL` to `http://slow-download-server:80/`, so the app does not download from geonames.org. To simulate a real download, the `slow-download-server` service (nginx) serves the local zipped files from `gostatic/` at a throttled speed:

- Fill `gostatic/` from geonames.org with `make download-datasets`. It lists the datasets with their sizes and defaults to your `ENABLED_DATASETS`. It then asks whether to also fetch the current daily `modifications-YYYY-MM-DD.txt` / `deletes-YYYY-MM-DD.txt` pair, defaulting to no (GeoNames only publishes the latest day's pair). Files keep the geonames.org names and Last-Modified dates, and files that haven't changed are skipped. Non-interactive: `make download-datasets DATASETS=cities15000,cities5000` (add `DAILY=yes` to include the daily pair) (`DATASETS=all` for everything). The source is the `GEONAMES_DUMP_URL` Makefile variable (default `https://download.geonames.org/export/dump/`, also used by `make remote-etag`); override it to use a mirror, e.g. `make download-datasets GEONAMES_DUMP_URL=https://mirror.example.com/dump/`. `gostatic/` is git-ignored.
- Change the download speed with `make download-speed` (interactive) or `make download-speed RATE=500k`. The value is nginx's `limit_rate` in `.devcontainer/nginx.conf`, in bytes per second per connection (`500k`, `2m`, `0` = unlimited; default `10m`). slow-download-server reloads within 2 seconds. Lower it to test the downloader's retry and timeout handling.
- The same folder is also served without throttling by the `gostatic` service at http://localhost:3002.
- To download from geonames.org instead, set `GEONAMES_URL = "https://download.geonames.org/export/dump/"` in `.env`.
