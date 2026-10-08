import pytest

from geonames_server.database.models.allcountries import AllCountries
from geonames_server.database.models.cities500 import Cities500
from geonames_server.database.models.cities1000 import Cities1000
from geonames_server.database.models.cities5000 import Cities5000
from geonames_server.database.models.cities15000 import Cities15000
from geonames_server.ingestion.registry import (
    ALL_DATASETS,
    DatasetInfo,
    get_all_datasets,
    get_enabled_datasets,
)


class TestAllDatasets:
    def test_contains_all_expected_keys(self):
        expected = {"allcountries", "cities500", "cities1000", "cities5000", "cities15000"}
        assert set(ALL_DATASETS.keys()) == expected

    def test_values_are_dataset_info(self):
        for v in ALL_DATASETS.values():
            assert isinstance(v, DatasetInfo)

    def test_allcountries_model(self):
        assert ALL_DATASETS["allcountries"].model is AllCountries

    def test_cities500_model(self):
        assert ALL_DATASETS["cities500"].model is Cities500

    def test_cities1000_model(self):
        assert ALL_DATASETS["cities1000"].model is Cities1000

    def test_cities5000_model(self):
        assert ALL_DATASETS["cities5000"].model is Cities5000

    def test_cities15000_model(self):
        assert ALL_DATASETS["cities15000"].model is Cities15000

    def test_zip_file_ends_with_zip(self):
        for ds in ALL_DATASETS.values():
            assert ds.zip_file.endswith(".zip")

    def test_txt_file_ends_with_txt(self):
        for ds in ALL_DATASETS.values():
            assert ds.txt_file.endswith(".txt")


class TestGetEnabledDatasets:
    def test_returns_matching_datasets(self):
        result = get_enabled_datasets(["cities500", "cities15000"])
        assert len(result) == 2
        names = {d.name for d in result}
        assert names == {"cities500", "cities15000"}

    def test_case_insensitive(self):
        result = get_enabled_datasets(["Cities500", "ALLCOUNTRIES"])
        assert len(result) == 2

    def test_strips_whitespace(self):
        result = get_enabled_datasets(["  cities500  "])
        assert len(result) == 1
        assert result[0].name == "cities500"

    def test_raises_on_unknown_dataset(self):
        with pytest.raises(ValueError, match="Unknown dataset"):
            get_enabled_datasets(["nonexistent"])

    def test_empty_list_returns_empty(self):
        assert get_enabled_datasets([]) == []


class TestGetAllDatasets:
    def test_returns_all_five(self):
        result = get_all_datasets()
        assert len(result) == 5

    def test_returns_list_of_dataset_info(self):
        for ds in get_all_datasets():
            assert isinstance(ds, DatasetInfo)


class TestDatasetInfoFrozen:
    def test_is_frozen(self):
        ds = ALL_DATASETS["cities500"]
        with pytest.raises(AttributeError):
            ds.name = "changed"
