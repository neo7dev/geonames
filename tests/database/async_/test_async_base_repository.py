import pytest

from geonames_server.database.models.cities15000 import Cities15000
from geonames_server.database.repository.async_.base_repository import (
    AsyncBaseRepository,
    NotFoundError,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


class TestAsyncBaseRepository:
    def _make_repo(self, session):
        return AsyncBaseRepository(Cities15000, session)

    async def test_add_and_get_by_id(self, async_session):
        repo = self._make_repo(async_session)
        city = Cities15000(
            geonameid=1,
            name="TestCity",
            asciiname="TestCity",
            latitude=0.0,
            longitude=0.0,
            feature_class="P",
            feature_code="PPL",
            country_code="XX",
        )
        result = await repo.add(city)
        assert result.geonameid == 1

        fetched = await repo.get_by_id(1)
        assert fetched is not None
        assert fetched.name == "TestCity"

    async def test_get_by_id_returns_none(self, async_session):
        repo = self._make_repo(async_session)
        assert await repo.get_by_id(99999) is None

    async def test_get_or_raise(self, async_session):
        repo = self._make_repo(async_session)
        with pytest.raises(NotFoundError):
            await repo.get_or_raise(99999)

    async def test_add_many(self, async_session):
        repo = self._make_repo(async_session)
        cities = [
            Cities15000(
                geonameid=i,
                name=f"City{i}",
                asciiname=f"City{i}",
                latitude=0.0,
                longitude=0.0,
                feature_class="P",
                feature_code="PPL",
                country_code="XX",
            )
            for i in range(10, 13)
        ]
        result = await repo.add_many(cities)
        assert len(result) == 3

    async def test_list_with_limit(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.list(limit=2)
        assert len(results) == 2

    async def test_list_all(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.list()
        assert len(results) > 0

    async def test_update(self, async_session):
        repo = self._make_repo(async_session)
        city = await repo.get_by_id(2950159)  # Berlin — exists in DB
        original_name = city.name
        city.name = "Berlin Updated"
        await repo.update(city)
        updated = await repo.get_by_id(2950159)
        assert updated.name == "Berlin Updated"
        assert updated.name != original_name

    async def test_delete(self, async_session):
        repo = self._make_repo(async_session)
        city = await repo.get_by_id(2950159)  # Berlin — exists in DB
        await repo.delete(city)
        assert await repo.get_by_id(2950159) is None

    async def test_delete_by_id(self, async_session):
        repo = self._make_repo(async_session)
        await repo.delete_by_id(2950159)  # Berlin — exists in DB
        assert await repo.get_by_id(2950159) is None

    async def test_delete_by_id_not_found(self, async_session):
        repo = self._make_repo(async_session)
        with pytest.raises(NotFoundError):
            await repo.delete_by_id(99999)

    async def test_bulk_insert(self, async_session):
        repo = self._make_repo(async_session)
        cities = [
            Cities15000(
                geonameid=i,
                name=f"Bulk{i}",
                asciiname=f"Bulk{i}",
                latitude=0.0,
                longitude=0.0,
                feature_class="P",
                feature_code="PPL",
                country_code="XX",
            )
            for i in range(100, 105)
        ]
        await repo.bulk_insert(cities)
        # Verify the inserted cities are retrievable
        for i in range(100, 105):
            assert await repo.get_by_id(i) is not None
