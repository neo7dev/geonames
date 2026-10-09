"""unlimited per-minute rate on the max tier

per_minute becomes nullable like the other limits (NULL = unlimited), and the
max tier (sr_dev, admin) drops its 600/minute cap.

Revision ID: 9a4d2b6e1c8f
Revises: 7c3e5a1f9b2d
Create Date: 2026-10-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a4d2b6e1c8f"
down_revision: Union[str, Sequence[str], None] = "7c3e5a1f9b2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "app"


def upgrade() -> None:
    op.alter_column(
        "tiers", "per_minute", existing_type=sa.Integer(), nullable=True, schema=SCHEMA
    )
    op.execute("UPDATE app.tiers SET per_minute = NULL WHERE name = 'max'")


def downgrade() -> None:
    op.execute("UPDATE app.tiers SET per_minute = 600 WHERE per_minute IS NULL")
    op.alter_column(
        "tiers", "per_minute", existing_type=sa.Integer(), nullable=False, schema=SCHEMA
    )
