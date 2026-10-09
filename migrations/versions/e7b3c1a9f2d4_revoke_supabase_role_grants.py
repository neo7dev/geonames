"""revoke_supabase_role_grants

Tables created in Supabase's public schema were automatically granted to the
anon, authenticated and service_role API roles, and kept those grants when
env.py moved them into the geonames schema. Revoke them: only the app's own
role reads these tables. New tables in the geonames schema get no such grants.

The roles only exist on Supabase; on plain Postgres this does nothing.

Revision ID: e7b3c1a9f2d4
Revises: 97d04a096bc4
Create Date: 2026-10-09 20:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7b3c1a9f2d4"
down_revision: Union[str, Sequence[str], None] = "97d04a096bc4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            api_role text;
        BEGIN
            FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
                    EXECUTE format(
                        'REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I',
                        current_schema(), api_role
                    );
                    EXECUTE format(
                        'REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %I',
                        current_schema(), api_role
                    );
                END IF;
            END LOOP;
        END
        $$;
        """
    )


def downgrade() -> None:
    # The grants were Supabase defaults, never something this app relied on.
    pass
