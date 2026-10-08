from __future__ import annotations

from dataclasses import dataclass
from typing import Type

from geonames_server.database.models.allcountries import AllCountries
from geonames_server.database.models.base import Base
from geonames_server.database.models.cities500 import Cities500
from geonames_server.database.models.cities1000 import Cities1000
from geonames_server.database.models.cities5000 import Cities5000
from geonames_server.database.models.cities15000 import Cities15000


@dataclass(frozen=True)
class DatasetInfo:
    """Metadata for a downloadable GeoNames dataset."""

    name: str
    zip_file: str
    txt_file: str
    model: Type[Base]


ALL_DATASETS: dict[str, DatasetInfo] = {
    "allcountries": DatasetInfo(
        name="allcountries",
        zip_file="allCountries.zip",
        txt_file="allCountries.txt",
        model=AllCountries,
    ),
    "cities500": DatasetInfo(
        name="cities500",
        zip_file="cities500.zip",
        txt_file="cities500.txt",
        model=Cities500,
    ),
    "cities1000": DatasetInfo(
        name="cities1000",
        zip_file="cities1000.zip",
        txt_file="cities1000.txt",
        model=Cities1000,
    ),
    "cities5000": DatasetInfo(
        name="cities5000",
        zip_file="cities5000.zip",
        txt_file="cities5000.txt",
        model=Cities5000,
    ),
    "cities15000": DatasetInfo(
        name="cities15000",
        zip_file="cities15000.zip",
        txt_file="cities15000.txt",
        model=Cities15000,
    ),
}


def get_enabled_datasets(enabled_names: list[str]) -> list[DatasetInfo]:
    """Return DatasetInfo objects for the given dataset names.

    Raises ValueError if any name is not in ALL_DATASETS.
    """
    result = []
    for name in enabled_names:
        key = name.strip().lower()
        if key not in ALL_DATASETS:
            raise ValueError(
                f"Unknown dataset '{name}'. Available: {', '.join(ALL_DATASETS.keys())}"
            )
        result.append(ALL_DATASETS[key])
    return result


def get_all_datasets() -> list[DatasetInfo]:
    """Return DatasetInfo objects for all available datasets."""
    return list(ALL_DATASETS.values())
