from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from geonames_server.database.repository.filters.dataset_metadata_filter import (
    DatasetMetaDataFilter,
    DatasetMetaDataOrderBy,
)
from geonames_server.database.repository.filters.deletion_filter import (
    DeletionFilter,
    DeletionOrderBy,
)
from geonames_server.database.repository.filters.geonames_filter import (
    GeoNamesFilter,
    GeoNamesOrderBy,
)


class TestGeoNamesFilter:
    def test_defaults_are_none(self):
        f = GeoNamesFilter()
        assert f.name is None
        assert f.population is None
        assert f.order_by is None
        assert f.limit is None

    def test_accepts_valid_fields(self):
        f = GeoNamesFilter(
            country_code="DE",
            min_population=1000,
            order_by=GeoNamesOrderBy.POPULATION_DESC,
            limit=10,
        )
        assert f.country_code == "DE"
        assert f.min_population == 1000

    def test_population_delta_and_minmax_conflict(self):
        with pytest.raises(ValidationError, match="Cannot specify"):
            GeoNamesFilter(
                population=5000,
                delta=100,
                min_population=1000,
            )

    def test_population_delta_without_minmax_ok(self):
        f = GeoNamesFilter(population=5000, delta=100)
        assert f.population == 5000
        assert f.delta == 100

    def test_minmax_without_delta_ok(self):
        f = GeoNamesFilter(min_population=1000, max_population=50000)
        assert f.min_population == 1000
        assert f.max_population == 50000

    def test_order_by_accepts_all_values(self):
        for val in GeoNamesOrderBy:
            f = GeoNamesFilter(order_by=val)
            assert f.order_by == val


class TestDeletionFilter:
    def test_defaults_are_none(self):
        f = DeletionFilter()
        assert f.deletion_date is None
        assert f.geonameid is None
        assert f.order_by is None

    def test_accepts_valid_fields(self):
        f = DeletionFilter(
            name="OldTown",
            reason="duplicate",
            deletion_date=date(2025, 3, 1),
            order_by=DeletionOrderBy.DELETION_DATE_DESC,
        )
        assert f.name == "OldTown"
        assert f.reason == "duplicate"

    def test_order_by_accepts_all_values(self):
        for val in DeletionOrderBy:
            f = DeletionFilter(order_by=val)
            assert f.order_by == val


class TestDatasetMetaDataFilter:
    def test_defaults_are_none(self):
        f = DatasetMetaDataFilter()
        assert f.dataset is None
        assert f.out_of_sync is None
        assert f.needs_rebuild is None

    def test_accepts_valid_fields(self):
        now = datetime.now(timezone.utc)
        f = DatasetMetaDataFilter(
            dataset="cities15000",
            out_of_sync=False,
            needs_rebuild=True,
            modified_after=now,
            order_by=DatasetMetaDataOrderBy.LAST_MODIFIED_DESC,
        )
        assert f.dataset == "cities15000"
        assert f.needs_rebuild is True

    def test_last_modified_and_modified_after_conflict(self):
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError, match="Cannot specify"):
            DatasetMetaDataFilter(
                last_modified=now,
                modified_after=now,
            )

    def test_last_modified_and_modified_before_conflict(self):
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError, match="Cannot specify"):
            DatasetMetaDataFilter(
                last_modified=now,
                modified_before=now,
            )

    def test_modified_after_without_last_modified_ok(self):
        now = datetime.now(timezone.utc)
        f = DatasetMetaDataFilter(modified_after=now)
        assert f.modified_after == now

    def test_order_by_accepts_all_values(self):
        for val in DatasetMetaDataOrderBy:
            f = DatasetMetaDataFilter(order_by=val)
            assert f.order_by == val
