"""search_indexes

Trigram indexes on name and asciiname, for the substring filters (ILIKE
'%...%'), and a population index, for min/max_population and ordering by
population. Without them those filters scan all of allcountries (~13M rows,
~2 s each). Building them on allcountries takes about a minute, and migrations
run before the API listens, so only empty tables get them here: the dataset
startup thread builds the rest concurrently (ingestion/indexes.py).

Revision ID: f2a8c6d1e3b7
Revises: b8e1f4c2d9a6
Create Date: 2026-10-10 06:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from geonames_server.database.extensions import GIN_TRGM_OPS, install_pg_trgm

# revision identifiers, used by Alembic.
revision: str = "f2a8c6d1e3b7"
down_revision: Union[str, Sequence[str], None] = "b8e1f4c2d9a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "allcountries",
    "cities500",
    "cities1000",
    "cities5000",
    "cities15000",
    "modifications",
]
TRGM_COLUMNS = ["name", "asciiname"]


def _has_rows(table: str) -> bool:
    return op.get_bind().scalar(text(f'SELECT EXISTS (SELECT 1 FROM "{table}")'))


def upgrade() -> None:
    install_pg_trgm(op.get_bind())
    for table in TABLES:
        if _has_rows(table):
            continue
        for column in TRGM_COLUMNS:
            op.create_index(
                f"ix_{table}_{column}_trgm",
                table,
                [column],
                postgresql_using="gin",
                postgresql_ops={column: GIN_TRGM_OPS},
            )
        op.create_index(f"ix_{table}_population", table, ["population"])


def downgrade() -> None:
    # pg_trgm stays: other objects may use it
    for table in TABLES:
        # Missing if the startup thread had not built them yet
        op.drop_index(f"ix_{table}_population", table_name=table, if_exists=True)
        for column in TRGM_COLUMNS:
            op.drop_index(f"ix_{table}_{column}_trgm", table_name=table, if_exists=True)
