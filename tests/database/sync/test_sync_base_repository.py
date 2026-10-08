import pytest

from geonames_server.database.models.cities15000 import Cities15000
from geonames_server.database.repository.sync.base_repository import (
    BaseRepository,
    NotFoundError,
)


class TestBaseRepository:
    def _make_repo(self, session):
        return BaseRepository(Cities15000, session)

    def test_add_and_get_by_id(self, sync_session, sync_engine):
        repo = self._make_repo(sync_session)
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
        result = repo.add(city)
        assert result.geonameid == 1

        fetched = repo.get_by_id(1)
        assert fetched is not None
        assert fetched.name == "TestCity"

    def test_get_by_id_returns_none(self, sync_session):
        repo = self._make_repo(sync_session)
        assert repo.get_by_id(99999) is None

    def test_get_or_raise(self, sync_session):
        repo = self._make_repo(sync_session)
        with pytest.raises(NotFoundError):
            repo.get_or_raise(99999)

    def test_add_many(self, sync_session):
        repo = self._make_repo(sync_session)
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
        result = repo.add_many(cities)
        assert len(result) == 3

    def test_list_with_limit(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.list(limit=2)
        assert len(results) == 2

    def test_list_all(self, sync_session):
        repo = self._make_repo(sync_session)
        results = repo.list()
        assert len(results) > 0

    def test_update(self, sync_session):
        repo = self._make_repo(sync_session)
        city = repo.get_by_id(2950159)  # Berlin — exists in DB
        original_name = city.name
        city.name = "Berlin Updated"
        repo.update(city)
        updated = repo.get_by_id(2950159)
        assert updated.name == "Berlin Updated"
        assert updated.name != original_name

    def test_delete(self, sync_session):
        repo = self._make_repo(sync_session)
        city = repo.get_by_id(2950159)  # Berlin — exists in DB
        repo.delete(city)
        assert repo.get_by_id(2950159) is None

    def test_delete_by_id(self, sync_session):
        repo = self._make_repo(sync_session)
        repo.delete_by_id(2950159)  # Berlin — exists in DB
        assert repo.get_by_id(2950159) is None

    def test_delete_by_id_not_found(self, sync_session):
        repo = self._make_repo(sync_session)
        with pytest.raises(NotFoundError):
            repo.delete_by_id(99999)

    def test_bulk_insert(self, sync_session):
        repo = self._make_repo(sync_session)
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
        repo.bulk_insert(cities)
        # Verify the inserted cities are retrievable
        for i in range(100, 105):
            assert repo.get_by_id(i) is not None
