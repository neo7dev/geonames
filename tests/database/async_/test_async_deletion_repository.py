from datetime import date

import pytest

from geonames_server.database.repository.async_.deletion_repository import (
    AsyncDeletionRepository,
)
from geonames_server.database.repository.filters.deletion_filter import DeletionFilter

pytestmark = pytest.mark.asyncio(loop_scope="function")


@pytest.mark.usefixtures("seed_deletions_async")
class TestAsyncDeletionRepository:
    def _make_repo(self, session):
        return AsyncDeletionRepository(session)

    async def test_get_by_geonameid(self, async_session):
        repo = self._make_repo(async_session)
        result = await repo.get_by_geonameid(9999901)
        assert result is not None
        assert result.name == "OldTown"

    async def test_get_by_geonameid_not_found(self, async_session):
        repo = self._make_repo(async_session)
        assert await repo.get_by_geonameid(1) is None

    async def test_get_by_deletion_date(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.get_by_deletion_date(date(2025, 3, 1))
        assert len(results) == 2

    async def test_get_by_reason(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.get_by_reason("duplicate")
        assert len(results) == 1
        assert results[0].name == "OldTown"

    async def test_filter_by_name(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(DeletionFilter(name="Ghost"))
        assert len(results) == 1
        assert results[0].name == "GhostCity"

    async def test_filter_by_reason(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(DeletionFilter(reason="merged"))
        assert len(results) == 1

    async def test_filter_with_ordering(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(DeletionFilter(order_by="name"))
        assert results[0].name == "GhostCity"

    async def test_filter_with_limit(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(DeletionFilter(limit=1))
        assert len(results) == 1

    async def test_filter_no_conditions(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(DeletionFilter())
        assert len(results) == 3
