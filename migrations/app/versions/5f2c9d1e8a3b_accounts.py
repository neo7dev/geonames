"""accounts

Tiers, roles, profiles and API keys in the app schema, seeded with the tiers
and roles from docs/plans/auth-tiers-rate-limiting.md.

On Supabase, profiles are linked to auth.users: a FK removes a profile (and its
keys) with its user, a trigger creates a profile with the default role for every
signup and keeps its email in sync, and existing users are backfilled. Plain
Postgres has no auth.users, so that part is skipped there.

Revision ID: 5f2c9d1e8a3b
Revises:
Create Date: 2026-10-09 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from geonames_server.log.mylogger import get_logger

log = get_logger()

# revision identifiers, used by Alembic.
revision: str = "5f2c9d1e8a3b"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "app"

# name, per_minute, per_hour, per_month, max_keys (None = unlimited)
TIERS = [
    ("basic", 10, 100, 5_000, 1),
    ("plus", 30, 500, 50_000, 3),
    ("pro", 120, 2_500, 500_000, 5),
    ("max", 600, None, None, None),
]

# name, tier, is_admin
ROLES = [
    ("free", "basic", False),
    ("tester", "plus", False),
    ("dev", "pro", False),
    ("sr_dev", "max", False),
    ("admin", "max", True),
]

# Runs as its owner (postgres) when GoTrue inserts or updates a user as
# supabase_auth_admin, which has no access to the app schema.
SYNC_PROFILE_FUNCTION = """
CREATE FUNCTION app.sync_profile_from_auth_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    INSERT INTO app.profiles (id, email) VALUES (NEW.id, NEW.email)
    ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email;
    RETURN NEW;
END
$$
"""

SYNC_PROFILE_TRIGGER = """
CREATE TRIGGER app_sync_profile
AFTER INSERT OR UPDATE OF email ON auth.users
FOR EACH ROW EXECUTE FUNCTION app.sync_profile_from_auth_user()
"""


def _has_auth_users() -> bool:
    return op.get_bind().scalar(sa.text("SELECT to_regclass('auth.users')")) is not None


def upgrade() -> None:
    tiers = op.create_table(
        "tiers",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("per_minute", sa.Integer(), nullable=False),
        sa.Column("per_hour", sa.Integer()),
        sa.Column("per_month", sa.Integer()),
        sa.Column("max_keys", sa.Integer()),
        sa.CheckConstraint("per_minute > 0", name="tiers_per_minute_positive"),
        sa.CheckConstraint("per_hour > 0", name="tiers_per_hour_positive"),
        sa.CheckConstraint("per_month > 0", name="tiers_per_month_positive"),
        sa.CheckConstraint("max_keys > 0", name="tiers_max_keys_positive"),
        schema=SCHEMA,
    )
    roles = op.create_table(
        "roles",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column(
            "tier",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.tiers.name", onupdate="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.Text()),
        sa.Column(
            "role",
            sa.Text(),
            sa.ForeignKey(f"{SCHEMA}.roles.name", onupdate="CASCADE"),
            nullable=False,
            server_default="free",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "api_keys",
        sa.Column(
            "id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey(f"{SCHEMA}.profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 64", name="api_keys_name_length"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "api_keys_active_user_id_idx",
        "api_keys",
        ["user_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.bulk_insert(
        tiers,
        [
            dict(zip(("name", "per_minute", "per_hour", "per_month", "max_keys"), row))
            for row in TIERS
        ],
    )
    op.bulk_insert(
        roles, [dict(zip(("name", "tier", "is_admin"), row)) for row in ROLES]
    )

    if not _has_auth_users():
        log.warning("auth.users not found: app.profiles is not linked to Supabase Auth")
        return

    op.create_foreign_key(
        "profiles_id_fkey",
        "profiles",
        "users",
        ["id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema="auth",
        ondelete="CASCADE",
    )
    op.execute(SYNC_PROFILE_FUNCTION)
    op.execute(SYNC_PROFILE_TRIGGER)
    # Users who signed up before this migration
    op.execute(
        "INSERT INTO app.profiles (id, email) SELECT id, email FROM auth.users "
        "ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    if _has_auth_users():
        op.execute("DROP TRIGGER IF EXISTS app_sync_profile ON auth.users")
    op.execute("DROP FUNCTION IF EXISTS app.sync_profile_from_auth_user()")
    op.drop_table("api_keys", schema=SCHEMA)
    op.drop_table("profiles", schema=SCHEMA)
    op.drop_table("roles", schema=SCHEMA)
    op.drop_table("tiers", schema=SCHEMA)
