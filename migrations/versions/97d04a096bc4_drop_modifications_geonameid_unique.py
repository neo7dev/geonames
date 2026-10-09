"""drop_modifications_geonameid_unique

The modifications staging table keeps every day's rows, keyed by
(geonameid, modification_date). The extra UNIQUE (geonameid) made staging fail
for any place modified on more than one day, which stalled daily updates.

Revision ID: 97d04a096bc4
Revises: d4a9b2c8f1e7
Create Date: 2026-10-09 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "97d04a096bc4"
down_revision: Union[str, Sequence[str], None] = "d4a9b2c8f1e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("modifications_geonameid_key", "modifications", type_="unique")


def downgrade() -> None:
    # The constraint cannot be restored while a place has rows for several days;
    # keep only each place's latest modification.
    op.execute(
        "DELETE FROM modifications m USING modifications newer "
        "WHERE newer.geonameid = m.geonameid "
        "AND newer.modification_date > m.modification_date"
    )
    op.create_unique_constraint(
        "modifications_geonameid_key", "modifications", ["geonameid"]
    )
