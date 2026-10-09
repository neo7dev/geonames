"""Link app.profiles to Supabase Auth's users, once auth.users exists.

The accounts migration links them only if auth.users exists when it runs. On a
database that gets the auth schema later (plain Postgres moved to Supabase, or
a restore in the wrong order), the migration is already recorded as applied,
so startup calls link_profiles_to_auth() after the migrations to add what is
missing: the FK that removes a profile (and its keys) with its user, the
trigger that creates a profile for every signup, and profiles for users who
signed up before.

SYNC_PROFILE_FUNCTION must match the latest migration that defines
app.sync_profile_from_auth_user().
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from geonames_server.log.mylogger import get_logger

log = get_logger()

# Runs as its owner (postgres) when GoTrue inserts or updates a user as
# supabase_auth_admin, which has no access to the app schema.
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
CREATE TRIGGER app_sync_profile
AFTER INSERT OR UPDATE OF email, banned_until, deleted_at ON auth.users
FOR EACH ROW EXECUTE FUNCTION app.sync_profile_from_auth_user()
"""


def _exists(conn: Connection, query: str) -> bool:
    return conn.execute(text(f"SELECT EXISTS ({query})")).scalar_one()


def link_profiles_to_auth(conn: Connection) -> bool:
    """Add the auth.users FK, signup trigger and missing profiles. Returns False without auth.users.

    Does nothing when everything is in place, so it takes no locks on
    auth.users on a normal startup. The caller commits.
    """
    if conn.execute(text("SELECT to_regclass('auth.users')")).scalar() is None:
        return False

    if not _exists(
        conn,
        "SELECT 1 FROM pg_constraint WHERE conname = 'profiles_id_fkey' "
        "AND conrelid = 'app.profiles'::regclass",
    ):
        log.warning("Linking app.profiles to auth.users")
        conn.execute(
            text(
                "ALTER TABLE app.profiles ADD CONSTRAINT profiles_id_fkey "
                "FOREIGN KEY (id) REFERENCES auth.users (id) ON DELETE CASCADE"
            )
        )

    if not _exists(
        conn,
        "SELECT 1 FROM pg_trigger WHERE tgname = 'app_sync_profile' "
        "AND tgrelid = 'auth.users'::regclass",
    ):
        log.warning("Installing the signup trigger on auth.users")
        conn.execute(text(SYNC_PROFILE_FUNCTION))
        conn.execute(text(SYNC_PROFILE_TRIGGER))
        # Users who signed up while the trigger was missing
        conn.execute(
            text(
                "INSERT INTO app.profiles (id, email, banned_until, deleted_at) "
                "SELECT id, email, banned_until, deleted_at FROM auth.users "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
    return True
