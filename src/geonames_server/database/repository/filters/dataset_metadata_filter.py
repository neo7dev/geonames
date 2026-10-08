from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, model_validator


class DatasetMetaDataOrderBy(StrEnum):
    DATASET = "dataset"
    DATASET_DESC = "-dataset"
    OUT_OF_SYNC = "out_of_sync"
    OUT_OF_SYNC_DESC = "-out_of_sync"
    NEEDS_REBUILD = "needs_rebuild"
    NEEDS_REBUILD_DESC = "-needs_rebuild"
    LAST_MODIFIED = "last_modified"
    LAST_MODIFIED_DESC = "-last_modified"
    REMOTE_LAST_MODIFIED = "remote_last_modified"
    REMOTE_LAST_MODIFIED_DESC = "-remote_last_modified"


class DatasetMetaDataFilter(BaseModel):
    dataset: str | None = None
    out_of_sync: bool | None = None
    needs_rebuild: bool | None = None
    etag: str | None = None
    remote_etag: str | None = None
    last_modified: datetime | None = None
    remote_last_modified: datetime | None = None

    # last_modified filters
    modified_after: datetime | None = None
    modified_before: datetime | None = None

    # remote_last_modified filters
    remote_modified_after: datetime | None = None
    remote_modified_before: datetime | None = None

    order_by: DatasetMetaDataOrderBy | None = None
    limit: int | None = None
    offset: int | None = None

    @model_validator(mode="after")
    def check_modified_filters(self):
        if self.last_modified and (self.modified_after or self.modified_before):
            raise ValueError(
                "Cannot specify modified_after/modified_before when last_modified is set"
            )
        return self
