# GeoNames Server

GeoNames Server is a FastAPI + PostgreSQL service that mirrors core GeoNames dumps into local database tables and exposes them through queryable REST endpoints. Users sign up in its [web UI](#web-ui) to get API keys and see their usage.

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

Interactive docs (public; paste your API key into Swagger's **Authorize** button to try requests):
- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- OpenAPI JSON: `GET /openapi.json`

Every data endpoint needs an API key in the `X-API-Key` header (see [Authentication](#authentication)). Without a valid key it answers `401`. `/health` and the docs need no key.

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
curl -H "X-API-Key: $API_KEY" "http://localhost:9500/cities15000/filter?country_code=US&min_population=1000000&order_by=-population&limit=20"
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

Route group: `/sync`. Admins only: a key whose role is not an admin role gets `403`.

1. Start sync
	- `POST /sync/`
	- Behavior:
	  - Starts background sync and returns `202 Accepted` with `sync_id`.
	  - Returns `409` if a sync is already running, or while startup is still checking and rebuilding datasets.

2. Track sync status
	- `GET /sync/{sync_id}`
	- Behavior:
	  - Returns lifecycle fields such as `status`, `current_step`, timestamps, per-dataset statuses, and optional error.
	  - Returns `404` for unknown sync ids.

Example:

```bash
# Start a sync
curl -X POST -H "X-API-Key: $API_KEY" http://localhost:9500/sync/

# Then poll status with returned sync_id
curl -H "X-API-Key: $API_KEY" "http://localhost:9500/sync/<sync_id>"
```

## Web UI

The API serves a web UI at `/ui` (and redirects `/` there): http://localhost:9500/ui/ locally. Users:
- **Sign up and log in** with email and password (Supabase Auth). Signup sends a confirmation link; in dev it lands in Mailpit at http://localhost:8025. Passwords need at least `PASSWORD_MIN_LENGTH` (8) characters. "Forgot password?" emails a reset link.
- **Overview**: the current minute, hour and month against their tier's limits (refreshed every 10 s), the tier's limits, and a curl example.
- **API keys**: create a key (shown once, with a copy button), see each key's prefix and last use, revoke keys. The tier's key limit is enforced.
- **Usage**: requests per day for the last 7, 30 or 90 UTC days (also as a table), and this month's requests per key.

Admins also get an **Administration** page: find users, change their role (and so their plan), deactivate, delete, restore or remove them, and create users. Light and dark mode follow the system until switched in the sidebar.

How it is served:
- The UI is a React single-page app in `frontend/`, built with Vite into static files. The Docker image builds it in a Node stage and the API serves the files from `UI_DIR` (`/app/ui` in the image); nothing Node runs in production.
- The browser talks to Supabase Auth directly at `SUPABASE_PUBLIC_URL` with the public `ANON_KEY`; the API hands both to the page at `/ui/config.json`, so one image fits any deployment. `SUPABASE_PUBLIC_URL` must be reachable from users' browsers.
- Email links (confirmation, reset) return to the page that sent them. `SITE_URL` is where Supabase Auth sends a link that names no allowed page: set it to the UI's public URL (`https://geo.example.com/ui/`). A UI on any other origin must be listed in `ADDITIONAL_REDIRECT_URLS`.
- The login session is kept in the browser's `localStorage`, so `/ui` responses carry a strict Content-Security-Policy that only allows the app's own scripts.

## Authentication

Accounts live in Supabase Auth, usually created through the [web UI](#web-ui); the API checks two kinds of credential:
- **API keys** (`X-API-Key` header) for the data endpoints and `/sync`.
- **Login tokens** (`Authorization: Bearer <access_token>` from Supabase Auth) for `/portal`, where users manage their keys.

Every signup gets the default role: the one marked `is_default` in `app.roles`, `free` to start with. Exactly one role must be marked, so to change the default, unmark the old one and mark the new one in the same transaction. Roles map to tiers, and each tier sets the user's request limits (see [Rate limits](#rate-limits)) and caps how many active API keys they can hold:

| Tier | Roles | /minute | /hour | /month (UTC) | Active API keys |
|---|---|---|---|---|---|
| basic | free (default) | 10 | 100 | 5,000 | 1 |
| plus | tester | 30 | 500 | 50,000 | 3 |
| pro | dev | 120 | 2,500 | 500,000 | 5 |
| max | sr_dev, admin | unlimited | unlimited | unlimited | unlimited |

Tiers, roles and the role -> tier mapping are rows in the `app` schema (`app.tiers`, `app.roles`), editable in Studio. The read-only view `app.user_tiers` shows every user's role, tier, limits (empty = unlimited) and active/revoked key counts in one table (Studio: Table Editor, schema `app`). Admin access (`/sync`) comes from `app.roles.is_admin`. To change a user's role, edit their row in `app.profiles` in Studio; it applies within `AUTH_CACHE_SECONDS` (60 s), as do changes to a tier's limits, bans and revoking a key in Studio. The first admin comes from `ADMIN_EMAIL` and `ADMIN_PASSWORD`: on startup, while no profile has an admin role, the server creates that user (confirmed, so they can log in at once) with the `admin` role, or makes the user admin if the email is already registered. Once an admin exists both settings are ignored; remove the password from `.env`. Further admins are made on the Administration page.

In the devcontainer, `make seed-users` (also run on every container start) creates one confirmed user per tier, all with password `dev-password`, each with a real API key kept in `.env.dev` (and so in `.env` after `make switch2dev`):

| User | Role / tier | API key in `.env` |
|---|---|---|
| `free@geonames.local` | free / basic | `DEV_API_KEY_FREE` |
| `tester@geonames.local` | tester / plus | `DEV_API_KEY_TESTER` |
| `dev@geonames.local` | dev / pro | `DEV_API_KEY_DEV` |
| `admin@geonames.local` | admin / max (can sync) | `DEV_API_KEY_ADMIN` |

It also creates two playground users with the same password and no API key, to try the Administration page on (change their plan, deactivate, delete, restore): `alice@geonames.local` (Alice Example, starts as free) and `bob@geonames.local` (Bob Example, starts as dev). They are only created when missing, never reset, so your changes stay; one deleted for good comes back on the next run.

Rerunning it restores the four tier users' roles and keys. It refuses unless `DEV_SEED_USERS=true`, which only `.env.dev` sets: never enable it or the keys on a deployed server. The seed key fills the free user's only key slot.

With the API running (`make run`), these targets use these users. `ROLE` picks them (comma-separated, default all), and `ARGS` passes any other option of the script (`--help` lists them):

- `make api-call ROLE=free ARGS="--count 5"` calls the API as them (random data requests, or `--path /metadata/ --param limit=3`; `--api-key` for any other key). `ARGS=--portal` logs each one in and prints their tier, limits and current usage.
- `make stress ROLE=dev ARGS="--concurrency 20 --duration 60"` sends concurrent traffic as every user, then prints calls, statuses and latency per role and the slowest routes. It exits 1 on server errors or calls without a response.
- `make stress-limits` checks each tier for real: a concurrent burst past the minute limit (exactly the calls left pass, the rest get 429 with Retry-After, `/portal/usage` agrees), the admin-only `/sync` refusing non-admins without counting the call, and the hourly limit (`LIMIT=1`) and monthly quota (`QUOTA=1`; neither means both), by setting the user's Redis counter a few calls short of the limit and putting it back afterwards. The calls are real: they count against the users' limits.
- `make reset-usage` resets the seeded users' hourly limit and monthly quota so they can call again: `ROLE=free LIMIT=1` for one user's hourly limit, `QUOTA=1` for the monthly quota, `MINUTE=1` to add the minute limit, `ARGS="--email you@example.com"` for any other user. It changes only the Redis counters, not the usage history in Postgres, and refuses unless `DEV_SEED_USERS=true`.

The web UI does all of this; to script it instead, get a key with curl. `ANON_KEY` is in `.env`; Kong requires it on the Auth API:

```bash
# 1. Sign up, then open the confirmation email (Mailpit at http://localhost:8025 in dev) and follow its link
curl -X POST http://localhost:8000/auth/v1/signup -H "apikey: $ANON_KEY" \
  -H "Content-Type: application/json" -d '{"email": "you@example.com", "password": "..."}'

# 2. Log in; the response's access_token is the login token (valid for JWT_EXPIRY seconds)
curl -X POST "http://localhost:8000/auth/v1/token?grant_type=password" -H "apikey: $ANON_KEY" \
  -H "Content-Type: application/json" -d '{"email": "you@example.com", "password": "..."}'

# 3. Create an API key. The response's "key" is shown only this once; only its hash is stored
curl -X POST http://localhost:9500/portal/keys -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" -d '{"name": "laptop"}'
```

Portal endpoints (login token):
- `GET /portal/me`: email, role, tier, limits and the number of active keys.
- `GET /portal/keys`: active keys (name, prefix, created date); never the key itself.
- `POST /portal/keys` (`{"name": "..."}`, 1-64 characters): `201` with the key, `409` when the tier's key limit is reached.
- `DELETE /portal/keys/{id}`: revokes the key (`204`); it stops working on the next request.
- `GET /portal/usage?days=30`: see [Rate limits](#rate-limits).

Deleting a user in Studio deletes their profile and keys. Banning a user, or soft-deleting them through the Auth admin API, keeps the rows but refuses their keys and login tokens (within `AUTH_CACHE_SECONDS`) until the ban ends.

## Rate limits

Every request with an API key counts against its user's limits for the current UTC minute, hour and calendar month (see the tier table above). All of a user's keys share one set of limits. Over a limit, the API answers `429` with a `Retry-After` header: the seconds until that window resets (when several limits are used up, the longest wait). For example:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 34

{"detail": "Rate limit exceeded: 10 requests per minute on the basic tier"}
```

- **What counts:** every request on the data endpoints, `/modifications`, `/deletions`, `/metadata` and `/sync` that passes the key check, whatever its status (a `404`, `422` or dataset-building `503` counts). Refused (`429`) requests, unknown keys, a non-admin's refused `/sync`, `/portal` and `/health` do not count. Unlimited tiers are counted but never refused.
- **Fixed windows:** the counters reset at the start of each minute, hour and month, so a client can make up to twice the per-minute limit across a minute boundary.
- **Redis and Postgres:** Redis holds the live counters. Every `USAGE_FLUSH_SECONDS` (5 minutes) the request counts per key and hour are copied to `app.api_key_usage`, along with each key's `last_used_at`. If Redis loses its data, each user's hour and month counters are rebuilt from there on their next request, so at most the last few minutes of usage are lost. Prod Redis also appends to disk every second.
- **Redis down:** requests keep being limited and counted in the app's memory, starting from each user's stored usage, and the counts still reach Postgres. When Redis is back, the outage requests are added to the affected users' Redis counters. `/health` reports Redis as `error` meanwhile. Without `REDIS_URL`, nothing is limited or counted (startup logs a warning).
  - Requests in flight when Redis first fails each wait up to 1 s (`socket_connect_timeout`). After that, Redis is retried every 30 s by one request while the others stay in memory.
  - A request whose Redis reply times out (Redis slow, not gone) is let through without being counted in memory, since Redis has most likely counted it already. If it had not, that request is missing from the counts.
  - The in-memory counts live only in the process. If the app stops while Postgres is also unreachable, they are lost; the shutdown warning says how many.
  - A request whose counter rebuild fails (Postgres unreachable) is also counted in memory and reaches Postgres at a later flush.
- **Studio:** the view `app.monthly_usage` shows each user's requests per month next to their monthly quota. It is up to one flush behind.

`GET /portal/usage` (login token) shows the signed-in user's usage:
- `current`: `minute`, `hour` and `month`, each with `used`, `limit` (null = unlimited) and `resets_at`. Live from Redis; with Redis down `live` is `false` and the counts come from memory or Postgres (`minute.used` may be null).
- `daily`: requests per UTC day for the last `days` days (1-90, default 30), zero-filled, oldest first.
- `keys`: active keys, and revoked keys used this month, with `requests_this_month`.

`daily` and `keys` come from Postgres, so they are up to one flush behind.

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

Generate the Supabase secrets and fill in the empty secret values in `.env` with the output. Run it once: the anon and service keys are signed with that run's `JWT_SECRET`. `docker compose` refuses to start while any secret is empty.

```bash
make supabase-keys
```

Also set:
- `ADMIN_EMAIL` and `ADMIN_PASSWORD`: the first start creates this admin account, so you can log in at `/ui` and manage users. Ignored once an admin exists; remove the password afterwards.
- The public URLs (`SUPABASE_PUBLIC_URL`, `API_EXTERNAL_URL`, `SITE_URL`).
- SMTP settings, optional. With `SMTP_HOST` empty no email is sent and everything else works, but not signup confirmation, "Forgot password?" or changing an email address: Supabase Auth answers as if it had sent the email. So without SMTP set `DISABLE_SIGNUP=true` (the UI then hides "Sign up") and create accounts on the Administration page; those are confirmed at once, and users can change their password on their profile page. The server checks SMTP at startup and warns when it is not configured, or, with a different warning, when it is filled in but the server refuses it (then those emails fail with an error). The web UI gets the result: when email cannot be sent, signup (unless `ENABLE_EMAIL_AUTOCONFIRM`), "Forgot password?", resending a confirmation and changing an email address stop before asking Supabase Auth, show a toast saying SMTP is not configured or not working, write it to the browser console, and report it to the server, which logs a warning (each kind at most once a minute). An email that fails to send later gets the same treatment.
- `APP_PORT` (default `9500`) and `KONG_HTTP_PORT` (default `8000`) if those host ports are taken.

TLS is not part of the stack: put your own reverse proxy (Caddy, nginx, Traefik) in front of the app port and Kong's `/auth/v1`, and use the `https://` addresses in the public URLs. Keep `POSTGRES_USER=postgres`, `POSTGRES_HOST=db` and `POSTGRES_DB=postgres`: Supabase expects that database name.

### 2) Start

The stack needs only `docker-compose.yml`, the `supabase/` directory and `.env`; the app runs the image `GEONAMES_IMAGE` (default `ghcr.io/neo7dev/geonames-server:latest`). To run the code in this checkout instead, build that image locally first with `make build-image`.

```bash
docker compose up -d
```

On every start the server first runs preflight checks, before migrations or anything else. It needs the database, Redis (when `REDIS_URL` is set), Supabase Auth and `JWT_SECRET`: each service gets a few attempts, and if one stays unreachable the server logs `Preflight failed: ...` for each problem and exits, and Docker restarts it. SMTP and the web UI settings (`SUPABASE_PUBLIC_URL`, `ANON_KEY`) only produce warnings.

This starts:
- `app` (FastAPI on port `APP_PORT`, `9500`, with the web UI at `/ui`)
- `db` (Supabase Postgres 17, internal network). Holds geonames data and Supabase auth
- `auth` (Supabase Auth: signup, login, OAuth), `meta` and `studio`
- `kong` on port `KONG_HTTP_PORT` (`8000`): Supabase Studio admin dashboard at `/` (basic auth `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`) and the Auth API at `/auth/v1`
- `redis` (internal network) for rate limiting and quotas

**Run one app instance only.** Keep a single `app` container with the image's single uvicorn worker: no `--workers`, no `docker compose up --scale app=N`, no second replica against the same database. The daily-update scheduler runs inside the process, so a second instance would run every sync twice. Rate limiting also keeps its API key cache and its fallback counters for Redis outages in process memory, so a second instance would let a key revoked on `/portal` keep working for up to `AUTH_CACHE_SECONDS` (60 s) longer and double the limits during a Redis outage.

Supabase config (Kong routes, db init scripts) lives in `supabase/`. It is a trimmed copy of the upstream self-hosted stack; PostgREST, Realtime, Storage, Edge Functions and Analytics are not deployed.

### 3) Verify Service Health

The API answers as soon as migrations finish; datasets download and load in the background. Check progress with:

```bash
curl "http://localhost:9500/health"
```

`status` is `ok` once every enabled dataset is built, `degraded` while datasets are still building (listed in `datasets.pending`), if the background startup failed, or if Redis is unreachable, and `error` (HTTP 503) only when the database is unreachable. The container's healthcheck uses this endpoint, so it stays healthy while a large dataset such as `allcountries` loads.

Until a dataset's first build finishes, its endpoints answer `503` with a `Retry-After` header instead of an empty result. A dataset that is rebuilt because it is out of date keeps serving its current data.

Open:
- `http://localhost:9500/ui/` to sign up and create an API key
- `http://localhost:9500/docs`

Quick API check:

```bash
curl -H "X-API-Key: $API_KEY" "http://localhost:9500/allcountries/?limit=5"
```

### 4) Follow the Logs

```bash
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
- `REDIS_URL` (e.g. `redis://redis:6379/0`; rate limits and usage counting, reported by `/health`. Unset: nothing is limited)
- `AUTH_CACHE_SECONDS` (default `60`; how long an API key lookup is cached, so role, tier, ban and Studio revoke changes apply within it; `0` turns the cache off)
- `USAGE_FLUSH_SECONDS` (default `300`; how often request counts are copied from Redis to Postgres)
- `JWT_SECRET` (Supabase Auth's JWT secret; required, used to verify login tokens on `/portal`)
- `SUPABASE_PUBLIC_URL` (Supabase's address as users' browsers reach it, e.g. `https://auth.example.com`; the web UI calls Supabase Auth there. Unset: `/ui/config.json` answers 503)
- `ANON_KEY` (Supabase's public anon key, passed to the web UI)
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_ADMIN_EMAIL` (Supabase Auth's email; the server only checks them at startup and warns when email is off or broken), `DISABLE_SIGNUP`, `ENABLE_EMAIL_AUTOCONFIRM` (read for the same warning)
- `ADMIN_EMAIL`, `ADMIN_PASSWORD` (the first admin account, created on startup while no admin exists; ignored afterwards)
- `PASSWORD_MIN_LENGTH` (default `8`; also passed to Supabase Auth as `GOTRUE_PASSWORD_MIN_LENGTH`, so the signup form and the server agree)
- `UI_DIR` (default `frontend/dist`; the built web UI. The image sets `/app/ui`)
- `GEONAMES_URL`
- `ENABLED_DATASETS`
- `MAX_RESULTS`
- `SYNC_SCHEDULE_HOURS`
- `FORCE_REBUILD`
- `LOG_LEVEL` (default `INFO`; `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`, any case. `DEBUG` also logs every SQL statement and outgoing HTTP request)

Notes:
- `MAX_RESULTS` caps list/filter endpoint page size.
- `FORCE_REBUILD=true` forces a full rebuild on next startup. It drops the `POSTGRES_SCHEMA` schema, so it never touches Supabase's own tables or user data.
- User data (tiers, roles, profiles, API keys) lives in the `app` schema with its own migration chain (`migrations/app`, `alembic -n app ...`, `make db-app-*`), so `FORCE_REBUILD` never drops it.
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
- A generated OpenAPI snapshot is in `openapi.yaml` (`make openapi` regenerates it; `make openapi-check` fails while it is stale).
- The web UI lives in `frontend/` (Vite, React, TypeScript, Tailwind, shadcn components). In the devcontainer, which has Node 24, `make ui-dev` serves it with hot reload at http://localhost:5173/ui/ and passes API calls to uvicorn on 9500 (start the API with `make run` too). `make ui-build` builds it into `frontend/dist`, which the API serves at http://localhost:9500/ui/; `make run` builds it first, so the API always serves the current UI. After changing a portal endpoint, run `make openapi` then `make ui-types` so the UI's TypeScript types follow; `make ui-types-check` fails while they are stale.

### Checks before deploying

Ruff is a dev dependency (`uv sync` installs it), with its rules in `pyproject.toml`. `make lint` / `make format` fix what they can; `make lint-check` / `make format-check` only report.

Before deploying, run `make predeploy` in the devcontainer. It runs the lint and format checks, `uv lock --check` (the image installs from `uv.lock`), `make openapi-check` (`openapi.yaml` matches the app), the whole test suite, including the Postgres tests (`make tests-pg`, against the throwaway `geonames_test` database; see `.devcontainer/README.md`), and the web UI's lint, type check, tests and type freshness (`make ui-lint ui-test ui-types-check`). Then run `make image-check`, in the devcontainer or on the host (the devcontainer's Docker CLI uses the host's Docker, so the image lands there either way): it builds the image, tags it with the version and `latest`, and checks that the app imports and reports that version, that each migration chain has exactly one head, and that the web UI is built in. `make push-image` publishes it.

The version is set in one place, `version` in `pyproject.toml`. It is the image's tag (and its `org.opencontainers.image.version` label), and the app reads it from the same file for `/health` (`version`), `/docs` and `openapi.yaml`. To release, bump it there and run `make openapi` (the spec carries it).

### Simulated downloads in development

In the devcontainer, `.env.dev` sets `GEONAMES_URL` to `http://slow-download-server:80/`, so the app does not download from geonames.org. To simulate a real download, the `slow-download-server` service (nginx) serves the local zipped files from `gostatic/` at a throttled speed:

- Fill `gostatic/` from geonames.org with `make download-datasets`. It lists the datasets with their sizes and defaults to your `ENABLED_DATASETS`. It then asks whether to also fetch the current daily `modifications-YYYY-MM-DD.txt` / `deletes-YYYY-MM-DD.txt` pair, defaulting to no (GeoNames only publishes the latest day's pair). Files keep the geonames.org names and Last-Modified dates, and files that haven't changed are skipped. Non-interactive: `make download-datasets DATASETS=cities15000,cities5000` (add `DAILY=yes` to include the daily pair) (`DATASETS=all` for everything). The source is the `GEONAMES_DUMP_URL` Makefile variable (default `https://download.geonames.org/export/dump/`, also used by `make remote-etag`); override it to use a mirror, e.g. `make download-datasets GEONAMES_DUMP_URL=https://mirror.example.com/dump/`. `gostatic/` is git-ignored.
- Change the download speed with `make download-speed` (interactive) or `make download-speed RATE=500k`. The value is nginx's `limit_rate` in `.devcontainer/nginx.conf`, in bytes per second per connection (`500k`, `2m`, `0` = unlimited; default `10m`). slow-download-server reloads within 2 seconds. Lower it to test the downloader's retry and timeout handling.
- The same folder is also served without throttling by the `gostatic` service at http://localhost:3002.
- To download from geonames.org instead, set `GEONAMES_URL = "https://download.geonames.org/export/dump/"` in `.env`.
