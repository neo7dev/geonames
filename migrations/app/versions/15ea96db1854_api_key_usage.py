"""api key usage

Hourly request counts per API key, flushed from Redis every few minutes, and
the batches already written, so a retried flush never counts twice. The
monthly_usage view shows each user's requests per month next to their quota.

Revision ID: 15ea96db1854
Revises: bf313717012e
Create Date: 2026-10-10 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "15ea96db1854"
down_revision: Union[str, Sequence[str], None] = "bf313717012e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "app"


def upgrade() -> None:
    op.create_table(
        "api_key_usage",
        sa.Column("key_id", sa.Uuid(), nullable=False),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requests", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("requests > 0", name="api_key_usage_requests_positive"),
        sa.ForeignKeyConstraint(
            ["key_id"], [f"{SCHEMA}.api_keys.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("key_id", "hour"),
        schema=SCHEMA,
    )
    op.create_table(
        "usage_flushes",
        sa.Column("batch", sa.Text(), nullable=False),
        sa.Column(
            "flushed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("batch"),
        schema=SCHEMA,
    )
    # The existing index covers active keys only; usage includes revoked keys.
    op.create_index(
        "api_keys_user_id_idx", "api_keys", ["user_id"], unique=False, schema=SCHEMA
    )
    op.execute(
        """
        CREATE VIEW app.monthly_usage WITH (security_invoker = true) AS
        SELECT
            p.id AS user_id,
            p.email,
            p.role,
            r.tier,
            date_trunc('month', u.hour AT TIME ZONE 'UTC')::date AS month,
            sum(u.requests)::bigint AS requests,
            t.per_month
        FROM app.api_key_usage u
        JOIN app.api_keys k ON k.id = u.key_id
        JOIN app.profiles p ON p.id = k.user_id
        JOIN app.roles r ON r.name = p.role
        JOIN app.tiers t ON t.name = r.tier
        GROUP BY p.id, r.name, t.name, 5
        """
    )
    op.execute(
        "COMMENT ON VIEW app.monthly_usage IS "
        "'Requests per user and UTC month, next to their monthly quota "
        "(NULL = unlimited). Up to 5 minutes behind the live counters.'"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS app.monthly_usage")
    op.drop_index("api_keys_user_id_idx", table_name="api_keys", schema=SCHEMA)
    op.drop_table("usage_flushes", schema=SCHEMA)
    op.drop_table("api_key_usage", schema=SCHEMA)
