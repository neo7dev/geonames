"""profile display name

app.profiles.display_name: the name a user goes by, up to 100 characters
(NULL when they gave none). Users change it on their profile page, admins on
the Administration page; geonames_admin may update it.

A signup's Supabase Auth metadata (full_name, from the signup form or an admin
creating the user) fills it when the profile is created. Later metadata
changes do not overwrite it: the profile page is where it is edited.

Revision ID: 8e2f4a6c1b93
Revises: 3b7e9c2a5d14
Create Date: 2026-10-10 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e2f4a6c1b93"
down_revision: Union[str, Sequence[str], None] = "3b7e9c2a5d14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "app"

# Keep in step with SYNC_PROFILE_FUNCTION in geonames_server/database/auth_link.py
SYNC_PROFILE_FUNCTION = """
CREATE OR REPLACE FUNCTION app.sync_profile_from_auth_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    INSERT INTO app.profiles (id, email, banned_until, deleted_at, display_name)
    VALUES (
        NEW.id, NEW.email, NEW.banned_until, NEW.deleted_at,
        left(nullif(btrim(NEW.raw_user_meta_data ->> 'full_name'), ''), 100)
    )
    ON CONFLICT (id) DO UPDATE SET
        email = EXCLUDED.email,
        banned_until = EXCLUDED.banned_until,
        deleted_at = EXCLUDED.deleted_at;
    RETURN NEW;
END
$$
"""

OLD_SYNC_PROFILE_FUNCTION = """
CREATE OR REPLACE FUNCTION app.sync_profile_from_auth_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    INSERT INTO app.profiles (id, email, banned_until, deleted_at)
    VALUES (NEW.id, NEW.email, NEW.banned_until, NEW.deleted_at)
    ON CONFLICT (id) DO UPDATE SET
        email = EXCLUDED.email,
        banned_until = EXCLUDED.banned_until,
        deleted_at = EXCLUDED.deleted_at;
    RETURN NEW;
END
$$
"""


# Only where the signup trigger is installed; without it, startup installs the
# current one once auth.users exists. Checked by the server, so offline (--sql)
# scripts work too.
def _if_signup_trigger(sql: str) -> str:
    literal = "'" + sql.replace("'", "''") + "'"
    return f"""
    DO $do$ BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'app_sync_profile' AND tgrelid = to_regclass('auth.users')
        ) THEN
            EXECUTE {literal};
        END IF;
    END $do$
    """


def upgrade() -> None:
    op.add_column("profiles", sa.Column("display_name", sa.Text()), schema=SCHEMA)
    op.create_check_constraint(
        "profiles_display_name_length",
        "profiles",
        "char_length(display_name) BETWEEN 1 AND 100",
        schema=SCHEMA,
    )
    op.execute(f"GRANT UPDATE (display_name) ON {SCHEMA}.profiles TO geonames_admin")
    op.execute(_if_signup_trigger(SYNC_PROFILE_FUNCTION))
    # Users who gave a name before this migration
    op.execute(
        _if_signup_trigger(
            "UPDATE app.profiles p SET display_name = "
            "left(nullif(btrim(u.raw_user_meta_data ->> 'full_name'), ''), 100) "
            "FROM auth.users u WHERE u.id = p.id"
        )
    )


def downgrade() -> None:
    op.execute(_if_signup_trigger(OLD_SYNC_PROFILE_FUNCTION))
    op.execute(f"REVOKE UPDATE (display_name) ON {SCHEMA}.profiles FROM geonames_admin")
    op.drop_constraint(
        "profiles_display_name_length", "profiles", schema=SCHEMA, type_="check"
    )
    op.drop_column("profiles", "display_name", schema=SCHEMA)
