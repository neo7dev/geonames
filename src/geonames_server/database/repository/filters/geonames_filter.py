from datetime import date
from enum import StrEnum

from pydantic import BaseModel, model_validator


class GeoNamesOrderBy(StrEnum):
    GEONAMEID = "geonameid"
    GEONAMEID_DESC = "-geonameid"
    NAME = "name"
    NAME_DESC = "-name"
    ASCIINAME = "asciiname"
    ASCIINAME_DESC = "-asciiname"
    POPULATION = "population"
    POPULATION_DESC = "-population"
    ELEVATION = "elevation"
    ELEVATION_DESC = "-elevation"
    MODIFICATION_DATE = "modification_date"
    MODIFICATION_DATE_DESC = "-modification_date"
    COUNTRY_CODE = "country_code"
    COUNTRY_CODE_DESC = "-country_code"
    TIMEZONE = "timezone"
    TIMEZONE_DESC = "-timezone"
    FEATURE_CLASS = "feature_class"
    FEATURE_CLASS_DESC = "-feature_class"
    FEATURE_CODE = "feature_code"
    FEATURE_CODE_DESC = "-feature_code"


class GeoNamesFilter(BaseModel):
    geonameid: int | None = None

    name: str | None = None
    asciiname: str | None = None
    alternatenames: str | None = None

    latitude: float | None = None
    longitude: float | None = None

    feature_class: str | None = None
    feature_code: str | None = None

    country_code: str | None = None
    cc2: str | None = None

    admin1_code: str | None = None
    admin2_code: str | None = None
    admin3_code: str | None = None
    admin4_code: str | None = None

    timezone: str | None = None

    population: int | None = None
    delta: int | None = None

    elevation: int | None = None
    dem: int | None = None

    modification_date: date | None = None

    min_population: int | None = None
    max_population: int | None = None

    modified_after: date | None = None
    modified_before: date | None = None

    order_by: GeoNamesOrderBy | None = None
    limit: int | None = None
    offset: int | None = None

    @model_validator(mode="after")
    def check_population_params(self):
        has_pop_delta = self.population is not None and self.delta is not None
        has_min_max = self.min_population is not None or self.max_population is not None
        if has_pop_delta and has_min_max:
            raise ValueError(
                "Cannot specify min_population/max_population when both population and delta are set"
            )
        return self
