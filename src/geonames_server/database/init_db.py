import asyncio

# Required for sqlalchemy.ext.asyncio to work properly, even if not directly used in this file
# This will help create tables for all models that are imported in geonames_server.database.models
import geonames_server.database.models  # noqa: F401
from geonames_server.database.models.base import Base
from geonames_server.database.session import async_engine
from geonames_server.log.mylogger import get_logger

log = get_logger()


async def _async_init_db():
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info(f"Created tables: {', '.join(Base.metadata.tables)}")


def init_db():
    asyncio.run(_async_init_db())
