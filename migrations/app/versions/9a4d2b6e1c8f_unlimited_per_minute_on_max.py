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
    # Restore only what upgrade changed. Another tier left without a per-minute
    # limit would have to be given one, which is not this migration's call.
    op.execute(
        """
        DO $$
        DECLARE
            unlimited text;
        BEGIN
            SELECT string_agg(name, ', ' ORDER BY name) INTO unlimited
            FROM app.tiers WHERE per_minute IS NULL AND name <> 'max';
            IF unlimited IS NOT NULL THEN
                RAISE EXCEPTION 'Tiers without a per-minute limit: %', unlimited
                    USING HINT = 'Set their per_minute before downgrading.';
            END IF;
        END $$
        """
    )
    op.execute("UPDATE app.tiers SET per_minute = 600 WHERE name = 'max'")
    op.alter_column(
        "tiers", "per_minute", existing_type=sa.Integer(), nullable=False, schema=SCHEMA
    )
