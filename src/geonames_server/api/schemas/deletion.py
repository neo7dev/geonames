from datetime import date

from pydantic import BaseModel, ConfigDict


class DeletionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    deletion_date: date
    geonameid: int
    name: str | None = None
    reason: str | None = None
