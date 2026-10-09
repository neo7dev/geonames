"""user_tiers view

One row per user with their role, tier, limits and API key counts, so Studio's
Table Editor (schema app) shows who is on which tier without a query.

security_invoker makes the view check the caller's privileges on the
underlying tables instead of the owner's. The app schema is not exposed to
Supabase's API roles, so only postgres (and Studio) can read it either way.

Revision ID: 7c3e5a1f9b2d
Revises: 5f2c9d1e8a3b
Create Date: 2026-10-09 23:30:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c3e5a1f9b2d"
down_revision: Union[str, Sequence[str], None] = "5f2c9d1e8a3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW app.user_tiers WITH (security_invoker = true) AS
        SELECT
            p.id AS user_id,
            p.email,
            p.role,
            r.tier,
            r.is_admin,
            t.per_minute,
            t.per_hour,
            t.per_month,
            t.max_keys,
            count(k.id) FILTER (WHERE k.revoked_at IS NULL) AS active_keys,
            count(k.id) FILTER (WHERE k.revoked_at IS NOT NULL) AS revoked_keys,
            p.created_at
        FROM app.profiles p
        JOIN app.roles r ON r.name = p.role
        JOIN app.tiers t ON t.name = r.tier
        LEFT JOIN app.api_keys k ON k.user_id = p.id
        GROUP BY p.id, r.name, t.name
        """
    )
    op.execute(
        "COMMENT ON VIEW app.user_tiers IS "
        "'Each user''s role, tier, limits (NULL = unlimited) and API key counts. "
        "Read-only: change a user''s tier by editing app.profiles.role.'"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS app.user_tiers")
