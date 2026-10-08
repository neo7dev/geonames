from datetime import date

from pydantic import BaseModel, ConfigDict


class GeoNameResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    geonameid: int
    name: str
    asciiname: str
    alternatenames: str | None = None
    latitude: float
    longitude: float
    feature_class: str | None = None
    feature_code: str | None = None
    country_code: str | None = None
    cc2: str | None = None
    admin1_code: str | None = None
    admin2_code: str | None = None
    admin3_code: str | None = None
    admin4_code: str | None = None
    population: int | None = None
    elevation: int | None = None
    dem: int | None = None
    timezone: str | None = None
    modification_date: date | None = None
