"""Tests for the Modifications model and CRUD via BaseRepository."""

from datetime import date

import pytest
from sqlalchemy import select

from geonames_server.database.models.modifications import Modifications
from geonames_server.database.repository.sync.base_repository import (
    BaseRepository,
    NotFoundError,
)


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


class TestModificationsModel:
    """Test the Modifications SQLAlchemy model itself."""

    def test_tablename(self):
        assert Modifications.__tablename__ == "modifications"

    def test_composite_primary_key(self):
        pk_cols = [c.name for c in Modifications.__table__.primary_key.columns]
        assert "geonameid" in pk_cols
        assert "modification_date" in pk_cols
        assert len(pk_cols) == 2

    def test_inherits_geobase_columns(self):
        col_names = {c.name for c in Modifications.__table__.columns}
        expected = {
            "geonameid", "name", "asciiname", "alternatenames",
            "latitude", "longitude", "feature_class", "feature_code",
            "country_code", "cc2", "admin1_code", "admin2_code",
            "admin3_code", "admin4_code", "population", "elevation",
            "dem", "timezone", "modification_date",
        }
        assert expected.issubset(col_names)

    def test_to_dict(self):
        mod = _make_modification()
        d = mod.to_dict()
        assert d["geonameid"] == 8000001
        assert d["name"] == "TestPlace"
        assert d["latitude"] == 52.52
        assert d["modification_date"] == "2025-07-01"

    def test_to_dict_with_none_modification_date(self):
        """GeoBase.to_dict handles None modification_date gracefully."""
        mod = _make_modification(modification_date=None)
        d = mod.to_dict()
        assert d["modification_date"] is None

    def test_repr(self):
        mod = _make_modification()
        r = repr(mod)
        assert "Modifications" in r
        assert "8000001" in r
        assert "TestPlace" in r

    def test_nullable_fields_default_to_none(self):
        mod = _make_modification()
        assert mod.alternatenames is None
        assert mod.cc2 is None
        assert mod.admin1_code is None
        assert mod.population is None
        assert mod.elevation is None
        assert mod.dem is None
        assert mod.timezone is None


class TestModificationsCRUD:
    """CRUD operations on Modifications via BaseRepository + sync_session."""

    def _repo(self, session):
        return BaseRepository(Modifications, session)

    def test_add_and_get_by_id(self, sync_session):
        repo = self._repo(sync_session)
        mod = _make_modification()
        repo.add(mod)

        # composite PK → get_by_id with tuple
        fetched = repo.get_by_id((8000001, date(2025, 7, 1)))
        assert fetched is not None
        assert fetched.name == "TestPlace"
        assert fetched.modification_date == date(2025, 7, 1)

    @pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning")
    def test_add_duplicate_pk_raises(self, sync_session):
        repo = self._repo(sync_session)
        repo.add(_make_modification())
        with pytest.raises(Exception):
            repo.add(_make_modification())

    @pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning")
    def test_same_geonameid_different_dates_rejected_by_unique(self, sync_session):
        """GeoBase defines geonameid with unique=True, so duplicate geonameid raises even with different dates."""
        repo = self._repo(sync_session)
        repo.add(_make_modification(modification_date=date(2025, 7, 1)))
        with pytest.raises(Exception):
            repo.add(_make_modification(modification_date=date(2025, 7, 2)))

    def test_different_geonameid_same_date(self, sync_session):
        repo = self._repo(sync_session)
        repo.add(_make_modification(geonameid=8000001, modification_date=date(2025, 7, 1)))
        repo.add(_make_modification(geonameid=8000002, modification_date=date(2025, 7, 1)))

        fetched1 = repo.get_by_id((8000001, date(2025, 7, 1)))
        fetched2 = repo.get_by_id((8000002, date(2025, 7, 1)))
        assert fetched1 is not None
        assert fetched2 is not None
        assert fetched1.geonameid != fetched2.geonameid

    def test_update(self, sync_session):
        repo = self._repo(sync_session)
        mod = _make_modification()
        repo.add(mod)

        mod.name = "UpdatedPlace"
        repo.update(mod)

        fetched = repo.get_by_id((8000001, date(2025, 7, 1)))
        assert fetched.name == "UpdatedPlace"

    def test_delete(self, sync_session):
        repo = self._repo(sync_session)
        mod = _make_modification()
        repo.add(mod)

        repo.delete(mod)
        assert repo.get_by_id((8000001, date(2025, 7, 1))) is None

    def test_delete_by_id(self, sync_session):
        repo = self._repo(sync_session)
        repo.add(_make_modification())
        repo.delete_by_id((8000001, date(2025, 7, 1)))
        assert repo.get_by_id((8000001, date(2025, 7, 1))) is None

    def test_delete_by_id_not_found(self, sync_session):
        repo = self._repo(sync_session)
        with pytest.raises(NotFoundError):
            repo.delete_by_id((9999999, date(2099, 1, 1)))

    def test_list_with_limit(self, sync_session):
        repo = self._repo(sync_session)
        for i in range(3):
            repo.add(_make_modification(geonameid=8000001 + i, modification_date=date(2025, 7, 1)))
        results = repo.list(limit=2)
        assert len(results) == 2

    def test_bulk_insert(self, sync_session):
        repo = self._repo(sync_session)
        mods = [
            _make_modification(geonameid=8000010 + i, modification_date=date(2025, 7, 1))
            for i in range(5)
        ]
        repo.bulk_insert(mods)
        results = repo.list()
        ids = {r.geonameid for r in results}
        expected = {8000010 + i for i in range(5)}
        assert expected.issubset(ids)

    def test_add_with_all_geobase_fields(self, sync_session):
        """Verify all inherited GeoBase fields persist correctly."""
        repo = self._repo(sync_session)
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
        repo.add(mod)
        fetched = repo.get_by_id((8000099, date(2025, 6, 15)))
        assert fetched.alternatenames == "FC,Fullcity"
        assert fetched.country_code == "FR"
        assert fetched.admin1_code == "11"
        assert fetched.population == 2161000
        assert fetched.elevation == 35
        assert fetched.dem == 42
        assert fetched.timezone == "Europe/Paris"

    def test_query_by_modification_date(self, sync_session):
        """Direct SQLAlchemy filter on modification_date column."""
        repo = self._repo(sync_session)
        repo.add(_make_modification(geonameid=8000051, modification_date=date(2025, 6, 1)))
        repo.add(_make_modification(geonameid=8000052, modification_date=date(2025, 7, 1)))
        repo.add(_make_modification(geonameid=8000053, modification_date=date(2025, 7, 1)))

        stmt = select(Modifications).where(Modifications.modification_date == date(2025, 7, 1))
        results = list(sync_session.scalars(stmt).all())
        assert len(results) == 2

    def test_query_by_country_code(self, sync_session):
        repo = self._repo(sync_session)
        repo.add(_make_modification(geonameid=8000061, country_code="DE"))
        repo.add(_make_modification(geonameid=8000062, country_code="FR"))

        stmt = select(Modifications).where(Modifications.country_code == "DE")
        results = list(sync_session.scalars(stmt).all())
        de_ids = {r.geonameid for r in results}
        assert 8000061 in de_ids
        assert 8000062 not in de_ids
