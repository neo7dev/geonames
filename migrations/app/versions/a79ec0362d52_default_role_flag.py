"""default role flag

New profiles got the role named 'free', hard-coded as profiles.role's default.
Roles are edited in Studio, so renaming or deleting 'free' made every signup
fail. The default role is now the one with roles.is_default, looked up by
app.default_role(), and a deferred constraint trigger keeps exactly one role
marked as default.

Revision ID: a79ec0362d52
Revises: 9a4d2b6e1c8f
Create Date: 2026-10-09 16:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a79ec0362d52"
down_revision: Union[str, Sequence[str], None] = "9a4d2b6e1c8f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "app"

DEFAULT_ROLE_FUNCTION = """
CREATE FUNCTION app.default_role() RETURNS text
LANGUAGE sql STABLE SET search_path = '' AS $$
    SELECT name FROM app.roles WHERE is_default
$$
"""

# Deferred, so switching the default (unmark one role, mark another) can be
# done in any order within a transaction.
CHECK_ONE_DEFAULT_FUNCTION = """
CREATE FUNCTION app.check_one_default_role() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE
    defaults integer;
BEGIN
    SELECT count(*) INTO defaults FROM app.roles WHERE is_default;
    IF defaults <> 1 THEN
        RAISE EXCEPTION 'app.roles must have exactly one default role, found %', defaults
            USING HINT = 'New users get the role with is_default = true; mark exactly one.';
    END IF;
    RETURN NULL;
END
$$
"""

CHECK_ONE_DEFAULT_TRIGGER = """
CREATE CONSTRAINT TRIGGER roles_one_default
AFTER INSERT OR UPDATE OF is_default OR DELETE ON app.roles
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app.check_one_default_role()
"""


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        schema=SCHEMA,
    )
    op.execute("UPDATE app.roles SET is_default = true WHERE name = 'free'")
    op.execute(DEFAULT_ROLE_FUNCTION)
    op.execute(CHECK_ONE_DEFAULT_FUNCTION)
    op.execute(CHECK_ONE_DEFAULT_TRIGGER)
    op.alter_column(
        "profiles",
        "role",
        existing_type=sa.Text(),
        server_default=sa.text("app.default_role()"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Back to a fixed default: whichever role is the default now. Looked up by
    # the server, so offline (--sql) scripts work too.
    op.execute(
        """
        DO $$ BEGIN
            EXECUTE format(
                'ALTER TABLE app.profiles ALTER COLUMN role SET DEFAULT %L',
                app.default_role()
            );
        END $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS roles_one_default ON app.roles")
    op.execute("DROP FUNCTION IF EXISTS app.check_one_default_role()")
    op.execute("DROP FUNCTION IF EXISTS app.default_role()")
    op.drop_column("roles", "is_default", schema=SCHEMA)
