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
| 4. Auth + API keys | Scoped (see Phase 4 scope) | Tiers, roles and user tables; new signups get the free role; login token checked for portal routes; API key (shown once, stored hashed) checked for data routes; `/portal/*` endpoints; `POST /sync` admin-only | Requests without a valid key get 401 and a non-admin sync gets 403 |
| 5. Rate limits + quota | Not started | One Redis check covers per-minute, hourly and monthly limits; 429 with `Retry-After`; usage copied to Postgres every 5 min; requests still go through if Redis is down | The 11th call in a minute on basic gets 429; a role change in Studio applies within 60s |
| 6. Frontend | Not started | React + Vite + Tailwind + shadcn at `/ui`: signup/login (email, GitHub, Google), key management, usage page | Sign up, create a key, call the API, see the usage update |
| 7. Ops (later) | Not started | CI that builds and pushes the image, backups, TLS, key scopes and expiry | Image published to ghcr |

## Tiers

| Tier | Roles | /min | /hour | /month | API keys |
|---|---|---|---|---|---|
| basic | free (default) | 10 | 100 | 5k | 1 |
| plus | tester | 30 | 500 | 50k | 3 |
| pro | dev | 120 | 2.5k | 500k | 5 |
| max | sr_dev, admin | 600 cap | unlimited | unlimited | unlimited |

## Phase 4 scope

Branch `phase-4-auth` from `main`.

### Verified first (supabase/postgres 17.6.1.136, throwaway container)

- `postgres` is not a superuser, but has SELECT, INSERT, UPDATE, DELETE, TRIGGER and REFERENCES on `auth.users`, which `supabase_auth_admin` owns. `auth.users` exists as soon as the db container has initialised, before GoTrue's first start.
- `postgres` can create a FK to `auth.users(id) ON DELETE CASCADE` and an `AFTER INSERT OR UPDATE OF email` trigger on it. A `SECURITY DEFINER` function owned by `postgres` with `search_path = ''` ran fine when the row was inserted and updated as `supabase_auth_admin` (GoTrue's role). Deleting the auth user cascaded to the profile.
- `postgres` cannot `SET ROLE supabase_auth_admin`. Tests insert into `auth.users` as `postgres`, which fires the same trigger.
- A database created for `TEST_PG_DSN` (`geonames_test`) has no `auth` schema. The migration must work without it.
- The default ACLs grant `anon` / `authenticated` / `service_role` access in `public`, `storage` and `graphql*`, not in a new schema. `app` gets no API-role grants without a revoke.

### Decisions

- **Separate Alembic chain for `app`.** `FORCE_REBUILD` drops the `geonames` schema and its `alembic_version`. If user tables shared that chain, a rebuild would rerun their migrations against existing tables, and a careless change could drop user data along with the geonames data. User data gets its own chain in `migrations/app/` (own `env.py`, `script.py.mako` and `versions/`), with its version table in `app.alembic_version`, selected by an `[app]` section in `alembic.ini` (`alembic -n app ...`). `db_migrations()` runs the geonames chain, then the app chain. `FORCE_REBUILD` never touches `app`. The Dockerfile already copies `migrations/`, and the geonames chain does not search subdirectories of `versions/`.
- **Separate declarative base.** `AppBase` uses `MetaData(schema="app")`, so the geonames `Base.metadata` (used by `FORCE_REBUILD`'s foreign-object check, the adoption of legacy `public` tables, and autogenerate) never sees user tables. App models are schema-qualified, so the session's `search_path=geonames` does not matter.
- **One credential per route family.** Data routes and `/sync` take an API key in `X-API-Key` (FastAPI `APIKeyHeader`; no query parameter, so keys stay out of access logs). `/portal/*` takes the GoTrue access token as `Authorization: Bearer` (`HTTPBearer`). Each is rejected in the other's place. `/health`, `/docs`, `/redoc` and `/openapi.json` stay public: the docs show only route shapes, and Swagger's Authorize slot (from the security schemes) takes the key, so every "Try it out" call without one gets 401.
- **Admin is data, not a role name.** `app.roles.is_admin` (seeded true for `admin` only) gates `/sync`, so Studio can grant it to another role.
- **API key format.** `gn_` + `secrets.token_urlsafe(32)` (256 bits). Stored as the SHA-256 hex of the whole key (a fast hash is right for random high-entropy keys) under a unique index, plus a display `prefix` (the first 11 characters, `gn_` + 8). Returned once at creation. Never logged.
- **Revoke, don't delete.** `DELETE /portal/keys/{id}` sets `revoked_at`, so phase 5 usage rows keep their key. The tier's `max_keys` caps active keys per user (1 / 3 / 5 / unlimited); revoked keys don't count. After a downgrade, keys over the new cap keep working, but no new key can be created until the user is under it.
- **JWT check.** HS256 with `JWT_SECRET`, `aud = authenticated`, `exp` and `sub` required, 30 s leeway, claim `role = authenticated`, and `is_anonymous` not true. This rejects the public `ANON_KEY` and `SERVICE_ROLE_KEY`, which are signed with the same secret but have no `aud`/`sub`. `iss` is not checked. A token stays valid until it expires (`JWT_EXPIRY`, 3600 s) after logout or a ban.
- **No lookup cache yet.** Each data request does one indexed query (api_keys → profiles → roles → tiers). Phase 5 adds the 60 s cache with the limits, which is where the "role change applies within 60 s" requirement lives. Until then role changes and revocations apply on the next request.
- **Not in phase 4:** enforcing limits, `last_used_at` writes (phase 5 fills them in its 5-minute usage flush; the column exists now), `/portal/usage` (phase 5), key scopes and expiry (phase 7). Studio's ban stops logins but not existing keys; deleting the user removes their keys via the cascade.

### Database (`migrations/app/versions/<rev>_accounts.py`)

`migrations/app/env.py` creates schema `app` and sets `version_table_schema="app"`. Autogenerate compares only `app` (`include_schemas=True` with an `include_name` filter).

| Table | Columns |
|---|---|
| `app.tiers` | `name text PK`, `per_minute int NOT NULL`, `per_hour int NULL`, `per_month int NULL`, `max_keys int NULL` (NULL = unlimited; `CHECK (> 0)`) |
| `app.roles` | `name text PK`, `tier text NOT NULL → tiers(name) ON UPDATE CASCADE`, `is_admin bool NOT NULL DEFAULT false` |
| `app.profiles` | `id uuid PK → auth.users(id) ON DELETE CASCADE`, `email text`, `role text NOT NULL DEFAULT 'free' → roles(name) ON UPDATE CASCADE`, `created_at timestamptz DEFAULT now()` |
| `app.api_keys` | `id uuid PK DEFAULT gen_random_uuid()`, `user_id uuid NOT NULL → profiles(id) ON DELETE CASCADE`, `name text NOT NULL CHECK (length 1-64)`, `prefix text NOT NULL`, `key_hash text NOT NULL UNIQUE`, `created_at timestamptz DEFAULT now()`, `last_used_at timestamptz`, `revoked_at timestamptz`; index on `(user_id) WHERE revoked_at IS NULL` |

- Seed the four tiers and five roles from the tiers table above (`admin` has `is_admin = true`).
- `app.handle_new_user()`: `SECURITY DEFINER`, `SET search_path = ''`, upserts `(id, email)` into `app.profiles`. Trigger `on_auth_user_created AFTER INSERT OR UPDATE OF email ON auth.users`. `profiles.email` is a convenience copy for Studio; the trigger keeps it in sync.
- Backfill: `INSERT INTO app.profiles (id, email) SELECT id, email FROM auth.users ON CONFLICT DO NOTHING`, for users who signed up before the migration.
- When `to_regclass('auth.users')` is NULL (plain Postgres, the `TEST_PG_DSN` database), skip the FK, trigger and backfill and log it.
- Downgrade drops the trigger, function and tables.

### Code

- `pyproject.toml` / `uv.lock`: add `pyjwt`; `make requirements`.
- `config.py`: `jwt_secret: SecretStr | None` (reads the `JWT_SECRET` that every env file already has). Add `app` to `_RESERVED_SCHEMAS`. The lifespan exits with a critical log if `jwt_secret` is unset, like the database check.
- `database/models/app_base.py` (`AppBase`), `database/models/accounts.py` (`Tier`, `Role`, `Profile`, `ApiKey`). Not imported by the geonames `env.py`.
- `database/migrations.py`: `db_migrations()` upgrades both chains. A helper builds each chain's `Config`.
- `api/auth.py`:
  - `Principal` (frozen dataclass): `user_id`, `email`, `key_id` (None for JWT), `role`, `is_admin`, `tier`, `per_minute`, `per_hour`, `per_month`, `max_keys`. Saved on `request.state.principal` for phase 5.
  - `generate_api_key() -> (key, prefix, key_hash)` and `hash_api_key(key)`.
  - `require_api_key`: missing or unknown or revoked key → 401 with `WWW-Authenticate: ApiKey`.
  - `require_admin`: `require_api_key`, then 403 unless `is_admin`.
  - `require_user`: verifies the JWT as above → 401 with `WWW-Authenticate: Bearer`; then loads the profile by `sub`, with a 403 if it has none (it should never happen with the trigger and backfill).
  - All lookups use `get_session`, so a request uses one connection.
- `api/app.py`: `include_router(..., dependencies=[Depends(require_api_key)])` for the five dataset routers, `/modifications`, `/deletions` and `/metadata`; `Depends(require_admin)` for `/sync` (start and status). `include_router` dependencies run before the router's own, so an unauthenticated request gets 401, not the dataset's 503, and `POST /sync` checks auth before its startup 409.
- `api/routers/portal.py` + `api/schemas/portal.py`, all on `require_user`:
  - `GET /portal/me` → `id`, `email`, `role`, `tier`, `limits {per_minute, per_hour, per_month, max_keys}`, plus `active_keys`.
  - `GET /portal/keys` → active keys, newest first: `id`, `name`, `prefix`, `created_at`, `last_used_at`. Never the key or hash.
  - `POST /portal/keys {name}` → 201 with the same fields plus `key`. Name is stripped, 1-64 characters (422). 409 when active keys have reached the tier's `max_keys`; the message names the limit.
  - `DELETE /portal/keys/{id}` → 204. 404 if the key is missing, revoked or owned by someone else.
- `docker-compose.yml` and `.devcontainer/docker-compose.yml`: app also `depends_on: auth: service_healthy`, so GoTrue's own migrations of `auth.users` finish before the app adds its trigger.
- Makefile: `db-app-revision`, `db-app-auto-revision`, `db-app-upgrade`, `db-app-downgrade` (the existing targets with `-n app`).
- Regenerate `openapi.yaml` (`scripts/openapi.py`). It now lists both security schemes.

### Tests

- `tests/api/conftest.py`: `client` overrides `require_api_key` / `require_admin` with an admin `Principal`, so existing tests are unchanged. New `auth_client` overrides only `get_session`. New fixtures: `make_user(role="free")` inserts into `auth.users` inside the rolled-back transaction (the trigger creates the profile), `make_key(user)` and `make_jwt(sub, **claims)`.
- `tests/api/test_api_key_auth.py`: for every protected route family, no key, an unknown key and a revoked key each get 401 with `WWW-Authenticate`, and a valid key gets 200. The public routes need no key. A dataset that is still building answers 401 before 503. A key sent as `Bearer` is not accepted. `/openapi.json` declares the `X-API-Key` and bearer schemes, and the protected operations reference them (that is what gives Swagger its Authorize slot).
- `tests/api/test_sync.py`: `POST /sync` and `GET /sync/{id}` give 403 for a free user and pass for admin. Setting `is_admin` on another role in the table grants access.
- `tests/api/test_portal.py`: no token, expired, wrong secret, wrong `aud`, `ANON_KEY`, `SERVICE_ROLE_KEY` and an API key as bearer each get 401; a valid token passes. `/me` shows free/basic for a new signup. Create returns the key once and list never does; the stored hash is the SHA-256; the new key works on a data route. A revoked key gets 401. Another user's key gets 404. A free user's second key gets 409, and works after revoking the first; a dev user can create 5 keys, and a max user more than 5. After a downgrade from dev to free, all 5 keys still work, but a new key gets 409. A role change shows on the next `/me`. The trigger and email update flow through `make_user`.
- `tests/test_api_keys.py`: key format, prefix and hash.
- `tests/database/test_app_migration_pg.py` (`TEST_PG_DSN`): the app chain upgrades with no `auth` schema; tiers and roles are seeded; API roles have no grants on `app`; `db_migrations(force=True)` keeps `app` tables and rows; downgrade then upgrade works.

### Docs

- README "Authentication": sign up through `http://localhost:8000/auth/v1/signup` with header `apikey: $ANON_KEY` (Kong's `key-auth` on `/auth/v1/`), confirm the email in Mailpit (:8025), get a token from `/auth/v1/token?grant_type=password`, `POST /portal/keys`, then call the API with `X-API-Key`. Make the first admin by setting `role = admin` on their `app.profiles` row in Studio. Phase 6's UI replaces the curl steps.
- Update Progress and Risks here.

### Smoke test (devcontainer)

Sign up, confirm in Mailpit, log in, create a key. `/cities15000` gives 401 without a key and 200 with it. `POST /sync` gives 403 as free; set the role to admin in Studio, and the next call gets 202. Revoke the key, and the next call gets 401. Run `FORCE_REBUILD=true`, and the user, profile and keys survive.

### Commits

1. App Alembic chain, `AppBase` and models, accounts migration with trigger and seeds, migration tests.
2. `api/auth.py`, API keys on data routes and admin on `/sync`, conftest fixtures and auth tests.
3. Portal router and tests.
4. Compose `depends_on`, Makefile targets, README, `openapi.yaml`, plan progress.

## Phase 6 notes

- Layout: `frontend/` at the repo root with its own `package.json`. Makefile targets `ui-dev` (Vite dev server) and `ui-build`.
- No Node at runtime. Vite builds static files at image build time and FastAPI serves them at `/ui`.
- Dockerfile: a Node build stage runs `npm ci && npm run build`; the existing uv-based Python image copies its `dist/`.
- Devcontainer: add the Node feature. In dev, the Vite dev server gives hot reload and proxies `/portal` and the API routes to uvicorn on 9500.
- shadcn copies component source (Radix + Tailwind) into `frontend/src/components/ui/`; it is our code, not a dependency. Tailwind v4 through `@tailwindcss/vite`.
- Auth: `supabase-js` in the browser handles signup, login and OAuth with GoTrue, then sends the login token to `/portal/*`.
- CORS: GoTrue is reached through Kong (:8000) and the UI is served by the app (:9500), so browser calls to Auth are cross-origin. Allow the UI origin in Kong's `cors` plugin (already enabled), or in production put both behind one domain (`/` to the app, `/auth/v1` to Kong) and skip CORS.

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
- In the supabase/postgres image, `postgres` is not a superuser. `roles.sql` must run. The phase 4 trigger and FK on `auth.users` need TRIGGER and REFERENCES, which `postgres` has in 17.6.1.136 (checked 2026-10-09). Recheck after an image upgrade.
- If GoTrue moves to asymmetric JWT signing keys, `require_user` has to verify against `/auth/v1/.well-known/jwks.json` instead of `JWT_SECRET`.
- In production, GoTrue needs SMTP to send confirmation emails. Dev uses Mailpit, which catches all outgoing mail. `.env.dev` points SMTP at `mailpit`, which only exists in the devcontainer stack.
- Keep a single uvicorn worker, or APScheduler jobs run twice.
- `.devcontainer/docker-compose.yml` passes `ENABLED_DATASETS` from `.devcontainer/.env` into the app container, which overrides the value in `.env`.
- A local `.env` copied before a setting was added keeps missing it (the dev machine's `.env` had no `REDIS_URL` until the smoke test). Compare `.env` with `.env.dev` after env changes (`make switch2dev` overwrites local edits).
