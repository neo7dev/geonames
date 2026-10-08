from datetime import datetime, timezone

import pytest

from geonames_server.database.repository.async_.dataset_metadata_repository import (
    AsyncDatasetMetaDataRepository,
)
from geonames_server.database.repository.filters.dataset_metadata_filter import (
    DatasetMetaDataFilter,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


@pytest.mark.usefixtures("seed_metadata_async")
class TestAsyncDatasetMetaDataRepository:
    def _make_repo(self, session):
        return AsyncDatasetMetaDataRepository(session)

    async def test_get_by_dataset(self, async_session):
        repo = self._make_repo(async_session)
        result = await repo.get_by_dataset("cities15000")
        assert result is not None
        assert result.dataset == "cities15000"

    async def test_get_by_dataset_not_found(self, async_session):
        repo = self._make_repo(async_session)
        assert await repo.get_by_dataset("nonexistent") is None

    async def test_get_out_of_sync(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.get_out_of_sync()
        assert len(results) == 1
        assert results[0].dataset == "allcountries"

    async def test_get_modified_after(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.get_modified_after(
            datetime(2025, 5, 1, tzinfo=timezone.utc)
        )
        assert len(results) >= 1

    async def test_mark_as_synced(self, async_session):
        repo = self._make_repo(async_session)
        # allcountries has mismatched etags (def456 vs def999),
        # so the before_update event listener will recompute out_of_sync=True
        result = await repo.mark_as_synced("allcountries")
        assert result is not None
        # Event listener overrides: etag mismatch means still out of sync
        assert result.out_of_sync is True

    async def test_mark_as_out_of_sync(self, async_session):
        repo = self._make_repo(async_session)
        # cities15000 has matching etags (abc123),
        # so the before_update event listener will recompute out_of_sync=False
        result = await repo.mark_as_out_of_sync("cities15000")
        assert result is not None
        # Event listener overrides: etag match means in sync
        assert result.out_of_sync is False

    async def test_mark_as_synced_not_found(self, async_session):
        repo = self._make_repo(async_session)
        result = await repo.mark_as_synced("nonexistent")
        assert result is None

    async def test_filter_by_dataset(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(DatasetMetaDataFilter(dataset="cities15000"))
        assert len(results) == 1

    async def test_filter_by_out_of_sync(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(DatasetMetaDataFilter(out_of_sync=True))
        assert len(results) == 1
        assert results[0].dataset == "allcountries"

    async def test_filter_by_etag(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(DatasetMetaDataFilter(etag="abc123"))
        assert len(results) == 1

    async def test_filter_by_min_content_length(self, async_session):
        # content_length removed; test filter by needs_rebuild instead
        repo = self._make_repo(async_session)
        results = await repo.filter(DatasetMetaDataFilter(needs_rebuild=True))
        assert len(results) == 1
        assert results[0].dataset == "allcountries"

    async def test_filter_by_max_content_length(self, async_session):
        # content_length removed; test filter by needs_rebuild=False instead
        repo = self._make_repo(async_session)
        results = await repo.filter(DatasetMetaDataFilter(needs_rebuild=False))
        assert len(results) == 2

    async def test_filter_with_ordering(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(DatasetMetaDataFilter(order_by="-dataset"))
        datasets = [r.dataset for r in results]
        assert datasets == sorted(datasets, reverse=True)

    async def test_filter_with_limit(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(DatasetMetaDataFilter(limit=1))
        assert len(results) == 1

    async def test_filter_no_conditions(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(DatasetMetaDataFilter())
        assert len(results) == 3
