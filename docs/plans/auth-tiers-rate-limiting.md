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
| 4. Auth + API keys | Done (merged to `main` 2026-10-09) | Tiers, roles and user tables; new signups get the default role (free); login token checked for portal routes; API key (shown once, stored hashed) checked for data routes; `/portal/*` endpoints; `POST /sync` admin-only | Requests without a valid key get 401 and a non-admin sync gets 403 |
| 5. Rate limits + quota | Done (branch `phase-5-rate-limits`, not merged) | One Redis check covers per-minute, hourly and monthly limits; 429 with `Retry-After`; usage copied to Postgres every 5 min; requests still go through if Redis is down | The 11th call in a minute on basic gets 429; a role change in Studio applies within 60s |
| 6. Frontend | Not started | React + Vite + Tailwind + shadcn at `/ui`: signup/login (email, GitHub, Google), key management, usage page | Sign up, create a key, call the API, see the usage update |
| 7. Ops (later) | Not started | CI that builds and pushes the image, backups, TLS, key scopes and expiry | Image published to ghcr |

## Tiers

| Tier | Roles | /min | /hour | /month | API keys |
|---|---|---|---|---|---|
| basic | free (default) | 10 | 100 | 5k | 1 |
| plus | tester | 30 | 500 | 50k | 3 |
| pro | dev | 120 | 2.5k | 500k | 5 |
| max | sr_dev, admin | unlimited | unlimited | unlimited | unlimited |

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
- **Not in phase 4:** enforcing limits, `last_used_at` writes (phase 5 fills them in its 5-minute usage flush; the column exists now), `/portal/usage` (phase 5), key scopes and expiry (phase 7). A ban or soft delete in Supabase Auth refuses the user's keys and login tokens (added after review, `c63628a`); deleting the user removes their keys via the cascade.

### Database (`migrations/app/versions/<rev>_accounts.py`)

`migrations/app/env.py` creates schema `app` and sets `version_table_schema="app"`. Autogenerate compares only `app` (`include_schemas=True` with an `include_name` filter).

| Table | Columns |
|---|---|
| `app.tiers` | `name text PK`, `per_minute int NULL`, `per_hour int NULL`, `per_month int NULL`, `max_keys int NULL` (NULL = unlimited; `CHECK (> 0)`) |
| `app.roles` | `name text PK`, `tier text NOT NULL → tiers(name) ON UPDATE CASCADE`, `is_admin bool NOT NULL DEFAULT false`, `is_default bool NOT NULL DEFAULT false` |
| `app.profiles` | `id uuid PK → auth.users(id) ON DELETE CASCADE`, `email text`, `role text NOT NULL DEFAULT app.default_role() → roles(name) ON UPDATE CASCADE` (the role with `roles.is_default`; a deferred trigger keeps exactly one), `created_at timestamptz DEFAULT now()` |
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

## Phase 5 scope

Branch `phase-5-rate-limits` from `main`.

### Verified first (devcontainer, redis 7.4.11, redis-py 7.1.1, 2026-10-09)

- The limiter script below, called through `Redis.register_script`, allowed 10 calls at a limit of 10 and returned "minute exceeded" for the 11th and 12th. Rejected calls did not increment the counters. The first `INCR` set the window's TTL.
- After `SCRIPT FLUSH`, redis-py's `AsyncScript` falls back from `EVALSHA` to `EVAL` by itself, so a Redis restart needs no handling.
- `RENAME` of a missing key raises `ResponseError("no such key")`. A refused connection raises `redis.exceptions.ConnectionError` and a timeout raises `redis.exceptions.TimeoutError`, both subclasses of `RedisError`.

### Decisions

- **Fixed windows, one script.** Counters per user for the current UTC minute, hour and calendar month: `rl:{user_id}:m:{epoch // 60}`, `rl:{user_id}:h:{epoch // 3600}`, `rl:{user_id}:mo:{YYYYMM}`. One Lua script checks all three and, only if none is exhausted, increments all three. That is one round trip per request. A fixed window allows up to twice the per-minute limit across a minute boundary; for an internal tool that is fine, and a sliding window would cost a sorted set per user.
- **Python owns the clock.** The script takes the limits, TTLs and current time as arguments; Python computes window ids, TTLs (window end + 60 s) and `Retry-After`. Tests set the time through one `_now()` function instead of waiting.
- **Limits are per user, across all their keys.** Unknown keys are refused by `require_api_key` before the limiter runs, so they never count.
- **Rejected requests do not count.** A client retrying in a loop while limited does not burn its hourly or monthly quota.
- **What counts.** Every request that passes the key check and the limit, on every API-key route (datasets, `/modifications`, `/deletions`, `/metadata`, `/sync`). It counts before the handler runs, so a 404, 422 or dataset-building 503 still counts. `/portal/*` and the public routes are not limited.
- **Unlimited tiers still count.** A NULL limit is passed as `-1`, which the script skips, but it still increments the counters, so max-tier usage shows on `/portal/usage` and in Postgres.
- **429.** Body names the exhausted limit, e.g. `Rate limit exceeded: 10 requests per minute on the basic tier`. `Retry-After` is the whole seconds (at least 1) until that window ends. The script checks month, then hour, then minute, so when several are exhausted the client is told the longest wait. No `X-RateLimit-*` headers on success; `/portal/usage` shows remaining quota.
- **Postgres is the durable record, Redis the fast copy.** The flush below writes per-key hourly counts to Postgres, so a user's hour and month totals can always be rebuilt from `app.api_key_usage`. At most the usage not yet flushed (up to 5 minutes) can be lost, and only when Redis itself loses data: an app crash leaves it in Redis for the next flush, and prod Redis appends to disk every second.
- **Rebuild counters when missing.** If the user's month counter is missing, the script changes nothing and returns "missing". Python then sums the user's month and current hour from `app.api_key_usage` (one indexed query), writes both with `SET ... NX EX <ttl>`, and runs the script once more (a second "missing" lets the request through rather than loop). `NX` means a racing request, or counters that are still there, are never overwritten. This covers an app restart, a Redis restart and a wiped Redis alike, and loads only users who make requests. On a user's first request of a month the query returns 0. The minute counter is not rebuilt.
- **No Redis configured.** If `REDIS_URL` is unset, requests go through uncounted and startup logs a warning once.
- **Redis outage: count and limit in memory.** If a Redis call fails (`RedisError` or `OSError`), a warning is logged once per outage and Redis is skipped for 30 s, so requests do not each wait out the 1 s connect timeout. Meanwhile the limiter uses in-process counters with the same windows. A user's first request in the outage loads their month and hour totals from Postgres (the rebuild query above), so all three limits still hold. Usage goes to an in-memory per-key, per-hour count that the flush task writes straight to Postgres. If Postgres is also down, requests go through and the counts stay in memory until a flush succeeds.
- **Recovery.** After the 30 s, the next request tries Redis again. On success the limiter switches back to Redis and starts a reconcile task: flush Redis's pending usage and the in-memory counts to Postgres, then `DEL` the hour and month counters of every user seen during the outage, so their next request rebuilds them from Postgres with the outage requests included. If the Postgres write fails, the counters are not deleted and the next flush retries the whole reconcile. Requests made between the switch and the `DEL` are counted in the usage hash but drop out of the rebuilt counters, a few seconds at most.
- **Key lookup cache.** `require_api_key` caches the `Principal` by key hash in process for `AUTH_CACHE_SECONDS` (default 60, 0 turns it off), up to 10,000 entries (expired entries are dropped first, then the oldest). Misses are not cached. So a role, tier-limit, ban or soft-delete change made in Studio applies within 60 s, which is the phase's "done when" condition. A revoke through `DELETE /portal/keys/{id}` drops that key from the cache at once; a revoke made in Studio applies within 60 s. This relies on the single instance below. Login tokens are not cached (`/portal` is low traffic).
- **One app instance only.** For now the app runs as a single container with one uvicorn worker, which the scheduler already required. Phase 5 builds on it: the key cache and the outage counters live in process memory. With a second worker or replica, a portal revoke would only clear its own process's cache (the key keeps working elsewhere for up to 60 s), and during a Redis outage each process would allow the full limit. README "Docker Quickstart" says so. Lifting this needs Postgres `LISTEN/NOTIFY` triggers that clear every process's cache (which would also make Studio changes instant) and shared outage counting; not planned until more than one instance is needed.
- **Usage in Redis first.** The same script adds 1 to field `n:{key_id}:{epoch_hour}` and sets `t:{key_id}` to the request time in hash `usage:pending`. Per key and hour is what the portal and Studio need; the per-user window counters above are only for limits.
- **Flush every 5 minutes, exactly once.** An asyncio task started in the lifespan (not APScheduler: the scheduler starts only after dataset rebuilds, which can take hours on a fresh database) calls `flush_usage()` every `USAGE_FLUSH_SECONDS` (default 300), and once more at shutdown. A flush:
  1. `RENAME usage:pending usage:flushing:{uuid4}` (atomic; new requests start a fresh hash). `no such key` means nothing to do.
  2. For every `usage:flushing:*` key (`SCAN`; leftovers from failed flushes come first), in one Postgres transaction: `INSERT INTO app.usage_flushes (batch) ... ON CONFLICT DO NOTHING RETURNING batch`. No row back means this batch was already written; skip to step 3. Otherwise upsert the hourly counts (`requests = api_key_usage.requests + excluded.requests`) and set `api_keys.last_used_at = greatest(last_used_at, to_timestamp(t))`. Rows are inserted with `INSERT ... SELECT ... FROM unnest(...) JOIN app.api_keys`, so a key deleted since (user deleted) is dropped instead of failing the batch on its FK. Commit.
  3. `DEL` the batch key.
  If Postgres fails, the batch stays in Redis and the next flush retries it. If the process dies between commit and `DEL`, the `usage_flushes` row stops the retry from counting twice. `usage_flushes` rows older than 7 days are deleted during the flush.
- **`/portal/usage` reads both.** Current minute, hour and month counts come live from Redis (`MGET` of the three counters), so they update on every call. History comes from Postgres, so it lags by up to 5 minutes. If Redis is down, `live` is false and the counts come from the in-memory outage counters.
- **Not in phase 5:** `X-RateLimit-*` headers, limits on `/portal`, per-key limits, usage charts (phase 6), running more than one instance (see "One app instance only").

### Database (`migrations/app/versions/<rev>_api_key_usage.py`)

| Table | Columns |
|---|---|
| `app.api_key_usage` | `key_id uuid NOT NULL → api_keys(id) ON DELETE CASCADE`, `hour timestamptz NOT NULL` (start of the UTC hour), `requests bigint NOT NULL CHECK (requests > 0)`; PK `(key_id, hour)` |
| `app.usage_flushes` | `batch text PK`, `flushed_at timestamptz NOT NULL DEFAULT now()` |

- Index `api_keys_user_id_idx` on `api_keys(user_id)`. The existing index only covers active keys, and usage queries include revoked keys.
- View `app.monthly_usage` (`security_invoker`, like `user_tiers`): `user_id`, `email`, `role`, `tier`, `month` (UTC), `requests`, `per_month`, so Studio shows who is near their quota.
- Downgrade drops the view, tables and index.

### Code

- `config.py`: `auth_cache_seconds: int = 60` (`ge=0`), `usage_flush_seconds: int = 300` (`ge=10`). Add both to `.env.example` / `.env.dev` / `.env.prod` with their defaults, commented.
- `api/rate_limit.py`:
  - The Lua script (as verified above, plus a first step that returns "missing" when the month counter does not exist; keys: three counters plus `usage:pending`; args: three limits, three TTLs, usage field, last-used field, request time). Registered once per Redis client.
  - `window_keys(user_id, now)` and `seconds_until_reset(window, now)`: window ids, TTLs and `Retry-After`; the month window ends at 00:00 UTC on the 1st.
  - `stored_usage(session, user_id, now) -> (month, hour)`: the rebuild query, a sum over `api_key_usage` joined to the user's keys (revoked included).
  - `LocalLimiter`: the outage counters. Per-user window counts (loaded from `stored_usage` on first use, dropped when their window ends), per-key hourly usage and last-used times, and the set of users seen. `check_and_count(principal, now)` applies the same rules as the script. `take_usage()` swaps out the usage for a flush; `restore_usage()` puts it back if the write fails.
  - `enforce_rate_limit(request, principal = Depends(require_api_key)) -> Principal`: no-op when `app.state.redis` is None. Otherwise runs the script, rebuilding the counters on "missing"; on a Redis error, or during the 30 s backoff, uses `LocalLimiter` instead. Raises 429 when a window is exhausted and returns the principal. The first success after an outage starts the reconcile task.
  - `current_usage(redis, user_id, now)` for `/portal/usage`: the three live counts, or the `LocalLimiter`'s counts if Redis fails.
- `api/usage_flush.py`: `flush_usage(redis, session_factory)` as above, which also writes `LocalLimiter.take_usage()` (works with Redis down); `reconcile_after_outage(...)` (flush, then `DEL` the outage users' hour and month counters; leaves them flagged for the next flush if the write fails); and `run_usage_flusher(app, stopping)` (the loop: sleep, flush, finish any pending reconcile, log and continue on any error).
- `api/auth.py`: the principal cache (`_cache_get`, `_cache_put`, `forget_api_key(key_id)`, `clear_principal_cache()` for tests), used by `require_api_key`. `require_admin` depends on `enforce_rate_limit` instead of `require_api_key`.
- `api/app.py`: `_api_key = [Depends(enforce_rate_limit)]`. Pass `responses={429: ...}` to those `include_router` calls so the docs show the 429. Lifespan: warn when `redis_url` is unset; start the flusher task after the Redis client is created, then on shutdown cancel it, run a final flush, and close Redis.
- `api/routers/portal.py`: `DELETE /portal/keys/{id}` calls `forget_api_key` after commit. New `GET /portal/usage?days=30` (`1 <= days <= 90`, 422 otherwise), on `require_user`:
  - `tier`, `live` (bool)
  - `current`: `minute`, `hour`, `month`, each `{used, limit, resets_at}` (`limit` null when unlimited; `used` null for minute and hour when not live)
  - `daily`: `[{date, requests}]` for the last `days` UTC days, all of the user's keys, oldest first, zero-filled
  - `keys`: every key the user has had requests on this month or still holds: `{id, name, prefix, revoked, requests_this_month}` (from Postgres, so up to 5 minutes behind)
- `api/schemas/portal.py`: `UsageWindow`, `DailyUsage`, `KeyUsage`, `UsageResponse`.
- Makefile: `tests-pg` also sets `TEST_REDIS_URL` (default: `REDIS_URL` from `.env` with database 15). The `make tests` help text says the Redis tests skip without it.
- Regenerate `openapi.yaml`.

### Tests

Redis tests use a real Redis from `TEST_REDIS_URL` and skip without it, like `TEST_PG_DSN`. A `redis` fixture runs `FLUSHDB` before and after each test. An autouse fixture clears the principal cache and the Redis backoff.

- `tests/test_rate_limit_windows.py` (no Redis): window ids, TTLs and `Retry-After` at 00:00:00, 00:00:59, the last second of an hour, 31 December and 29 February; `Retry-After` is never below 1.
- `tests/api/test_rate_limit.py` (Redis + Postgres):
  - A free (basic) user: 10 calls in a minute pass, the 11th gets 429 with `Retry-After` up to the minute's end; next minute (clock moved) it passes again.
  - The 101st call in an hour and the 5001st in a month get 429 with the hour's and month's `Retry-After` (counters preset in Redis). Minute and month both exhausted: the month's `Retry-After`.
  - Rejected calls do not raise the counts. Two keys of one tester share one limit. Two users do not affect each other.
  - A max user is never limited, and their calls are counted.
  - `/sync` counts; `/portal/me` and `/health` don't. An unknown key gets 401 and counts nothing.
  - A limited user asking for a dataset that is still building gets 429, not 503.
  - Missing counters are rebuilt: with flushed `api_key_usage` rows of 4,990 requests this month and the Redis counters deleted, the 11th call after that gets 429 for the month. A new month starts at 0. Counters already in Redis are never overwritten by the rebuild.
  - Redis down (client on an unused port): one warning is logged, and a second request within 30 s does not touch Redis. Limits still hold from memory: a user with 4,999 flushed requests this month gets one more call, then 429. Their outage calls reach `api_key_usage` at the next flush, with Redis still down.
  - Recovery: after the outage, the outage users' Redis counters are rebuilt and include the outage calls. If the Postgres write fails during reconcile, the counters stay and the next flush finishes the job.
  - Redis and Postgres both down: requests pass; the counts are written once Postgres is back.
  - No Redis configured: requests pass, nothing is counted.
  - The done-when check: change the user's role from free to dev with an `UPDATE`; the 11th call still gets 429 until the cache entry expires (clock moved 61 s), then passes.
- `tests/api/test_principal_cache.py` (Postgres): a second request does not query the database; a role or tier-limit change, ban and Studio revoke apply after `AUTH_CACHE_SECONDS`; a portal revoke applies on the next request; `AUTH_CACHE_SECONDS=0` disables the cache; the size bound evicts the oldest.
- `tests/api/test_usage_flush.py` (Redis + Postgres): requests across two hours give two `api_key_usage` rows and set `last_used_at`; a second flush adds nothing; a Postgres failure keeps the batch and the next flush writes it once; a batch already in `usage_flushes` is deleted without writing; a deleted key's counts are dropped and the rest are written; old `usage_flushes` rows are pruned. Lifespan: shutdown runs a final flush.
- `tests/api/test_portal.py`: `/portal/usage` shows live counts that rise with each call, null limits for max, `live: false` and the in-memory counts when Redis is down, zero-filled daily history from flushed rows, revoked keys with `revoked: true`, nothing from other users, 422 for `days` 0 and 91.
- `tests/database/test_app_migration_pg.py`: the new tables, index and view exist after upgrade and are gone after downgrade; `alembic -n app check` still finds no drift.
- Existing tests are unchanged: `client` overrides `require_api_key`, and without `app.state.redis` the limiter does nothing.

### Docs

- README "Rate limits": the tiers table, what counts, 429 and `Retry-After`, fixed-window bursts at boundaries, Postgres as the durable record (up to 5 minutes of usage lost if Redis loses its data), in-memory limits while Redis is down, `/portal/usage` (live counts, history up to 5 minutes behind), and `app.monthly_usage` in Studio. The new settings.
- README "Docker Quickstart" already states the one-instance limit (added while scoping); extend it if the implementation changes the reasons.
- Update Progress here.

### Smoke test (devcontainer)

With the seeded dev users (`make seed-users`): 11 calls in a row with `DEV_API_KEY_FREE` on `/metadata/` give 10 × 200, then 429 with `Retry-After`. `/portal/usage` shows `minute.used = 10`. Set the user's role to `dev` in Studio; within 60 s the calls pass. Stop Redis (`docker stop`): one warning in the log, and the free key still gets 429 after 10 calls in a minute. Start it again: the counters are rebuilt and include the outage calls. Wait for a flush (or restart the app): `app.api_key_usage` has the rows, `last_used_at` is set and `app.monthly_usage` shows the user. `FLUSHALL` in Redis: the next call rebuilds the month count from Postgres.

### Commits

1. Usage migration (tables, index, view) and migration tests.
2. Principal cache in `require_api_key`, portal revoke clears it, cache tests.
3. `api/rate_limit.py`, wired into the API-key routes, settings, window and limit tests.
4. Usage flush task in the lifespan and its tests.
5. Counter rebuild from Postgres and its tests.
6. In-memory limiter for Redis outages, reconcile on recovery, and their tests.
7. `/portal/usage` and its tests.
8. Makefile, env files, README, `openapi.yaml`, plan progress.

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

### Phase 4: Auth + API keys (`f8eb5dd`, `49899f5`)

Built as scoped above, on `phase-4-auth`.

- Signups now get a profile from the `app_sync_profile` trigger, which runs `app.sync_profile_from_auth_user()`. `alembic -n app check` finds no drift between the models and the migration.
- Tests: 660 pass with `TEST_PG_DSN` set (key, JWT, portal, tier limit, admin, trigger and cascade tests, plus the migration tests on a database without `auth`).
- Smoke test (2026-10-09, devcontainer, real GoTrue):
  - Sign up through Kong, confirm via the Mailpit link, log in: `/portal/me` shows free/basic with `max_keys` 1.
  - Keys: the first is created, a second gets 409. A data route answers 401 without a key and 200 with it.
  - Sync: `POST /sync` gives 403 as free. After setting the role to admin in the database, it gives 202 and the sync completes.
  - Revoke and delete: a revoked key gets 401. Deleting the user from `auth.users` removed the profile and keys.
  - `FORCE_REBUILD` was not run live, because it reloads every dataset; `test_force_rebuild_keeps_user_data` covers it.
- `pyjwt` 2.15.1 added.
- Found while checking migrations, fixed separately:
  - `modifications.population` was still INTEGER, because `a684673d5b5c` recreated the table after the bigint migration. Migration `b8e1f4c2d9a6` widens it (`f8d1e07`).
  - Autogenerate on the geonames chain wanted to drop `alembic_version` (`2eac71b`).
  - Tests now run `alembic check` on both chains.
- Code review fixes (branch `phase-4-review-fixes`, one commit each, merged to `main` 2026-10-09):
  - `/sync`'s admin check no longer holds a database connection, mid-transaction, for the whole background sync (`de25f73`).
  - A blank `JWT_SECRET` counts as unset, so startup refuses to run; secrets under 32 characters are rejected (`d4f0c06`).
  - Signups get the role marked `roles.is_default` through `app.default_role()`, not the literal `'free'`; a deferred trigger keeps exactly one default (migration `a79ec0362d52`, `28f8b3f`).
  - Startup and `seed-users` add the `auth.users` FK, signup trigger and missing profiles if the accounts migration ran before `auth.users` existed (`database/auth_link.py`, `8202032`).
  - The trigger copies `banned_until` and `deleted_at` onto profiles; keys and logins of banned or soft-deleted users are refused (migration `bf313717012e`, `c63628a`).
  - Key names reject control characters (422, not 500 on NUL) (`ffd149d`).
  - The app chain works in offline `--sql` mode (`00a60a9`).
  - Migrations hold a Postgres advisory lock, so startup and `seed-users` cannot race (`9b67af4`).
  - `seed-users` keeps within the tier's key limit (`f2bd631`), and the devcontainer start skips it instead of failing when `.env` lacks the dev settings (`1e22758`).
  - The `9a4d2b6e1c8f` downgrade restores only the max tier's cap (`23a3686`).
  - The database URL is percent-encoded and escaped for Alembic, so any password works (`0763303`).
  - Tests only write rows they created (`a4494e2`); portal and Makefile cleanups (`f6e42da`, `70e7bc1`).
- Ruff added as a dev dependency with pinned rules, codebase formatted, and `make predeploy` (lint, format, lockfile, all tests with Postgres) plus `make image-check` added (`159b52f`, `6c13f14`, `281b64c`).
- Tests: 703 pass with `TEST_PG_DSN` set (`make tests-pg`). `make image-check` passes.

### Phase 5: Rate limits + quota (`561c06a`..`5a5738e`)

Built as scoped above, on `phase-5-rate-limits`, one commit per step: usage tables and view (`561c06a`), key lookup cache (`e1908a4`), Redis limiter (`e8870d7`), usage flush (`de4c8cd`), counter rebuild (`bab272f`), in-memory outage limiter (`e4ee8b6`), `/portal/usage` (`5a5738e`).

- Changed from the scope:
  - `require_admin` still depends on `require_api_key`. `/sync` takes `[require_admin, enforce_rate_limit]` instead (FastAPI runs the key lookup once), which avoids an import cycle and means a non-admin's refused `/sync` does not count.
  - `Usage` (the per-key, per-hour counts) lives in `api/rate_limit.py`, shared by the outage limiter and the flush.
  - If the rebuild query fails, the request goes through uncounted and the next request tries again.
  - Regenerating `openapi.yaml` also picked up `per_minute` being nullable, which the snapshot had missed since `9a4d2b6e1c8f`.
- Tests: 774 pass with `TEST_PG_DSN` and `TEST_REDIS_URL` set (`make tests-pg`). They use database 15 of the devcontainer Redis.
- Smoke test (2026-10-09, devcontainer, `uvicorn` on 9500, seeded dev users):
  - `DEV_API_KEY_FREE`: 10 × 200, then 429 with `Retry-After: 58` and the per-minute message. `/portal/usage` showed `minute.used = 10`, live.
  - Role change: the cache's 60 s matches the minute window, so a free → dev change cannot be told apart from the minute resetting. Checked with free → admin on `GET /sync/{id}` instead: 403 until 60 s after the cached lookup, then 404.
  - Redis stopped: 10 × 200 then 429 from memory, `/health` degraded, one warning. Redis started: the next call logged "Redis is back", the flush woke at once, wrote 12 requests and deleted the free user's counters; the next call rebuilt the month counter to 48 (47 flushed + 1). `FLUSHALL`: the next call rebuilt it from Postgres, dropping the one unflushed call, as designed.
  - `app.monthly_usage` and `last_used_at` showed every dev user's flushed calls. Shutdown logged the final flush.

## Risks

- A connection without the schema's `search_path` would read and write `public` without any error. Mitigated in phase 2: one setting feeds every connection, and startup asserts it.
- In the supabase/postgres image, `postgres` is not a superuser. `roles.sql` must run. The phase 4 trigger and FK on `auth.users` need TRIGGER and REFERENCES, which `postgres` has in 17.6.1.136 (checked 2026-10-09). Recheck after an image upgrade.
- If GoTrue moves to asymmetric JWT signing keys, `require_user` has to verify against `/auth/v1/.well-known/jwks.json` instead of `JWT_SECRET`.
- In production, GoTrue needs SMTP to send confirmation emails. Dev uses Mailpit, which catches all outgoing mail. `.env.dev` points SMTP at `mailpit`, which only exists in the devcontainer stack.
- Run one app instance with one uvicorn worker (README "Docker Quickstart"). A second would run APScheduler jobs twice and, from phase 5, keep using revoked keys for up to 60 s and allow each process the full limit during a Redis outage.
- `.devcontainer/docker-compose.yml` passes `ENABLED_DATASETS` from `.devcontainer/.env` into the app container, which overrides the value in `.env`.
- A local `.env` copied before a setting was added keeps missing it (the dev machine's `.env` had no `REDIS_URL` until the smoke test). Compare `.env` with `.env.dev` after env changes (`make switch2dev` overwrites local edits).
