"""add_last_applied_date

Revision ID: c3f1a7d2e9b4
Revises: a684673d5b5c
Create Date: 2026-10-09 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f1a7d2e9b4"
down_revision: Union[str, Sequence[str], None] = "a684673d5b5c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dataset_metadata", sa.Column("last_applied_date", sa.Date(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("dataset_metadata", "last_applied_date")
