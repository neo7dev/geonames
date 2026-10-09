"""Integration tests for the app schema's own migration chain (user data).

Needs TEST_PG_DSN pointing at a throwaway database: each test drops the app and
geonames schemas. That database has no Supabase auth schema, so these tests
also cover the migration on plain Postgres; tests/api covers the auth.users
trigger against the Supabase database.
"""

from __future__ import annotations

import os
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
from geonames_server.database.models.app_base import APP_SCHEMA

DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN not set")

APP_TABLES = {"tiers", "roles", "profiles", "api_keys", "alembic_version"}
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
        _query(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture(autouse=True)
def clean_db():
    _reset()
    yield
    _reset()


def test_fresh_database_creates_app_tables_and_seeds():
    command.upgrade(_config(), "head")

    assert _tables(APP_SCHEMA) == APP_TABLES
    assert _query("SELECT name, per_minute, per_hour, per_month, max_keys FROM app.tiers ORDER BY per_minute") == [
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


def test_user_tiers_view_checks_the_callers_privileges():
    command.upgrade(_config(), "head")

    assert _query(
        "SELECT reloptions FROM pg_class WHERE oid = 'app.user_tiers'::regclass"
    ) == [(["security_invoker=true"],)]
    assert _query("SELECT count(*) FROM app.user_tiers") == [(0,)]
    grants = _query(
        "SELECT grantee FROM information_schema.role_table_grants "
        "WHERE table_schema = %s AND table_name = 'user_tiers' AND grantee = ANY(%s)",
        APP_SCHEMA,
        list(API_ROLES),
    )
    assert grants == []


def test_downgrade_and_upgrade_again():
    cfg = _config()
    command.upgrade(cfg, "head")

    command.downgrade(cfg, "base")
    assert _tables(APP_SCHEMA) == {"alembic_version"}
    assert _query("SELECT to_regclass('app.user_tiers')") == [(None,)]

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
                migrations, "_alembic_config", lambda section="alembic": _config(section)
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

    assert _query("SELECT email, role FROM app.profiles") == [("kept@example.com", "dev")]
    assert "dataset_metadata" in _tables(settings.postgres_schema)


def test_models_match_migrations():
    cfg = _config()
    command.upgrade(cfg, "head")

    command.check(cfg)
