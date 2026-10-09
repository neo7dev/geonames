# Plan: Deployable image, auth, tiers, rate limiting

Goal: ship one deployable app image, run via docker compose alongside Redis and self-hosted Supabase. Users sign up (email + password, GitHub, Google), manage API keys, and view usage/quota. Roles map to tiers. Admin dashboard is Supabase Studio. Internal tool, no paywall.

Phases 1-3 were built on `dev-supabase-stack` and are merged into `main` with the review fixes below. Each later phase gets its own branch.

## Decisions

- Single Supabase Postgres: geonames data in `geonames` schema, user tables in `app` schema.
- Frontend: React + Vite + Tailwind + shadcn SPA, served by FastAPI at `/ui`.
- Limits per user (aggregate across keys): per-minute rate limit, hourly limit, calendar-month (UTC) quota.
- Tiers and role → tier mapping stored in DB tables, editable in Studio.
- Trimmed Supabase stack: db, auth (GoTrue), meta, Studio and Kong only. Not deployed:
  - PostgREST and GraphQL: FastAPI does all data access, and exposing them would open tables to the public anon key.
  - Realtime, Storage, Edge Functions: not needed.
  - Analytics (Logflare + Vector): it only feeds Studio's admin Logs/Reports pages with Supabase's internal logs, and needs ~1 GB RAM plus Docker socket access. Users see their usage through our own tracking (phases 5 and 6), not Supabase analytics.
- Kong is the gateway (upstream now defaults to Envoy; Kong's config is smaller to trim and tolerates missing services).
- Users never get Studio access: it has one shared password and full database access.

## Phases

| Phase | Status | What | Done when |
|---|---|---|---|
| 1. Dev infra | Done | Devcontainer compose runs a trimmed self-hosted Supabase (db, auth, kong, meta, Studio), Redis and Mailpit; the app points at the Supabase db; no app code changes | Studio opens, datasets ingest into the Supabase db, integration tests pass against it |
| 2. Schema split | Done | Geonames tables move to a `geonames` schema, kept separate from user data and Supabase's own tables | Tests show the tables in `geonames` and nothing left in `public` |
| 3. Deploy image + compose | Done | Prod Dockerfile; root compose with the same services as dev, plus Redis; startup no longer waits for data loads; public `/health` | `docker compose up` brings it all up, `/health` returns 200, Studio opens |
| 4. Auth + API keys | Not started | Tiers, roles and user tables; new signups get the free role; login token checked for portal routes; API key (shown once, stored hashed) checked for data routes; `/portal/*` endpoints; `POST /sync` admin-only | Requests without a valid key get 401 and a non-admin sync gets 403 |
| 5. Rate limits + quota | Not started | One Redis check covers per-minute, hourly and monthly limits; 429 with `Retry-After`; usage copied to Postgres every 5 min; requests still go through if Redis is down | The 11th call in a minute on basic gets 429; a role change in Studio applies within 60s |
| 6. Frontend | Not started | React + Vite + Tailwind + shadcn at `/ui`: signup/login (email, GitHub, Google), key management, usage page | Sign up, create a key, call the API, see the usage update |
| 7. Ops (later) | Not started | CI that builds and pushes the image, backups, TLS, key scopes and expiry | Image published to ghcr |

## Tiers

| Tier | Roles | /min | /hour | /month |
|---|---|---|---|---|
| basic | free (default) | 10 | 100 | 5k |
| plus | tester | 30 | 500 | 50k |
| pro | dev | 120 | 2.5k | 500k |
| max | sr_dev, admin | 600 cap | unlimited | unlimited |

## Progress

### Phase 1: Dev infra (`776c8f2`)

- `.devcontainer/docker-compose.yml`: postgres:16 replaced by `supabase/postgres` 17, plus auth, meta, Studio, Kong (port 8000), Mailpit (port 8025) and Redis. The app connects as `postgres` to database `postgres`, which Supabase expects.
- `supabase/`: vendored, trimmed upstream config shared by dev and deploy: `kong.yml` (auth, meta and Studio routes only), `kong-entrypoint.sh`, `db/roles.sql`, `db/jwt.sql`.
- Dev-only Supabase secrets in `.devcontainer/.env` (upstream demo keys expire in January 2027, so new ones were generated).
- Upstream fixes needed: `roles.sql` dropped `supabase_functions_admin`, which only exists with edge functions and aborted database init. SMTP user/pass stay empty for Mailpit because GoTrue refuses SMTP auth over plain connections.

### Phase 2: Schema split (`1b5eb63`)

- `POSTGRES_SCHEMA` setting (default `geonames`). Every connection (SQLAlchemy engines, raw psycopg2, Alembic) sets `search_path` to it, so queries, ingestion and the table swap needed no SQL changes.
- `migrations/env.py` creates the schema and moves tables from older versions out of `public` (with data and migration history) before Alembic reads its version table.
- Migration `e7b3c1a9f2d4` revokes the `anon` / `authenticated` / `service_role` grants the moved tables kept from `public`. New tables in `geonames` get no such grants, so rebuild swaps no longer bring them back. This fixed Studio's "RLS disabled" security advisor errors.
- `FORCE_REBUILD` drops only the schema (plus legacy `public` tables), never Supabase's tables. Startup fails if connections don't resolve tables in the schema.

### Phase 3: Deploy image + compose (`49cd9e0`, `61a7838`)

- Root `docker-compose.yml`: postgres:16 replaced by the same Supabase services as dev, plus Redis (append-only, healthchecked). Database port not published.
- `make supabase-keys` (`supabase/generate-keys.py`) prints deployment secrets: Postgres and dashboard passwords, JWT secret, and the anon/service keys signed with it. `.env.example` and `.env.prod` leave them empty, and compose refuses to start until they are set; `.env.dev` holds the dev values.
- Startup serves the API right after migrations; remote metadata check, rebuilds and then the scheduler run in a background daemon thread.
- `GET /health`: database, Redis and per-dataset readiness. 503 only when the database is down, so the container stays healthy while large datasets load. The image's `HEALTHCHECK` uses it. It pings one Redis client shared through `app.state.redis`, which phase 5 can reuse.
- Dockerfile installs dependencies from `uv.lock` (uv 0.12.24) into `/opt/venv` instead of pip and `requirements.txt`.
- `REDIS_URL` setting, set in all env files.

### Added along the way

- `make download-datasets` (`6534336`): interactively downloads dataset zips from `GEONAMES_DUMP_URL` (default geonames.org) into `gostatic/` for `slow-download-server`, keeping remote names and Last-Modified dates. Optionally fetches the current daily modifications/deletes pair (default no; geonames.org only publishes the latest day's pair). Non-interactive: `DATASETS=... DAILY=yes`.
- `make download-speed` (`6534336`): interactively sets `slow-download-server`'s speed; nginx reloads automatically when `nginx.conf` changes.
- `make help` (`6534336`): lists targets by section, built from `##` comments above each target.
- Downloader skips unchanged files (`ed87dcf`): full rebuilds always forced a fresh download. Downloads now set the file's mtime to the remote Last-Modified; a forced download first sends a HEAD and reuses the local file when size and Last-Modified match. Rebuilds also record the dump's real date as `last_applied_date`.
- README documents dataset source URLs and simulated downloads in dev (`5f088e2`).
- Devcontainer enables all datasets (`3d95589`) and uses https for `GEONAMES_URL` (`15147db`).
- The old postgres:16 container, its volume and image were removed from the dev machine.

### Review fixes (`6d90937`..`7aa00d4`)

A code review of phases 1-3 found ten issues, fixed one commit each:

- Compose requires every Supabase secret (`${VAR:?...}`) instead of shipping a known placeholder value (`6d90937`).
- `POSTGRES_SCHEMA` cannot name a Postgres or Supabase schema, and `FORCE_REBUILD` refuses to drop a schema holding objects the app did not create (`fd74446`). It must also be a plain lowercase identifier, because `search_path` gets it unquoted (`ffc2a51`).
- `POST /sync` answers 409 while startup is still checking and rebuilding datasets (`f2b9e60`).
- A dataset's routes answer 503 with `Retry-After: 60` until its first build finishes; a stale dataset being rebuilt keeps serving (`fa09cef`).
- Downloader: forced downloads replace an unreadable file (`6bfc1cd`); malformed `Content-Length` / `Last-Modified` headers count as missing instead of failing the build (`27ccf5d`).
- `make download-datasets` no longer exits silently when `.env` has no `ENABLED_DATASETS` (`871b6a1`).
- `/health` shares one Redis client and drops a redundant `SELECT 1` (`61c5b0d`).
- The Supabase services (db, auth, meta, Studio, Kong) are defined once in `supabase/docker-compose.yml` and pulled into both stacks with `include:`; `.devcontainer/supabase.override.yml` publishes db on 5432 in dev (`7aa00d4`).

### Smoke test (2026-10-09)

- Devcontainer, app on a fresh database: during startup `/health` listed the datasets as pending, a dataset route answered 503 with `Retry-After: 60` and `POST /sync` answered 409. Once the builds finished: 200, 202, and the sync completed. Three `/health` calls used one Redis connection, and shutdown closed it.
- Startup used to report `done` even when dataset builds failed. `build_all_datasets` now returns the failed names, and startup reports `failed` if there are any, so `/health` shows `degraded` with `startup: failed`. Checked live with an unreachable `GEONAMES_URL`.
- Root stack (`docker compose up --build`, separate project on other ports): every service healthy, Studio answers 401 without basic auth and 200 with it, Auth `/auth/v1/health` answers 200, `/health` answers 200.

## Risks

- A connection without the schema's `search_path` would read and write `public` without any error. Mitigated in phase 2: one setting feeds every connection, and startup asserts it.
- In the supabase/postgres image, `postgres` is not a superuser. `roles.sql` must run, and the phase 4 trigger on `auth.users` needs privileges.
- In production, GoTrue needs SMTP to send confirmation emails. Dev uses Mailpit, which catches all outgoing mail. `.env.dev` points SMTP at `mailpit`, which only exists in the devcontainer stack.
- Keep a single uvicorn worker, or APScheduler jobs run twice.
- `.devcontainer/docker-compose.yml` passes `ENABLED_DATASETS` from `.devcontainer/.env` into the app container, which overrides the value in `.env`.
- A local `.env` copied before a setting was added keeps missing it (the dev machine's `.env` had no `REDIS_URL` until the smoke test). Compare `.env` with `.env.dev` after env changes (`make switch2dev` overwrites local edits).
