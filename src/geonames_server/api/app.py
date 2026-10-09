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
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from geonames_server.api.routers import (
    dataset_metadata,
    deletions,
    health,
    modifications,
    sync,
)
from geonames_server.api.routers.geonames import create_geoname_router
from geonames_server.config.config import settings
from geonames_server.database.connection.connection import Connection
from geonames_server.database.migrations import db_migrations
from geonames_server.database.models.allcountries import AllCountries
from geonames_server.database.models.cities500 import Cities500
from geonames_server.database.models.cities1000 import Cities1000
from geonames_server.database.models.cities5000 import Cities5000
from geonames_server.database.models.cities15000 import Cities15000
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
        if app.state.redis is not None:
            await app.state.redis.aclose()
        log.info("Scheduler stopped — shutdown complete")


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
        build_all_datasets()
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

# Health router
app.include_router(health.router, prefix="/health")

# Geo dataset routers
for _model, _tag, _name in [
    (AllCountries, "All Countries", "allcountries"),
    (Cities500, "Cities 500", "cities500"),
    (Cities1000, "Cities 1000", "cities1000"),
    (Cities5000, "Cities 5000", "cities5000"),
    (Cities15000, "Cities 15000", "cities15000"),
]:
    app.include_router(create_geoname_router(_model, _tag, _name), prefix=f"/{_name}")

# Modifications router
app.include_router(modifications.router, prefix="/modifications")

# Deletions router
app.include_router(deletions.router, prefix="/deletions")

# Dataset metadata router
app.include_router(dataset_metadata.router, prefix="/metadata")

# Sync router
app.include_router(sync.router, prefix="/sync")
