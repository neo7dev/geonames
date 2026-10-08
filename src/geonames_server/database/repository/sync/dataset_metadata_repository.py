import datetime
from typing import List, Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from geonames_server.database.models.dataset_metadata import DatasetMetaData
from geonames_server.database.repository.filters.dataset_metadata_filter import (
    DatasetMetaDataFilter,
)
from geonames_server.database.repository.sync.base_repository import BaseRepository


class DatasetMetaDataRepository(BaseRepository[DatasetMetaData]):
    def __init__(self, session: Session):
        super().__init__(DatasetMetaData, session)

    # ----------------------------------
    # Convenience Lookups
    # ----------------------------------

    def get_by_dataset(
        self,
        dataset: str,
    ) -> Optional[DatasetMetaData]:
        stmt = select(self.model).where(self.model.dataset == dataset)
        return self.session.scalars(stmt).first()

    def get_out_of_sync(self) -> List[DatasetMetaData]:
        stmt = select(self.model).where(self.model.out_of_sync.is_(True))
        return list(self.session.scalars(stmt).all())

    # New timestamp helpers

    def get_modified_after(
        self,
        timestamp: datetime,
    ) -> List[DatasetMetaData]:
        stmt = select(self.model).where(self.model.last_modified >= timestamp)
        return list(self.session.scalars(stmt).all())

    def get_updated_after(
        self,
        timestamp: datetime,
    ) -> List[DatasetMetaData]:
        stmt = select(self.model).where(self.model.last_updated >= timestamp)
        return list(self.session.scalars(stmt).all())

    def mark_as_synced(
        self,
        dataset: str,
    ) -> Optional[DatasetMetaData]:
        stmt = select(self.model).where(self.model.dataset == dataset)
        metadata = self.session.scalars(stmt).first()

        if metadata:
            metadata.out_of_sync = False
            metadata.last_modified = datetime.datetime.now(datetime.timezone.utc)
            self.session.commit()
            self.session.refresh(metadata)

        return metadata

    def mark_as_out_of_sync(
        self,
        dataset: str,
    ) -> Optional[DatasetMetaData]:
        stmt = select(self.model).where(self.model.dataset == dataset)
        metadata = self.session.scalars(stmt).first()

        if metadata:
            metadata.out_of_sync = True
            metadata.last_modified = datetime.datetime.now(datetime.timezone.utc)
            self.session.commit()
            self.session.refresh(metadata)

        return metadata

    # ----------------------------------
    # Composable Filter
    # ----------------------------------

    def filter(
        self,
        filters: DatasetMetaDataFilter,
    ) -> List[DatasetMetaData]:
        stmt = select(self.model)
        conditions = []

        if filters.dataset:
            conditions.append(self.model.dataset == filters.dataset)

        if filters.out_of_sync is not None:
            conditions.append(self.model.out_of_sync == filters.out_of_sync)

        if filters.needs_rebuild is not None:
            conditions.append(self.model.needs_rebuild == filters.needs_rebuild)

        if filters.etag:
            conditions.append(self.model.etag == filters.etag)

        # last_modified filters

        if filters.last_modified:
            conditions.append(self.model.last_modified == filters.last_modified)

        if filters.modified_after:
            conditions.append(self.model.last_modified >= filters.modified_after)

        if filters.modified_before:
            conditions.append(self.model.last_modified <= filters.modified_before)

        if conditions:
            stmt = stmt.where(and_(*conditions))

        # Ordering

        if filters.order_by:
            field = filters.order_by
            if field.startswith("-"):
                stmt = stmt.order_by(desc(getattr(self.model, field[1:])))
            else:
                stmt = stmt.order_by(getattr(self.model, field))

        # Pagination

        if filters.offset:
            stmt = stmt.offset(filters.offset)

        if filters.limit:
            stmt = stmt.limit(filters.limit)

        return list(self.session.scalars(stmt).all())
