from datetime import date

from sqlalchemy import Date, Integer
from sqlalchemy.orm import Mapped, mapped_column

from geonames_server.database.models.geobase import GeoBase

# ! Thing with SQLAlchemy models is that the rows are mapped to the class variables in the order they are defined in the class.
# ! And if I want to override the default order of the columns, I can have 3 options:
# ! 1. Don't redeclare the geonameid column, just override the geonameid since I am not even changing anything, I have it here for clarity
# ! 2. Use sort_order which is weight of the column, the lower the number it will appear ealier and higher number means later
# ! 3. Rewrite the modifications class instead of inheriting from GeoBase
# ! 4. Secret 4th option is that after alembic is done auto generating the migrations/versions .py file, edit it to make sure that the modification_date for Modifications class is at the bottom (or at the right place where it should be)
# ! The order of column matters because I am using COPY function to ingest data from a text file
class Modifications(GeoBase):
    __tablename__ = "modifications"

    geonameid: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        unique=True,
        sort_order=-1,
    )

    modification_date: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
        sort_order=100,

    )
