"""GeoNames Server entry point.

Runs database setup, checks remote metadata, rebuilds datasets that need it
(blocking), starts AsyncIOScheduler for daily updates, then serves the
FastAPI application.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from geonames_server.api.routers import dataset_metadata, deletions, modifications, sync
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

    # 4. HEAD-check all datasets to refresh remote metadata
    #    Event listeners auto-compute needs_rebuild / out_of_sync
    log.info("Checking remote metadata for all datasets")
    await asyncio.get_event_loop().run_in_executor(
        None, check_remote_metadata, all_datasets
    )
    log.info("Remote metadata check complete")

    # 5. Rebuild enabled datasets that need it (blocks API until done)
    log.info("Checking for datasets that need rebuilding")
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, build_all_datasets)
    except Exception:
        log.exception("Dataset rebuild failed")
    log.info("Dataset rebuild step complete")

    # 6. Start the scheduler for daily updates
    log.info("Starting AsyncIOScheduler for daily updates")
    start_scheduler()
    log.info("Scheduler started")

    log.info("GeoNames Server ready — API is now accepting requests")
    try:
        yield
    finally:
        log.info("Shutting down GeoNames Server")
        stop_scheduler()
        log.info("Scheduler stopped — shutdown complete")


app = FastAPI(title="GeoNames Server", version=settings.app_version, lifespan=lifespan)

# Geo dataset routers
app.include_router(
    create_geoname_router(AllCountries, "All Countries"), prefix="/allcountries"
)
app.include_router(create_geoname_router(Cities500, "Cities 500"), prefix="/cities500")
app.include_router(
    create_geoname_router(Cities1000, "Cities 1000"), prefix="/cities1000"
)
app.include_router(
    create_geoname_router(Cities5000, "Cities 5000"), prefix="/cities5000"
)
app.include_router(
    create_geoname_router(Cities15000, "Cities 15000"), prefix="/cities15000"
)

# Modifications router
app.include_router(modifications.router, prefix="/modifications")

# Deletions router
app.include_router(deletions.router, prefix="/deletions")

# Dataset metadata router
app.include_router(dataset_metadata.router, prefix="/metadata")

# Sync router
app.include_router(sync.router, prefix="/sync")
