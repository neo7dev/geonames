import datetime
from typing import List, Optional

from sqlalchemy import and_, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.database.models.dataset_metadata import DatasetMetaData
from geonames_server.database.repository.async_.base_repository import (
    AsyncBaseRepository,
)
from geonames_server.database.repository.filters.dataset_metadata_filter import (
    DatasetMetaDataFilter,
)


class AsyncDatasetMetaDataRepository(AsyncBaseRepository[DatasetMetaData]):
    def __init__(self, session: AsyncSession):
        super().__init__(DatasetMetaData, session)

    async def list(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[DatasetMetaData]:
        stmt = select(self.model).order_by(self.model.dataset.asc())
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        result = await self.session.scalars(stmt)
        return list(result.all())

    # ----------------------------------
    # Convenience Lookups
    # ----------------------------------

    async def get_by_dataset(
        self,
        dataset: str,
    ) -> Optional[DatasetMetaData]:
        stmt = select(self.model).where(self.model.dataset == dataset)
        result = await self.session.scalars(stmt)
        return result.first()

    async def get_out_of_sync(self) -> List[DatasetMetaData]:
        stmt = select(self.model).where(self.model.out_of_sync.is_(True))
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get_needs_rebuild(self) -> List[DatasetMetaData]:
        stmt = select(self.model).where(self.model.needs_rebuild.is_(True))
        result = await self.session.scalars(stmt)
        return list(result.all())

    # Timestamp helpers

    async def get_modified_after(
        self,
        timestamp: datetime,
    ) -> List[DatasetMetaData]:
        stmt = select(self.model).where(self.model.last_modified >= timestamp)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get_updated_after(
        self,
        timestamp: datetime,
    ) -> List[DatasetMetaData]:
        stmt = select(self.model).where(self.model.last_updated >= timestamp)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def is_out_of_sync(
        self,
        dataset: str,
    ) -> bool:
        stmt = select(self.model.out_of_sync).where(self.model.dataset == dataset)
        result = await self.session.scalars(stmt)
        return result.scalar_one_or_none() or False

    async def needs_rebuild(
        self,
        dataset: str,
    ) -> bool:
        stmt = select(self.model.needs_rebuild).where(self.model.dataset == dataset)
        result = await self.session.scalars(stmt)
        return result.scalar_one_or_none() or False

    async def mark_out_of_sync(
        self,
        dataset: str,
        out_of_sync: bool = True,
    ):
        stmt = (
            update(self.model)
            .where(self.model.dataset == dataset)
            .values(out_of_sync=out_of_sync)
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def mark_as_synced(
        self,
        dataset: str,
    ) -> Optional[DatasetMetaData]:
        stmt = select(self.model).where(self.model.dataset == dataset)
        result = await self.session.scalars(stmt)
        metadata = result.first()

        if metadata:
            metadata.out_of_sync = False
            metadata.last_modified = datetime.datetime.now(datetime.timezone.utc)
            await self.session.commit()
            await self.session.refresh(metadata)

        return metadata

    async def mark_as_out_of_sync(
        self,
        dataset: str,
    ) -> Optional[DatasetMetaData]:
        stmt = select(self.model).where(self.model.dataset == dataset)
        result = await self.session.scalars(stmt)
        metadata = result.first()

        if metadata:
            metadata.out_of_sync = True
            metadata.last_modified = datetime.datetime.now(datetime.timezone.utc)
            await self.session.commit()
            await self.session.refresh(metadata)

        return metadata

    async def mark_needs_rebuild(
        self,
        dataset: str,
        needs_rebuild: bool = True,
    ):
        stmt = (
            update(self.model)
            .where(self.model.dataset == dataset)
            .values(needs_rebuild=needs_rebuild)
        )
        await self.session.execute(stmt)
        await self.session.commit()

    # ----------------------------------
    # Composable Filter
    # ----------------------------------

    async def filter(
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

        if filters.remote_etag:
            conditions.append(self.model.remote_etag == filters.remote_etag)

        if filters.remote_last_modified:
            conditions.append(
                self.model.remote_last_modified == filters.remote_last_modified
            )

        if filters.remote_modified_after:
            conditions.append(
                self.model.remote_last_modified >= filters.remote_modified_after
            )

        if filters.remote_modified_before:
            conditions.append(
                self.model.remote_last_modified <= filters.remote_modified_before
            )

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

        result = await self.session.scalars(stmt)
        return list(result.all())
