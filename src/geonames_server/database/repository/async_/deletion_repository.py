from typing import List, Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.database.models.deletions import Deletions
from geonames_server.database.repository.async_.base_repository import (
    AsyncBaseRepository,
)
from geonames_server.database.repository.filters.deletion_filter import DeletionFilter


class AsyncDeletionRepository(AsyncBaseRepository[Deletions]):
    def __init__(self, session: AsyncSession):
        super().__init__(Deletions, session)

    async def get_by_geonameid(
        self,
        geonameid: int,
    ) -> Optional[Deletions]:
        stmt = select(self.model).where(self.model.geonameid == geonameid)
        result = await self.session.scalars(stmt)
        return result.first()

    async def get_by_deletion_date(
        self,
        deletion_date,
    ) -> List[Deletions]:
        stmt = select(self.model).where(self.model.deletion_date == deletion_date)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get_by_reason(
        self,
        reason: str,
    ) -> List[Deletions]:
        stmt = select(self.model).where(self.model.reason.ilike(f"%{reason}%"))
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def filter(
        self,
        filters: DeletionFilter,
    ) -> List[Deletions]:
        stmt = select(self.model)
        conditions = []

        if filters.geonameid:
            conditions.append(self.model.geonameid == filters.geonameid)

        if filters.name:
            conditions.append(self.model.name.ilike(f"%{filters.name}%"))

        if filters.reason:
            conditions.append(self.model.reason.ilike(f"%{filters.reason}%"))

        if filters.deletion_date:
            conditions.append(self.model.deletion_date == filters.deletion_date)

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

        # Limit
        if filters.limit:
            stmt = stmt.limit(filters.limit)

        result = await self.session.scalars(stmt)
        return list(result.all())
