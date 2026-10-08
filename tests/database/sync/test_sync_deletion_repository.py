from datetime import date

import pytest

from geonames_server.database.repository.filters.deletion_filter import DeletionFilter
from geonames_server.database.repository.sync.deletion_repository import (
    DeletionRepository,
)


@pytest.mark.usefixtures("seed_deletions")
class TestDeletionRepository:
    def _make_repo(self, session):
        return DeletionRepository(session)

    def test_get_by_geonameid(self, sync_session):
        repo = self._make_repo(sync_session)
        result = repo.get_by_geonameid(9999901)
        assert result is not None
        assert result.name == "OldTown"

    def test_get_by_geonameid_not_found(self, sync_session):
        repo = self._make_repo(sync_session)
        assert repo.get_by_geonameid(1) is None

    def test_get_by_deletion_date(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.get_by_deletion_date(date(2025, 3, 1))
        assert len(results) == 2

    def test_get_by_reason(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.get_by_reason("duplicate")
        assert len(results) == 1
        assert results[0].name == "OldTown"

    def test_filter_by_name(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DeletionFilter(name="Ghost"))
        assert len(results) == 1
        assert results[0].name == "GhostCity"

    def test_filter_by_reason(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DeletionFilter(reason="merged"))
        assert len(results) == 1

    def test_filter_by_deletion_date(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DeletionFilter(deletion_date=date(2025, 4, 1)))
        assert len(results) == 1

    def test_filter_with_ordering(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DeletionFilter(order_by="name"))
        assert results[0].name == "GhostCity"

    def test_filter_with_limit(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DeletionFilter(limit=1))
        assert len(results) == 1

    def test_filter_no_conditions(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.filter(DeletionFilter())
        assert len(results) == 3
