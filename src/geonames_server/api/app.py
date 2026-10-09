"""GeoNames Server entry point.

Runs database setup, then serves the FastAPI application while a background
thread checks remote metadata, rebuilds datasets that need it and finally
starts the AsyncIOScheduler for daily updates. /health reports progress.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI
from redis.asyncio import Redis

from geonames_server.api.auth import require_admin
from geonames_server.api.rate_limit import RateLimiter, enforce_rate_limit
from geonames_server.api.routers import (
    dataset_metadata,
    deletions,
    health,
    modifications,
    portal,
    sync,
)
from geonames_server.api.routers.geonames import create_geoname_router
from geonames_server.api.usage_flush import flush_usage, run_usage_flusher
from geonames_server.config.config import settings
from geonames_server.database.connection.connection import Connection
from geonames_server.database.migrations import db_migrations
from geonames_server.database.models.allcountries import AllCountries
from geonames_server.database.models.cities500 import Cities500
from geonames_server.database.models.cities1000 import Cities1000
from geonames_server.database.models.cities5000 import Cities5000
from geonames_server.database.models.cities15000 import Cities15000
from geonames_server.database.session import asyncSessionLocal
from geonames_server.ingestion.pipeline import (
    build_all_datasets,
    check_remote_metadata,
    seed_dataset_metadata,
)
from geonames_server.ingestion.registry import get_all_datasets
from geonames_server.ingestion.scheduler import start_scheduler, stop_scheduler
from geonames_server.log.mylogger import get_logger

log = get_logger()


def _change_log_levels() -> None:
    """Match third-party logger levels to the application's LOG_LEVEL setting."""
    _level = settings.log_level.upper()
    logging.getLogger("sqlalchemy.engine").setLevel(_level)
    logging.getLogger("uvicorn").setLevel(_level)
    logging.getLogger("uvicorn.error").setLevel(_level)
    logging.getLogger("uvicorn.access").setLevel(_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _change_log_levels()

    log.info("Starting GeoNames Server lifespan")

    # 1. Check database connection — exit 1 if unavailable
    connection = Connection(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
    )
    try:
        connection.check()
    except Exception:
        log.critical("Database is not available — cannot start the server")
        sys.exit(1)
    log.info("Database connection verified")

    if settings.jwt_secret is None:
        log.critical("JWT_SECRET is not set — cannot verify login tokens")
        sys.exit(1)

    # 2. Initialize database (force drops all tables + re-migrates)
    force = settings.force_rebuild
    log.info("Running database migrations", extra={"force_rebuild": force})
    db_migrations(force=force)
    log.info("Database migrations complete")

    # 3. Seed metadata rows for ALL datasets
    all_datasets = get_all_datasets()
    log.info("Seeding dataset metadata for %d dataset(s)", len(all_datasets))
    seed_dataset_metadata(all_datasets)
    log.info("Dataset metadata seeded")

    # Shared Redis client; it connects on first use, so a Redis outage does not
    # stop startup (/health reports it instead).
    app.state.redis = (
        Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        if settings.redis_url
        else None
    )
    flusher = None
    if app.state.redis is None:
        log.warning("REDIS_URL is not set — rate limits are off, usage is not counted")
        app.state.rate_limiter = None
    else:
        app.state.rate_limiter = RateLimiter(app.state.redis, asyncSessionLocal)
        # Copies the request counts to Postgres every USAGE_FLUSH_SECONDS. Not an
        # APScheduler job: the scheduler starts only after the dataset rebuilds.
        flusher = asyncio.create_task(
            run_usage_flusher(
                app.state.rate_limiter, asyncSessionLocal, settings.usage_flush_seconds
            ),
            name="usage-flusher",
        )

    # 4-6. Remote check, rebuilds and scheduler run in the background, so the API
    #      (and /health) answer while large datasets download and load.
    app.state.dataset_startup = "pending"
    stopping = threading.Event()
    threading.Thread(
        target=_prepare_datasets,
        args=(app, all_datasets, asyncio.get_running_loop(), stopping),
        name="dataset-startup",
        # A daemon thread does not hold up shutdown; an interrupted load rolls
        # back because each dataset is loaded and swapped in one transaction.
        daemon=True,
    ).start()

    log.info("GeoNames Server ready — API is now accepting requests")
    try:
        yield
    finally:
        log.info("Shutting down GeoNames Server")
        stopping.set()
        stop_scheduler()
        if flusher is not None:
            flusher.cancel()
            with suppress(asyncio.CancelledError):
                await flusher
            await _final_usage_flush(app)
        if app.state.redis is not None:
            await app.state.redis.aclose()
        log.info("Scheduler stopped — shutdown complete")


async def _final_usage_flush(app: FastAPI) -> None:
    try:
        written = await flush_usage(app.state.rate_limiter, asyncSessionLocal)
        log.info(f"Final usage flush wrote {written} request(s)")
    except Exception as e:
        # Only what Redis holds survives the restart.
        lost = app.state.rate_limiter.local.unwritten_total()
        log.warning(
            f"Final usage flush failed ({e!r}); the next start flushes what Redis "
            f"holds, {lost} request(s) counted in memory are lost"
        )


def _prepare_datasets(
    app: FastAPI,
    all_datasets: list,
    loop: asyncio.AbstractEventLoop,
    stopping: threading.Event,
) -> None:
    """Refresh remote metadata, rebuild datasets that need it, start the scheduler.

    The scheduler starts only after the rebuilds, so its startup daily update
    does not race a rebuild. It also starts if the rebuild failed: daily updates
    and the rebuild flags still need to run.
    """
    app.state.dataset_startup = "running"
    try:
        # 4. HEAD-check all datasets to refresh remote metadata
        #    Event listeners auto-compute needs_rebuild / out_of_sync
        log.info("Checking remote metadata for all datasets")
        check_remote_metadata(all_datasets)
        log.info("Remote metadata check complete")

        # 5. Rebuild enabled datasets that need it
        log.info("Checking for datasets that need rebuilding")
        failed = build_all_datasets()
        if failed:
            log.error(f"Startup could not build dataset(s): {failed}")
            app.state.dataset_startup = "failed"
        else:
            log.info("Dataset rebuild step complete")
            app.state.dataset_startup = "done"
    except Exception:
        log.exception("Dataset startup preparation failed")
        app.state.dataset_startup = "failed"

    # 6. Start the scheduler for daily updates. It must start on the event loop's
    #    thread; checking `stopping` there orders it against shutdown's stop_scheduler.
    def _start() -> None:
        if not stopping.is_set():
            log.info("Starting AsyncIOScheduler for daily updates")
            start_scheduler()

    try:
        loop.call_soon_threadsafe(_start)
    except RuntimeError:
        log.info("Event loop closed before the scheduler could start")


app = FastAPI(title="GeoNames Server", version=settings.app_version, lifespan=lifespan)

# Health router, public like the docs
app.include_router(health.router, prefix="/health")

# Data routes need an API key and count against the caller's rate limits.
# Router-level dependencies run before the routers' own, so a request without a
# key gets 401, and a limited one 429, before any dataset check.
_api_key = [Depends(enforce_rate_limit)]
_limit_responses = {
    429: {"description": "Rate limit exceeded; retry after Retry-After seconds"}
}

# Geo dataset routers
for _model, _tag, _name in [
    (AllCountries, "All Countries", "allcountries"),
    (Cities500, "Cities 500", "cities500"),
    (Cities1000, "Cities 1000", "cities1000"),
    (Cities5000, "Cities 5000", "cities5000"),
    (Cities15000, "Cities 15000", "cities15000"),
]:
    app.include_router(
        create_geoname_router(_model, _tag, _name),
        prefix=f"/{_name}",
        dependencies=_api_key,
        responses=_limit_responses,
    )

# Modifications router
app.include_router(
    modifications.router,
    prefix="/modifications",
    dependencies=_api_key,
    responses=_limit_responses,
)

# Deletions router
app.include_router(
    deletions.router,
    prefix="/deletions",
    dependencies=_api_key,
    responses=_limit_responses,
)

# Dataset metadata router
app.include_router(
    dataset_metadata.router,
    prefix="/metadata",
    dependencies=_api_key,
    responses=_limit_responses,
)

# Sync router: admins only. The admin check comes first, so a refused
# non-admin call does not count against their limits.
app.include_router(
    sync.router,
    prefix="/sync",
    dependencies=[Depends(require_admin), Depends(enforce_rate_limit)],
    responses=_limit_responses,
)

# Account pages, authenticated with the Supabase Auth login token
app.include_router(portal.router, prefix="/portal")
