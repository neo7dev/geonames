from datetime import date

from sqlalchemy import BigInteger, Date, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from geonames_server.database.extensions import GIN_TRGM_OPS
from geonames_server.database.models.base import Base


class GeoBase(Base):
    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> tuple:
        """Indexes for the /filter routes: substring name searches (trigram) and
        population ranges and ordering. Load-and-swap copies them to new tables."""
        table = cls.__tablename__
        return tuple(
            Index(
                f"ix_{table}_{column}_trgm",
                column,
                postgresql_using="gin",
                postgresql_ops={column: GIN_TRGM_OPS},
            )
            for column in ("name", "asciiname", "alternatenames")
        ) + (Index(f"ix_{table}_population", "population"),)

    geonameid: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        unique=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    asciiname: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    alternatenames: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    latitude: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    longitude: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    feature_class: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    feature_code: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    # Can be nullable because some geonames entries don't have a country code (e.g. Antarctica)
    country_code: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    cc2: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    admin1_code: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    admin2_code: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    admin3_code: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    admin4_code: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    population: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    elevation: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    dem: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    timezone: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    modification_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    def to_dict(self) -> dict:
        return {
            "geonameid": self.geonameid,
            "name": self.name,
            "asciiname": self.asciiname,
            "alternatenames": self.alternatenames,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "feature_class": self.feature_class,
            "feature_code": self.feature_code,
            "country_code": self.country_code,
            "cc2": self.cc2,
            "admin1_code": self.admin1_code,
            "admin2_code": self.admin2_code,
            "admin3_code": self.admin3_code,
            "admin4_code": self.admin4_code,
            "population": self.population,
            "elevation": self.elevation,
            "dem": self.dem,
            "timezone": self.timezone,
            "modification_date": self.modification_date.isoformat()
            if self.modification_date
            else None,
        }

    def __repr__(self) -> str:
        # ! or just this
        # return f"{self.__class__.__name__}({self.__dict__})"
        return (
            f"<{self.__class__.__name__} "
            f"geonameid={self.geonameid} "
            f"name={self.name!r} "
            f"asciiname={self.asciiname!r} "
            f"alternatenames={self.alternatenames!r} "
            f"latitude={self.latitude} "
            f"longitude={self.longitude} "
            f"feature_class={self.feature_class!r} "
            f"feature_code={self.feature_code!r} "
            f"country_code={self.country_code!r} "
            f"cc2={self.cc2!r} "
            f"admin1_code={self.admin1_code!r} "
            f"admin2_code={self.admin2_code!r} "
            f"admin3_code={self.admin3_code!r} "
            f"admin4_code={self.admin4_code!r} "
            f"population={self.population} "
            f"elevation={self.elevation} "
            f"dem={self.dem} "
            f"timezone={self.timezone!r} "
            f"modification_date={self.modification_date.isoformat() if self.modification_date else None}>"
        )
