"""admin user management

Admins manage accounts from the web UI's Administration page. Two flags on
app.profiles, set there and nowhere else: is_active (false = deactivated, can
be switched back on) and is_deleted (soft-deleted, can be restored). Unlike
banned_until and deleted_at they are not copied from auth.users, so the signup
trigger never overwrites them. Either one refuses the user's keys and logins.

The admin endpoints run as geonames_admin, a role that cannot log in and may
only read the account tables and change a profile's role and flags. The app's
own database user is made a member, so it can SET ROLE to it.

Roles are cluster-wide: upgrade reuses geonames_admin if another database
already created it, and downgrade drops it only if no other database still
grants it anything.

Revision ID: 3b7e9c2a5d14
Revises: 15ea96db1854
Create Date: 2026-10-10 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b7e9c2a5d14"
down_revision: Union[str, Sequence[str], None] = "15ea96db1854"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "app"
ADMIN_ROLE = "geonames_admin"

USER_TIERS_VIEW = """
CREATE OR REPLACE VIEW app.user_tiers WITH (security_invoker = true) AS
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
    p.created_at{extra}
FROM app.profiles p
JOIN app.roles r ON r.name = p.role
JOIN app.tiers t ON t.name = r.tier
LEFT JOIN app.api_keys k ON k.user_id = p.id
GROUP BY p.id, r.name, t.name
"""


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "profiles",
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        schema=SCHEMA,
    )
    # New columns go last, so the view can be replaced in place.
    op.execute(USER_TIERS_VIEW.format(extra=",\n    p.is_active,\n    p.is_deleted"))

    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_ROLE}') THEN
                CREATE ROLE {ADMIN_ROLE} NOLOGIN;
            END IF;
            EXECUTE format('GRANT {ADMIN_ROLE} TO %I', current_user);
        END $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {ADMIN_ROLE}")
    op.execute(
        f"GRANT SELECT ON {SCHEMA}.tiers, {SCHEMA}.roles, {SCHEMA}.profiles, "
        f"{SCHEMA}.api_keys TO {ADMIN_ROLE}"
    )
    op.execute(
        f"GRANT UPDATE (role, is_active, is_deleted) ON {SCHEMA}.profiles "
        f"TO {ADMIN_ROLE}"
    )


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {SCHEMA} FROM {ADMIN_ROLE}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM {ADMIN_ROLE}")
    op.execute(
        f"""
        DO $$ BEGIN
            DROP ROLE {ADMIN_ROLE};
        EXCEPTION WHEN dependent_objects_still_exist THEN
            RAISE NOTICE '{ADMIN_ROLE} is still used by another database; kept';
        END $$
        """
    )
    op.execute("DROP VIEW app.user_tiers")
    op.execute(USER_TIERS_VIEW.format(extra=""))
    op.execute(
        "COMMENT ON VIEW app.user_tiers IS "
        "'Each user''s role, tier, limits (NULL = unlimited) and API key counts. "
        "Read-only: change a user''s tier by editing app.profiles.role.'"
    )
    op.drop_column("profiles", "is_deleted", schema=SCHEMA)
    op.drop_column("profiles", "is_active", schema=SCHEMA)
