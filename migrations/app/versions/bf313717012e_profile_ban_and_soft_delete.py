"""profile ban and soft delete

Banning a user in Supabase Auth sets auth.users.banned_until, and a soft delete
sets deleted_at but keeps the row, so the profile and its API keys stayed and
the keys kept working. The signup trigger now copies both columns onto
app.profiles, where the API checks them.

Revision ID: bf313717012e
Revises: a79ec0362d52
Create Date: 2026-10-09 16:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bf313717012e"
down_revision: Union[str, Sequence[str], None] = "a79ec0362d52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "app"

# Keep in step with SYNC_PROFILE_FUNCTION in geonames_server/database/auth_link.py
SYNC_PROFILE_FUNCTION = """
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

SYNC_PROFILE_TRIGGER = """
CREATE OR REPLACE TRIGGER app_sync_profile
AFTER INSERT OR UPDATE OF email, banned_until, deleted_at ON auth.users
FOR EACH ROW EXECUTE FUNCTION app.sync_profile_from_auth_user()
"""

OLD_SYNC_PROFILE_FUNCTION = """
CREATE OR REPLACE FUNCTION app.sync_profile_from_auth_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    INSERT INTO app.profiles (id, email) VALUES (NEW.id, NEW.email)
    ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email;
    RETURN NEW;
END
$$
"""

OLD_SYNC_PROFILE_TRIGGER = """
CREATE OR REPLACE TRIGGER app_sync_profile
AFTER INSERT OR UPDATE OF email ON auth.users
FOR EACH ROW EXECUTE FUNCTION app.sync_profile_from_auth_user()
"""


# Only where the signup trigger is installed; without it, startup installs the
# current one once auth.users exists. Checked by the server, so offline (--sql)
# scripts work too.
def _if_signup_trigger(*statements: str) -> str:
    body = "\n".join(f"EXECUTE {_literal(sql)};" for sql in statements)
    return f"""
    DO $do$ BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'app_sync_profile' AND tgrelid = to_regclass('auth.users')
        ) THEN
            {body}
        END IF;
    END $do$
    """


def _literal(sql: str) -> str:
    return "'" + sql.replace("'", "''") + "'"


def upgrade() -> None:
    op.add_column(
        "profiles", sa.Column("banned_until", sa.DateTime(timezone=True)), schema=SCHEMA
    )
    op.add_column(
        "profiles", sa.Column("deleted_at", sa.DateTime(timezone=True)), schema=SCHEMA
    )
    op.execute(
        _if_signup_trigger(
            SYNC_PROFILE_FUNCTION,
            SYNC_PROFILE_TRIGGER,
            "UPDATE app.profiles p SET banned_until = u.banned_until, "
            "deleted_at = u.deleted_at FROM auth.users u WHERE u.id = p.id",
        )
    )


def downgrade() -> None:
    op.execute(_if_signup_trigger(OLD_SYNC_PROFILE_TRIGGER, OLD_SYNC_PROFILE_FUNCTION))
    op.drop_column("profiles", "deleted_at", schema=SCHEMA)
    op.drop_column("profiles", "banned_until", schema=SCHEMA)
