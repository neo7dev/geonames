"""Integration tests for the app schema's own migration chain (user data).

Needs TEST_PG_DSN pointing at a throwaway database: each test drops the app and
geonames schemas. That database has no Supabase auth schema, so these tests
also cover the migration on plain Postgres; tests/api covers the auth.users
trigger against the Supabase database.
"""

from __future__ import annotations

import io
import os
import threading
from datetime import datetime, timezone
from unittest.mock import patch

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from psycopg2 import sql
from psycopg2.extensions import parse_dsn
from sqlalchemy import URL, create_engine

from geonames_server.config.config import settings
from geonames_server.database import migrations
from geonames_server.database.auth_link import link_profiles_to_auth
from geonames_server.database.models.app_base import APP_SCHEMA

DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN not set")

APP_TABLES = {
    "tiers",
    "roles",
    "profiles",
    "api_keys",
    "api_key_usage",
    "usage_flushes",
    "account_events",
    "alembic_version",
}
API_ROLES = ("anon", "authenticated", "service_role")


def _config(section: str = "app") -> Config:
    params = parse_dsn(DSN)
    url = URL.create(
        "postgresql+psycopg",
        username=params.get("user"),
        password=params.get("password"),
        host=params.get("host"),
        port=int(params["port"]) if params.get("port") else None,
        database=params.get("dbname"),
    )
    cfg = Config(str(settings.alembic_ini), ini_section=section)
    # ConfigParser treats % as interpolation
    cfg.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    return cfg


def _query(query: str | sql.Composable, *args) -> list[tuple]:
    """Run one statement, commit, and return its rows (if any)."""
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(query, args)
            rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


def _tables(schema: str) -> set[str]:
    rows = _query("SELECT tablename FROM pg_tables WHERE schemaname = %s", schema)
    return {name for (name,) in rows}


def _reset() -> None:
    for schema in (APP_SCHEMA, settings.postgres_schema):
        _query(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )


@pytest.fixture(autouse=True)
def clean_db():
    _reset()
    yield
    _reset()


def test_fresh_database_creates_app_tables_and_seeds():
    command.upgrade(_config(), "head")

    assert _tables(APP_SCHEMA) == APP_TABLES
    assert _query(
        "SELECT name, per_minute, per_hour, per_month, max_keys FROM app.tiers ORDER BY per_minute"
    ) == [
        ("basic", 10, 100, 5000, 1),
        ("plus", 30, 500, 50000, 3),
        ("pro", 120, 2500, 500000, 5),
        ("max", None, None, None, None),
    ]
    assert _query("SELECT name, tier, is_admin FROM app.roles ORDER BY name") == [
        ("admin", "max", True),
        ("dev", "pro", False),
        ("free", "basic", False),
        ("sr_dev", "max", False),
        ("tester", "plus", False),
    ]


def test_without_supabase_auth_profiles_are_not_linked():
    command.upgrade(_config(), "head")

    assert _query("SELECT to_regclass('auth.users')") == [(None,)]
    fks = _query(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'app.profiles'::regclass AND contype = 'f'"
    )
    assert fks == [("profiles_role_fkey",)]


def test_api_roles_get_no_access():
    command.upgrade(_config(), "head")

    grants = _query(
        "SELECT grantee, table_name FROM information_schema.role_table_grants "
        "WHERE table_schema = %s AND grantee = ANY(%s)",
        APP_SCHEMA,
        list(API_ROLES),
    )
    assert grants == []


def test_admin_role_may_read_accounts_and_change_name_role_and_flags():
    command.upgrade(_config(), "head")

    tables = _query(
        "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = %s AND grantee = 'geonames_admin' ORDER BY 1, 2",
        APP_SCHEMA,
    )
    columns = _query(
        "SELECT column_name FROM information_schema.column_privileges "
        "WHERE table_schema = %s AND table_name = 'profiles' "
        "AND grantee = 'geonames_admin' AND privilege_type = 'UPDATE' ORDER BY 1",
        APP_SCHEMA,
    )
    assert tables == [
        ("account_events", "SELECT"),
        ("api_keys", "SELECT"),
        ("profiles", "SELECT"),
        ("roles", "SELECT"),
        ("tiers", "SELECT"),
    ]
    assert columns == [("display_name",), ("is_active",), ("is_deleted",), ("role",)]
    # The migrating user can switch to it
    assert _query("SELECT pg_has_role(current_user, 'geonames_admin', 'SET')") == [
        (True,)
    ]


@pytest.mark.parametrize("view", ["user_tiers", "monthly_usage"])
def test_views_check_the_callers_privileges(view):
    command.upgrade(_config(), "head")

    assert _query(
        "SELECT reloptions FROM pg_class WHERE oid = %s::regclass", f"app.{view}"
    ) == [(["security_invoker=true"],)]
    assert _query(f"SELECT count(*) FROM app.{view}") == [(0,)]
    grants = _query(
        "SELECT grantee FROM information_schema.role_table_grants "
        "WHERE table_schema = %s AND table_name = %s AND grantee = ANY(%s)",
        APP_SCHEMA,
        view,
        list(API_ROLES),
    )
    assert grants == []


def test_monthly_usage_sums_a_users_keys_per_utc_month():
    command.upgrade(_config(), "head")
    _query(
        "INSERT INTO app.profiles (id, email) VALUES "
        "('00000000-0000-0000-0000-000000000001', 'u@example.com');"
        "INSERT INTO app.api_keys (id, user_id, name, prefix, key_hash, revoked_at) VALUES "
        "('00000000-0000-0000-0000-00000000000a', "
        "'00000000-0000-0000-0000-000000000001', 'a', 'gn_a', 'ha', NULL), "
        "('00000000-0000-0000-0000-00000000000b', "
        "'00000000-0000-0000-0000-000000000001', 'b', 'gn_b', 'hb', now());"
        "INSERT INTO app.api_key_usage (key_id, hour, requests) VALUES "
        "('00000000-0000-0000-0000-00000000000a', '2026-09-30 23:00+00', 4), "
        "('00000000-0000-0000-0000-00000000000a', '2026-10-01 00:00+00', 5), "
        "('00000000-0000-0000-0000-00000000000b', '2026-10-09 12:00+00', 6)"
    )

    rows = _query(
        "SELECT email, tier, month::text, requests, per_month "
        "FROM app.monthly_usage ORDER BY month"
    )

    assert rows == [
        ("u@example.com", "basic", "2026-09-01", 4, 5000),
        ("u@example.com", "basic", "2026-10-01", 11, 5000),
    ]


def test_downgrade_and_upgrade_again():
    cfg = _config()
    command.upgrade(cfg, "head")

    command.downgrade(cfg, "base")
    assert _tables(APP_SCHEMA) == {"alembic_version"}
    assert _query("SELECT to_regclass('app.user_tiers')") == [(None,)]
    assert _query("SELECT to_regclass('app.monthly_usage')") == [(None,)]

    command.upgrade(cfg, "head")
    assert _tables(APP_SCHEMA) == APP_TABLES


def test_force_rebuild_keeps_user_data():
    engine = create_engine(
        "postgresql+psycopg2://",
        creator=lambda: psycopg2.connect(DSN, options=settings.postgres_options),
    )
    try:
        with (
            patch.object(
                migrations,
                "_alembic_config",
                lambda section="alembic": _config(section),
            ),
            patch.object(migrations, "sync_engine", engine),
        ):
            migrations.db_migrations()
            _query(
                "INSERT INTO app.profiles (id, email, role) "
                "VALUES (gen_random_uuid(), 'kept@example.com', 'dev')"
            )

            migrations.db_migrations(force=True)
    finally:
        engine.dispose()

    assert _query("SELECT email, role FROM app.profiles") == [
        ("kept@example.com", "dev")
    ]
    assert "dataset_metadata" in _tables(settings.postgres_schema)


def test_models_match_migrations():
    cfg = _config()
    command.upgrade(cfg, "head")

    command.check(cfg)


@pytest.fixture
def late_auth_schema():
    """A minimal auth.users, created after the app migrations ran without it."""
    yield lambda: _query(
        "CREATE SCHEMA auth; CREATE TABLE auth.users "
        "(id uuid PRIMARY KEY, email text, banned_until timestamptz, deleted_at timestamptz, "
        "raw_user_meta_data jsonb)"
    )
    _query("DROP SCHEMA IF EXISTS auth CASCADE")


def test_profiles_are_linked_once_auth_users_appears(late_auth_schema):
    command.upgrade(_config(), "head")
    late_auth_schema()
    _query(
        "INSERT INTO auth.users (id, email) "
        "VALUES ('00000000-0000-0000-0000-000000000001', 'early@example.com')"
    )

    engine = create_engine(
        _config().get_main_option("sqlalchemy.url").replace("%%", "%")
    )
    try:
        for _ in range(2):  # the second run finds everything in place
            with engine.begin() as conn:
                assert link_profiles_to_auth(conn)
    finally:
        engine.dispose()

    # Backfilled, then created by the trigger on signup
    _query(
        "INSERT INTO auth.users (id, email) "
        "VALUES ('00000000-0000-0000-0000-000000000002', 'late@example.com')"
    )
    assert _query("SELECT email, role FROM app.profiles ORDER BY email") == [
        ("early@example.com", "free"),
        ("late@example.com", "free"),
    ]
    # Deleting a user removes the profile
    _query("DELETE FROM auth.users WHERE email = 'early@example.com'")
    assert _query("SELECT email FROM app.profiles") == [("late@example.com",)]


def test_linking_is_skipped_without_auth_users():
    command.upgrade(_config(), "head")

    engine = create_engine(
        _config().get_main_option("sqlalchemy.url").replace("%%", "%")
    )
    try:
        with engine.begin() as conn:
            assert not link_profiles_to_auth(conn)
    finally:
        engine.dispose()


def _offline_sql(*args) -> str:
    """The SQL script `alembic -n app upgrade/downgrade --sql` prints."""
    cfg = _config()
    cfg.output_buffer = io.StringIO()
    getattr(command, args[0])(cfg, args[1], sql=True)
    return cfg.output_buffer.getvalue()


def _run_script(script: str) -> None:
    conn = psycopg2.connect(DSN)
    conn.autocommit = True  # the script has its own BEGIN / COMMIT
    try:
        with conn.cursor() as cur:
            cur.execute(script)  # no parameters, so % is not a placeholder
    finally:
        conn.close()


def test_offline_scripts_build_and_remove_the_schema():
    _run_script(_offline_sql("upgrade", "head"))
    assert _tables(APP_SCHEMA) == APP_TABLES
    assert _query("SELECT name FROM app.roles WHERE is_default") == [("free",)]

    _run_script(_offline_sql("downgrade", "head:base"))
    # Offline downgrades to base drop the version table as well
    assert _tables(APP_SCHEMA) == set()


def test_app_migrations_wait_for_the_migration_lock():
    engine = create_engine(
        "postgresql+psycopg2://",
        creator=lambda: psycopg2.connect(DSN, options=settings.postgres_options),
    )
    holder = psycopg2.connect(DSN)
    try:
        with (
            patch.object(
                migrations,
                "_alembic_config",
                lambda section="alembic": _config(section),
            ),
            patch.object(migrations, "sync_engine", engine),
        ):
            with holder.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_lock(%s)", (migrations._MIGRATION_LOCK_KEY,)
                )
            worker = threading.Thread(target=migrations.upgrade_app_schema)
            worker.start()

            worker.join(timeout=1)
            assert worker.is_alive()
            assert _tables(APP_SCHEMA) == set()

            with holder.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(%s)", (migrations._MIGRATION_LOCK_KEY,)
                )
            worker.join(timeout=10)
            assert not worker.is_alive()
    finally:
        holder.close()
        engine.dispose()

    assert _tables(APP_SCHEMA) == APP_TABLES


def test_downgrade_restores_only_the_max_tiers_per_minute_cap():
    cfg = _config()
    command.upgrade(cfg, "head")
    _query("INSERT INTO app.tiers (name, per_minute) VALUES ('enterprise', NULL)")

    with pytest.raises(Exception, match="Tiers without a per-minute limit: enterprise"):
        command.downgrade(cfg, "7c3e5a1f9b2d")

    _query("UPDATE app.tiers SET per_minute = 50 WHERE name = 'enterprise'")
    command.downgrade(cfg, "7c3e5a1f9b2d")
    assert _query(
        "SELECT name, per_minute FROM app.tiers WHERE name IN ('max', 'enterprise') ORDER BY name"
    ) == [("enterprise", 50), ("max", 600)]


def test_existing_accounts_get_their_signup_logged():
    cfg = _config()
    command.upgrade(cfg, "8e2f4a6c1b93")
    _query(
        "INSERT INTO app.profiles (id, email, created_at) "
        "VALUES ('00000000-0000-0000-0000-00000000000a', 'old@example.com', "
        "'2026-01-02 03:04:05+00')"
    )

    command.upgrade(cfg, "head")

    assert _query(
        "SELECT user_email, event, source, actor_id = user_id, "
        "details ->> 'backfilled', created_at FROM app.account_events"
    ) == [
        (
            "old@example.com",
            "signed_up",
            "user",
            True,
            "true",
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        )
    ]
