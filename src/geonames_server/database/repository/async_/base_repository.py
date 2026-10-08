from typing import Generic, Iterable, List, Optional, Type, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class RepositoryError(Exception):
    """Base repository exception."""

    pass


class NotFoundError(RepositoryError):
    """Raised when an entity is not found."""

    pass


class AsyncBaseRepository(Generic[T]):
    def __init__(self, model: Type[T], session: AsyncSession):
        self.model = model
        self.session = session

    # -----------------------
    # Internal Helpers
    # -----------------------

    async def _commit(self):
        try:
            await self.session.commit()
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise RepositoryError(str(e)) from e

    # -----------------------
    # Create
    # -----------------------

    async def add(
        self,
        entity: T,
    ) -> T:
        self.session.add(entity)
        await self._commit()
        await self.session.refresh(entity)
        return entity

    async def add_many(
        self,
        entities: Iterable[T],
    ) -> List[T]:
        self.session.add_all(list(entities))
        await self._commit()
        return list(entities)

    # -----------------------
    # Read
    # -----------------------

    async def get_by_id(
        self,
        id_,
    ) -> Optional[T]:
        return await self.session.get(self.model, id_)

    async def get_or_raise(
        self,
        id_,
    ) -> T:
        entity = await self.get_by_id(id_)
        if not entity:
            raise NotFoundError(f"{self.model.__name__} with id={id_} not found")
        return entity

    async def list(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[T]:
        stmt = select(self.model).order_by(self.model.geonameid.desc())

        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)

        result = await self.session.scalars(stmt)
        return list(result.all())

    # -----------------------
    # Update
    # -----------------------

    async def update(
        self,
        entity: T,
    ) -> T:
        self.session.add(entity)
        await self._commit()
        return entity

    # -----------------------
    # Delete
    # -----------------------

    async def delete(
        self,
        entity: T,
    ) -> None:
        await self.session.delete(entity)
        await self._commit()

    async def delete_by_id(
        self,
        id_,
    ) -> None:
        entity = await self.get_or_raise(id_)
        await self.delete(entity)

    # -----------------------
    # Bulk (useful for ingestion)
    # -----------------------

    async def bulk_insert(
        self,
        entities: List[T],
    ) -> None:
        try:
            self.session.add_all(entities)
            await self.session.commit()
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise RepositoryError(str(e)) from e
