from datetime import date
from enum import StrEnum

from pydantic import BaseModel


class DeletionOrderBy(StrEnum):
    DELETION_DATE = "deletion_date"
    DELETION_DATE_DESC = "-deletion_date"
    GEONAMEID = "geonameid"
    GEONAMEID_DESC = "-geonameid"
    NAME = "name"
    NAME_DESC = "-name"
    REASON = "reason"
    REASON_DESC = "-reason"


class DeletionFilter(BaseModel):
    deletion_date: date | None = None
    geonameid: int | None = None

    name: str | None = None
    reason: str | None = None

    order_by: DeletionOrderBy | None = None
    limit: int | None = None
    offset: int | None = None
