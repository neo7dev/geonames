"""modifications_population_bigint

65cb544a01cf widened population to BIGINT everywhere, but a684673d5b5c then
recreated the modifications table with INTEGER. Populations above 2^31 (Earth,
continents) in a daily modifications file would not fit.

Revision ID: b8e1f4c2d9a6
Revises: e7b3c1a9f2d4
Create Date: 2026-10-09 23:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e1f4c2d9a6"
down_revision: Union[str, Sequence[str], None] = "e7b3c1a9f2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "modifications",
        "population",
        existing_type=sa.INTEGER(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "modifications",
        "population",
        existing_type=sa.BigInteger(),
        type_=sa.INTEGER(),
        existing_nullable=True,
    )
