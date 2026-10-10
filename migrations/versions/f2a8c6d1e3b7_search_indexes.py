"""search_indexes

Trigram indexes on name and asciiname, for the substring filters (ILIKE
'%...%'), and a population index, for min/max_population and ordering by
population. Without them those filters scan all of allcountries (~13M rows,
~2 s each). Building them on allcountries takes about a minute.

Revision ID: f2a8c6d1e3b7
Revises: b8e1f4c2d9a6
Create Date: 2026-10-10 06:30:00.000000

"""

from typing import Sequence, Union

from alembic import op

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


def upgrade() -> None:
    install_pg_trgm(op.get_bind())
    # Index builds on allcountries are much faster with more sort memory
    op.execute("SET LOCAL maintenance_work_mem = '512MB'")
    for table in TABLES:
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
        op.drop_index(f"ix_{table}_population", table_name=table)
        for column in TRGM_COLUMNS:
            op.drop_index(f"ix_{table}_{column}_trgm", table_name=table)
