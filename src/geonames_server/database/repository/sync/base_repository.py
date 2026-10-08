from typing import Type, TypeVar, Generic, List, Optional, Iterable
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select

T = TypeVar("T")


class RepositoryError(Exception):
    """Base repository exception."""

    pass


class NotFoundError(RepositoryError):
    """Raised when an entity is not found."""

    pass


class BaseRepository(Generic[T]):
    def __init__(self, model: Type[T], session: Session):
        self.model = model
        self.session = session

    # -----------------------
    # Internal Helpers
    # -----------------------

    def _commit(self):
        try:
            self.session.commit()
        except SQLAlchemyError as e:
            self.session.rollback()
            raise RepositoryError(str(e)) from e

    # -----------------------
    # Create
    # -----------------------

    def add(
        self,
        entity: T,
    ) -> T:
        self.session.add(entity)
        self._commit()
        self.session.refresh(entity)
        return entity

    def add_many(
        self,
        entities: Iterable[T],
    ) -> List[T]:
        self.session.add_all(list(entities))
        self._commit()
        return list(entities)

    # -----------------------
    # Read
    # -----------------------

    def get_by_id(
        self,
        id_,
    ) -> Optional[T]:
        return self.session.get(self.model, id_)

    def get_or_raise(
        self,
        id_,
    ) -> T:
        entity = self.get_by_id(id_)
        if not entity:
            raise NotFoundError(f"{self.model.__name__} with id={id_} not found")
        return entity

    def list(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[T]:
        stmt = select(self.model)

        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)

        return list(self.session.scalars(stmt).all())

    # -----------------------
    # Update
    # -----------------------

    def update(
        self,
        entity: T,
    ) -> T:
        self.session.add(entity)
        self._commit()
        return entity

    # -----------------------
    # Delete
    # -----------------------

    def delete(
        self,
        entity: T,
    ) -> None:
        self.session.delete(entity)
        self._commit()

    def delete_by_id(
        self,
        id_,
    ) -> None:
        entity = self.get_or_raise(id_)
        self.delete(entity)

    # -----------------------
    # Bulk (useful for ingestion)
    # -----------------------

    def bulk_insert(
        self,
        entities: List[T],
    ) -> None:
        try:
            self.session.bulk_save_objects(entities)
            self.session.commit()
        except SQLAlchemyError as e:
            self.session.rollback()
            raise RepositoryError(str(e)) from e
