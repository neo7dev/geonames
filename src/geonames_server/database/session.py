from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import create_engine  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from geonames_server.config.config import settings

# Do NOT use echo=True/"debug" — it adds a raw StreamHandler to the
# sqlalchemy.engine logger, duplicating every message.  Instead the
# sqlalchemy.engine logger level is controlled in logger_config.json
# and can be overridden to DEBUG via settings.debug.

# Unqualified table names resolve in settings.postgres_schema, not public.
_connect_args = {"options": settings.postgres_options}

# sync engine for sync sessions / repositories
sync_engine = create_engine(
    settings.postges_url, echo=False, connect_args=_connect_args
)

# async engine for async sessions
async_engine = create_async_engine(
    settings.postges_url, echo=False, connect_args=_connect_args
)

# Match third-party logger levels to the application's LOG_LEVEL setting

sessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)

asyncSessionLocal = async_sessionmaker(
    bind=async_engine, expire_on_commit=False, class_=AsyncSession
)


@contextmanager
def get_session():
    session = sessionLocal()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


@asynccontextmanager
async def get_async_session():
    async with asyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise e
