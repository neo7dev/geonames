from datetime import date, datetime, timezone

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.database.models.dataset_metadata import DatasetMetaData
from geonames_server.database.models.deletions import Deletions
from geonames_server.database.session import async_engine as _async_engine


@pytest_asyncio.fixture
async def async_session():
    connection = await _async_engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(bind=connection)
    await session.begin_nested()

    @event.listens_for(session.sync_session, "after_transaction_end")
    def restart_savepoint(sync_session, trans):
        if trans.nested and not trans._parent.nested:
            sync_session.begin_nested()

    yield session
    await session.close()
    await transaction.rollback()
    await connection.close()


@pytest_asyncio.fixture
async def seed_deletions_async(async_session):
    deletions = [
        Deletions(
            geonameid=9999901,
            name="OldTown",
            deletion_date=date(2025, 3, 1),
            reason="duplicate",
        ),
        Deletions(
            geonameid=9999902,
            name="GhostCity",
            deletion_date=date(2025, 4, 1),
            reason="never existed",
        ),
        Deletions(
            geonameid=9999903,
            name="Removed Place",
            deletion_date=date(2025, 3, 1),
            reason="merged",
        ),
    ]
    async_session.add_all(deletions)
    await async_session.commit()
    return deletions


@pytest_asyncio.fixture
async def seed_metadata_async(async_session):
    now = datetime.now(timezone.utc)
    # Clear any pre-existing rows to avoid UniqueViolation
    await async_session.execute(DatasetMetaData.__table__.delete())
    metadata = [
        DatasetMetaData(
            dataset="cities15000",
            txt_file="cities15000.txt",
            zip_file="cities15000.zip",
            out_of_sync=False,
            needs_rebuild=False,
            last_modified=now,
            remote_last_modified=now,
            etag="abc123",
            remote_etag="abc123",
        ),
        DatasetMetaData(
            dataset="allcountries",
            txt_file="allCountries.txt",
            zip_file="allCountries.zip",
            out_of_sync=True,
            needs_rebuild=True,
            last_modified=datetime(2025, 6, 1, tzinfo=timezone.utc),
            remote_last_modified=datetime(2025, 7, 1, tzinfo=timezone.utc),
            etag="def456",
            remote_etag="def999",
        ),
        DatasetMetaData(
            dataset="cities500",
            txt_file="cities500.txt",
            zip_file="cities500.zip",
            out_of_sync=False,
            needs_rebuild=False,
            last_modified=now,
            remote_last_modified=now,
            etag="ghi789",
            remote_etag="ghi789",
        ),
    ]
    async_session.add_all(metadata)
    await async_session.commit()
    return metadata
