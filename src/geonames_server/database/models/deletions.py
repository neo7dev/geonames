from datetime import date

from sqlalchemy import Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from geonames_server.database.models.base import Base


class Deletions(Base):
    __tablename__ = "deletions"

    deletion_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        primary_key=True,
    )

    geonameid: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"geonameid={self.geonameid}, "
            f"deletion_date={self.deletion_date}, "
            f"name={self.name}, "
            f"reason={self.reason}>"
        )

    def to_dict(self) -> dict:
        return {
            "geonameid": self.geonameid,
            "deletion_date": self.deletion_date.isoformat(),
            "name": self.name,
            "reason": self.reason,
        }
