# Plan: Deployable image, auth, tiers, rate limiting

Goal: ship one deployable app image, run via docker compose alongside Redis and self-hosted Supabase. Users sign up (email + password, GitHub, Google), manage API keys, and view usage/quota. Roles map to tiers. Admin dashboard is Supabase Studio. Internal tool, no paywall.

## Decisions

- Single Supabase Postgres: geonames data in `geonames` schema, user tables in `app` schema.
- Frontend: React + Vite + Tailwind + shadcn SPA, served by FastAPI at `/ui`.
- Limits per user (aggregate across keys): per-minute rate limit, hourly limit, calendar-month (UTC) quota.
- Tiers and role → tier mapping stored in DB tables, editable in Studio.

## Phases

| Phase | What | Done when |
|---|---|---|
| 1. Dev infra | Devcontainer compose runs a trimmed self-hosted Supabase (db, auth, kong, rest, meta, Studio), Redis and Mailpit; the app points at the Supabase db; no app code changes | Studio opens, datasets ingest into the Supabase db, integration tests pass against it |
| 2. Schema split | Geonames tables move to a `geonames` schema, kept separate from user data and Supabase's own tables | Tests show the tables in `geonames` and nothing left in `public` |
| 3. Deploy image + compose | Prod Dockerfile; root compose with the same services as dev; startup no longer waits for data loads; public `/health` | `docker compose up` brings it all up, `/health` returns 200, Studio opens |
| 4. Auth + API keys | Tiers, roles and user tables; new signups get the free role; login token checked for portal routes; API key (shown once, stored hashed) checked for data routes; `/portal/*` endpoints; `POST /sync` admin-only | Requests without a valid key get 401 and a non-admin sync gets 403 |
| 5. Rate limits + quota | One Redis check covers per-minute, hourly and monthly limits; 429 with `Retry-After`; usage copied to Postgres every 5 min; requests still go through if Redis is down | The 11th call in a minute on basic gets 429; a role change in Studio applies within 60s |
| 6. Frontend | React + Vite + Tailwind + shadcn at `/ui`: signup/login (email, GitHub, Google), key management, usage page | Sign up, create a key, call the API, see the usage update |
| 7. Ops (later) | CI that builds and pushes the image, backups, TLS, key scopes and expiry | Image published to ghcr |

## Tiers

| Tier | Roles | /min | /hour | /month |
|---|---|---|---|---|
| basic | free (default) | 10 | 100 | 5k |
| plus | tester | 30 | 500 | 50k |
| pro | dev | 120 | 2.5k | 500k |
| max | sr_dev, admin | 600 cap | unlimited | unlimited |

## Risks

- A connection without the `geonames` search_path writes to `public` without any error. Use one connection helper and assert the schema at startup.
- In the supabase/postgres image, `postgres` is not a superuser. `roles.sql` must run, and the trigger on `auth.users` needs privileges.
- Moving from Postgres 16 to the Supabase image means re-ingesting geonames data instead of migrating it.
- In production, GoTrue needs SMTP to send confirmation emails. Dev uses Mailpit, which catches all outgoing mail.
- Keep a single uvicorn worker, or APScheduler jobs run twice.
