"""Async tests for the Modifications model via AsyncBaseRepository."""

from datetime import date

import pytest
from sqlalchemy import select

from geonames_server.database.models.modifications import Modifications
from geonames_server.database.repository.async_.base_repository import (
    AsyncBaseRepository,
    NotFoundError,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _make_modification(**overrides) -> Modifications:
    defaults = dict(
        geonameid=8000001,
        name="TestPlace",
        asciiname="TestPlace",
        latitude=52.52,
        longitude=13.405,
        feature_class="P",
        feature_code="PPL",
        country_code="DE",
        modification_date=date(2025, 7, 1),
    )
    defaults.update(overrides)
    return Modifications(**defaults)


class TestAsyncModificationsCRUD:
    """CRUD operations on Modifications via AsyncBaseRepository."""

    def _repo(self, session):
        return AsyncBaseRepository(Modifications, session)

    async def test_add_and_get_by_id(self, async_session):
        repo = self._repo(async_session)
        mod = _make_modification()
        await repo.add(mod)

        fetched = await repo.get_by_id((8000001, date(2025, 7, 1)))
        assert fetched is not None
        assert fetched.name == "TestPlace"
        assert fetched.modification_date == date(2025, 7, 1)

    @pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning")
    async def test_add_duplicate_pk_raises(self, async_session):
        repo = self._repo(async_session)
        await repo.add(_make_modification())
        with pytest.raises(Exception):
            await repo.add(_make_modification())

    async def test_same_geonameid_different_dates_allowed(self, async_session):
        """A place can be modified on several days; the key is (geonameid, date)."""
        repo = self._repo(async_session)
        await repo.add(_make_modification(modification_date=date(2025, 7, 1)))
        await repo.add(_make_modification(modification_date=date(2025, 7, 2)))

        assert await repo.get_by_id((8000001, date(2025, 7, 1))) is not None
        assert await repo.get_by_id((8000001, date(2025, 7, 2))) is not None

    async def test_different_geonameid_same_date(self, async_session):
        repo = self._repo(async_session)
        await repo.add(_make_modification(geonameid=8000001, modification_date=date(2025, 7, 1)))
        await repo.add(_make_modification(geonameid=8000002, modification_date=date(2025, 7, 1)))

        fetched1 = await repo.get_by_id((8000001, date(2025, 7, 1)))
        fetched2 = await repo.get_by_id((8000002, date(2025, 7, 1)))
        assert fetched1 is not None
        assert fetched2 is not None
        assert fetched1.geonameid != fetched2.geonameid

    async def test_update(self, async_session):
        repo = self._repo(async_session)
        mod = _make_modification()
        await repo.add(mod)

        mod.name = "UpdatedPlace"
        await repo.update(mod)

        fetched = await repo.get_by_id((8000001, date(2025, 7, 1)))
        assert fetched.name == "UpdatedPlace"

    async def test_delete(self, async_session):
        repo = self._repo(async_session)
        mod = _make_modification()
        await repo.add(mod)

        await repo.delete(mod)
        assert await repo.get_by_id((8000001, date(2025, 7, 1))) is None

    async def test_delete_by_id(self, async_session):
        repo = self._repo(async_session)
        await repo.add(_make_modification())
        await repo.delete_by_id((8000001, date(2025, 7, 1)))
        assert await repo.get_by_id((8000001, date(2025, 7, 1))) is None

    async def test_delete_by_id_not_found(self, async_session):
        repo = self._repo(async_session)
        with pytest.raises(NotFoundError):
            await repo.delete_by_id((9999999, date(2099, 1, 1)))

    async def test_list_with_limit(self, async_session):
        repo = self._repo(async_session)
        for i in range(3):
            await repo.add(_make_modification(geonameid=8000001 + i, modification_date=date(2025, 7, 1)))
        results = await repo.list(limit=2)
        assert len(results) == 2

    async def test_bulk_insert(self, async_session):
        repo = self._repo(async_session)
        mods = [
            _make_modification(geonameid=8000010 + i, modification_date=date(2025, 7, 1))
            for i in range(5)
        ]
        await repo.bulk_insert(mods)
        results = await repo.list()
        ids = {r.geonameid for r in results}
        expected = {8000010 + i for i in range(5)}
        assert expected.issubset(ids)

    async def test_add_with_all_geobase_fields(self, async_session):
        """Verify all inherited GeoBase fields persist correctly."""
        repo = self._repo(async_session)
        mod = Modifications(
            geonameid=8000099,
            name="FullCity",
            asciiname="FullCity",
            alternatenames="FC,Fullcity",
            latitude=48.8566,
            longitude=2.3522,
            feature_class="P",
            feature_code="PPLC",
            country_code="FR",
            cc2="FR",
            admin1_code="11",
            admin2_code="75",
            admin3_code="751",
            admin4_code="75101",
            population=2161000,
            elevation=35,
            dem=42,
            timezone="Europe/Paris",
            modification_date=date(2025, 6, 15),
        )
        await repo.add(mod)
        fetched = await repo.get_by_id((8000099, date(2025, 6, 15)))
        assert fetched.alternatenames == "FC,Fullcity"
        assert fetched.country_code == "FR"
        assert fetched.admin1_code == "11"
        assert fetched.population == 2161000
        assert fetched.elevation == 35
        assert fetched.dem == 42
        assert fetched.timezone == "Europe/Paris"

    async def test_query_by_modification_date(self, async_session):
        repo = self._repo(async_session)
        await repo.add(_make_modification(geonameid=8000051, modification_date=date(2025, 6, 1)))
        await repo.add(_make_modification(geonameid=8000052, modification_date=date(2025, 7, 1)))
        await repo.add(_make_modification(geonameid=8000053, modification_date=date(2025, 7, 1)))

        stmt = select(Modifications).where(Modifications.modification_date == date(2025, 7, 1))
        result = await async_session.scalars(stmt)
        results = list(result.all())
        assert len(results) == 2

    async def test_query_by_country_code(self, async_session):
        repo = self._repo(async_session)
        await repo.add(_make_modification(geonameid=8000061, country_code="DE"))
        await repo.add(_make_modification(geonameid=8000062, country_code="FR"))

        stmt = select(Modifications).where(Modifications.country_code == "DE")
        result = await async_session.scalars(stmt)
        results = list(result.all())
        de_ids = {r.geonameid for r in results}
        assert 8000061 in de_ids
        assert 8000062 not in de_ids
