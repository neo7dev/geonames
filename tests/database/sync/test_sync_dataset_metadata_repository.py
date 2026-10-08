from datetime import datetime, timezone

import pytest

from geonames_server.database.repository.filters.dataset_metadata_filter import (
    DatasetMetaDataFilter,
)
from geonames_server.database.repository.sync.dataset_metadata_repository import (
    DatasetMetaDataRepository,
)


@pytest.mark.usefixtures("seed_metadata")
class TestDatasetMetaDataRepository:
    def _make_repo(self, session):
        return DatasetMetaDataRepository(session)

    def test_get_by_dataset(self, sync_session):
        repo = self._make_repo(sync_session)
        result = repo.get_by_dataset("cities15000")
        assert result is not None
        assert result.dataset == "cities15000"

    def test_get_by_dataset_not_found(self, sync_session):
        repo = self._make_repo(sync_session)
        assert repo.get_by_dataset("nonexistent") is None

    def test_get_out_of_sync(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.get_out_of_sync()
        assert len(results) == 1
        assert results[0].dataset == "allcountries"

    def test_get_modified_after(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.get_modified_after(datetime(2025, 5, 1, tzinfo=timezone.utc))
        assert len(results) >= 1

    def test_mark_as_synced(self, sync_session):
        repo = self._make_repo(sync_session)
        # allcountries has mismatched etags (def456 vs def999),
        # so the before_update event listener will recompute out_of_sync=True
        result = repo.mark_as_synced("allcountries")
        assert result is not None
        # Event listener overrides: etag mismatch means still out of sync
        assert result.out_of_sync is True

    def test_mark_as_out_of_sync(self, sync_session):
        repo = self._make_repo(sync_session)
        # cities15000 has matching etags (abc123),
        # so the before_update event listener will recompute out_of_sync=False
        result = repo.mark_as_out_of_sync("cities15000")
        assert result is not None
        # Event listener overrides: etag match means in sync
        assert result.out_of_sync is False

    def test_mark_as_synced_not_found(self, sync_session):
        repo = self._make_repo(sync_session)
        result = repo.mark_as_synced("nonexistent")
        assert result is None

    def test_filter_by_dataset(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DatasetMetaDataFilter(dataset="cities15000"))
        assert len(results) == 1

    def test_filter_by_out_of_sync(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DatasetMetaDataFilter(out_of_sync=True))
        assert len(results) == 1
        assert results[0].dataset == "allcountries"

    def test_filter_by_etag(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DatasetMetaDataFilter(etag="abc123"))
        assert len(results) == 1

    def test_filter_by_min_content_length(self, sync_session):
        # content_length removed; test filter by needs_rebuild instead
        repo = self._make_repo(sync_session)
        results = repo.filter(DatasetMetaDataFilter(needs_rebuild=True))
        assert len(results) == 1
        assert results[0].dataset == "allcountries"

    def test_filter_by_max_content_length(self, sync_session):
        # content_length removed; test filter by needs_rebuild=False instead
        repo = self._make_repo(sync_session)
        results = repo.filter(DatasetMetaDataFilter(needs_rebuild=False))
        assert len(results) == 2

    def test_filter_with_ordering(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DatasetMetaDataFilter(order_by="-dataset"))
        datasets = [r.dataset for r in results]
        assert datasets == sorted(datasets, reverse=True)

    def test_filter_with_limit(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DatasetMetaDataFilter(limit=1))
        assert len(results) == 1

    def test_filter_no_conditions(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DatasetMetaDataFilter())
        assert len(results) == 3
