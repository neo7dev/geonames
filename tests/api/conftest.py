from datetime import date, datetime, timezone

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api.app import app
from geonames_server.api.dependencies import get_session
from geonames_server.database.models.cities15000 import Cities15000
from geonames_server.database.models.dataset_metadata import DatasetMetaData
from geonames_server.database.models.deletions import Deletions
from geonames_server.database.models.modifications import Modifications
from geonames_server.database.session import async_engine


@pytest_asyncio.fixture
async def async_session():
    connection = await async_engine.connect()
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
async def client(async_session):
    async def _override_get_session():
        yield async_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seed_geonames(async_session):
    records = [
        Cities15000(
            geonameid=99000001,
            name="TestBerlin",
            asciiname="TestBerlin",
            alternatenames="Berlijn,Berlino",
            latitude=52.52437,
            longitude=13.41053,
            feature_class="P",
            feature_code="PPLC",
            country_code="DE",
            cc2=None,
            admin1_code="16",
            admin2_code="00",
            admin3_code=None,
            admin4_code=None,
            population=3677472,
            elevation=74,
            dem=43,
            timezone="Europe/Berlin",
            modification_date=date(2025, 3, 1),
        ),
        Cities15000(
            geonameid=99000002,
            name="TestTokyo",
            asciiname="TestTokyo",
            alternatenames="Tokio,Tokijo",
            latitude=35.6895,
            longitude=139.69171,
            feature_class="P",
            feature_code="PPLC",
            country_code="JP",
            cc2=None,
            admin1_code="40",
            admin2_code=None,
            admin3_code=None,
            admin4_code=None,
            population=8336599,
            elevation=40,
            dem=40,
            timezone="Asia/Tokyo",
            modification_date=date(2025, 1, 15),
        ),
        Cities15000(
            geonameid=99000003,
            name="TestLondon",
            asciiname="TestLondon",
            alternatenames="Londres,Londra",
            latitude=51.50853,
            longitude=-0.12574,
            feature_class="P",
            feature_code="PPLC",
            country_code="GB",
            cc2=None,
            admin1_code="ENG",
            admin2_code="GLA",
            admin3_code=None,
            admin4_code=None,
            population=7556900,
            elevation=11,
            dem=25,
            timezone="Europe/London",
            modification_date=date(2025, 2, 10),
        ),
        Cities15000(
            geonameid=99000004,
            name="TestMunich",
            asciiname="TestMunich",
            alternatenames="Muenchen,München",
            latitude=48.13743,
            longitude=11.57549,
            feature_class="P",
            feature_code="PPLA",
            country_code="DE",
            cc2=None,
            admin1_code="02",
            admin2_code="091",
            admin3_code=None,
            admin4_code=None,
            population=1260391,
            elevation=524,
            dem=524,
            timezone="Europe/Berlin",
            modification_date=date(2025, 2, 20),
        ),
    ]
    async_session.add_all(records)
    await async_session.commit()
    return records


@pytest_asyncio.fixture
async def seed_deletions(async_session):
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
async def seed_metadata(async_session):
    now = datetime.now(timezone.utc)
    # Clear any pre-existing rows to avoid UniqueViolation
    await async_session.execute(
        DatasetMetaData.__table__.delete()
    )
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


@pytest_asyncio.fixture
async def seed_modifications(async_session):
    records = [
        Modifications(
            geonameid=88000001,
            name="ModifiedBerlin",
            asciiname="ModifiedBerlin",
            alternatenames=None,
            latitude=52.52,
            longitude=13.41,
            feature_class="P",
            feature_code="PPLC",
            country_code="DE",
            cc2=None,
            admin1_code="16",
            admin2_code=None,
            admin3_code=None,
            admin4_code=None,
            population=3700000,
            elevation=34,
            dem=34,
            timezone="Europe/Berlin",
            modification_date=date(2025, 3, 8),
        ),
        Modifications(
            geonameid=88000002,
            name="ModifiedTokyo",
            asciiname="ModifiedTokyo",
            alternatenames=None,
            latitude=35.68,
            longitude=139.69,
            feature_class="P",
            feature_code="PPLC",
            country_code="JP",
            cc2=None,
            admin1_code="40",
            admin2_code=None,
            admin3_code=None,
            admin4_code=None,
            population=8400000,
            elevation=40,
            dem=40,
            timezone="Asia/Tokyo",
            modification_date=date(2025, 3, 8),
        ),
        Modifications(
            geonameid=88000003,
            name="ModifiedParis",
            asciiname="ModifiedParis",
            alternatenames=None,
            latitude=48.85,
            longitude=2.35,
            feature_class="P",
            feature_code="PPLC",
            country_code="FR",
            cc2=None,
            admin1_code="11",
            admin2_code=None,
            admin3_code=None,
            admin4_code=None,
            population=2161000,
            elevation=35,
            dem=35,
            timezone="Europe/Paris",
            modification_date=date(2025, 4, 1),
        ),
    ]
    async_session.add_all(records)
    await async_session.commit()
    return records
