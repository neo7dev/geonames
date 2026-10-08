from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DatasetMetaDataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dataset: str
    zip_file: str | None = None
    txt_file: str
    out_of_sync: bool | None = None
    needs_rebuild: bool | None = None
    etag: str | None = None
    remote_etag: str | None = None
    last_modified: datetime | None = None
    remote_last_modified: datetime | None = None
