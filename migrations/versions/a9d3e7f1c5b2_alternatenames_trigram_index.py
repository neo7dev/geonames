"""alternatenames_trigram_index

A trigram index on alternatenames, for the alternatenames substring filter
(ILIKE '%...%'), which otherwise scans all of allcountries. The column is
long, so this is the largest of the search indexes. Like the others, only
empty tables get it here; the dataset startup thread builds the rest
concurrently (ingestion/indexes.py).

Revision ID: a9d3e7f1c5b2
Revises: f2a8c6d1e3b7
Create Date: 2026-10-10 07:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from geonames_server.database.extensions import GIN_TRGM_OPS, install_pg_trgm

# revision identifiers, used by Alembic.
revision: str = "a9d3e7f1c5b2"
down_revision: Union[str, Sequence[str], None] = "f2a8c6d1e3b7"
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


def _has_rows(table: str) -> bool:
    return op.get_bind().scalar(text(f'SELECT EXISTS (SELECT 1 FROM "{table}")'))


def upgrade() -> None:
    install_pg_trgm(op.get_bind())
    for table in TABLES:
        if _has_rows(table):
            continue
        op.create_index(
            f"ix_{table}_alternatenames_trgm",
            table,
            ["alternatenames"],
            postgresql_using="gin",
            postgresql_ops={"alternatenames": GIN_TRGM_OPS},
        )


def downgrade() -> None:
    for table in TABLES:
        op.drop_index(
            f"ix_{table}_alternatenames_trgm", table_name=table, if_exists=True
        )
