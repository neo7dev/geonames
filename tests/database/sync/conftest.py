from datetime import date, datetime, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from geonames_server.database.models.dataset_metadata import DatasetMetaData
from geonames_server.database.models.deletions import Deletions
from geonames_server.database.session import sync_engine as _engine


@pytest.fixture
def sync_engine():
    return _engine


@pytest.fixture
def sync_session():
    connection = _engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, trans):
        if trans.nested and not trans._parent.nested:
            session.begin_nested()

    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def seed_deletions(sync_session):
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
    sync_session.add_all(deletions)
    sync_session.commit()
    return deletions


@pytest.fixture
def seed_metadata(sync_session):
    now = datetime.now(timezone.utc)
    # Clear any pre-existing rows to avoid UniqueViolation
    sync_session.execute(DatasetMetaData.__table__.delete())
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
    sync_session.add_all(metadata)
    sync_session.commit()
    return metadata
