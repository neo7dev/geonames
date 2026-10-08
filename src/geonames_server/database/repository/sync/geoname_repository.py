from datetime import date
from typing import Generic, List, TypeVar

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from geonames_server.database.repository.filters.geonames_filter import GeoNamesFilter
from geonames_server.database.repository.sync.base_repository import BaseRepository

T = TypeVar("T")


class GeoNamesRepository(BaseRepository[T], Generic[T]):
    def __init__(self, model, session: Session):
        super().__init__(model, session)

    # -----------------------
    # Name Searches
    # -----------------------

    def search_by_name(
        self,
        name: str,
        limit: int | None = None,
    ) -> List[T]:
        stmt = select(self.model).where(self.model.name.ilike(f"%{name}%"))

        if limit:
            stmt = stmt.limit(limit)

        return list(self.session.scalars(stmt).all())

    def search_by_asciiname(
        self,
        asciiname: str,
        limit: int | None = None,
    ) -> List[T]:
        stmt = select(self.model).where(
            func.lower(self.model.asciiname).like(f"%{asciiname.lower()}%")
        )

        if limit:
            stmt = stmt.limit(limit)

        return list(self.session.scalars(stmt).all())

    # -----------------------
    # Population Queries
    # -----------------------

    def get_by_population(
        self,
        population: int,
        delta: int = 0,
    ):
        min_population = population - delta
        max_population = population + delta

        stmt = select(self.model).where(
            and_(
                self.model.population >= min_population,
                self.model.population <= max_population,
            )
        )

        return list(self.session.scalars(stmt).all())

    def get_min_population(
        self,
        min_population: int,
    ) -> List[T]:
        stmt = select(self.model).where(self.model.population >= min_population)
        return list(self.session.scalars(stmt).all())

    def get_max_population(
        self,
        max_population: int,
    ) -> List[T]:
        stmt = select(self.model).where(self.model.population <= max_population)
        return list(self.session.scalars(stmt).all())

    def get_population_between(
        self,
        min_population: int,
        max_population: int,
    ) -> List[T]:
        stmt = select(self.model).where(
            and_(
                self.model.population >= min_population,
                self.model.population <= max_population,
            )
        )
        return list(self.session.scalars(stmt).all())

    # -----------------------
    # Timezone
    # -----------------------

    def get_by_timezone(
        self,
        timezone: str,
    ) -> List[T]:
        stmt = select(self.model).where(self.model.timezone == timezone)
        return list(self.session.scalars(stmt).all())

    # -----------------------
    # Country
    # -----------------------

    def get_by_country_code(
        self,
        country_code: str,
    ) -> List[T]:
        stmt = select(self.model).where(self.model.country_code == country_code.upper())
        return list(self.session.scalars(stmt).all())

    # -----------------------
    # Modification Date
    # -----------------------

    def get_by_modification_date(
        self,
        mod_date: date,
    ) -> List[T]:
        stmt = select(self.model).where(self.model.modification_date == mod_date)
        return list(self.session.scalars(stmt).all())

    def get_modified_after(
        self,
        mod_date: date,
    ) -> List[T]:
        stmt = select(self.model).where(self.model.modification_date >= mod_date)
        return list(self.session.scalars(stmt).all())

    def get_modified_between(
        self,
        start_date: date,
        end_date: date,
    ) -> List[T]:
        stmt = select(self.model).where(
            and_(
                self.model.modification_date >= start_date,
                self.model.modification_date <= end_date,
            )
        )
        return list(self.session.scalars(stmt).all())

    def filter(
        self,
        filters: GeoNamesFilter,
    ) -> List[T]:
        stmt = select(self.model)
        conditions = []

        if filters.geonameid:
            conditions.append(self.model.geonameid == filters.geonameid)

        if filters.name:
            conditions.append(self.model.name.ilike(f"%{filters.name}%"))

        if filters.asciiname:
            conditions.append(self.model.asciiname.ilike(f"%{filters.asciiname}%"))

        if filters.alternatenames:
            conditions.append(
                self.model.alternatenames.ilike(f"%{filters.alternatenames}%")
            )

        if filters.latitude:
            conditions.append(self.model.latitude == filters.latitude)

        if filters.longitude:
            conditions.append(self.model.longitude == filters.longitude)

        if filters.feature_class:
            conditions.append(self.model.feature_class == filters.feature_class)

        if filters.feature_code:
            conditions.append(self.model.feature_code == filters.feature_code)

        if filters.country_code:
            conditions.append(self.model.country_code == filters.country_code.upper())

        if filters.cc2:
            conditions.append(self.model.cc2 == filters.cc2.upper())

        if filters.admin1_code:
            conditions.append(self.model.admin1_code == filters.admin1_code)

        if filters.admin2_code:
            conditions.append(self.model.admin2_code == filters.admin2_code)

        if filters.admin3_code:
            conditions.append(self.model.admin3_code == filters.admin3_code)

        if filters.admin4_code:
            conditions.append(self.model.admin4_code == filters.admin4_code)

        if filters.timezone:
            conditions.append(self.model.timezone == filters.timezone)

        if filters.population is not None:
            delta = filters.delta or 0
            min_pop = filters.population - delta
            max_pop = filters.population + delta
            conditions.append(
                and_(
                    self.model.population >= min_pop,
                    self.model.population <= max_pop,
                )
            )

        if filters.min_population:
            conditions.append(self.model.population >= filters.min_population)

        if filters.max_population:
            conditions.append(self.model.population <= filters.max_population)

        if filters.elevation:
            conditions.append(self.model.elevation == filters.elevation)

        if filters.dem:
            conditions.append(self.model.dem == filters.dem)

        if filters.modification_date:
            conditions.append(self.model.modification_date == filters.modification_date)

        if filters.modified_after:
            conditions.append(self.model.modification_date >= filters.modified_after)

        if filters.modified_before:
            conditions.append(self.model.modification_date <= filters.modified_before)

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

        return list(self.session.scalars(stmt).all())
