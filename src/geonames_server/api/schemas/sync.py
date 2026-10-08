from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SyncAcceptedResponse(BaseModel):
    sync_id: str
    message: str


class DatasetStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    last_modified: datetime | None = None
    out_of_sync: bool | None = None
    needs_rebuild: bool | None = None
    status: str


class SyncStatusResponse(BaseModel):
    sync_id: str
    status: str
    current_step: str
    started_at: datetime
    completed_at: datetime | None = None
    datasets: list[DatasetStatusResponse] = []
    error: str | None = None
