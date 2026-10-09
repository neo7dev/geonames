"""backfill_last_applied_date

Revision ID: d4a9b2c8f1e7
Revises: c3f1a7d2e9b4
Create Date: 2026-10-09 13:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a9b2c8f1e7"
down_revision: Union[str, Sequence[str], None] = "c3f1a7d2e9b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# last_modified is set to "now" on every successful build or sync, so the day
# before it is the last day whose daily files were applied. At worst one extra
# day is replayed, which is idempotent. Only NULLs are filled.
BACKFILL_SQL = """
UPDATE dataset_metadata
SET last_applied_date = (last_modified AT TIME ZONE 'UTC')::date - 1
WHERE last_applied_date IS NULL AND last_modified IS NOT NULL
"""


def upgrade() -> None:
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    pass
