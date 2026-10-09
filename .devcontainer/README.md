# Geonames server

Geonames server is a project which serves a database for datasets available on geonames.org

Do `make switch2dev` then `make run` to get started.

## Services

| Service | URL | Purpose |
|---|---|---|
| app | http://localhost:9500 | Dev container (run the API here) |
| db | localhost:5432 | Supabase Postgres 17. Holds geonames data, app data and Supabase auth. Connect as `postgres` to database `postgres` |
| kong | http://localhost:8000 | Supabase gateway: Studio dashboard at `/` (basic auth `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`), Auth API at `/auth/v1` |
| studio, meta | via kong | Supabase Studio admin dashboard and its Postgres metadata API |
| auth | via kong `/auth/v1` | Supabase Auth (GoTrue): signup, login, OAuth |
| mailpit | http://localhost:8025 | Catches all auth emails (signup confirmation, password reset). Nothing is delivered |
| redis | localhost:6379 | Rate limiting, quotas, caching |
| pgadmin | http://localhost:5050 | Postgres admin UI |
| gostatic | http://localhost:3002 | Serves geonames dumps from `gostatic/` |
| slow-download-server | http://localhost:3003 | Nginx serving `gostatic/` with a configurable download speed (`nginx.conf`) to test downloader retries and timeouts |

Supabase config (Kong routes, db init scripts) lives in `supabase/` at the repo root. It is a trimmed copy of the upstream self-hosted stack: PostgREST, Realtime, Storage, Edge Functions and Analytics are not deployed.

All secrets in `.devcontainer/.env` are dev-only throwaway values.

## Dev users

On every start the container runs `make seed-users`, which creates four confirmed users with password `dev-password`: `free@`, `tester@`, `dev@` and `admin@geonames.local`, one per tier. Their API keys are `DEV_API_KEY_FREE`, `_TESTER`, `_DEV` and `_ADMIN` in `.env.dev` / `.env`; only the admin can run `/sync`. Rerun it to restore their roles and keys. It needs `DEV_SEED_USERS=true` (set in `.env.dev`).

```sh
KEY=$(sed -n 's/^DEV_API_KEY_FREE *= *//p' .env)
curl -H "X-API-Key: $KEY" "http://localhost:9500/cities15000/?limit=5"
```

## OAuth (optional)

Create a GitHub and/or Google OAuth app with callback URL `http://localhost:8000/auth/v1/callback`, put the client id and secret in `.devcontainer/.env`, set `GITHUB_ENABLED` / `GOOGLE_ENABLED` to `true`, then recreate the `auth` service.

## Integration tests

`postgres` can create databases, so the `*_pg.py` tests can use a throwaway one:

```sh
psql -h db -U postgres -c "create database geonames_test"
TEST_PG_DSN="host=db port=5432 user=postgres password=dev-postgres-password dbname=geonames_test" uv run pytest tests
```

## Resetting Supabase

Supabase init scripts only run on an empty data directory. To start over, remove the `supabase-db-data` and `supabase-db-config` volumes.
